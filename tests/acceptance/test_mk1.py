"""Acceptance tests for MK1 (marketplace module and tables).

Written from docs/plan/03-marketplace/, docs/specs/ (marketplace v1, storage v2, hook-contract v3,
trace-events) and docs/adr/ only.

`marketplace` does not exist before MK1 lands; its import is guarded so this file collects and
the tests fail instead of erroring at collection.
"""

import os
import uuid

import psycopg
import pytest

import db
import hooks
import trace

try:
    import marketplace
except ImportError:
    marketplace = None

ALICE, BOB, CAROL, DAVE = "alice", "bob", "carol", "dave"
UNKNOWN_ID = 999999
MK_TABLES = ("features", "feature_versions", "subscriptions", "reports")


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
def empty_schema(monkeypatch):
    """Like `database`, without `db.init()`: an empty schema for REQ-MK1-1."""
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{base}?options=-csearch_path%3D{schema}"
    monkeypatch.setenv("DATABASE_URL", url)
    yield schema, url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **fields: recorded.append({"kind": kind, **fields}))
    return recorded


class FakeApi:
    """Any object with get(path): records calls, returns fixed data."""

    def __init__(self, user, responses=None):
        self.responses = {"/api/me": {"id": user}, **(responses or {})}
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return self.responses.get(path, [])


def hook(route, body="return data"):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n' + "".join(f"    {line}\n" for line in body.splitlines())


