"""Acceptance tests for HR1 (hook runtime), written from docs/specs/hook-contract.md only.

Iteration 02 (HS2): fixtures moved from hook files to the database (hook-contract v2, storage v1).
"""

import os
import textwrap
import uuid

import pytest

import hooks

# Until DB1 lands, psycopg and db are missing; keep the file collectable so tests fail, not error.
try:
    import psycopg

    import db
except ImportError:
    psycopg = db = None

USER = "alice"

EXAMPLE_HOOK = textwrap.dedent('''\
    ROUTE = "GET /api/invoices"

    def run(data, ctx):
        for invoice in data:
            if datetime.fromisoformat(invoice["date"]).weekday() == 4:
                invoice["amount"] = round(invoice["amount"] * 0.85, 2)
        ctx.log("discounted Friday invoices")
        return data
''')

DENY_LIST = "eval exec compile open globals locals vars getattr setattr delattr input breakpoint type object print".split()


class FakeApi:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return self.responses[path]


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
def ws(database):
    """The test's own schema URL (iteration 01 used a temporary hook-file folder)."""
    return database


def hook(route, body="return data"):
    """Valid hook source with the given ROUTE and run body (one or more lines)."""
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n' + textwrap.indent(body, "    ") + "\n"


def write(ws, user, filename, code):
    """Seeds a hook as iteration 01's `<name>.py` / `<name>.py.off` file did, through hooks.save_hook."""
    name = filename.removesuffix(".off").removesuffix(".py")
    hooks.save_hook(user, name, code)
    if filename.endswith(".off"):
        hooks.toggle_hook(user, name)


def insert(ws, user, name, code, route="GET /api/x", enabled=True):
    """Direct `hooks` row, for rows save_hook refuses (guard-failing code, invalid user id)."""
    with psycopg.connect(ws) as conn:
        conn.execute(
            "INSERT INTO hooks (user_id, name, route, code, enabled, version) VALUES (%s, %s, %s, %s, %s, 1)",
            (user, name, route, code, enabled),
        )


def sql(ws, query, *args):
    with psycopg.connect(ws) as conn:
        return conn.execute(query, args).fetchall()


def stored_code(ws, user, name):
    rows = sql(ws, "SELECT code FROM hooks WHERE user_id = %s AND name = %s", user, name)
    return rows[0][0] if rows else None


def no_rows(ws):
    return sql(ws, "SELECT 1 FROM hooks") == [] and sql(ws, "SELECT 1 FROM hook_versions") == []


def ctx(user=USER, api=None):
    return hooks.Ctx(user, api if api is not None else FakeApi())


def run(user, method, path, data, c=None):
    """run_hooks with v2 `applied` entries reduced to their names (hook-contract v2, change 3)."""
    result, applied = hooks.run_hooks(user, method, path, data, c if c is not None else ctx(user))
    return result, [entry["name"] for entry in applied]


# REQ-HR1-1 -----------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "import math\n" + hook("GET /api/x"),
    "import os\n" + hook("GET /api/x"),
    "from math import sqrt\n" + hook("GET /api/x"),
    "from os import path\n" + hook("GET /api/x"),
    hook("GET /api/x", "import json\nreturn data"),
], ids=["import_math", "import_os", "from_math", "from_os", "import_in_run"])
def test_REQ_HR1_1_rejects_import(ws, code):
    with pytest.raises(ValueError):
        hooks.check_code(code)


def test_REQ_HR1_1_rejects_global(ws):
    code = "X = 1\n" + hook("GET /api/x", "global X\nX = 2\nreturn data")
    with pytest.raises(ValueError):
        hooks.check_code(code)


def test_REQ_HR1_1_rejects_nonlocal(ws):
    body = "n = 0\ndef inner():\n    nonlocal n\n    n = 1\ninner()\nreturn data"
    with pytest.raises(ValueError):
        hooks.check_code(hook("GET /api/x", body))


@pytest.mark.parametrize("body", [
    "_x = 1\nreturn data",
    "return _secret",
    "def _helper():\n    return 1\nreturn data",
    "return __builtins__",
], ids=["assign_private", "read_private", "private_def", "dunder_name"])
def test_REQ_HR1_1_rejects_private_name(ws, body):
    with pytest.raises(ValueError):
        hooks.check_code(hook("GET /api/x", body))


