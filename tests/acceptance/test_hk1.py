"""Acceptance tests for HK1 (serialized hook saves, expected version, chain dry run).

Written from docs/plan/04-conversations/, docs/specs/ (hook-contract v4, marketplace v2, storage)
and docs/adr/ (0023, 0025) only.

REQ-HK1-9 also requires test_hr1.py, test_hs2.py, test_sf1.py, test_mk1.py and test_mk2.py to pass
unchanged; `make check` runs them from this same folder, so here only the v3 `dry_run` is checked.
"""

import concurrent.futures
import os
import threading
import uuid

import psycopg
import pytest

import db
import hooks

try:
    import marketplace
except ImportError:
    marketplace = None

USER, OTHER = "alice", "bob"
OWNER, SUB = "carol", "dave"
ROUTE = "GET /api/invoices"


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
    def __init__(self, user=USER, responses=None):
        self.responses = {"/api/me": {"id": user}, **(responses or {})}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return self.responses.get(path, [])


def hook(route, body="return data"):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n' + "".join(f"    {line}\n" for line in body.splitlines())


def mark(marker, route=ROUTE):
    return hook(route, f'data[0]["marks"] = data[0].get("marks", []) + ["{marker}"]\nreturn data')


def raises_on_dict(message, route=ROUTE):
    """Passes a list through; raises with `message` when given a dict (a wrapped output)."""
    return hook(route, f'if isinstance(data, dict):\n    int("{message}")\nreturn data')


WRAP = hook(ROUTE, 'return {"rows": data}')
DATA = [{"id": 1, "amount": 10}]


def fresh():
    return [dict(row) for row in DATA]


def marks(result):
    return result[0].get("marks", [])


def sql(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def state(url):
    """Every row of hooks and hook_versions, of every user."""
    return (
        sql(url, "SELECT user_id, name, route, code, enabled, version FROM hooks ORDER BY user_id, name"),
        sql(url, "SELECT user_id, name, version, code FROM hook_versions ORDER BY user_id, name, version"),
    )


def hook_row(url, user, name):
    return sql(url, "SELECT code, version FROM hooks WHERE user_id = %s AND name = %s", user, name)


def expect_conflict(url, current, *args, **kwargs):
    before = state(url)
    with pytest.raises(hooks.Conflict) as exc:
        hooks.save_hook(*args, **kwargs)
    e = exc.value
    assert isinstance(e, ValueError)
    assert str(e).startswith("conflict:")
    assert e.current == current
    assert state(url) == before
    return e


def concurrently(fn, n=8):
    barrier = threading.Barrier(n, timeout=10)

    def worker(i):
        barrier.wait()
        return fn(i)

    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(worker, i) for i in range(n)]
        outcomes = []
        for f in futures:
            try:
                outcomes.append(("ok", f.result(timeout=10)))
            except Exception as e:  # noqa: BLE001 - every outcome is asserted by the caller
                outcomes.append(("err", e))
    return outcomes


# REQ-HK1-1 -----------------------------------------------------------------

def test_REQ_HK1_1_zero_on_missing_hook_saves_as_without(database):
    code = mark("A")
    diff = hooks.save_hook(USER, "disc", code, expected_version=0)
    plain = hooks.save_hook(OTHER, "disc", code)
    assert diff == plain
    assert hook_row(database, USER, "disc") == [(code, 1)]
    assert sql(database, "SELECT version, code FROM hook_versions WHERE user_id = %s", USER) == [(1, code)]


def test_REQ_HK1_1_matching_version_saves_as_without(database):
    for user in (USER, OTHER):
        hooks.save_hook(user, "disc", mark("A"))
        hooks.save_hook(user, "disc", mark("B"))
    diff = hooks.save_hook(USER, "disc", mark("C"), expected_version=2)
    plain = hooks.save_hook(OTHER, "disc", mark("C"))
    assert diff == plain
    assert "a/disc.py" in diff and "b/disc.py" in diff
    assert hook_row(database, USER, "disc") == [(mark("C"), 3)]
    assert sql(database, "SELECT version FROM hook_versions WHERE user_id = %s ORDER BY version", USER) == [(1,), (2,), (3,)]
    users_rows = [r[1:] for r in state(database)[0] if r[0] == USER]
    others_rows = [r[1:] for r in state(database)[0] if r[0] == OTHER]
    assert users_rows == others_rows


def test_REQ_HK1_1_matching_version_on_disabled_hook_saves_enabled(database):
    hooks.save_hook(USER, "disc", mark("A"))
    hooks.toggle_hook(USER, "disc")
    hooks.save_hook(USER, "disc", mark("B"), expected_version=1)
    assert sql(database, "SELECT code, version, enabled FROM hooks WHERE user_id = %s", USER) == [(mark("B"), 2, True)]