def sql(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def mk_counts(url):
    return tuple(sql(url, f"SELECT count(*) FROM {t}")[0][0] for t in MK_TABLES)


def hook_state(url, user):
    return (
        sql(url, "SELECT name, code, enabled, version FROM hooks WHERE user_id = %s ORDER BY name", user),
        sql(url, "SELECT name, version, code FROM hook_versions WHERE user_id = %s ORDER BY name, version", user),
    )


def seed(owner, name, code, title="Title", description="Description"):
    hooks.save_hook(owner, name, code)
    return marketplace.publish(owner, name, title, description)["id"]


def republish(owner, name, code, title="Title", description="Description"):
    hooks.save_hook(owner, name, code)
    return marketplace.publish(owner, name, title, description)


def expect_error(error, status, fn, *args, **kwargs):
    with pytest.raises(marketplace.Error) as exc:
        fn(*args, **kwargs)
    assert (exc.value.error, exc.value.status) == (error, status)
    return exc.value


def of_kind(events, kind):
    return [e for e in events if e["kind"] == kind]


def listing(user, **kwargs):
    return {e["id"]: e for e in marketplace.search(user, **kwargs)}


LISTING_KEYS = {"id", "title", "description", "route", "owner", "version", "subscribers", "published_at", "mine", "subscribed"}
DETAIL_KEYS = LISTING_KEYS | {"published", "code"}
SUBSCRIPTION_KEYS = {"feature_id", "name", "title", "route", "owner", "version", "latest_version", "update_available", "enabled", "published"}
OWN_KEYS = {"source", "name", "route", "enabled", "code", "feature_id", "published", "published_version"}

X1 = hook("GET /x/one", "return {'v': 1}")
X2 = hook("GET /x/one", "return {'v': 2}")
FAILS = hook("GET /x/one", "int('kaboom_marker')\nreturn data")


# REQ-MK1-1 -----------------------------------------------------------------

EXPECTED_COLUMNS = {
    "hooks": {
        "user_id": ("text", False), "name": ("text", False), "route": ("text", False), "code": ("text", False),
        "enabled": ("boolean", False), "version": ("integer", False), "updated_at": ("timestamp with time zone", False),
    },
    "hook_versions": {
        "user_id": ("text", False), "name": ("text", False), "version": ("integer", False), "code": ("text", False),
        "saved_at": ("timestamp with time zone", False),
    },
    "messages": {
        "id": ("bigint", False), "user_id": ("text", False), "turn": ("jsonb", False),
        "created_at": ("timestamp with time zone", False), "conversation_id": ("text", False),
    },
    "features": {
        "id": ("bigint", False), "owner_id": ("text", False), "hook_name": ("text", False), "title": ("text", False),
        "description": ("text", False), "version": ("integer", False), "published": ("boolean", False),
        "created_at": ("timestamp with time zone", False),
    },
    "feature_versions": {
        "feature_id": ("bigint", False), "version": ("integer", False), "code": ("text", False),
        "route": ("text", False), "published_at": ("timestamp with time zone", False),
    },
    "subscriptions": {
        "id": ("bigint", False), "user_id": ("text", False), "feature_id": ("bigint", False),
        "version": ("integer", False), "enabled": ("boolean", False), "created_at": ("timestamp with time zone", False),
    },
    "reports": {
        "id": ("bigint", False), "feature_id": ("bigint", False), "user_id": ("text", False), "reason": ("text", False),
        "created_at": ("timestamp with time zone", False),
    },
}

# (constraint type, columns, referenced table, referenced columns)
EXPECTED_KEYS = {
    "features": {("p", ("id",), None, ()), ("u", ("owner_id", "hook_name"), None, ())},
    "feature_versions": {("p", ("feature_id", "version"), None, ()), ("f", ("feature_id",), "features", ("id",))},
    "subscriptions": {
        ("p", ("id",), None, ()),
        ("u", ("user_id", "feature_id"), None, ()),
        ("f", ("feature_id", "version"), "feature_versions", ("feature_id", "version")),
    },
    "reports": {("p", ("id",), None, ()), ("f", ("feature_id",), "features", ("id",))},
}

KEYS_QUERY = """
SELECT c.contype::text,
       ARRAY(SELECT a.attname::text FROM unnest(c.conkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute a ON a.attrelid = c.conrelid AND a.attnum = k.n ORDER BY k.i),
       cf.relname::text,
       ARRAY(SELECT a.attname::text FROM unnest(c.confkey) WITH ORDINALITY k(n, i)
             JOIN pg_attribute a ON a.attrelid = c.confrelid AND a.attnum = k.n ORDER BY k.i)
FROM pg_constraint c
JOIN pg_class t ON t.oid = c.conrelid
JOIN pg_namespace ns ON ns.oid = t.relnamespace
LEFT JOIN pg_class cf ON cf.oid = c.confrelid
WHERE ns.nspname = %s AND t.relname = %s AND c.contype IN ('p', 'u', 'f')
"""


def table_names(url, schema):
    rows = sql(url, "SELECT table_name FROM information_schema.tables WHERE table_schema = %s AND table_type = 'BASE TABLE'", schema)
    return {r[0] for r in rows}


def table_columns(url, schema, table):
    rows = sql(
        url,
        "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns "
        "WHERE table_schema = %s AND table_name = %s",
        schema, table,
    )
    return {name: (dtype, nullable == "YES", default) for name, dtype, nullable, default in rows}


def table_keys(url, schema, table):
    return {(t, tuple(cols), ref, tuple(refcols or ())) for t, cols, ref, refcols in sql(url, KEYS_QUERY, schema, table)}


def test_REQ_MK1_1_init_creates_seven_tables(empty_schema):
    schema, url = empty_schema
    assert table_names(url, schema) == set()
    db.init()
    assert table_names(url, schema) == set(EXPECTED_COLUMNS)


@pytest.mark.parametrize("table", list(EXPECTED_COLUMNS))
def test_REQ_MK1_1_columns_types_and_nullability(empty_schema, table):
    schema, url = empty_schema
    db.init()
    got = {name: (dtype, nullable) for name, (dtype, nullable, _) in table_columns(url, schema, table).items()}
    assert got == EXPECTED_COLUMNS[table]


@pytest.mark.parametrize("table", list(EXPECTED_KEYS))
def test_REQ_MK1_1_primary_unique_and_foreign_keys(empty_schema, table):
    schema, url = empty_schema
    db.init()
    assert table_keys(url, schema, table) == EXPECTED_KEYS[table]


@pytest.mark.parametrize("table", ["features", "subscriptions", "reports"])
def test_REQ_MK1_1_bigserial_ids(empty_schema, table):
    schema, url = empty_schema
    db.init()
    dtype, _, default = table_columns(url, schema, table)["id"]
    assert dtype == "bigint"
    assert default is not None and default.startswith("nextval(")


def test_REQ_MK1_1_second_init_keeps_rows_in_all_seven_tables(database):
    with psycopg.connect(database) as conn:
        conn.execute("INSERT INTO hooks (user_id, name, route, code, version) VALUES ('u1', 'h', 'GET /x/a', 'c', 1)")
        conn.execute("INSERT INTO hook_versions (user_id, name, version, code) VALUES ('u1', 'h', 1, 'c')")
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('u1', '[]'::jsonb)")
        fid = conn.execute(
            "INSERT INTO features (owner_id, hook_name, title, description, version) "
            "VALUES ('u1', 'h', 't', 'd', 1) RETURNING id"
        ).fetchone()[0]
        conn.execute("INSERT INTO feature_versions (feature_id, version, code, route) VALUES (%s, 1, 'c', 'GET /x/a')", (fid,))
        conn.execute("INSERT INTO subscriptions (user_id, feature_id, version) VALUES ('u2', %s, 1)", (fid,))
        conn.execute("INSERT INTO reports (feature_id, user_id, reason) VALUES (%s, 'u2', 'r')", (fid,))
    tables = ("hooks", "hook_versions", "messages") + MK_TABLES
    before = {t: sql(database, f"SELECT * FROM {t}") for t in tables}
    assert all(len(rows) == 1 for rows in before.values())

    db.init()

    assert {t: sql(database, f"SELECT * FROM {t}") for t in tables} == before


# REQ-MK1-2 -----------------------------------------------------------------

def test_REQ_MK1_2_first_publish(database, events):
    code = hook("GET /api/invoices", "return data")
    hooks.save_hook(ALICE, "disc", code)
    events.clear()

    result = marketplace.publish(ALICE, "disc", "Friday discount", "15% off on Fridays")

    assert set(result) == {"id", "version", "created"}
    fid = result["id"]
    assert isinstance(fid, int) and not isinstance(fid, bool)
    assert result == {"id": fid, "version": 1, "created": True}
    assert sql(database, "SELECT id, owner_id, hook_name, title, description, version, published FROM features") == [
        (fid, ALICE, "disc", "Friday discount", "15% off on Fridays", 1, True)
    ]
    assert sql(database, "SELECT feature_id, version, code, route FROM feature_versions") == [
        (fid, 1, code, "GET /api/invoices")
    ]
    published = of_kind(events, "feature.published")
    assert len(published) == 1
    assert published[0] == {"kind": "feature.published", "user": ALICE, "feature_id": fid, "hook": "disc", "version": 1, "created": True}


# REQ-MK1-3 -----------------------------------------------------------------

def test_REQ_MK1_3_missing_hook_not_found(database):
    hooks.save_hook(BOB, "disc", X1)
    expect_error("not_found", 404, marketplace.publish, ALICE, "disc", "Title", "Description")
    assert mk_counts(database) == (0, 0, 0, 0)


@pytest.mark.parametrize("title,description", [
    ("", "Description"),
    ("   ", "Description"),
    ("a" * 81, "Description"),
    ("Title", ""),
    ("Title", "d" * 501),
], ids=["title_empty", "title_blank", "title_81", "description_empty", "description_501"])
def test_REQ_MK1_3_bad_title_or_description_invalid(database, title, description):
    hooks.save_hook(ALICE, "disc", X1)
    expect_error("invalid", 422, marketplace.publish, ALICE, "disc", title, description)
    assert mk_counts(database) == (0, 0, 0, 0)


def test_REQ_MK1_3_hook_failing_guard_invalid(database):
    bad = "import os\n" + hook("GET /x/one")
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO hooks (user_id, name, route, code, enabled, version) VALUES (%s, 'evil', 'GET /x/one', %s, true, 1)",
            (ALICE, bad),
        )
    expect_error("invalid", 422, marketplace.publish, ALICE, "evil", "Title", "Description")
    assert mk_counts(database) == (0, 0, 0, 0)