@pytest.mark.parametrize("body", [
    "return ctx._secret",
    "return data.__class__",
    "return ctx.api._session",
    "x = ().__class__.__bases__\nreturn data",
], ids=["single_underscore", "dunder_class", "nested", "escape_chain"])
def test_REQ_HR1_1_rejects_private_attribute(ws, body):
    with pytest.raises(ValueError):
        hooks.check_code(hook("GET /api/x", body))


@pytest.mark.parametrize("name", DENY_LIST)
def test_REQ_HR1_1_rejects_each_deny_listed_name(ws, name):
    with pytest.raises(ValueError):
        hooks.check_code(hook("GET /api/x", f"f = {name}\nreturn data"))


@pytest.mark.parametrize("name", DENY_LIST)
def test_REQ_HR1_1_rejects_each_deny_listed_name_called(ws, name):
    with pytest.raises(ValueError):
        hooks.check_code(hook("GET /api/x", f"{name}('x')\nreturn data"))


def test_REQ_HR1_1_rejects_invalid_python(ws):
    with pytest.raises(ValueError):
        hooks.check_code('ROUTE = "GET /api/x"\ndef run(data, ctx)\n    return data\n')


def test_REQ_HR1_1_save_hook_rejects_guarded_code(ws):
    with pytest.raises(ValueError):
        hooks.save_hook(USER, "bad", "import os\n" + hook("GET /api/x"))
    assert no_rows(ws)


# REQ-HR1-2 -----------------------------------------------------------------

@pytest.mark.parametrize("code", [
    "def run(data, ctx):\n    return data\n",
    "ROUTE = 123\n\ndef run(data, ctx):\n    return data\n",
    "ROUTE = None\n\ndef run(data, ctx):\n    return data\n",
    "ROUTE = ['GET', '/api/x']\n\ndef run(data, ctx):\n    return data\n",
    hook("FETCH /api/x"),
    hook("HEAD /api/x"),
    hook("OPTIONS /api/x"),
    hook("GET api/x"),
    hook("GET"),
    hook("/api/x"),
    hook("POST /x/report"),
    hook("PUT /x/report"),
    hook("PATCH /x/report"),
    hook("DELETE /x/report"),
    'ROUTE = "GET /api/x"\n',
    'ROUTE = "GET /api/x"\nrun = 5\n',
], ids=[
    "route_missing", "route_int", "route_none", "route_list",
    "method_fetch", "method_head", "method_options",
    "path_no_slash", "no_path", "no_method",
    "virtual_post", "virtual_put", "virtual_patch", "virtual_delete",
    "run_missing", "run_not_callable",
])
def test_REQ_HR1_2_rejects_invalid_route_or_run(ws, code):
    with pytest.raises(ValueError):
        hooks.check_code(code)


@pytest.mark.parametrize("route,expected", [
    ("GET /x/report", ("GET", "/x/report")),
    ("POST /api/invoices", ("POST", "/api/invoices")),
    ("PUT /api/patients/{id}", ("PUT", "/api/patients/{id}")),
    ("PATCH /api/x", ("PATCH", "/api/x")),
    ("DELETE /api/x", ("DELETE", "/api/x")),
])
def test_REQ_HR1_2_accepts_valid_routes(ws, route, expected):
    assert hooks.check_code(hook(route)) == expected


# REQ-HR1-3 -----------------------------------------------------------------

def test_REQ_HR1_3_accepts_spec_example(ws):
    assert hooks.check_code(EXAMPLE_HOOK) == ("GET", "/api/invoices")


def test_REQ_HR1_3_spec_example_runs(ws):
    write(ws, USER, "friday.py", EXAMPLE_HOOK)
    data = [{"date": "2024-01-05", "amount": 100.0}, {"date": "2024-01-04", "amount": 100.0}]
    c = ctx()
    result, applied = run(USER, "GET", "/api/invoices", data, c)
    assert result == [{"date": "2024-01-05", "amount": 85.0}, {"date": "2024-01-04", "amount": 100.0}]
    assert applied == ["friday"]
    assert c.logs == ["discounted Friday invoices"]