@pytest.mark.parametrize("expected", [1, 3])
def test_REQ_HK1_1_other_version_conflicts(database, expected):
    hooks.save_hook(USER, "disc", mark("A"))
    hooks.save_hook(USER, "disc", mark("B"))
    expect_conflict(database, 2, USER, "disc", mark("C"), expected_version=expected)


def test_REQ_HK1_1_zero_on_existing_hook_conflicts(database):
    hooks.save_hook(USER, "disc", mark("A"))
    expect_conflict(database, 1, USER, "disc", mark("C"), expected_version=0)


def test_REQ_HK1_1_one_on_never_created_hook_conflicts(database):
    hooks.save_hook(USER, "other", mark("A"))
    expect_conflict(database, 0, USER, "disc", mark("C"), expected_version=1)


def test_REQ_HK1_1_last_version_of_deleted_hook_conflicts(database):
    hooks.save_hook(USER, "disc", mark("A"))
    hooks.save_hook(USER, "disc", mark("B"))
    hooks.delete_hook(USER, "disc")
    expect_conflict(database, 0, USER, "disc", mark("C"), expected_version=2)


def test_REQ_HK1_1_conflict_is_a_value_error_class(database):
    assert issubclass(hooks.Conflict, ValueError)


# REQ-HK1-2 -----------------------------------------------------------------

def _assert_serialized(url, first, last, codes, outcomes):
    assert [kind for kind, _ in outcomes] == ["ok"] * 8, [o for o in outcomes if o[0] == "err"]
    rows = sql(url, "SELECT version, code FROM hook_versions WHERE user_id = %s AND name = 'race'", USER)
    assert sorted(v for v, _ in rows) == list(range(first, last + 1))
    by_version = dict(rows)
    assert set(codes) <= set(by_version.values())
    assert hook_row(url, USER, "race") == [(by_version[last], last)]


def test_REQ_HK1_2_eight_concurrent_first_saves(database):
    codes = [mark(f"T{i}") for i in range(8)]
    outcomes = concurrently(lambda i: hooks.save_hook(USER, "race", codes[i]))
    _assert_serialized(database, 1, 8, codes, outcomes)


def test_REQ_HK1_2_eight_concurrent_saves_of_existing_hook(database):
    hooks.save_hook(USER, "race", mark("start"))
    codes = [mark(f"T{i}") for i in range(8)]
    outcomes = concurrently(lambda i: hooks.save_hook(USER, "race", codes[i]))
    _assert_serialized(database, 1, 9, codes, outcomes)


# REQ-HK1-3 -----------------------------------------------------------------

def test_REQ_HK1_3_eight_concurrent_saves_with_expected_version(database):
    hooks.save_hook(USER, "race", mark("start"))
    hooks.save_hook(USER, "race", mark("second"))
    codes = [mark(f"T{i}") for i in range(8)]
    outcomes = concurrently(lambda i: hooks.save_hook(USER, "race", codes[i], expected_version=2))
    oks = [v for kind, v in outcomes if kind == "ok"]
    errs = [v for kind, v in outcomes if kind == "err"]
    assert len(oks) == 1
    assert len(errs) == 7
    for e in errs:
        assert isinstance(e, hooks.Conflict), repr(e)
        assert str(e).startswith("conflict:")
        assert e.current == 3
    rows = sql(database, "SELECT version FROM hook_versions WHERE user_id = %s ORDER BY version", USER)
    assert rows == [(1,), (2,), (3,)]


def test_REQ_HK1_3_concurrent_creates_with_zero(database):
    codes = [mark(f"T{i}") for i in range(8)]
    outcomes = concurrently(lambda i: hooks.save_hook(USER, "race", codes[i], expected_version=0))
    assert [kind for kind, _ in outcomes].count("ok") == 1
    errs = [v for kind, v in outcomes if kind == "err"]
    assert len(errs) == 7 and all(isinstance(e, hooks.Conflict) and e.current == 1 for e in errs), errs
    assert sql(database, "SELECT count(*) FROM hook_versions WHERE user_id = %s", USER) == [(1,)]


# REQ-HK1-4 -----------------------------------------------------------------

def test_REQ_HK1_4_read_hook_version_after_saves_and_undo(database):
    hooks.save_hook(USER, "disc", mark("A"))
    assert hooks.read_hook_version(USER, "disc") == tuple(hook_row(database, USER, "disc")[0]) == (mark("A"), 1)
    hooks.save_hook(USER, "disc", mark("B"))
    assert hooks.read_hook_version(USER, "disc") == tuple(hook_row(database, USER, "disc")[0]) == (mark("B"), 2)
    hooks.undo_hook(USER, "disc")
    assert hooks.read_hook_version(USER, "disc") == tuple(hook_row(database, USER, "disc")[0]) == (mark("A"), 1)