# REQ-MK1-4 -----------------------------------------------------------------

def test_REQ_MK1_4_versions_follow_publishes_not_saves(database):
    fid = seed(ALICE, "one", X1, "T1", "D1")
    hooks.save_hook(ALICE, "one", X2)

    detail = marketplace.get_feature(ALICE, fid)
    assert (detail["code"], detail["version"]) == (X1, 1)

    assert marketplace.publish(ALICE, "one", "T2", "D2") == {"id": fid, "version": 2, "created": False}
    detail = marketplace.get_feature(ALICE, fid)
    assert (detail["code"], detail["version"], detail["title"], detail["description"]) == (X2, 2, "T2", "D2")
    assert sql(database, "SELECT version, code FROM feature_versions WHERE feature_id = %s ORDER BY version", fid) == [(1, X1), (2, X2)]

    assert marketplace.publish(ALICE, "one", "T3", "D2") == {"id": fid, "version": 2, "created": False}
    assert sql(database, "SELECT count(*) FROM feature_versions")[0][0] == 2
    assert sql(database, "SELECT title, version FROM features WHERE id = %s", fid) == [("T3", 2)]
    assert marketplace.get_feature(ALICE, fid)["title"] == "T3"


# REQ-MK1-5 -----------------------------------------------------------------

@pytest.fixture
def market(database):
    """f1, f2 by alice, f3 by carol, f4 by carol unpublished.
    Subscribers: f2 by bob and dave, f3 by bob. f1 republished last (newest)."""
    f1 = seed(ALICE, "one", hook("GET /x/one", "return 1"), "Friday discount", "15% off invoices issued on a Friday")
    f2 = seed(ALICE, "two", hook("GET /x/two", "return 2"), "Hide DNI", "Mask patient documents")
    f3 = seed(CAROL, "three", hook("GET /x/three", "return 3"), "Age column", "Adds the age to patients")
    f4 = seed(CAROL, "four", hook("GET /x/four", "return 4"), "Hidden friday thing", "Unpublished")
    marketplace.unpublish(CAROL, f4)
    marketplace.subscribe(BOB, f2, FakeApi(BOB))
    marketplace.subscribe(DAVE, f2, FakeApi(DAVE))
    marketplace.subscribe(BOB, f3, FakeApi(BOB))
    republish(ALICE, "one", hook("GET /x/one", "return 11"), "Friday discount", "15% off invoices issued on a Friday")
    return f1, f2, f3, f4