# REQ-HR1-4 -----------------------------------------------------------------

def test_REQ_HR1_4_save_writes_file(ws):
    code = hook("GET /api/x")
    hooks.save_hook(USER, "my_hook", code)
    assert stored_code(ws, USER, "my_hook") == code


def test_REQ_HR1_4_save_returns_diff_from_empty(ws):
    code = hook("GET /api/x")
    diff = hooks.save_hook(USER, "my_hook", code)
    assert "@@" in diff
    assert '+ROUTE = "GET /api/x"' in diff.splitlines()


def test_REQ_HR1_4_save_returns_diff_from_previous(ws):
    hooks.save_hook(USER, "my_hook", hook("GET /api/old"))
    diff = hooks.save_hook(USER, "my_hook", hook("GET /api/new"))
    lines = diff.splitlines()
    assert "@@" in diff
    assert '-ROUTE = "GET /api/old"' in lines
    assert '+ROUTE = "GET /api/new"' in lines
    assert not any(l.startswith(("+", "-")) and "def run" in l for l in lines)
    assert stored_code(ws, USER, "my_hook") == hook("GET /api/new")


def test_REQ_HR1_4_save_accepts_40_char_name(ws):
    name = "a" * 40
    hooks.save_hook(USER, name, hook("GET /api/x"))
    assert stored_code(ws, USER, name) == hook("GET /api/x")


def test_REQ_HR1_4_save_replaces_disabled_hook_enabled(ws):
    write(ws, USER, "my_hook.py.off", hook("GET /api/old"))
    hooks.save_hook(USER, "my_hook", hook("GET /api/new"))
    assert stored_code(ws, USER, "my_hook") == hook("GET /api/new")
    assert sql(ws, "SELECT enabled FROM hooks WHERE user_id = %s AND name = %s", USER, "my_hook") == [(True,)]


@pytest.mark.parametrize("name", [
    "", "a" * 41, "Bad", "my-hook", "my.hook", "my hook", "../evil", "a/b", "hook.py", "ñ",
])
def test_REQ_HR1_4_invalid_name_writes_nothing(ws, name):
    with pytest.raises(ValueError):
        hooks.save_hook(USER, name, hook("GET /api/x"))
    assert no_rows(ws)


def test_REQ_HR1_4_invalid_code_writes_nothing(ws):
    with pytest.raises(ValueError):
        hooks.save_hook(USER, "my_hook", hook("GET /api/x", "return eval('1')"))
    assert no_rows(ws)


def test_REQ_HR1_4_invalid_code_keeps_previous(ws):
    good = hook("GET /api/x")
    hooks.save_hook(USER, "my_hook", good)
    with pytest.raises(ValueError):
        hooks.save_hook(USER, "my_hook", "import os\n" + good)
    assert stored_code(ws, USER, "my_hook") == good


# REQ-HR1-5 -----------------------------------------------------------------

def test_REQ_HR1_5_runs_matching_hook(ws):
    write(ws, USER, "h.py", hook("GET /api/x", "return data + ['h']"))
    assert run(USER, "GET", "/api/x", []) == (["h"], ["h"])


def test_REQ_HR1_5_no_match_returns_data_unchanged(ws):
    write(ws, USER, "h.py", hook("GET /api/x", "return 'changed'"))
    data = {"k": 1}
    assert run(USER, "GET", "/api/y", data) == ({"k": 1}, [])


def test_REQ_HR1_5_no_folder_returns_data_unchanged(ws):
    assert run(USER, "GET", "/api/x", [1, 2]) == ([1, 2], [])


def test_REQ_HR1_5_method_must_match(ws):
    write(ws, USER, "h.py", hook("POST /api/x", "return 'changed'"))
    assert run(USER, "GET", "/api/x", "orig") == ("orig", [])
    assert run(USER, "POST", "/api/x", "orig") == ("changed", ["h"])