def test_REQ_HK1_4_read_hook_version_missing(database):
    with pytest.raises(KeyError):
        hooks.read_hook_version(USER, "nope")
    hooks.save_hook(USER, "gone", mark("A"))
    hooks.delete_hook(USER, "gone")
    with pytest.raises(KeyError):
        hooks.read_hook_version(USER, "gone")


def test_REQ_HK1_4_versions_includes_disabled(database):
    hooks.save_hook(USER, "one", mark("A"))
    hooks.save_hook(USER, "two", mark("A"))
    hooks.save_hook(USER, "two", mark("B"))
    hooks.save_hook(USER, "off", mark("A"))
    hooks.toggle_hook(USER, "off")
    hooks.save_hook(OTHER, "theirs", mark("A"))
    assert hooks.versions(USER) == {"one": 1, "two": 2, "off": 1}
    assert hooks.versions(USER) == dict(sql(database, "SELECT name, version FROM hooks WHERE user_id = %s", USER))


@pytest.mark.parametrize("user", ["nobody", "bad id!", ""])
def test_REQ_HK1_4_versions_empty(database, user):
    hooks.save_hook(USER, "one", mark("A"))
    assert hooks.versions(user) == {}


# REQ-HK1-5 -----------------------------------------------------------------

FEATURES = [{"name": "@1", "code": mark("@1")}, {"name": "@2", "code": mark("@2")}]


@pytest.fixture
def chain(database):
    hooks.save_hook(USER, "a_hook", mark("a_hook"))
    hooks.save_hook(USER, "z_hook", mark("z_hook"))
    return database


def test_REQ_HK1_5_candidate_inserted_at_its_name(chain):
    before = state(chain)
    out = hooks.dry_run(USER, mark("m_hook"), fresh(), FakeApi(), name="m_hook", features=FEATURES)
    assert marks(out) == ["@1", "@2", "a_hook", "m_hook", "z_hook"]
    assert state(chain) == before


def test_REQ_HK1_5_candidate_replaces_same_name(chain):
    before = state(chain)
    out = hooks.dry_run(USER, mark("cand"), fresh(), FakeApi(), name="a_hook", features=FEATURES)
    assert marks(out) == ["@1", "@2", "cand", "z_hook"]
    assert state(chain) == before


def test_REQ_HK1_5_disabled_hook_does_not_run(chain):
    hooks.save_hook(USER, "b_hook", mark("b_hook"))
    hooks.toggle_hook(USER, "b_hook")
    before = state(chain)
    out = hooks.dry_run(USER, mark("m_hook"), fresh(), FakeApi(), name="m_hook", features=FEATURES)
    assert marks(out) == ["@1", "@2", "a_hook", "m_hook", "z_hook"]
    assert state(chain) == before


def test_REQ_HK1_5_disabled_hook_with_candidate_name_runs_candidate(chain):
    hooks.save_hook(USER, "b_hook", mark("b_hook"))
    hooks.toggle_hook(USER, "b_hook")
    before = state(chain)
    out = hooks.dry_run(USER, mark("cand"), fresh(), FakeApi(), name="b_hook", features=FEATURES)
    assert marks(out) == ["@1", "@2", "a_hook", "cand", "z_hook"]
    assert state(chain) == before


def test_REQ_HK1_5_hooks_on_other_route_do_not_run(chain):
    hooks.save_hook(USER, "c_hook", mark("c_hook", "GET /api/patients"))
    before = state(chain)
    out = hooks.dry_run(USER, mark("m_hook"), fresh(), FakeApi(), name="m_hook", features=FEATURES)
    assert marks(out) == ["@1", "@2", "a_hook", "m_hook", "z_hook"]
    assert state(chain) == before


# REQ-HK1-6 -----------------------------------------------------------------

def test_REQ_HK1_6_candidate_failure_has_hook_none(chain):
    before = state(chain)
    with pytest.raises(hooks.HookError) as exc:
        hooks.dry_run(USER, hook(ROUTE, 'int("cand_boom")\nreturn data'), fresh(), FakeApi(), name="m_hook", features=FEATURES)
    assert exc.value.hook is None
    assert state(chain) == before


def test_REQ_HK1_6_later_own_hook_failing_on_candidate_output(database):
    hooks.save_hook(USER, "a_hook", mark("a_hook"))
    hooks.save_hook(USER, "z_hook", raises_on_dict("z_boom"))
    assert marks(hooks.dry_run(USER, raises_on_dict("z_boom"), fresh(), FakeApi())) == []
    before = state(database)
    with pytest.raises(hooks.HookError) as exc:
        hooks.dry_run(USER, WRAP, fresh(), FakeApi(), name="m_hook")
    assert exc.value.hook == "z_hook"
    assert "z_boom" in str(exc.value)
    assert state(database) == before