def test_REQ_MK1_5_only_published_with_exact_keys(market):
    f1, f2, f3, f4 = market
    for user in (ALICE, BOB, CAROL, DAVE):
        entries = marketplace.search(user)
        assert {e["id"] for e in entries} == {f1, f2, f3}
        for e in entries:
            assert set(e) == LISTING_KEYS


def test_REQ_MK1_5_listing_values(market):
    f1, f2, f3, _ = market
    by_id = listing(BOB)
    assert {k: v for k, v in by_id[f1].items() if k != "published_at"} == {
        "id": f1, "title": "Friday discount", "description": "15% off invoices issued on a Friday",
        "route": "GET /x/one", "owner": ALICE, "version": 2, "subscribers": 0, "mine": False, "subscribed": False,
    }
    assert isinstance(by_id[f1]["published_at"], str)
    assert [by_id[f]["subscribers"] for f in (f1, f2, f3)] == [0, 2, 1]


def test_REQ_MK1_5_mine_and_subscribed_flags(market):
    f1, f2, f3, _ = market
    flags = lambda user: {f: (e["mine"], e["subscribed"]) for f, e in listing(user).items()}
    assert flags(ALICE) == {f1: (True, False), f2: (True, False), f3: (False, False)}
    assert flags(BOB) == {f1: (False, False), f2: (False, True), f3: (False, True)}
    assert flags(CAROL) == {f1: (False, False), f2: (False, False), f3: (True, False)}
    assert flags(DAVE) == {f1: (False, False), f2: (False, True), f3: (False, False)}


@pytest.mark.parametrize("q,expected", [
    ("friday", ["f1"]),
    ("FRIDAY Discount", ["f1"]),
    ("invoices friday", ["f1"]),
    ("hide mask", ["f2"]),
    ("patients", ["f3"]),
    ("friday dni", []),
    ("", ["f1", "f2", "f3"]),
], ids=["one_word", "case_insensitive", "description_and_title", "words_split_title_description", "description_only", "not_all_words", "empty"])
def test_REQ_MK1_5_q_matches_every_word(market, q, expected):
    ids = dict(zip(["f1", "f2", "f3"], market[:3]))
    assert {e["id"] for e in marketplace.search(DAVE, q=q)} == {ids[k] for k in expected}


def test_REQ_MK1_5_sort_subscribers_default(market):
    f1, f2, f3, _ = market
    assert [e["id"] for e in marketplace.search(DAVE)] == [f2, f3, f1]
    assert [e["id"] for e in marketplace.search(DAVE, sort="subscribers")] == [f2, f3, f1]


def test_REQ_MK1_5_sort_newest(market):
    f1, f2, f3, _ = market
    assert [e["id"] for e in marketplace.search(DAVE, sort="newest")] == [f1, f3, f2]


def test_REQ_MK1_5_unknown_sort_invalid(market):
    expect_error("invalid", 422, marketplace.search, DAVE, sort="popular")


# REQ-MK1-6 -----------------------------------------------------------------

WHO_V1 = hook("GET /x/who", f"if ctx.user != '{BOB}':\n    int('wrong user')\nme = ctx.api.get('/api/me')\nreturn {{'user': ctx.user, 'me': me['id']}}")
WHO_V2 = hook("GET /x/who", f"if ctx.user != '{BOB}':\n    int('wrong user')\nme = ctx.api.get('/api/me')\nreturn {{'user': ctx.user, 'me': me['id'], 'v': 2}}")


