"""Acceptance tests for HS2 (hook storage on Postgres, versions, undo, dry_run, per-hook timing).

Written from docs/plan/02-postgres/, docs/specs/ (hook-contract v2, storage v1, proxy v2,
trace-events) and docs/adr/ only.
"""

import os
import pathlib
import uuid

import httpx
import pytest
from fastapi.testclient import TestClient

import hooks
import trace
from main import app

# Until DB1 lands, psycopg and db are missing; keep the file collectable so tests fail, not error.
try:
    import psycopg

    import db
except ImportError:
    psycopg = db = None

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
USER = "alice"


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


@pytest.fixture
def core_env(database, monkeypatch):
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    return database


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **fields: recorded.append({"kind": kind, **fields}))
    return recorded


class FakeApi:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return self.responses[path]


def hook(route, body="return data"):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n' + "".join(f"    {line}\n" for line in body.splitlines())


def sql(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def hook_row(url, user, name):
    rows = sql(url, "SELECT user_id, name, route, code, enabled, version FROM hooks WHERE user_id = %s AND name = %s", user, name)
    return rows[0] if rows else None


def versions(url, user, name):
    return sql(url, "SELECT version, code FROM hook_versions WHERE user_id = %s AND name = %s ORDER BY version", user, name)


def counts(url):
    return tuple(sql(url, f"SELECT count(*) FROM {t}")[0][0] for t in ("hooks", "hook_versions", "messages"))


def insert(url, user, name, code, route="GET /api/x", enabled=True):
    with psycopg.connect(url) as conn:
        conn.execute(
            "INSERT INTO hooks (user_id, name, route, code, enabled, version) VALUES (%s, %s, %s, %s, %s, 1)",
            (user, name, route, code, enabled),
        )


def run(user, method, path, data, api=None):
    return hooks.run_hooks(user, method, path, data, hooks.Ctx(user, api or FakeApi()))


def login(user):
    client = TestClient(app)
    assert client.get(f"/login?user={user}").status_code == 200
    return client


def demo(path, user):
    return httpx.get(DEMO_URL + path, cookies={"session": user}).json()


def of_kind(events, kind):
    return [e for e in events if e["kind"] == kind]


V1 = hook("GET /api/invoices", "for i in data:\n    i['tag'] = 'v1'\nreturn data")
V2 = hook("GET /api/invoices", "for i in data:\n    i['tag'] = 'v2'\nreturn data")
V3 = hook("GET /api/invoices", "for i in data:\n    i['tag'] = 'v3'\nreturn data")


# REQ-HS2-1 -----------------------------------------------------------------

def test_REQ_HS2_1_save_writes_one_row(database):
    code = hook("GET /api/x", "ctx.log('hi')\nreturn data")
    hooks.save_hook(USER, "my_hook", code)
    assert sql(database, "SELECT user_id, name, route, code, enabled, version FROM hooks") == [
        (USER, "my_hook", "GET /api/x", code, True, 1)
    ]


def test_REQ_HS2_1_save_returns_diff_from_empty_with_labels(database):
    code = hook("GET /api/x", "ctx.log('hi')\nreturn data")
    lines = [line.rstrip("\n") for line in hooks.save_hook(USER, "my_hook", code).splitlines()]
    assert any(line.startswith("--- a/my_hook.py") for line in lines)
    assert any(line.startswith("+++ b/my_hook.py") for line in lines)
    assert any(line.startswith("@@") for line in lines)
    for source_line in code.splitlines():
        assert "+" + source_line in lines
    body = [line for line in lines if not line.startswith(("---", "+++", "@@"))]
    assert not any(line.startswith("-") for line in body)


@pytest.mark.parametrize("user,name,code", [
    ("../alice", "my_hook", hook("GET /api/x")),
    ("", "my_hook", hook("GET /api/x")),
    ("a" * 65, "my_hook", hook("GET /api/x")),
    (USER, "Bad-Name", hook("GET /api/x")),
    (USER, "a" * 41, hook("GET /api/x")),
    (USER, "my_hook", "import os\n" + hook("GET /api/x")),
    (USER, "my_hook", hook("GET /api/x", "return eval('1')")),
], ids=["user_traversal", "user_empty", "user_too_long", "name_bad_chars", "name_too_long", "code_import", "code_eval"])
def test_REQ_HS2_1_invalid_input_raises_and_writes_nothing(database, user, name, code):
    with pytest.raises(ValueError):
        hooks.save_hook(user, name, code)
    assert sql(database, "SELECT 1 FROM hooks") == []
    assert sql(database, "SELECT 1 FROM hook_versions") == []


# REQ-HS2-2 -----------------------------------------------------------------

def test_REQ_HS2_2_each_save_adds_next_version(database):
    for n, code in enumerate([V1, V2, V3], start=1):
        hooks.save_hook(USER, "h", code)
        assert versions(database, USER, "h") == [(v, c) for v, c in zip(range(1, n + 1), [V1, V2, V3])]
        assert hook_row(database, USER, "h")[5] == n
        assert hook_row(database, USER, "h")[3] == code


def test_REQ_HS2_2_versions_are_per_user_and_name(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "other", V2)
    hooks.save_hook("bob", "h", V3)
    assert versions(database, USER, "h") == [(1, V1)]
    assert versions(database, USER, "other") == [(1, V2)]
    assert versions(database, "bob", "h") == [(1, V3)]


def test_REQ_HS2_2_toggle_adds_no_version(database):
    hooks.save_hook(USER, "h", V1)
    hooks.toggle_hook(USER, "h")
    hooks.toggle_hook(USER, "h")
    assert versions(database, USER, "h") == [(1, V1)]
    assert hook_row(database, USER, "h")[5] == 1


def test_REQ_HS2_2_delete_keeps_versions_and_resave_continues(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "h", V2)
    hooks.delete_hook(USER, "h")
    assert hook_row(database, USER, "h") is None
    assert versions(database, USER, "h") == [(1, V1), (2, V2)]

    hooks.save_hook(USER, "h", V3)
    assert versions(database, USER, "h") == [(1, V1), (2, V2), (3, V3)]
    assert hook_row(database, USER, "h")[3:] == (V3, True, 3)


# REQ-HS2-3 -----------------------------------------------------------------

def test_REQ_HS2_3_list_hooks_sorted_with_enabled(database):
    b, a, c = hook("GET /api/b"), hook("GET /x/a"), hook("POST /api/c")
    hooks.save_hook(USER, "b", b)
    hooks.save_hook(USER, "a", a)
    hooks.save_hook(USER, "c", c)
    hooks.toggle_hook(USER, "b")
    hooks.save_hook("bob", "z", hook("GET /api/z"))
    assert hooks.list_hooks(USER) == [
        {"name": "a", "route": "GET /x/a", "enabled": True, "code": a},
        {"name": "b", "route": "GET /api/b", "enabled": False, "code": b},
        {"name": "c", "route": "POST /api/c", "enabled": True, "code": c},
    ]


@pytest.mark.parametrize("user", ["carol", "../alice", "", "a" * 65])
def test_REQ_HS2_3_list_hooks_empty(database, user):
    hooks.save_hook(USER, "mine", hook("GET /api/x"))
    assert hooks.list_hooks(user) == []


def test_REQ_HS2_3_read_hook(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "h", V2)
    assert hooks.read_hook(USER, "h") == V2


@pytest.mark.parametrize("fn", ["read_hook", "toggle_hook", "delete_hook"])
def test_REQ_HS2_3_missing_hook_keyerror(database, fn):
    hooks.save_hook("bob", "h", V1)
    with pytest.raises(KeyError):
        getattr(hooks, fn)(USER, "h")
    assert hook_row(database, "bob", "h")[3:] == (V1, True, 1)


def test_REQ_HS2_3_toggle_visible_from_separate_connection(database):
    hooks.save_hook(USER, "h", V1)
    assert hooks.toggle_hook(USER, "h") is False
    assert sql(database, "SELECT enabled FROM hooks WHERE user_id = %s AND name = 'h'", USER) == [(False,)]
    assert hooks.toggle_hook(USER, "h") is True
    assert sql(database, "SELECT enabled FROM hooks WHERE user_id = %s AND name = 'h'", USER) == [(True,)]


def test_REQ_HS2_3_toggled_off_hook_does_not_run(database):
    hooks.save_hook(USER, "h", hook("GET /api/x", "return 'changed'"))
    hooks.toggle_hook(USER, "h")
    assert run(USER, "GET", "/api/x", "orig") == ("orig", [])


def test_REQ_HS2_3_delete_removes_row(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "keep", V2)
    assert hooks.delete_hook(USER, "h") is None
    assert sql(database, "SELECT name FROM hooks WHERE user_id = %s", USER) == [("keep",)]
    assert [h["name"] for h in hooks.list_hooks(USER)] == ["keep"]


# REQ-HS2-4 -----------------------------------------------------------------

def test_REQ_HS2_4_guard_failing_row_fails_run_and_lists_route_none(database):
    bad = "import os\n" + hook("GET /api/x")
    insert(database, USER, "evil", bad, route="GET /api/x")
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "evil"
    assert hooks.list_hooks(USER) == [{"name": "evil", "route": None, "enabled": True, "code": bad}]


def test_REQ_HS2_4_row_changed_in_database_is_guarded(database):
    hooks.save_hook(USER, "h", hook("GET /api/x"))
    bad = hook("GET /api/x", "return eval('1')")
    with psycopg.connect(database) as conn:
        conn.execute("UPDATE hooks SET code = %s WHERE user_id = %s AND name = 'h'", (bad, USER))
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "h"
    assert hooks.list_hooks(USER)[0]["route"] is None


# REQ-HS2-5 -----------------------------------------------------------------

def test_REQ_HS2_5_entries_per_hook_in_run_order(database):
    hooks.save_hook(USER, "b_second", hook("GET /api/{thing}", "ctx.log('from b')\nctx.log('b again')\nreturn data + ['b']"))
    hooks.save_hook(USER, "a_first", hook("GET /api/x", "ctx.log('from a', 1)\nreturn data + ['a']"))
    hooks.save_hook(USER, "c_quiet", hook("GET /api/x", "return data + ['c']"))
    hooks.save_hook(USER, "d_other", hook("GET /api/y", "ctx.log('never')\nreturn data"))
    c = hooks.Ctx(USER, FakeApi())
    result, applied = hooks.run_hooks(USER, "GET", "/api/x", [], c)
    assert result == ["a", "b", "c"]
    assert [(e["name"], e["route"], e["logs"]) for e in applied] == [
        ("a_first", "GET /api/x", ["from a 1"]),
        ("b_second", "GET /api/{thing}", ["from b", "b again"]),
        ("c_quiet", "GET /api/x", []),
    ]
    for e in applied:
        assert set(e) == {"name", "route", "ms", "logs"}
        assert isinstance(e["ms"], (int, float)) and not isinstance(e["ms"], bool)
        assert e["ms"] >= 0


def test_REQ_HS2_5_no_match_returns_data_and_empty_list(database):
    hooks.save_hook(USER, "h", hook("GET /api/x", "return 'changed'"))
    data = {"k": 1}
    assert run(USER, "GET", "/api/other", data) == ({"k": 1}, [])
    assert run("nobody", "GET", "/api/x", data) == ({"k": 1}, [])


def test_REQ_HS2_5_ms_is_per_hook(database):
    hooks.save_hook(USER, "a_fast", hook("GET /api/x", "return data"))
    hooks.save_hook(USER, "b_slow", hook("GET /api/x", "n = 0\nfor i in range(300000):\n    n = n + i\nreturn data"))
    _, applied = run(USER, "GET", "/api/x", [])
    fast, slow = applied
    assert (fast["name"], slow["name"]) == ("a_fast", "b_slow")
    assert fast["ms"] < slow["ms"]


# REQ-HS2-6 -----------------------------------------------------------------

def test_REQ_HS2_6_dry_run_returns_output_with_ctx(database):
    api = FakeApi({"/api/me": {"id": USER}})
    code = hook("GET /api/nothing/matches/{x}", "me = ctx.api.get('/api/me')\nctx.log('x')\nreturn {'user': ctx.user, 'me': me['id'], 'data': data}")
    before = counts(database)
    assert hooks.dry_run(USER, code, [1, 2], api) == {"user": USER, "me": USER, "data": [1, 2]}
    assert api.calls == ["/api/me"]
    assert counts(database) == before == (0, 0, 0)


def test_REQ_HS2_6_dry_run_virtual_none(database):
    assert hooks.dry_run(USER, hook("GET /x/report", "return {'none': data is None}"), None, FakeApi()) == {"none": True}
    assert counts(database) == (0, 0, 0)


def test_REQ_HS2_6_dry_run_does_not_touch_existing_hooks(database):
    hooks.save_hook(USER, "h", V1)
    before = sql(database, "SELECT user_id, name, code, enabled, version FROM hooks")
    hooks.dry_run(USER, V2, [{"x": 1}], FakeApi())
    assert sql(database, "SELECT user_id, name, code, enabled, version FROM hooks") == before
    assert versions(database, USER, "h") == [(1, V1)]


@pytest.mark.parametrize("code", [
    "import os\n" + hook("GET /api/x"),
    hook("GET /api/x", "return eval('1')"),
    "def run(data, ctx):\n    return data\n",
], ids=["import", "eval", "no_route"])
def test_REQ_HS2_6_dry_run_guard_rejects(database, code):
    with pytest.raises(ValueError):
        hooks.dry_run(USER, code, [], FakeApi())
    assert counts(database) == (0, 0, 0)


@pytest.mark.parametrize("body", [
    "raise ValueError('kaboom')",
    "return data[0]['missing']",
    "return {1, 2}",
    "return date(2024, 1, 5)",
], ids=["raises", "key_error", "set_not_json", "date_not_json"])
def test_REQ_HS2_6_dry_run_failure_is_hook_error_with_none(database, body):
    with pytest.raises(hooks.HookError) as exc:
        hooks.dry_run(USER, hook("GET /api/x", body), [{}], FakeApi())
    assert exc.value.hook is None
    assert counts(database) == (0, 0, 0)


# REQ-HS2-7 -----------------------------------------------------------------

def test_REQ_HS2_7_undo_restores_previous_version(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "h", V2)
    version, diff = hooks.undo_hook(USER, "h")
    assert version == 1
    lines = diff.splitlines()
    assert "-        i['tag'] = 'v2'" in lines
    assert "+        i['tag'] = 'v1'" in lines
    assert hooks.read_hook(USER, "h") == V1
    assert hook_row(database, USER, "h")[3:] == (V1, True, 1)
    assert versions(database, USER, "h") == [(1, V1), (2, V2)]


def test_REQ_HS2_7_undo_keeps_enabled(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "h", V2)
    hooks.toggle_hook(USER, "h")
    assert hooks.undo_hook(USER, "h")[0] == 1
    assert hook_row(database, USER, "h")[3:] == (V1, False, 1)


def test_REQ_HS2_7_second_undo_fails_and_changes_nothing(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "h", V2)
    hooks.undo_hook(USER, "h")
    before = (hook_row(database, USER, "h"), versions(database, USER, "h"))
    with pytest.raises(ValueError):
        hooks.undo_hook(USER, "h")
    assert (hook_row(database, USER, "h"), versions(database, USER, "h")) == before


def test_REQ_HS2_7_undo_single_version_fails(database):
    hooks.save_hook(USER, "h", V1)
    with pytest.raises(ValueError):
        hooks.undo_hook(USER, "h")
    assert hook_row(database, USER, "h")[3:] == (V1, True, 1)
    assert versions(database, USER, "h") == [(1, V1)]


def test_REQ_HS2_7_undo_missing_keyerror(database):
    hooks.save_hook("bob", "h", V1)
    hooks.save_hook("bob", "h", V2)
    with pytest.raises(KeyError):
        hooks.undo_hook(USER, "h")
    assert hook_row(database, "bob", "h")[5] == 2


def test_REQ_HS2_7_save_after_undo_creates_version_3(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "h", V2)
    hooks.undo_hook(USER, "h")
    hooks.save_hook(USER, "h", V3)
    assert versions(database, USER, "h") == [(1, V1), (2, V2), (3, V3)]
    assert hook_row(database, USER, "h")[3:] == (V3, True, 3)


def test_REQ_HS2_7_undo_restored_code_changes_run(database):
    hooks.save_hook(USER, "h", V1)
    hooks.save_hook(USER, "h", V2)
    hooks.undo_hook(USER, "h")
    assert run(USER, "GET", "/api/invoices", [{}])[0] == [{"tag": "v1"}]


# REQ-HS2-8 -----------------------------------------------------------------

def test_REQ_HS2_8_chained_hooks_emit_own_applied_events(core_env, events):
    hooks.save_hook("gerente", "a_flag", hook("GET /api/invoices", "ctx.log('from a')\nfor i in data:\n    i['a'] = 1\nreturn data"))
    hooks.save_hook("gerente", "b_mark", hook("GET /api/{resource}", "ctx.log('from b', len(data))\nctx.log('b again')\nfor i in data:\n    i['b'] = i['a'] + 1\nreturn data"))
    client = login("gerente")
    original = demo("/api/invoices", "gerente")
    events.clear()

    r = client.get("/api/invoices")

    assert r.status_code == 200
    assert r.headers["x-fitit-hooks"] == "a_flag,b_mark"
    assert r.json() == [{**i, "a": 1, "b": 2} for i in original]
    applied = of_kind(events, "hook.applied")
    assert [(e["user"], e["hook"], e["route"], e["logs"]) for e in applied] == [
        ("gerente", "a_flag", "GET /api/invoices", ["from a"]),
        ("gerente", "b_mark", "GET /api/{resource}", [f"from b {len(original)}", "b again"]),
    ]
    for e in applied:
        assert set(e) == {"kind", "user", "hook", "route", "ms", "logs"}
        assert isinstance(e["ms"], (int, float)) and e["ms"] >= 0


def test_REQ_HS2_8_applied_ms_is_per_hook(core_env, events):
    hooks.save_hook("gerente", "a_fast", hook("GET /api/invoices", "return data"))
    hooks.save_hook("gerente", "b_slow", hook("GET /api/invoices", "n = 0\nfor i in range(300000):\n    n = n + i\nreturn data"))
    client = login("gerente")
    events.clear()

    client.get("/api/invoices")

    fast, slow = of_kind(events, "hook.applied")
    assert (fast["hook"], slow["hook"]) == ("a_fast", "b_slow")
    assert fast["ms"] < slow["ms"]


# REQ-HS2-9 -----------------------------------------------------------------

def test_REQ_HS2_9_anonymous_401(core_env):
    hooks.save_hook("gerente", "h", V1)
    hooks.save_hook("gerente", "h", V2)
    r = TestClient(app).post("/fitit/hooks/h/undo")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthenticated"}
    assert hook_row(core_env, "gerente", "h")[3:] == (V2, True, 2)


def test_REQ_HS2_9_missing_404(core_env):
    hooks.save_hook("recepcion", "h", V1)
    hooks.save_hook("recepcion", "h", V2)
    r = login("gerente").post("/fitit/hooks/h/undo")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}
    assert hook_row(core_env, "recepcion", "h")[5] == 2


