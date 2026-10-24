import pytest

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
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES", str(tmp_path))
    return tmp_path


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


def test_save_writes_and_diffs(ws):
    diff = hooks.save_hook("u1", "a", hook("GET /a"))
    assert (ws / "u1" / "a.py").read_text() == hook("GET /a")
    assert diff.startswith("--- a/a.py\n+++ b/a.py\n") and '+ROUTE = "GET /a"' in diff
    diff = hooks.save_hook("u1", "a", hook("GET /b"))
    assert '-ROUTE = "GET /a"' in diff and '+ROUTE = "GET /b"' in diff
    assert hooks.read_hook("u1", "a") == hook("GET /b")


@pytest.mark.parametrize("user,name,code", [
    ("u1", "Bad-Name", hook("GET /a")), ("u1", "", hook("GET /a")), ("u1", "a" * 41, hook("GET /a")),
    ("u1", "ok", "import os\n" + hook("GET /a")), ("../u2", "ok", hook("GET /a")),
])
def test_save_invalid_writes_nothing(ws, user, name, code):
    with pytest.raises(ValueError):
        hooks.save_hook(user, name, code)
    assert list(ws.rglob("*")) == []


def test_save_replaces_disabled(ws):
    hooks.save_hook("u1", "a", hook("GET /a"))
    assert hooks.toggle_hook("u1", "a") is False
    hooks.save_hook("u1", "a", hook("GET /b"))
    assert sorted(p.name for p in (ws / "u1").iterdir()) == ["a.py"]


def test_list_toggle_delete(ws):
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
    assert hooks.run_hooks("u1", "GET", "/api/patients/7", [], ctx) == (["a", "b"], ["a_first", "b_second"])
    for path in ("/api/patients/", "/api/patients", "/api/patients/7/x", "/api/patients//"):
        assert hooks.run_hooks("u1", "GET", path, [], ctx) == ([], [])
    assert hooks.run_hooks("u1", "POST", "/api/patients/1", [], ctx) == (["d"], ["d_post"])


def test_run_is_per_user():
    hooks.save_hook("u1", "a", hook("GET /a", "return 'u1'"))
    assert hooks.run_hooks("u2", "GET", "/a", "raw", hooks.Ctx("u2", Api())) == ("raw", [])
    assert hooks.run_hooks("bad/../u1", "GET", "/a", "raw", hooks.Ctx("x", Api())) == ("raw", [])


def test_disabled_does_not_run(ws):
    (ws / "u1").mkdir()
    (ws / "u1" / "off.py.off").write_text(hook("GET /a", "return 'off'"))
    ctx = hooks.Ctx("u1", Api())
    assert hooks.run_hooks("u1", "GET", "/a", "raw", ctx) == ("raw", [])
    assert hooks.toggle_hook("u1", "off") is True
    assert hooks.run_hooks("u1", "GET", "/a", "raw", ctx) == ("off", ["off"])
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


def test_tampered_file_fails_closed(ws):
    (ws / "u1").mkdir()
    (ws / "u1" / "evil.py").write_text("import os\n" + hook("GET /a"))
    with pytest.raises(hooks.HookError) as e:
        hooks.run_hooks("u1", "GET", "/a", {}, hooks.Ctx("u1", Api()))
    assert e.value.hook == "evil"


def test_ctx_and_scope():
    hooks.save_hook("u1", "a", hook("GET /a", "\n    ".join([
        "ctx.log('user', ctx.user, 1)",
        "d = datetime(2026, 10, 23) + timedelta(days=1)",
        "return [ctx.user, ctx.api.get('/api/me'), d.date() > date(2026, 1, 1), math.floor(2.5)]",
    ])))
    api = Api()
    ctx = hooks.Ctx("u1", api)
    result, applied = hooks.run_hooks("u1", "GET", "/a", None, ctx)
    assert result == ["u1", {"path": "/api/me"}, True, 2] and applied == ["a"]
    assert api.calls == ["/api/me"] and ctx.logs == ["user u1 1"]


def test_example_hook_runs():
    hooks.save_hook("u1", "friday", EXAMPLE)
    data = [{"date": "2026-10-23", "amount": 100}, {"date": "2026-10-22", "amount": 100}]
    ctx = hooks.Ctx("u1", Api())
    assert hooks.run_hooks("u1", "GET", "/api/invoices", data, ctx)[0] == [
        {"date": "2026-10-23", "amount": 85.0}, {"date": "2026-10-22", "amount": 100}]
    assert ctx.logs == ["discounted Friday invoices"]