def test_REQ_MK1_6_subscribe_dry_runs_as_subscriber_and_pins_latest(database, events):
    fid = seed(ALICE, "who", WHO_V1, "Who", "Shows who")
    republish(ALICE, "who", WHO_V2, "Who", "Shows who")
    assert listing(CAROL)[fid]["subscribers"] == 0
    api = FakeApi(BOB)
    events.clear()

    entry = marketplace.subscribe(BOB, fid, api)

    assert "/api/me" in api.calls
    assert sql(database, "SELECT user_id, feature_id, version, enabled FROM subscriptions") == [(BOB, fid, 2, True)]
    assert set(entry) == SUBSCRIPTION_KEYS
    assert entry == {
        "feature_id": fid, "name": f"@{fid}", "title": "Who", "route": "GET /x/who", "owner": ALICE,
        "version": 2, "latest_version": 2, "update_available": False, "enabled": True, "published": True,
    }
    assert listing(CAROL)[fid]["subscribers"] == 1
    subscribed = of_kind(events, "feature.subscribed")
    assert subscribed == [{"kind": "feature.subscribed", "user": BOB, "feature_id": fid, "version": 2}]


def test_REQ_MK1_6_get_route_dry_run_reads_data_through_api(database):
    code = hook("GET /api/invoices", "for i in data:\n    i['seen'] = True\nreturn data")
    fid = seed(ALICE, "inv", code)
    api = FakeApi(BOB, {"/api/invoices": [{"id": 1}]})
    marketplace.subscribe(BOB, fid, api)
    assert "/api/invoices" in api.calls


# REQ-MK1-7 -----------------------------------------------------------------

def test_REQ_MK1_7_dry_run_failure_on_subscriber_data(database, events):
    code = hook("GET /api/invoices", "for i in data:\n    if i['flag']:\n        int('kaboom_marker')\nreturn data")
    fid = seed(ALICE, "inv", code)
    events.clear()
    err = expect_error("dry_run_failed", 422, marketplace.subscribe, BOB, fid, FakeApi(BOB, {"/api/invoices": [{"flag": True}]}))
    assert "kaboom_marker" in str(err)
    assert sql(database, "SELECT count(*) FROM subscriptions")[0][0] == 0
    assert of_kind(events, "feature.subscribed") == []


def test_REQ_MK1_7_own_feature(database):
    fid = seed(ALICE, "one", X1)
    expect_error("own_feature", 409, marketplace.subscribe, ALICE, fid, FakeApi(ALICE))
    assert sql(database, "SELECT count(*) FROM subscriptions")[0][0] == 0


def test_REQ_MK1_7_already_subscribed(database):
    fid = seed(ALICE, "one", X1)
    marketplace.subscribe(BOB, fid, FakeApi(BOB))
    expect_error("already_subscribed", 409, marketplace.subscribe, BOB, fid, FakeApi(BOB))
    assert sql(database, "SELECT user_id, feature_id FROM subscriptions") == [(BOB, fid)]


def test_REQ_MK1_7_unknown_feature(database):
    seed(ALICE, "one", X1)
    expect_error("not_found", 404, marketplace.subscribe, BOB, UNKNOWN_ID, FakeApi(BOB))
    assert sql(database, "SELECT count(*) FROM subscriptions")[0][0] == 0


def test_REQ_MK1_7_unpublished_feature(database):
    fid = seed(ALICE, "one", X1)
    marketplace.unpublish(ALICE, fid)
    expect_error("not_found", 404, marketplace.subscribe, BOB, fid, FakeApi(BOB))
    assert sql(database, "SELECT count(*) FROM subscriptions")[0][0] == 0


# REQ-MK1-8 -----------------------------------------------------------------

FLAGGED_V2 = hook("GET /x/one", "if not ctx.api.get('/api/flag')['ok']:\n    int('kaboom_marker')\nreturn {'v': 2}")


@pytest.fixture
def pinned(database):
    """bob subscribed to alice's feature at version 1; alice then published version 2 (FLAGGED_V2)."""
    fid = seed(ALICE, "one", X1, "One", "First")
    marketplace.subscribe(BOB, fid, FakeApi(BOB))
    assert republish(ALICE, "one", FLAGGED_V2, "One", "First")["version"] == 2
    return fid


def test_REQ_MK1_8_subscriber_stays_on_pinned_version(pinned):
    fid = pinned
    assert marketplace.active(BOB) == [{"name": f"@{fid}", "code": X1}]
    entries = marketplace.subscriptions(BOB)
    assert len(entries) == 1
    assert set(entries[0]) == SUBSCRIPTION_KEYS
    assert (entries[0]["version"], entries[0]["latest_version"], entries[0]["update_available"]) == (1, 2, True)


def test_REQ_MK1_8_failing_update_keeps_pin(database, pinned, events):
    fid = pinned
    events.clear()
    expect_error("dry_run_failed", 422, marketplace.accept_update, BOB, fid, FakeApi(BOB, {"/api/flag": {"ok": False}}))
    assert sql(database, "SELECT version FROM subscriptions WHERE user_id = %s", BOB) == [(1,)]
    assert marketplace.active(BOB) == [{"name": f"@{fid}", "code": X1}]
    assert of_kind(events, "feature.updated") == []