def test_REQ_HR1_5_path_must_match_exactly(ws):
    write(ws, USER, "h.py", hook("GET /api/invoices", "return 'changed'"))
    assert run(USER, "GET", "/api/invoices/1", "orig") == ("orig", [])
    assert run(USER, "GET", "/api/invoice", "orig") == ("orig", [])
    assert run(USER, "GET", "/api", "orig") == ("orig", [])


def test_REQ_HR1_5_param_matches_one_segment(ws):
    write(ws, USER, "h.py", hook("GET /api/patients/{id}", "return 'changed'"))
    assert run(USER, "GET", "/api/patients/7", "orig") == ("changed", ["h"])
    assert run(USER, "GET", "/api/patients/abc", "orig") == ("changed", ["h"])


def test_REQ_HR1_5_param_does_not_match_two_segments(ws):
    write(ws, USER, "h.py", hook("GET /api/patients/{id}", "return 'changed'"))
    assert run(USER, "GET", "/api/patients/7/notes", "orig") == ("orig", [])


def test_REQ_HR1_5_param_does_not_match_empty_segment(ws):
    write(ws, USER, "h.py", hook("GET /api/patients/{id}", "return 'changed'"))
    assert run(USER, "GET", "/api/patients/", "orig") == ("orig", [])
    assert run(USER, "GET", "/api/patients", "orig") == ("orig", [])


def test_REQ_HR1_5_param_in_middle(ws):
    write(ws, USER, "h.py", hook("GET /api/patients/{id}/notes", "return 'changed'"))
    assert run(USER, "GET", "/api/patients/7/notes", "orig") == ("changed", ["h"])
    assert run(USER, "GET", "/api/patients/7/8/notes", "orig") == ("orig", [])
    assert run(USER, "GET", "/api/patients//notes", "orig") == ("orig", [])


def test_REQ_HR1_5_name_order_and_chaining(ws):
    # Written in reverse order so file creation order differs from name order.
    for name in ["c_third", "b_second", "a_first"]:
        write(ws, USER, f"{name}.py", hook("GET /api/x", f"return data + ['{name}']"))
    result, applied = run(USER, "GET", "/api/x", ["start"])
    assert result == ["start", "a_first", "b_second", "c_third"]
    assert applied == ["a_first", "b_second", "c_third"]


def test_REQ_HR1_5_each_receives_previous_output(ws):
    write(ws, USER, "a.py", hook("GET /api/x", "return {'n': data['n'] + 1}"))
    write(ws, USER, "b.py", hook("GET /api/x", "return {'n': data['n'] * 10}"))
    assert run(USER, "GET", "/api/x", {"n": 1}) == ({"n": 20}, ["a", "b"])


def test_REQ_HR1_5_applied_names_exclude_non_matching(ws):
    write(ws, USER, "a.py", hook("GET /api/x", "return data + ['a']"))
    write(ws, USER, "b.py", hook("GET /api/other", "return data + ['b']"))
    write(ws, USER, "c.py", hook("POST /api/x", "return data + ['c']"))
    write(ws, USER, "d.py", hook("GET /api/x", "return data + ['d']"))
    assert run(USER, "GET", "/api/x", []) == (["a", "d"], ["a", "d"])


def test_REQ_HR1_5_virtual_endpoint_gets_none(ws):
    write(ws, USER, "v.py", hook("GET /x/report", "return {'was_none': data is None}"))
    assert run(USER, "GET", "/x/report", None) == ({"was_none": True}, ["v"])


def test_REQ_HR1_7_list_hooks_reports_enabled_state(ws):
    write(ws, USER, "b.py", hook("GET /api/b"))
    write(ws, USER, "a.py.off", hook("GET /api/a"))
    assert hooks.list_hooks(USER) == [
        {"name": "a", "route": "GET /api/a", "enabled": False, "code": hook("GET /api/a")},
        {"name": "b", "route": "GET /api/b", "enabled": True, "code": hook("GET /api/b")},
    ]


# REQ-HR1-6 -----------------------------------------------------------------

def test_REQ_HR1_6_hook_not_run_for_other_user(ws):
    hooks.save_hook("alice", "mine", hook("GET /api/x", "return 'alice'"))
    assert run("bob", "GET", "/api/x", "orig") == ("orig", [])
    assert run("alice", "GET", "/api/x", "orig") == ("alice", ["mine"])


