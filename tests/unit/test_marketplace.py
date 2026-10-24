import os
import uuid

import psycopg
import pytest

import db
import hooks
import marketplace
import trace

LISTING = {"id", "title", "description", "route", "owner", "version", "subscribers", "published_at", "mine",
           "subscribed"}
SUBSCRIPTION = {"feature_id", "name", "title", "route", "owner", "version", "latest_version", "update_available",
                "enabled", "published"}


def hook(route="GET /api/items", body="return data"):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n    {body}\n'


def marker(m):
    return hook(body=f'data[0]["marks"] = data[0].get("marks", []) + ["{m}"]\n    return data')


class Api:
    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return [{"id": 1}]


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


@pytest.fixture(autouse=True)
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **f: recorded.append((kind, f)))
    return recorded


def sql(query, *args):
    with db.connect() as conn:
        cur = conn.execute(query, args)
        return cur.fetchall() if cur.description else None


def counts():
    return [sql(f"SELECT count(*) FROM {t}")[0][0] for t in ("features", "feature_versions", "subscriptions", "reports")]


def seed(owner="owner", name="h", code=None, title="Title", description="Description"):
    hooks.save_hook(owner, name, code or hook())
    return marketplace.publish(owner, name, title, description)["id"]


def error(code, status, fn, *args):
    with pytest.raises(marketplace.Error) as e:
        fn(*args)
    assert (e.value.error, e.value.status) == (code, status)
    return e.value


def test_schema_of_the_marketplace_tables(database):
    schema = database.split("%3D")[1]
    tz = "timestamp with time zone"
    assert set(sql("SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns"
                   " WHERE table_schema = %s AND table_name IN"
                   " ('features', 'feature_versions', 'subscriptions', 'reports')", schema)) == {
        ("features", "id", "bigint", "NO"), ("features", "owner_id", "text", "NO"),
        ("features", "hook_name", "text", "NO"), ("features", "title", "text", "NO"),
        ("features", "description", "text", "NO"), ("features", "version", "integer", "NO"),
        ("features", "published", "boolean", "NO"), ("features", "created_at", tz, "NO"),
        ("feature_versions", "feature_id", "bigint", "NO"), ("feature_versions", "version", "integer", "NO"),
        ("feature_versions", "code", "text", "NO"), ("feature_versions", "route", "text", "NO"),
        ("feature_versions", "published_at", tz, "NO"),
        ("subscriptions", "id", "bigint", "NO"), ("subscriptions", "user_id", "text", "NO"),
        ("subscriptions", "feature_id", "bigint", "NO"), ("subscriptions", "version", "integer", "NO"),
        ("subscriptions", "enabled", "boolean", "NO"), ("subscriptions", "created_at", tz, "NO"),
        ("reports", "id", "bigint", "NO"), ("reports", "feature_id", "bigint", "NO"),
        ("reports", "user_id", "text", "NO"), ("reports", "reason", "text", "NO"),
        ("reports", "created_at", tz, "NO"),
    }
    constraints = sql("SELECT conrelid::regclass::text, contype, pg_get_constraintdef(oid) FROM pg_constraint"
                      " WHERE connamespace = %s::regnamespace AND contype IN ('p', 'u', 'f')", schema)
    assert {c for c in constraints if c[0] in ("features", "feature_versions", "subscriptions", "reports")} == {
        ("features", "p", "PRIMARY KEY (id)"), ("features", "u", "UNIQUE (owner_id, hook_name)"),
        ("feature_versions", "p", "PRIMARY KEY (feature_id, version)"),
        ("feature_versions", "f", "FOREIGN KEY (feature_id) REFERENCES features(id)"),
        ("subscriptions", "p", "PRIMARY KEY (id)"), ("subscriptions", "u", "UNIQUE (user_id, feature_id)"),
        ("subscriptions", "f", "FOREIGN KEY (feature_id, version) REFERENCES feature_versions(feature_id, version)"),
        ("reports", "p", "PRIMARY KEY (id)"), ("reports", "f", "FOREIGN KEY (feature_id) REFERENCES features(id)"),
    }