def test_REQ_MK1_8_passing_update_moves_pin_then_up_to_date(database, pinned, events):
    fid = pinned
    sub_id = sql(database, "SELECT id FROM subscriptions WHERE user_id = %s", BOB)[0][0]
    events.clear()

    entry = marketplace.accept_update(BOB, fid, FakeApi(BOB, {"/api/flag": {"ok": True}}))

    assert set(entry) == SUBSCRIPTION_KEYS
    assert (entry["version"], entry["latest_version"], entry["update_available"]) == (2, 2, False)
    assert sql(database, "SELECT id, version FROM subscriptions WHERE user_id = %s", BOB) == [(sub_id, 2)]
    assert marketplace.active(BOB) == [{"name": f"@{fid}", "code": FLAGGED_V2}]
    assert of_kind(events, "feature.updated") == [{"kind": "feature.updated", "user": BOB, "feature_id": fid, "version": 2}]

    expect_error("up_to_date", 409, marketplace.accept_update, BOB, fid, FakeApi(BOB, {"/api/flag": {"ok": True}}))
    assert sql(database, "SELECT id, version FROM subscriptions WHERE user_id = %s", BOB) == [(sub_id, 2)]


# REQ-MK1-9 -----------------------------------------------------------------

def test_REQ_MK1_9_unpublish_by_non_owner_forbidden(database, events):
    fid = seed(ALICE, "one", X1)
    events.clear()
    expect_error("forbidden", 403, marketplace.unpublish, BOB, fid)
    assert sql(database, "SELECT published FROM features WHERE id = %s", fid) == [(True,)]
    assert of_kind(events, "feature.unpublished") == []


def test_REQ_MK1_9_unpublish_by_owner(database, events):
    fid = seed(ALICE, "one", X1, "One", "First")
    marketplace.subscribe(BOB, fid, FakeApi(BOB))
    events.clear()

    assert marketplace.unpublish(ALICE, fid) is None

    assert sql(database, "SELECT published FROM features WHERE id = %s", fid) == [(False,)]
    assert of_kind(events, "feature.unpublished") == [{"kind": "feature.unpublished", "user": ALICE, "feature_id": fid}]
    for user in (ALICE, BOB, CAROL):
        assert fid not in listing(user)
    expect_error("not_found", 404, marketplace.subscribe, CAROL, fid, FakeApi(CAROL))
    assert marketplace.active(BOB) == [{"name": f"@{fid}", "code": X1}]
    assert [(e["feature_id"], e["published"]) for e in marketplace.subscriptions(BOB)] == [(fid, False)]
    for user in (ALICE, BOB):
        detail = marketplace.get_feature(user, fid)
        assert set(detail) == DETAIL_KEYS
        assert (detail["id"], detail["published"], detail["code"]) == (fid, False, X1)
    expect_error("not_found", 404, marketplace.get_feature, CAROL, fid)


# REQ-MK1-10 ----------------------------------------------------------------

@pytest.fixture
def two_subs(database):
    f1 = seed(ALICE, "one", X1, "One", "First")
    f2 = seed(ALICE, "two", hook("GET /x/two", "return 2"), "Two", "Second")
    marketplace.subscribe(BOB, f1, FakeApi(BOB))
    marketplace.subscribe(BOB, f2, FakeApi(BOB))
    return f1, f2


def test_REQ_MK1_10_toggle(database, two_subs, events):
    f1, f2 = two_subs
    events.clear()

    assert marketplace.toggle(BOB, f1) is False
    assert sql(database, "SELECT enabled FROM subscriptions WHERE feature_id = %s", f1) == [(False,)]
    assert [e["name"] for e in marketplace.active(BOB)] == [f"@{f2}"]
    assert [(e["feature_id"], e["enabled"]) for e in marketplace.subscriptions(BOB)] == [(f1, False), (f2, True)]

    assert marketplace.toggle(BOB, f1) is True
    assert [e["name"] for e in marketplace.active(BOB)] == [f"@{f1}", f"@{f2}"]
    assert of_kind(events, "feature.toggled") == [
        {"kind": "feature.toggled", "user": BOB, "feature_id": f1, "enabled": False},
        {"kind": "feature.toggled", "user": BOB, "feature_id": f1, "enabled": True},
    ]


