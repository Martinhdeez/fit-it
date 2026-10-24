import os
import uuid

import psycopg
import pytest

import db
import hooks

EXAMPLE = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        if datetime.fromisoformat(invoice["date"]).weekday() == 4:
            invoice["amount"] = round(invoice["amount"] * 0.85, 2)
    ctx.log("discounted Friday invoices")
    return data
'''


def hook(route, body="return data"):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n    {body}\n'


class Api:
    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return {"path": path}


@pytest.fixture(autouse=True)
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


def sql(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def insert(url, user, name, code, enabled=True):
    with psycopg.connect(url) as conn:
        conn.execute("INSERT INTO hooks (user_id, name, route, code, enabled, version) VALUES (%s, %s, '', %s, %s, 1)",
                     (user, name, code, enabled))


@pytest.mark.parametrize("code", [
    "import os\n", "from os import path\n", "def f():\n    global x\n",
    "def f():\n    x = 1\n    def g():\n        nonlocal x\n",
    "_x = 1\n", "data.__class__\n", "data._x\n", "def _f(): pass\n", "def f(_a): pass\n",
    *[f"{n}\n" for n in sorted(hooks.BANNED_NAMES)],
    "def run(:\n",
])
def test_guard_rejects(code):
    with pytest.raises(ValueError):
        hooks.check_code(code + hook("GET /a"))


def test_guard_line_prefix():
    with pytest.raises(ValueError, match=r"^line 3: "):
        hooks.check_code('ROUTE = "GET /a"\ndef run(data, ctx):\n    return open("x")\n')


@pytest.mark.parametrize("code", [
    "def run(data, ctx):\n    return data\n",
    "ROUTE = 1\ndef run(data, ctx):\n    return data\n",
    hook("FETCH /a"), hook("get /a"), hook("GET a"), hook("GET"), hook("GET /a b"),
    hook("POST /x/report"),
    'ROUTE = "GET /a"\n',
    'ROUTE = "GET /a"\nrun = 1\n',
])
def test_guard_rejects_shape(code):
    with pytest.raises(ValueError):
        hooks.check_code(code)


def test_guard_accepts_example():
    assert hooks.check_code(EXAMPLE) == ("GET", "/api/invoices")
    assert hooks.check_code(hook("DELETE /api/patients/{id}")) == ("DELETE", "/api/patients/{id}")
    assert hooks.check_code(hook("GET /x/report")) == ("GET", "/x/report")


def test_save_writes_and_diffs(database):
    diff = hooks.save_hook("u1", "a", hook("GET /a"))
    assert sql(database, "SELECT user_id, name, route, code, enabled, version FROM hooks") == [
        ("u1", "a", "GET /a", hook("GET /a"), True, 1)]
    assert diff.startswith("--- a/a.py\n+++ b/a.py\n") and '+ROUTE = "GET /a"' in diff
    diff = hooks.save_hook("u1", "a", hook("GET /b"))
    assert '-ROUTE = "GET /a"' in diff and '+ROUTE = "GET /b"' in diff
    assert hooks.read_hook("u1", "a") == hook("GET /b")


@pytest.mark.parametrize("user,name,code", [
    ("u1", "Bad-Name", hook("GET /a")), ("u1", "", hook("GET /a")), ("u1", "a" * 41, hook("GET /a")),
    ("u1", "ok", "import os\n" + hook("GET /a")), ("../u2", "ok", hook("GET /a")),
])
def test_save_invalid_writes_nothing(database, user, name, code):
    with pytest.raises(ValueError):
        hooks.save_hook(user, name, code)
    assert sql(database, "SELECT * FROM hooks") == sql(database, "SELECT * FROM hook_versions") == []


def test_save_replaces_disabled(database):
    hooks.save_hook("u1", "a", hook("GET /a"))
    assert hooks.toggle_hook("u1", "a") is False
    hooks.save_hook("u1", "a", hook("GET /b"))
    assert sql(database, "SELECT name, enabled, version FROM hooks") == [("a", True, 2)]


def test_versions_survive_toggle_and_delete(database):
    hooks.save_hook("u1", "a", hook("GET /a"))
    hooks.save_hook("u1", "a", hook("GET /b"))
    hooks.toggle_hook("u1", "a")
    hooks.delete_hook("u1", "a")
    hooks.save_hook("u1", "a", hook("GET /c"))
    assert sql(database, "SELECT version, code FROM hook_versions ORDER BY version") == [
        (1, hook("GET /a")), (2, hook("GET /b")), (3, hook("GET /c"))]
    assert sql(database, "SELECT version FROM hooks") == [(3,)]


def test_undo(database):
    with pytest.raises(KeyError):
        hooks.undo_hook("u1", "a")
    hooks.save_hook("u1", "a", hook("GET /a"))
    with pytest.raises(ValueError, match="no earlier version"):
        hooks.undo_hook("u1", "a")
    hooks.save_hook("u1", "a", hook("GET /b"))
    hooks.toggle_hook("u1", "a")
    version, diff = hooks.undo_hook("u1", "a")
    assert version == 1 and '-ROUTE = "GET /b"' in diff and '+ROUTE = "GET /a"' in diff
    assert sql(database, "SELECT code, route, enabled, version FROM hooks") == [(hook("GET /a"), "GET /a", False, 1)]
    assert len(sql(database, "SELECT * FROM hook_versions")) == 2
    with pytest.raises(ValueError):
        hooks.undo_hook("u1", "a")
    hooks.save_hook("u1", "a", hook("GET /c"))
    assert sql(database, "SELECT version FROM hooks") == [(3,)]


def test_undo_guards_restored_code(database):
    hooks.save_hook("u1", "a", hook("GET /a"))
    hooks.save_hook("u1", "a", hook("GET /b"))
    with psycopg.connect(database) as conn:
        conn.execute("UPDATE hook_versions SET code = 'import os' WHERE version = 1")
    with pytest.raises(ValueError, match="import"):
        hooks.undo_hook("u1", "a")
    assert sql(database, "SELECT version, code FROM hooks") == [(2, hook("GET /b"))]


def test_list_toggle_delete(database):
    assert hooks.list_hooks("nobody") == []
    hooks.save_hook("u1", "b", hook("GET /b"))
    hooks.save_hook("u1", "a", hook("GET /a"))
    hooks.toggle_hook("u1", "b")
    assert hooks.list_hooks("u1") == [
        {"name": "a", "route": "GET /a", "enabled": True, "code": hook("GET /a")},
        {"name": "b", "route": "GET /b", "enabled": False, "code": hook("GET /b")},
    ]
    hooks.delete_hook("u1", "b")
    assert [h["name"] for h in hooks.list_hooks("u1")] == ["a"]
    for fn in (hooks.read_hook, hooks.toggle_hook, hooks.delete_hook):
        with pytest.raises(KeyError):
            fn("u1", "b")


def test_run_matching_order_chaining():
    hooks.save_hook("u1", "b_second", hook("GET /api/patients/{id}", "return data + ['b']"))
    hooks.save_hook("u1", "a_first", hook("GET /api/patients/{id}", "return data + ['a']"))
    hooks.save_hook("u1", "c_other", hook("GET /api/invoices", "return data + ['c']"))
    hooks.save_hook("u1", "d_post", hook("POST /api/patients/{id}", "return data + ['d']"))
    ctx = hooks.Ctx("u1", Api())
    result, applied = hooks.run_hooks("u1", "GET", "/api/patients/7", [], ctx)
    assert result == ["a", "b"] and [a["name"] for a in applied] == ["a_first", "b_second"]
    for path in ("/api/patients/", "/api/patients", "/api/patients/7/x", "/api/patients//"):
        assert hooks.run_hooks("u1", "GET", path, [], ctx) == ([], [])
    result, applied = hooks.run_hooks("u1", "POST", "/api/patients/1", [], ctx)
    assert result == ["d"] and [a["name"] for a in applied] == ["d_post"]


def test_run_is_per_user():
    hooks.save_hook("u1", "a", hook("GET /a", "return 'u1'"))
    assert hooks.run_hooks("u2", "GET", "/a", "raw", hooks.Ctx("u2", Api())) == ("raw", [])
    assert hooks.run_hooks("bad/../u1", "GET", "/a", "raw", hooks.Ctx("x", Api())) == ("raw", [])


def test_disabled_does_not_run(database):
    insert(database, "u1", "off", hook("GET /a", "return 'off'"), enabled=False)
    ctx = hooks.Ctx("u1", Api())
    assert hooks.run_hooks("u1", "GET", "/a", "raw", ctx) == ("raw", [])
    assert hooks.toggle_hook("u1", "off") is True
    assert hooks.run_hooks("u1", "GET", "/a", "raw", ctx)[0] == "off"
    assert hooks.toggle_hook("u1", "off") is False
    assert hooks.run_hooks("u1", "GET", "/a", "raw", ctx) == ("raw", [])


@pytest.mark.parametrize("body", [
    "return data['missing']", "return hasattr(data, 'x')", "return iter(data)", "return date(2026, 1, 1)",
])
def test_failure_raises_hookerror(body):
    hooks.save_hook("u1", "a_ok", hook("GET /a"))
    hooks.save_hook("u1", "b_bad", hook("GET /a", body))
    with pytest.raises(hooks.HookError) as e:
        hooks.run_hooks("u1", "GET", "/a", {}, hooks.Ctx("u1", Api()))
    assert e.value.hook == "b_bad" and str(e.value)


def test_tampered_row_fails_closed(database):
    insert(database, "u1", "evil", "import os\n" + hook("GET /a"))
    with pytest.raises(hooks.HookError) as e:
        hooks.run_hooks("u1", "GET", "/a", {}, hooks.Ctx("u1", Api()))
    assert e.value.hook == "evil"
    assert hooks.list_hooks("u1")[0]["route"] is None


def test_ctx_and_scope():
    hooks.save_hook("u1", "a", hook("GET /a", "\n    ".join([
        "ctx.log('user', ctx.user, 1)",
        "d = datetime(2026, 10, 23) + timedelta(days=1)",
        "return [ctx.user, ctx.api.get('/api/me'), d.date() > date(2026, 1, 1), math.floor(2.5)]",
    ])))
    api = Api()
    ctx = hooks.Ctx("u1", api)
    result, applied = hooks.run_hooks("u1", "GET", "/a", None, ctx)
    assert result == ["u1", {"path": "/api/me"}, True, 2] and [a["name"] for a in applied] == ["a"]
    assert api.calls == ["/api/me"] and ctx.logs == ["user u1 1"]


def test_example_hook_runs():
    hooks.save_hook("u1", "friday", EXAMPLE)
    data = [{"date": "2026-10-23", "amount": 100}, {"date": "2026-10-22", "amount": 100}]
    ctx = hooks.Ctx("u1", Api())
    assert hooks.run_hooks("u1", "GET", "/api/invoices", data, ctx)[0] == [
        {"date": "2026-10-23", "amount": 85.0}, {"date": "2026-10-22", "amount": 100}]
    assert ctx.logs == ["discounted Friday invoices"]


def test_applied_entries_have_own_logs_and_time():
    hooks.save_hook("u1", "a", hook("GET /a", "ctx.log('from a')\n    return data"))
    hooks.save_hook("u1", "b", hook("GET /a", "ctx.log('from b', 2)\n    return data"))
    ctx = hooks.Ctx("u1", Api())
    _, applied = hooks.run_hooks("u1", "GET", "/a", 1, ctx)
    assert [{k: a[k] for k in ("name", "route", "logs")} for a in applied] == [
        {"name": "a", "route": "GET /a", "logs": ["from a"]}, {"name": "b", "route": "GET /a", "logs": ["from b 2"]}]
    assert all(isinstance(a["ms"], float) and a["ms"] >= 0 for a in applied)
    assert ctx.logs == ["from a", "from b 2"]


def test_dry_run(database):
    api = Api()
    code = hook("GET /nothing/matches", "ctx.log('x')\n    return [ctx.user, ctx.api.get('/p'), data]")
    assert hooks.dry_run("u1", code, 7, api) == ["u1", {"path": "/p"}, 7]
    assert api.calls == ["/p"]
    with pytest.raises(ValueError):
        hooks.dry_run("u1", "import os\n" + hook("GET /a"), None, api)
    for body in ("return 1 / 0", "return date(2026, 1, 1)"):
        with pytest.raises(hooks.HookError) as e:
            hooks.dry_run("u1", hook("GET /a", body), None, api)
        assert e.value.hook is None and str(e.value)
    assert sql(database, "SELECT * FROM hooks") == sql(database, "SELECT * FROM hook_versions") == []