def test_init_is_idempotent_with_marketplace_rows():
    fid = seed()
    marketplace.subscribe("sub", fid, Api())
    marketplace.report("sub", fid, "bad")
    db.init()
    assert counts() == [1, 1, 1, 1]


def test_publish_creates_then_versions_only_on_changed_code(events):
    hooks.save_hook("owner", "h", hook())
    assert marketplace.publish("owner", "h", " T ", "D") == {"id": 1, "version": 1, "created": True}
    assert events[-1] == ("feature.published",
                          {"user": "owner", "feature_id": 1, "hook": "h", "version": 1, "created": True})
    assert sql("SELECT owner_id, hook_name, title, description, version, published FROM features") == [
        ("owner", "h", "T", "D", 1, True)]
    assert sql("SELECT version, code, route FROM feature_versions") == [(1, hook(), "GET /api/items")]

    hooks.save_hook("owner", "h", marker("B"))
    assert marketplace.get_feature("owner", 1)["code"] == hook()
    assert marketplace.publish("owner", "h", "T2", "D2") == {"id": 1, "version": 2, "created": False}
    assert marketplace.publish("owner", "h", "T3", "D3") == {"id": 1, "version": 2, "created": False}
    detail = marketplace.get_feature("owner", 1)
    assert (detail["title"], detail["code"], detail["version"]) == ("T3", marker("B"), 2)
    assert counts()[:2] == [1, 2]


def test_publish_errors_write_nothing():
    hooks.save_hook("owner", "h", hook())
    error("not_found", 404, marketplace.publish, "owner", "missing", "T", "D")
    for title, description in [("", "D"), ("   ", "D"), ("x" * 81, "D"), ("T", ""), ("T", "x" * 501)]:
        error("invalid", 422, marketplace.publish, "owner", "h", title, description)
    sql("INSERT INTO hooks (user_id, name, route, code, version) VALUES ('owner', 'bad', '', 'import os', 1)")
    error("invalid", 422, marketplace.publish, "owner", "bad", "T", "D")
    assert counts() == [0, 0, 0, 0]


def test_search_filters_flags_and_sorts():
    a = seed("o1", "a", title="Friday discount", description="Invoices")
    b = seed("o2", "b", title="Hide DNI", description="patients friday")
    c = seed("o3", "c", title="Other", description="nothing")
    marketplace.subscribe("u", b, Api())
    marketplace.subscribe("o1", b, Api())
    marketplace.subscribe("u", c, Api())
    marketplace.unpublish("o3", c)

    found = marketplace.search("u")
    assert [f["id"] for f in found] == [b, a]
    assert all(set(f) == LISTING for f in found)
    assert (found[0]["subscribed"], found[0]["subscribers"], found[1]["subscribed"]) == (True, 2, False)
    assert [f["mine"] for f in marketplace.search("o1")] == [False, True]
    assert [f["id"] for f in marketplace.search("u", "FRIDAY")] == [b, a]
    assert [f["id"] for f in marketplace.search("u", "friday  invoices")] == [a]
    assert marketplace.search("u", "friday nope") == []
    assert [f["id"] for f in marketplace.search("u", sort="newest")] == [b, a]
    error("invalid", 422, marketplace.search, "u", "", "bogus")


def test_subscribe_dry_runs_as_the_subscriber(events):
    fid = seed(code=hook(body='if ctx.user != "sub":\n        {}["wrong user"]\n'
                              '    return ctx.api.get("/api/me")'))
    api = Api()
    entry = marketplace.subscribe("sub", fid, api)
    assert api.calls == ["/api/items", "/api/me"]
    assert set(entry) == SUBSCRIPTION
    assert (entry["name"], entry["version"], entry["enabled"], entry["update_available"]) == (f"@{fid}", 1, True, False)
    assert events[-1] == ("feature.subscribed", {"user": "sub", "feature_id": fid, "version": 1})
    assert sql("SELECT user_id, version, enabled FROM subscriptions") == [("sub", 1, True)]

    e = error("dry_run_failed", 422, marketplace.subscribe, "other", fid, Api())
    assert "wrong user" in str(e)
    error("own_feature", 409, marketplace.subscribe, "owner", fid, Api())
    error("already_subscribed", 409, marketplace.subscribe, "sub", fid, Api())
    error("not_found", 404, marketplace.subscribe, "x", 999, Api())
    marketplace.unpublish("owner", fid)
    error("not_found", 404, marketplace.subscribe, "y", fid, Api())
    assert counts()[2] == 1