def test_REQ_MK1_10_unsubscribe_and_resubscribe_goes_last(database, two_subs, events):
    f1, f2 = two_subs
    events.clear()

    assert marketplace.unsubscribe(BOB, f1) is None

    assert sql(database, "SELECT feature_id FROM subscriptions WHERE user_id = %s", BOB) == [(f2,)]
    assert of_kind(events, "feature.unsubscribed") == [{"kind": "feature.unsubscribed", "user": BOB, "feature_id": f1}]
    marketplace.subscribe(BOB, f1, FakeApi(BOB))
    assert [e["name"] for e in marketplace.active(BOB)] == [f"@{f2}", f"@{f1}"]


@pytest.mark.parametrize("action", ["toggle", "unsubscribe", "accept_update"])
def test_REQ_MK1_10_without_subscription_not_found(database, two_subs, action):
    f1, _ = two_subs
    args = (CAROL, f1, FakeApi(CAROL)) if action == "accept_update" else (CAROL, f1)
    before = sql(database, "SELECT * FROM subscriptions ORDER BY id")
    expect_error("not_found", 404, getattr(marketplace, action), *args)
    assert sql(database, "SELECT * FROM subscriptions ORDER BY id") == before


# REQ-MK1-11 ----------------------------------------------------------------

DISC_V1 = hook("GET /x/disc", "return {'v': 1}")
DISC_V2 = hook("GET /x/disc", "return {'v': 2}")


@pytest.fixture
def forkable(database):
    """bob subscribed to alice's `disc` at version 1; version 2 exists."""
    fid = seed(ALICE, "disc", DISC_V1, "Disc", "Discount")
    marketplace.subscribe(BOB, fid, FakeApi(BOB))
    republish(ALICE, "disc", DISC_V2, "Disc", "Discount")
    return fid


def test_REQ_MK1_11_fork_copies_pinned_code(database, forkable, events):
    fid = forkable
    events.clear()

    result = marketplace.fork(BOB, fid, FakeApi(BOB))

    assert set(result) == {"name", "route", "diff"}
    assert (result["name"], result["route"]) == ("disc", "GET /x/disc")
    assert isinstance(result["diff"], str)
    assert hooks.read_hook(BOB, "disc") == DISC_V1
    assert sql(database, "SELECT version FROM hooks WHERE user_id = %s AND name = 'disc'", BOB) == [(1,)]
    assert sql(database, "SELECT count(*) FROM subscriptions WHERE user_id = %s", BOB) == [(0,)]
    kinds = [e["kind"] for e in events if e["kind"] in ("hook.saved", "feature.forked")]
    assert kinds == ["hook.saved", "feature.forked"]
    saved = of_kind(events, "hook.saved")[0]
    assert (saved["user"], saved["hook"]) == (BOB, "disc")
    assert of_kind(events, "feature.forked") == [{"kind": "feature.forked", "user": BOB, "feature_id": fid, "version": 1, "hook": "disc"}]


def assert_fork_wrote_nothing(database, before, events):
    assert (hook_state(database, BOB), sql(database, "SELECT * FROM subscriptions ORDER BY id")) == before
    assert of_kind(events, "hook.saved") == [] and of_kind(events, "feature.forked") == []


def snapshot(database):
    return hook_state(database, BOB), sql(database, "SELECT * FROM subscriptions ORDER BY id")


def test_REQ_MK1_11_name_taken(database, forkable, events):
    hooks.save_hook(BOB, "mine", X1)
    before = snapshot(database)
    events.clear()
    expect_error("name_taken", 409, marketplace.fork, BOB, forkable, FakeApi(BOB), name="mine")
    assert_fork_wrote_nothing(database, before, events)


def test_REQ_MK1_11_default_name_taken(database, forkable, events):
    hooks.save_hook(BOB, "disc", X1)
    before = snapshot(database)
    events.clear()
    expect_error("name_taken", 409, marketplace.fork, BOB, forkable, FakeApi(BOB))
    assert_fork_wrote_nothing(database, before, events)


def test_REQ_MK1_11_invalid_name(database, forkable, events):
    before = snapshot(database)
    events.clear()
    expect_error("invalid", 422, marketplace.fork, BOB, forkable, FakeApi(BOB), name="Bad-Name")
    assert_fork_wrote_nothing(database, before, events)


def test_REQ_MK1_11_owner_own_feature(database, forkable, events):
    before = hook_state(database, ALICE)
    events.clear()
    expect_error("own_feature", 409, marketplace.fork, ALICE, forkable, FakeApi(ALICE), name="copy")
    assert hook_state(database, ALICE) == before
    assert of_kind(events, "feature.forked") == []


