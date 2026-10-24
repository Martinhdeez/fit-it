import os
import sys
import time
import uuid

import psycopg
import pytest

import db
import hooks

LOOP = 'ROUTE = "GET /api/x"\n\ndef run(data, ctx):\n    while True:\n        pass\n'
CALLS = 'ROUTE = "GET /api/x"\n\ndef run(data, ctx):\n    while True:\n        ctx.api.get("/api/y")\n'


class Api:
    def get(self, path):
        return None


def returning(expr):
    return f'ROUTE = "GET /api/x"\n\ndef run(data, ctx):\n    return {expr}\n'


@pytest.fixture(autouse=True)
def database(monkeypatch):
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    monkeypatch.setenv("DATABASE_URL", f"{base}?options=-csearch_path%3D{schema}")
    monkeypatch.setenv("FITIT_HOOK_TIMEOUT_MS", "100")
    db.init()
    yield
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def run(code):
    hooks.save_hook("u1", "h", code)
    return hooks.run_hooks("u1", "GET", "/api/x", [1], hooks.Ctx("u1", Api()))


@pytest.mark.parametrize("code", [LOOP, CALLS])
def test_loop_hits_time_limit(code):
    start = time.monotonic()
    with pytest.raises(hooks.HookError) as e:
        run(code)
    assert time.monotonic() - start < 1
    assert e.value.hook == "h" and "time limit" in str(e.value)


def test_dry_run_hits_time_limit():
    with pytest.raises(hooks.HookError) as e:
        hooks.dry_run("u1", LOOP, None, Api())
    assert e.value.hook is None and "time limit" in str(e.value)


def test_previous_tracer_restored():
    def mine(frame, event, arg):
        return None

    sys.settrace(mine)
    try:
        run(returning("data"))
        assert sys.gettrace() is mine
        with pytest.raises(hooks.HookError):
            run(LOOP)
        assert sys.gettrace() is mine
    finally:
        sys.settrace(None)


def test_output_cap(monkeypatch):
    monkeypatch.setenv("FITIT_HOOK_MAX_BYTES", "1000")
    assert run(returning('"ñ" * 400'))[0] == "ñ" * 400  # 802 bytes as UTF-8 JSON
    with pytest.raises(hooks.HookError) as e:
        run(returning('"ñ" * 600'))  # 1202 bytes
    assert "output" in str(e.value)


def test_defaults_let_normal_hooks_run(monkeypatch):
    monkeypatch.delenv("FITIT_HOOK_TIMEOUT_MS")
    assert run(returning("data + [2]"))[0] == [1, 2]