def test_dry_run_data_by_route():
    virtual = seed(name="v", code=hook("GET /x/hi", 'return {"data": data}'))
    param = seed(name="p", code=hook("GET /api/items/{id}", "return {}[0]"))
    api = Api()
    marketplace.subscribe("u", virtual, api)
    marketplace.subscribe("u", param, api)  # route with {param}: guard only, no run
    assert api.calls == []


def test_pinned_versions_and_accept_update(events):
    fid = seed(code=marker("A"))
    marketplace.subscribe("sub", fid, Api())
    hooks.save_hook("owner", "h", hook(body='{}["v2 broken"]'))
    marketplace.publish("owner", "h", "T", "D")
    assert marketplace.active("sub") == [{"name": f"@{fid}", "code": marker("A")}]
    (entry,) = marketplace.subscriptions("sub")
    assert (entry["version"], entry["latest_version"], entry["update_available"]) == (1, 2, True)
    (sub_id,) = sql("SELECT id FROM subscriptions")[0]

    error("dry_run_failed", 422, marketplace.accept_update, "sub", fid, Api())
    assert sql("SELECT version FROM subscriptions") == [(1,)]

    hooks.save_hook("owner", "h", marker("C"))
    marketplace.publish("owner", "h", "T", "D")
    entry = marketplace.accept_update("sub", fid, Api())
    assert (entry["version"], entry["latest_version"], entry["update_available"]) == (3, 3, False)
    assert sql("SELECT id, version FROM subscriptions") == [(sub_id, 3)]
    assert events[-1] == ("feature.updated", {"user": "sub", "feature_id": fid, "version": 3})
    error("up_to_date", 409, marketplace.accept_update, "sub", fid, Api())


def test_subscribe_and_accept_update_dry_run_the_chain():
    hooks.save_hook("sub", "zz", hook(body='return [r["id"] for r in data]'))
    wrap = hook(body='return {"rows": data}')
    breaking, fine = seed(name="w", code=wrap), seed(code=marker("A"))
    e = error("dry_run_failed", 422, marketplace.subscribe, "sub", breaking, Api())
    assert str(e).startswith("zz failed:")
    assert counts()[2] == 0
    hooks.toggle_hook("sub", "zz")
    marketplace.subscribe("sub", breaking, Api())
    marketplace.unsubscribe("sub", breaking)
    hooks.toggle_hook("sub", "zz")

    marketplace.subscribe("sub", fine, Api())
    hooks.save_hook("owner", "h", wrap)
    marketplace.publish("owner", "h", "T", "D")
    assert "zz failed:" in str(error("dry_run_failed", 422, marketplace.accept_update, "sub", fine, Api()))
    assert sql("SELECT version FROM subscriptions WHERE feature_id = %s", fine) == [(1,)]


def test_unpublish(events):
    fid = seed()
    marketplace.subscribe("sub", fid, Api())
    error("forbidden", 403, marketplace.unpublish, "sub", fid)
    error("not_found", 404, marketplace.unpublish, "owner", 999)
    marketplace.unpublish("owner", fid)
    assert events[-1] == ("feature.unpublished", {"user": "owner", "feature_id": fid})
    marketplace.unpublish("owner", fid)
    assert marketplace.search("owner") == marketplace.search("sub") == []
    assert marketplace.active("sub") == [{"name": f"@{fid}", "code": hook()}]
    assert marketplace.subscriptions("sub")[0]["published"] is False
    assert marketplace.get_feature("owner", fid)["published"] is False
    assert set(marketplace.get_feature("sub", fid)) == LISTING | {"published", "code"}
    error("not_found", 404, marketplace.get_feature, "stranger", fid)
    error("not_found", 404, marketplace.unpublish, "stranger", fid)
    assert marketplace.publish("owner", "h", "T", "D")["created"] is False
    assert marketplace.get_feature("stranger", fid)["published"] is True