def test_REQ_HR1_6_each_user_only_own_hooks(ws):
    hooks.save_hook("alice", "a_hook", hook("GET /api/x", "return data + ['alice']"))
    hooks.save_hook("bob", "b_hook", hook("GET /api/x", "return data + ['bob']"))
    assert run("alice", "GET", "/api/x", []) == (["alice"], ["a_hook"])
    assert run("bob", "GET", "/api/x", []) == (["bob"], ["b_hook"])
    assert hooks.list_hooks("carol") == []


def test_REQ_HR1_6_user_id_prefix_does_not_leak(ws):
    hooks.save_hook("alice", "mine", hook("GET /api/x", "return 'alice'"))
    assert run("alic", "GET", "/api/x", "orig") == ("orig", [])
    assert run("alice2", "GET", "/api/x", "orig") == ("orig", [])


@pytest.mark.parametrize("user", ["../alice", "alice/..", ".", "..", "", "a" * 65])
def test_REQ_HR1_6_invalid_user_id_gets_no_hooks(ws, user):
    hooks.save_hook("alice", "mine", hook("GET /api/x", "return 'alice'"))
    insert(ws, user, "root", hook("GET /api/x", "return 'root'"))
    assert run(user, "GET", "/api/x", "orig") == ("orig", [])


# REQ-HR1-7 -----------------------------------------------------------------

def test_REQ_HR1_7_off_file_does_not_run(ws):
    write(ws, USER, "h.py.off", hook("GET /api/x", "return 'changed'"))
    assert run(USER, "GET", "/api/x", "orig") == ("orig", [])


def test_REQ_HR1_7_toggle_disables_then_reenables(ws):
    hooks.save_hook(USER, "h", hook("GET /api/x", "return 'changed'"))
    assert run(USER, "GET", "/api/x", "orig") == ("changed", ["h"])

    assert hooks.toggle_hook(USER, "h") is False
    assert run(USER, "GET", "/api/x", "orig") == ("orig", [])
    assert sql(ws, "SELECT enabled FROM hooks WHERE user_id = %s AND name = 'h'", USER) == [(False,)]

    assert hooks.toggle_hook(USER, "h") is True
    assert run(USER, "GET", "/api/x", "orig") == ("changed", ["h"])
    assert sql(ws, "SELECT enabled FROM hooks WHERE user_id = %s AND name = 'h'", USER) == [(True,)]


def test_REQ_HR1_7_toggle_off_file_enables(ws):
    write(ws, USER, "h.py.off", hook("GET /api/x", "return 'changed'"))
    assert hooks.toggle_hook(USER, "h") is True
    assert run(USER, "GET", "/api/x", "orig") == ("changed", ["h"])


def test_REQ_HR1_7_disabled_skipped_in_chain(ws):
    write(ws, USER, "a.py", hook("GET /api/x", "return data + ['a']"))
    write(ws, USER, "b.py.off", hook("GET /api/x", "return data + ['b']"))
    write(ws, USER, "c.py", hook("GET /api/x", "return data + ['c']"))
    assert run(USER, "GET", "/api/x", []) == (["a", "c"], ["a", "c"])


def test_REQ_HR1_7_toggle_missing_raises_keyerror(ws):
    with pytest.raises(KeyError):
        hooks.toggle_hook(USER, "nope")


# REQ-HR1-8 -----------------------------------------------------------------

def test_REQ_HR1_8_raising_hook(ws):
    write(ws, USER, "boom.py", hook("GET /api/x", "raise ValueError('kaboom')"))
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "boom"
    assert str(exc.value) != ""


def test_REQ_HR1_8_runtime_error_in_hook(ws):
    write(ws, USER, "div.py", hook("GET /api/x", "return 1 / 0"))
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "div"


def test_REQ_HR1_8_names_the_failing_hook_in_chain(ws):
    write(ws, USER, "a_ok.py", hook("GET /api/x", "return data + ['a']"))
    write(ws, USER, "b_bad.py", hook("GET /api/x", "return data['missing']"))
    write(ws, USER, "c_ok.py", hook("GET /api/x", "return data + ['c']"))
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "b_bad"


