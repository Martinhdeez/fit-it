"""Acceptance tests for SF1 (hook time limit and output cap), written from
docs/plan/02-postgres/, docs/specs/hook-contract.md (v2, "Limits"), proxy.md (v2),
storage.md and ADR 0020 only.

Every call that may hang runs in a daemon thread with a watchdog (`within`), so a
missing time limit fails the test instead of blocking the suite. The direct
"loop forever" hook spins on `while not data["stop"]`: the test never sets the
flag before its assertions, and sets it afterwards so a leaked thread can exit.
"""

import os
import sys
import textwrap
import threading
import time
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import hooks
from main import app

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
USER = "alice"
SLACK = 1.0  # contracts.md: allowed slack is one second over the limit
WATCHDOG = 6.0

LOOP_FOREVER = textwrap.dedent('''\
    ROUTE = "GET /api/loop"

    def run(data, ctx):
        while not data["stop"]:
            pass
        return data
''')

BUSY_ONE_SECOND = textwrap.dedent('''\
    ROUTE = "GET /api/busy"

    def run(data, ctx):
        end = datetime.now() + timedelta(seconds=1)
        while datetime.now() < end:
            pass
        return {"done": True}
''')

# Through the core the data comes from the app, so this loop has no flag; it is
# "forever" relative to any limit under test and ends on its own after 30 s.
CORE_LOOP = textwrap.dedent('''\
    ROUTE = "GET /api/invoices"

    def run(data, ctx):
        end = datetime.now() + timedelta(seconds=30)
        while datetime.now() < end:
            pass
        return data
''')


def returns_string(route, size):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n    return "{"x" * size}"\n'


@pytest.fixture
def database(monkeypatch):
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{base}?options=-csearch_path%3D{schema}"
    monkeypatch.setenv("DATABASE_URL", url)
    db.init()
    yield url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


class FakeApi:
    def get(self, path):
        return {"path": path}


def ctx(user=USER):
    return hooks.Ctx(user, FakeApi())


def within(fn, release=None):
    """Run fn in a daemon thread. Returns {"value"|"error", "elapsed"}; fails if it hangs."""
    out = {}

    def target():
        start = time.monotonic()
        try:
            out["value"] = fn()
        except BaseException as e:
            out["error"] = e
        out["elapsed"] = time.monotonic() - start

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(WATCHDOG)
    if release is not None:
        release()
    assert not t.is_alive(), f"call did not return within {WATCHDOG} s"
    return out


def run_loop(name="looper"):
    hooks.save_hook(USER, name, LOOP_FOREVER)
    data = {"stop": False}
    return within(lambda: hooks.run_hooks(USER, "GET", "/api/loop", data, ctx()),
                  release=lambda: data.update(stop=True))


# REQ-SF1-1 -----------------------------------------------------------------

def test_REQ_SF1_1_loop_forever_fails_with_time_limit(database, monkeypatch):
    monkeypatch.setenv("FITIT_HOOK_TIMEOUT_MS", "200")
    out = run_loop("looper")
    assert isinstance(out.get("error"), hooks.HookError), out
    assert out["error"].hook == "looper"
    assert "time limit" in str(out["error"])
    assert out["elapsed"] < 0.2 + SLACK


# REQ-SF1-2 -----------------------------------------------------------------

def test_REQ_SF1_2_core_returns_502_without_app_data(database, monkeypatch):
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_HOOK_TIMEOUT_MS", "200")
    hooks.save_hook("gerente", "looper", CORE_LOOP)
    client = TestClient(app)
    assert client.get("/login?user=gerente").status_code == 200

    # Baseline: the same request for a user without hooks (session lookup + forwarding).
    other = TestClient(app)
    other.get("/login?user=recepcion")
    start = time.monotonic()
    assert other.get("/api/invoices").status_code == 200
    baseline = time.monotonic() - start

    out = within(lambda: client.get("/api/invoices"))
    r = out["value"]
    assert r.status_code == 502
    body = r.json()
    assert body["error"] == "hook_failed"
    assert body["hook"] == "looper"
    for invoice in httpx.get(DEMO_URL + "/api/invoices", cookies={"session": "gerente"}).json():
        assert invoice["patient"] not in r.text
    assert out["elapsed"] < 0.2 + SLACK + baseline