def test_REQ_HK1_6_stored_hook_failing_guard(database):
    hooks.save_hook(USER, "z_hook", mark("z_hook"))
    bad = hook(ROUTE, "return data.__class__")
    sql_exec(database, "UPDATE hooks SET code = %s WHERE user_id = %s AND name = 'z_hook'", bad, USER)
    before = state(database)
    with pytest.raises(hooks.HookError) as exc:
        hooks.dry_run(USER, mark("m_hook"), fresh(), FakeApi(), name="m_hook")
    assert exc.value.hook == "z_hook"
    assert state(database) == before


def sql_exec(url, query, *args):
    with psycopg.connect(url) as conn:
        conn.execute(query, args)


# REQ-HK1-7 -----------------------------------------------------------------

def test_REQ_HK1_7_feature_candidate_replaces_in_place(chain):
    out = hooks.dry_run(USER, mark("cand"), fresh(), FakeApi(), name="@2", features=FEATURES)
    assert marks(out) == ["@1", "cand", "a_hook", "z_hook"]


def test_REQ_HK1_7_new_feature_candidate_runs_after_features(chain):
    out = hooks.dry_run(USER, mark("cand"), fresh(), FakeApi(), name="@9", features=FEATURES)
    assert marks(out) == ["@1", "@2", "cand", "a_hook", "z_hook"]


def test_REQ_HK1_7_later_feature_failure_names_it(database):
    features = [{"name": "@1", "code": mark("@1")}, {"name": "@2", "code": raises_on_dict("f2_boom")}]
    with pytest.raises(hooks.HookError) as exc:
        hooks.dry_run(USER, WRAP, fresh(), FakeApi(), name="@1", features=features)
    assert exc.value.hook == "@2"
    assert "f2_boom" in str(exc.value)


# REQ-HK1-8 -----------------------------------------------------------------

ZZ_TOTAL = hook(ROUTE, 'data[0]["marks"] = [data[0]["amount"]]\nreturn data')


def sub_api():
    return FakeApi(SUB, {"/api/invoices": fresh()})


def expect_dry_run_failed(fn, *args):
    with pytest.raises(marketplace.Error) as exc:
        fn(*args)
    assert (exc.value.error, exc.value.status) == ("dry_run_failed", 422)
    return exc.value


def test_REQ_HK1_8_subscribe_refused_when_own_hook_breaks(database):
    hooks.save_hook(OWNER, "wrap", WRAP)
    fid = marketplace.publish(OWNER, "wrap", "Wrap rows", "Wraps the rows")["id"]
    hooks.save_hook(SUB, "zz_total", ZZ_TOTAL)

    err = expect_dry_run_failed(marketplace.subscribe, SUB, fid, sub_api())
    assert "zz_total" in str(err)
    assert sql(database, "SELECT count(*) FROM subscriptions") == [(0,)]

    hooks.toggle_hook(SUB, "zz_total")
    marketplace.subscribe(SUB, fid, sub_api())
    assert sql(database, "SELECT user_id, feature_id, version FROM subscriptions") == [(SUB, fid, 1)]


def test_REQ_HK1_8_accept_update_refused_when_own_hook_breaks(database):
    hooks.save_hook(OWNER, "feat", mark("v1"))
    fid = marketplace.publish(OWNER, "feat", "Feature", "Marks rows")["id"]
    hooks.save_hook(SUB, "zz_total", ZZ_TOTAL)
    marketplace.subscribe(SUB, fid, sub_api())
    hooks.save_hook(OWNER, "feat", WRAP)
    assert marketplace.publish(OWNER, "feat", "Feature", "Marks rows")["version"] == 2

    err = expect_dry_run_failed(marketplace.accept_update, SUB, fid, sub_api())
    assert "zz_total" in str(err)
    assert sql(database, "SELECT version FROM subscriptions WHERE user_id = %s", SUB) == [(1,)]


# REQ-HK1-9 -----------------------------------------------------------------

def test_REQ_HK1_9_dry_run_without_name_runs_only_candidate(chain):
    before = state(chain)
    out = hooks.dry_run(USER, mark("cand"), fresh(), FakeApi())
    assert marks(out) == ["cand"]
    assert state(chain) == before


def test_REQ_HK1_9_dry_run_without_name_ignores_failing_route_hooks(database):
    hooks.save_hook(USER, "z_hook", raises_on_dict("z_boom"))
    assert hooks.dry_run(USER, WRAP, fresh(), FakeApi()) == {"rows": fresh()}