@pytest.mark.parametrize("builtin", ["hasattr", "iter", "next", "repr", "chr", "id", "callable", "issubclass"])
def test_REQ_HR1_8_builtin_outside_allow_list(ws, builtin):
    write(ws, USER, "sneaky.py", hook("GET /api/x", f"f = {builtin}\nreturn data"))
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "sneaky"


def test_REQ_HR1_8_hasattr_call(ws):
    write(ws, USER, "sneaky.py", hook("GET /api/x", "return hasattr(ctx, 'user')"))
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "sneaky"


def test_REQ_HR1_8_guard_rejected_file_on_load(ws):
    insert(ws, USER, "evil", "import os\n" + hook("GET /api/x"))
    with pytest.raises(hooks.HookError) as exc:
        run(USER, "GET", "/api/x", [])
    assert exc.value.hook == "evil"


@pytest.mark.parametrize("builtin", "len sum min max abs round sorted reversed any all enumerate zip range map filter str int float bool list dict set tuple isinstance".split())
def test_REQ_HR1_8_allow_listed_builtins_available(ws, builtin):
    write(ws, USER, "ok.py", hook("GET /api/x", f"f = {builtin}\nreturn 'ok'"))
    assert run(USER, "GET", "/api/x", None) == ("ok", ["ok"])


# REQ-HR1-9 -----------------------------------------------------------------

def test_REQ_HR1_9_ctx_object(ws):
    api = FakeApi({"/api/me": {"id": "alice"}})
    c = hooks.Ctx("alice", api)
    assert c.user == "alice"
    assert c.logs == []
    assert c.api.get("/api/me") == {"id": "alice"}
    assert api.calls == ["/api/me"]


def test_REQ_HR1_9_ctx_user_inside_hook(ws):
    write(ws, "bob", "who.py", hook("GET /api/x", "return ctx.user"))
    assert run("bob", "GET", "/api/x", None, ctx("bob")) == ("bob", ["who"])


def test_REQ_HR1_9_ctx_api_get_delegates(ws):
    api = FakeApi({"/api/patients": [{"id": 1}, {"id": 2}], "/api/invoices": [{"amount": 3}]})
    body = "p = ctx.api.get('/api/patients')\ni = ctx.api.get('/api/invoices')\nreturn {'patients': len(p), 'invoices': i}"
    write(ws, USER, "agg.py", hook("GET /x/summary", body))
    result, applied = run(USER, "GET", "/x/summary", None, ctx(USER, api))
    assert result == {"patients": 2, "invoices": [{"amount": 3}]}
    assert applied == ["agg"]
    assert api.calls == ["/api/patients", "/api/invoices"]


def test_REQ_HR1_9_ctx_log_lines(ws):
    write(ws, USER, "a.py", hook("GET /api/x", "ctx.log('hello', 1, 2.5)\nctx.log('second')\nreturn data"))
    write(ws, USER, "b.py", hook("GET /api/x", "ctx.log('from b')\nreturn data"))
    c = ctx()
    run(USER, "GET", "/api/x", [], c)
    assert c.logs == ["hello 1 2.5", "second", "from b"]


def test_REQ_HR1_9_date_helpers(ws):
    body = textwrap.dedent("""\
        d = date(2024, 1, 5)
        dt = datetime.fromisoformat("2024-01-05T10:00:00")
        return {
            "date": (d + timedelta(days=1)).isoformat(),
            "weekday": dt.weekday(),
            "datetime": (dt + timedelta(hours=2)).isoformat(),
            "sqrt": math.sqrt(16),
            "floor": math.floor(2.7),
        }""")
    write(ws, USER, "dates.py", hook("GET /api/x", body))
    result, applied = run(USER, "GET", "/api/x", None)
    assert result == {
        "date": "2024-01-06",
        "weekday": 4,
        "datetime": "2024-01-05T12:00:00",
        "sqrt": 4.0,
        "floor": 2,
    }
    assert applied == ["dates"]