# REQ-SF1-3 -----------------------------------------------------------------

def test_REQ_SF1_3_dry_run_loop_forever_fails(database, monkeypatch):
    monkeypatch.setenv("FITIT_HOOK_TIMEOUT_MS", "200")
    data = {"stop": False}
    out = within(lambda: hooks.dry_run(USER, LOOP_FOREVER, data, FakeApi()),
                 release=lambda: data.update(stop=True))
    assert isinstance(out.get("error"), hooks.HookError), out
    assert out["error"].hook is None
    assert out["elapsed"] < 0.2 + SLACK


# REQ-SF1-4 -----------------------------------------------------------------

def test_REQ_SF1_4_output_over_cap_fails(database, monkeypatch):
    monkeypatch.setenv("FITIT_HOOK_MAX_BYTES", "1000")
    hooks.save_hook(USER, "big", returns_string("GET /api/big", 2000))  # 2002 bytes as JSON
    with pytest.raises(hooks.HookError) as err:
        hooks.run_hooks(USER, "GET", "/api/big", {}, ctx())
    assert err.value.hook == "big"
    assert "output" in str(err.value)


def test_REQ_SF1_4_output_under_cap_runs(database, monkeypatch):
    monkeypatch.setenv("FITIT_HOOK_MAX_BYTES", "1000")
    hooks.save_hook(USER, "small", returns_string("GET /api/small", 500))  # 502 bytes as JSON
    result, applied = hooks.run_hooks(USER, "GET", "/api/small", {}, ctx())
    assert result == "x" * 500
    assert [a["name"] for a in applied] == ["small"]


# REQ-SF1-5 -----------------------------------------------------------------

def test_REQ_SF1_5_defaults_allow_one_second_busy_wait(database, monkeypatch):
    monkeypatch.delenv("FITIT_HOOK_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("FITIT_HOOK_MAX_BYTES", raising=False)
    hooks.save_hook(USER, "busy", BUSY_ONE_SECOND)
    out = within(lambda: hooks.run_hooks(USER, "GET", "/api/busy", {}, ctx()))
    assert "error" not in out, out
    result, applied = out["value"]
    assert result == {"done": True}
    assert [a["name"] for a in applied] == ["busy"]


def test_REQ_SF1_5_defaults_stop_loop_forever_within_three_seconds(database, monkeypatch):
    monkeypatch.delenv("FITIT_HOOK_TIMEOUT_MS", raising=False)
    monkeypatch.delenv("FITIT_HOOK_MAX_BYTES", raising=False)
    out = run_loop("looper")
    assert isinstance(out.get("error"), hooks.HookError), out
    assert out["error"].hook == "looper"
    assert out["elapsed"] < 3.0


# REQ-SF1-6 -----------------------------------------------------------------

def tracer(frame, event, arg):
    return None


def with_tracer(fn):
    """Install `tracer` on the calling thread, run fn, report sys.gettrace() afterwards."""
    def call():
        sys.settrace(tracer)
        try:
            try:
                fn()
            except hooks.HookError:
                pass
            return sys.gettrace()
        finally:
            sys.settrace(None)
    return call


def test_REQ_SF1_6_tracer_restored_after_normal_run(database, monkeypatch):
    monkeypatch.setenv("FITIT_HOOK_TIMEOUT_MS", "200")
    hooks.save_hook(USER, "small", returns_string("GET /api/small", 10))
    out = within(with_tracer(lambda: hooks.run_hooks(USER, "GET", "/api/small", {}, ctx())))
    assert "error" not in out, out
    assert out["value"] is tracer


def test_REQ_SF1_6_tracer_restored_after_timeout(database, monkeypatch):
    monkeypatch.setenv("FITIT_HOOK_TIMEOUT_MS", "200")
    hooks.save_hook(USER, "looper", LOOP_FOREVER)
    data = {"stop": False}
    out = within(with_tracer(lambda: hooks.run_hooks(USER, "GET", "/api/loop", data, ctx())),
                 release=lambda: data.update(stop=True))
    assert "error" not in out, out
    assert out["value"] is tracer