def test_toggle_unsubscribe_and_order(events):
    a, b = seed(name="a"), seed(name="b")
    marketplace.subscribe("u", a, Api())
    marketplace.subscribe("u", b, Api())
    assert marketplace.toggle("u", a) is False
    assert events[-1] == ("feature.toggled", {"user": "u", "feature_id": a, "enabled": False})
    assert [f["name"] for f in marketplace.active("u")] == [f"@{b}"]
    assert [s["feature_id"] for s in marketplace.subscriptions("u")] == [a, b]
    assert marketplace.toggle("u", a) is True
    marketplace.unsubscribe("u", a)
    assert events[-1] == ("feature.unsubscribed", {"user": "u", "feature_id": a})
    marketplace.subscribe("u", a, Api())
    assert [f["name"] for f in marketplace.active("u")] == [f"@{b}", f"@{a}"]
    for fn in (marketplace.toggle, marketplace.unsubscribe):
        error("not_found", 404, fn, "nobody", a)
    error("not_found", 404, marketplace.accept_update, "nobody", a, Api())


def test_fork_copies_the_pinned_version(events):
    fid = seed(code=marker("A"))
    marketplace.subscribe("sub", fid, Api())
    hooks.save_hook("owner", "h", marker("B"))
    marketplace.publish("owner", "h", "T", "D")
    events.clear()
    result = marketplace.fork("sub", fid, Api())
    assert result["name"] == "h" and result["route"] == "GET /api/items" and "+++ b/h.py" in result["diff"]
    assert hooks.read_hook("sub", "h") == marker("A")
    assert marketplace.subscriptions("sub") == []
    assert [k for k, _ in events] == ["hook.saved", "feature.forked"]
    assert events[1][1] == {"user": "sub", "feature_id": fid, "version": 1, "hook": "h"}

    assert marketplace.fork("other", fid, Api(), "copy")["name"] == "copy"
    assert hooks.read_hook("other", "copy") == marker("B")


def test_fork_errors_write_nothing():
    fid = seed(code=hook(body='if ctx.user == "bad":\n        {}["bad"]\n    return data'))
    hooks.save_hook("u", "h", hook())
    error("name_taken", 409, marketplace.fork, "u", fid, Api())
    error("invalid", 422, marketplace.fork, "u", fid, Api(), "Bad Name")
    error("own_feature", 409, marketplace.fork, "owner", fid, Api())
    error("dry_run_failed", 422, marketplace.fork, "bad", fid, Api())
    marketplace.unpublish("owner", fid)
    error("not_found", 404, marketplace.fork, "x", fid, Api())
    assert sql("SELECT user_id, name FROM hooks ORDER BY user_id") == [("owner", "h"), ("u", "h")]


def test_report(events):
    fid = seed()
    marketplace.report("u", fid, "spam")
    assert sql("SELECT feature_id, user_id, reason FROM reports") == [(fid, "u", "spam")]
    assert events[-1] == ("feature.reported", {"user": "u", "feature_id": fid, "reason": "spam"})
    error("invalid", 422, marketplace.report, "u", fid, "")
    error("invalid", 422, marketplace.report, "u", fid, "x" * 501)
    error("not_found", 404, marketplace.report, "u", 999, "spam")
    assert counts()[3] == 1


def test_my_features():
    other = seed("o", "x")
    marketplace.subscribe("u", other, Api())
    marketplace.toggle("u", other)
    hooks.save_hook("u", "b", hook())
    own = seed("u", "a")
    hooks.toggle_hook("u", "a")
    listed = marketplace.my_features("u")
    assert [(f["source"], f["name"], f["enabled"]) for f in listed] == [
        ("subscribed", f"@{other}", False), ("own", "a", False), ("own", "b", True)]
    assert set(listed[0]) == SUBSCRIPTION | {"source"}
    assert listed[1] == {"source": "own", "name": "a", "route": "GET /api/items", "enabled": False, "code": hook(),
                         "feature_id": own, "published": True, "published_version": 1}
    assert (listed[2]["feature_id"], listed[2]["published"], listed[2]["published_version"]) == (None, False, None)