def test_REQ_HS2_9_single_version_409(core_env, events):
    hooks.save_hook("gerente", "h", V1)
    events.clear()
    r = login("gerente").post("/fitit/hooks/h/undo")
    assert r.status_code == 409
    body = r.json()
    assert body["error"] == "undo_failed"
    assert isinstance(body["detail"], str)
    assert hook_row(core_env, "gerente", "h")[3:] == (V1, True, 1)
    assert of_kind(events, "hook.restored") == []


def test_REQ_HS2_9_undo_200_restores_behaviour_and_emits_event(core_env, events):
    hooks.save_hook("gerente", "h", V1)
    hooks.save_hook("gerente", "h", V2)
    client = login("gerente")
    original = demo("/api/invoices", "gerente")
    assert client.get("/api/invoices").json() == [{**i, "tag": "v2"} for i in original]
    events.clear()

    r = client.post("/fitit/hooks/h/undo")

    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"name", "version", "diff"}
    assert (body["name"], body["version"]) == ("h", 1)
    assert "+        i['tag'] = 'v1'" in body["diff"].splitlines()
    restored = of_kind(events, "hook.restored")
    assert len(restored) == 1
    assert set(restored[0]) == {"kind", "user", "hook", "version"}
    assert (restored[0]["user"], restored[0]["hook"], restored[0]["version"]) == ("gerente", "h", 1)

    r = client.get("/api/invoices")
    assert r.headers["x-fitit-hooks"] == "h"
    assert r.json() == [{**i, "tag": "v1"} for i in original]


# REQ-HS2-10 ----------------------------------------------------------------
# Passing is decided by running test_hr1.py, test_px1.py, test_ag1.py and test_demo_flow.py.
# This check only proves their fixtures moved from hook files to the database fixture.

@pytest.mark.parametrize("name", ["test_hr1.py", "test_px1.py", "test_ag1.py", "test_demo_flow.py"])
def test_REQ_HS2_10_iteration_01_suites_use_database_fixture(name):
    source = (pathlib.Path(__file__).parent / name).read_text(encoding="utf-8")
    assert "WORKSPACES" not in source
    assert "tmp_path" not in source
    assert "def database(monkeypatch):" in source
    assert 'monkeypatch.setenv("DATABASE_URL", url)' in source