def test_REQ_MK1_11_dry_run_failed(database, events):
    fid = seed(ALICE, "disc", hook("GET /x/disc", f"if ctx.user == '{BOB}':\n    int('kaboom_marker')\nreturn 1"))
    before = snapshot(database)
    events.clear()
    expect_error("dry_run_failed", 422, marketplace.fork, BOB, fid, FakeApi(BOB))
    assert_fork_wrote_nothing(database, before, events)


def test_REQ_MK1_11_unpublished_not_subscribed_not_found(database, events):
    fid = seed(ALICE, "disc", DISC_V1)
    marketplace.unpublish(ALICE, fid)
    before = snapshot(database)
    events.clear()
    expect_error("not_found", 404, marketplace.fork, BOB, fid, FakeApi(BOB))
    assert_fork_wrote_nothing(database, before, events)


# REQ-MK1-12 ----------------------------------------------------------------

def test_REQ_MK1_12_report(database, events):
    fid = seed(ALICE, "one", X1)
    events.clear()
    assert marketplace.report(CAROL, fid, "misleading title") is None
    assert sql(database, "SELECT feature_id, user_id, reason FROM reports") == [(fid, CAROL, "misleading title")]
    assert of_kind(events, "feature.reported") == [
        {"kind": "feature.reported", "user": CAROL, "feature_id": fid, "reason": "misleading title"}
    ]


@pytest.mark.parametrize("reason", ["", "r" * 501], ids=["empty", "501"])
def test_REQ_MK1_12_bad_reason_invalid(database, events, reason):
    fid = seed(ALICE, "one", X1)
    events.clear()
    expect_error("invalid", 422, marketplace.report, CAROL, fid, reason)
    assert sql(database, "SELECT count(*) FROM reports")[0][0] == 0
    assert of_kind(events, "feature.reported") == []


def test_REQ_MK1_12_unknown_feature(database, events):
    seed(ALICE, "one", X1)
    events.clear()
    expect_error("not_found", 404, marketplace.report, CAROL, UNKNOWN_ID, "spam")
    assert sql(database, "SELECT count(*) FROM reports")[0][0] == 0
    assert of_kind(events, "feature.reported") == []


# REQ-MK1-13 ----------------------------------------------------------------

def test_REQ_MK1_13_my_features(database):
    f1 = seed(ALICE, "one", X1, "One", "First")
    f2 = seed(CAROL, "two", hook("GET /x/two", "return 2"), "Two", "Second")
    marketplace.subscribe(BOB, f2, FakeApi(BOB))   # subscribed first, though its id is higher
    marketplace.subscribe(BOB, f1, FakeApi(BOB))
    marketplace.toggle(BOB, f2)

    a_code = hook("GET /x/a", "return 'a'")
    b1, b2 = hook("GET /api/invoices", "return data"), hook("GET /api/invoices", "return data[:1]")
    c_code = hook("GET /x/c", "return 'c'")
    hooks.save_hook(BOB, "b_hook", b1)
    fb = marketplace.publish(BOB, "b_hook", "B", "Bee")["id"]
    hooks.save_hook(BOB, "b_hook", b2)
    marketplace.publish(BOB, "b_hook", "B", "Bee")
    hooks.save_hook(BOB, "c_hook", c_code)
    fc = marketplace.publish(BOB, "c_hook", "C", "Cee")["id"]
    marketplace.unpublish(BOB, fc)
    hooks.save_hook(BOB, "a_hook", a_code)
    hooks.toggle_hook(BOB, "a_hook")

    result = marketplace.my_features(BOB)

    assert result == [
        {"source": "subscribed", "name": f"@{f2}", "route": "GET /x/two", "enabled": False,
         "feature_id": f2, "title": "Two", "owner": CAROL,
         "version": 1, "latest_version": 1, "update_available": False, "published": True},
        {"source": "subscribed", "name": f"@{f1}", "route": "GET /x/one", "enabled": True,
         "feature_id": f1, "title": "One", "owner": ALICE,
         "version": 1, "latest_version": 1, "update_available": False, "published": True},
        {"source": "own", "name": "a_hook", "route": "GET /x/a", "enabled": False, "code": a_code,
         "feature_id": None, "published": False, "published_version": None},
        {"source": "own", "name": "b_hook", "route": "GET /api/invoices", "enabled": True, "code": b2,
         "feature_id": fb, "published": True, "published_version": 2},
        {"source": "own", "name": "c_hook", "route": "GET /x/c", "enabled": True, "code": c_code,
         "feature_id": fc, "published": False, "published_version": 1},
    ]
    for e in result:
        assert set(e) == (SUBSCRIPTION_KEYS | {"source"} if e["source"] == "subscribed" else OWN_KEYS)


def test_REQ_MK1_13_empty(database):
    assert marketplace.my_features(BOB) == []
