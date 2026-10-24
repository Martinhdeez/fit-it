"""Acceptance tests for MK2 (subscribed features in the proxy, marketplace routes, My features).

Written from docs/plan/03-marketplace/, docs/specs/ (marketplace v1, proxy v3, hook-contract v3,
storage v2, trace-events) and docs/adr/ only.

REQ-MK2-13 also requires the iteration 01 and 02 suites to pass unchanged; `make check` runs them
from this same folder, so here only the `GET /fitit/hooks` v2 shape is checked with marketplace data.
"""

import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient

import hooks
import main
import trace
from main import app

# Until MK1 lands, marketplace is missing; keep the file collectable so tests fail, not error.
try:
    import marketplace
except ImportError:
    marketplace = None

import psycopg

import db

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
OWNER, SUB, OTHER = "gerente", "recepcion", "dr_garcia"

LISTING_KEYS = {"id", "title", "description", "route", "owner", "version", "subscribers",
                "published_at", "mine", "subscribed"}
DETAIL_KEYS = LISTING_KEYS | {"published", "code"}
SUB_KEYS = {"feature_id", "name", "title", "route", "owner", "version", "latest_version",
            "update_available", "enabled", "published"}
MY_SUB_KEYS = SUB_KEYS | {"source"}
MY_OWN_KEYS = {"source", "name", "route", "enabled", "code", "feature_id", "published", "published_version"}


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


def mark(marker, route="GET /api/invoices"):
    return hook(route, f'data[0]["marks"] = data[0].get("marks", []) + ["{marker}"]\nreturn data')


def tag(value):
    return hook("GET /api/invoices", f'data[0]["tag"] = "{value}"\nreturn data')


PASS = hook("GET /api/invoices")
RAISES_ON_DATA = hook("GET /api/invoices", 'return data[0]["no_such_field"]')


def publish(owner, name, code, title="Some feature", description="Does something useful"):
    hooks.save_hook(owner, name, code)
    return marketplace.publish(owner, name, title, description)["id"]


def subscribe(user, feature_id):
    return marketplace.subscribe(user, feature_id, main.Api(f"session={user}"))


def fake_subscribe(user, feature_id):
    return marketplace.subscribe(user, feature_id, FakeApi({"/api/invoices": [{"id": 1}]}))


def login(user):
    client = TestClient(app)
    r = client.get(f"/login?user={user}")
    assert r.status_code == 200
    assert client.cookies.get("session") == user
    return client


def sql(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def of_kind(events, kind):
    return [e for e in events if e["kind"] == kind]


def names_header(r):
    value = r.headers.get("x-fitit-hooks")
    return value.split(",") if value else []


def assert_error(r, status, error):
    assert r.status_code == status, r.text
    body = r.json()
    if status == 404:
        assert body == {"error": "not_found"}
    else:
        assert set(body) == {"error", "detail"}
        assert body["error"] == error
        assert isinstance(body["detail"], str)


def roundtrip(value):
    return json.loads(json.dumps(value))


# REQ-MK2-1 -----------------------------------------------------------------

def test_REQ_MK2_1_feature_runs_as_the_subscriber(core_env):
    fid = publish(OWNER, "whoami", hook("GET /x/whoami", 'return {"user": ctx.user, "me": ctx.api.get("/api/me")["id"]}'))
    subscribe(SUB, fid)
    r = login(SUB).get("/x/whoami")
    assert r.status_code == 200
    assert r.json() == {"user": SUB, "me": SUB}


# REQ-MK2-2 -----------------------------------------------------------------

def test_REQ_MK2_2_features_then_own_hooks_in_order(core_env, events):
    # B is published first, so subscription order (A then B) differs from id order.
    b = publish(OWNER, "b_mark", mark("B"))
    a = publish(OWNER, "a_mark", mark("A"))
    subscribe(SUB, a)
    subscribe(SUB, b)
    hooks.save_hook(SUB, "own", mark("own"))
    client = login(SUB)
    events.clear()

    r = client.get("/api/invoices")
    names = [f"@{a}", f"@{b}", "own"]
    assert r.status_code == 200
    assert r.json()[0]["marks"] == ["A", "B", "own"]
    assert r.headers["x-fitit-hooks"] == ",".join(names)

    applied = of_kind(events, "hook.applied")
    assert [e["hook"] for e in applied] == names
    assert all(e["user"] == SUB for e in applied)
    proxied = [e for e in of_kind(events, "request.proxied") if e["path"] == "/api/invoices"]
    assert len(proxied) == 1
    assert proxied[0]["hooks"] == names


# REQ-MK2-3 -----------------------------------------------------------------

def test_REQ_MK2_3_pinned_until_accept_update(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    subscribe(SUB, fid)
    client = login(SUB)
    assert client.get("/api/invoices").json()[0]["tag"] == "v1"

    hooks.save_hook(OWNER, "tagger", tag("v2"))
    assert client.get("/api/invoices").json()[0]["tag"] == "v1"
    marketplace.publish(OWNER, "tagger", "Some feature", "Does something useful")
    assert client.get("/api/invoices").json()[0]["tag"] == "v1"

    assert client.post(f"/fitit/marketplace/features/{fid}/accept-update").status_code == 200
    assert client.get("/api/invoices").json()[0]["tag"] == "v2"

    hooks.save_hook(OWNER, "tagger", tag("v3"))
    assert client.get("/api/invoices").json()[0]["tag"] == "v2"


def test_REQ_MK2_3_toggled_off_subscription_does_not_run(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    subscribe(SUB, fid)
    client = login(SUB)
    assert client.post(f"/fitit/marketplace/features/{fid}/toggle").status_code == 200
    r = client.get("/api/invoices")
    assert r.status_code == 200
    assert "tag" not in r.json()[0]
    assert "x-fitit-hooks" not in r.headers


# REQ-MK2-4 -----------------------------------------------------------------

def test_REQ_MK2_4_failing_feature_gives_502(core_env, events):
    # A route with {param} skips the dry run, so the subscription succeeds and the run fails.
    route = "GET /api/patients/{id}"
    fid = publish(OWNER, "breaks", hook(route, 'return data["no_such_field"]'))
    subscribe(SUB, fid)
    client = login(SUB)
    patient = client.get("/api/patients")
    events.clear()

    r = client.get("/api/patients/1")
    assert r.status_code == 502
    body = r.json()
    assert set(body) == {"error", "hook", "detail"}
    assert body["error"] == "hook_failed"
    assert body["hook"] == f"@{fid}"
    for p in patient.json():
        if p["id"] == 1:
            assert p["name"] not in r.text
            assert p["dni"] not in r.text
    failed = of_kind(events, "hook.failed")
    assert len(failed) == 1
    assert failed[0]["hook"] == f"@{fid}"
    assert failed[0]["route"] == route
    assert failed[0]["user"] == SUB


# REQ-MK2-5 -----------------------------------------------------------------

def test_REQ_MK2_5_pinned_code_failing_guard_gives_502(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    subscribe(SUB, fid)
    with psycopg.connect(core_env) as conn:
        conn.execute("UPDATE feature_versions SET code = %s WHERE feature_id = %s AND version = 1",
                     ("import os\n" + tag("v1"), fid))
    r = login(SUB).get("/api/invoices")
    assert r.status_code == 502
    assert r.json()["error"] == "hook_failed"
    assert r.json()["hook"] == f"@{fid}"


# REQ-MK2-6 -----------------------------------------------------------------

def test_REQ_MK2_6_virtual_endpoint_only_from_feature(core_env):
    fid = publish(OWNER, "virtual", hook("GET /x/only_feature", 'return {"from": "feature"}'))
    subscribe(SUB, fid)
    client = login(SUB)
    r = client.get("/x/only_feature")
    assert r.status_code == 200
    assert r.json() == {"from": "feature"}

    assert client.post(f"/fitit/marketplace/features/{fid}/unsubscribe").status_code == 204
    r = client.get("/x/only_feature")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


# REQ-MK2-7 -----------------------------------------------------------------

def test_REQ_MK2_7_deleting_published_hook_unpublishes(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    subscribe(SUB, fid)
    owner, sub, other = login(OWNER), login(SUB), login(OTHER)

    assert owner.delete("/fitit/hooks/tagger").status_code == 204

    for client in (owner, sub, other):
        r = client.get("/fitit/marketplace/features")
        assert r.status_code == 200
        assert fid not in [f["id"] for f in r.json()]
    assert fid not in [e.get("feature_id") for e in owner.get("/fitit/features").json()]
    assert_error(other.post(f"/fitit/marketplace/features/{fid}/subscribe"), 404, "not_found")
    r = sub.get("/api/invoices")
    assert r.status_code == 200
    assert r.json()[0]["tag"] == "v1"
    assert r.headers["x-fitit-hooks"] == f"@{fid}"


# REQ-MK2-8 -----------------------------------------------------------------

def test_REQ_MK2_8_listing_anonymous_401(core_env):
    r = TestClient(app).get("/fitit/marketplace/features")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthenticated"}


def test_REQ_MK2_8_listing_q_and_flags(core_env):
    friday = publish(OWNER, "friday", PASS, "Friday discount", "15% off invoices issued on a Friday")
    dni = publish(OWNER, "dni", PASS, "Hide DNI", "Masks the patient DNI column")
    debt = publish(OWNER, "debt", PASS, "Debt per doctor", "Unpaid INVOICES grouped by doctor")
    hidden = publish(OWNER, "hidden", PASS, "Hidden invoices", "Unpublished friday invoices")
    marketplace.unpublish(OWNER, hidden)
    subscribe(SUB, dni)
    client = login(SUB)

    r = client.get("/fitit/marketplace/features")
    assert r.status_code == 200
    listing = r.json()
    assert sorted(f["id"] for f in listing) == sorted([friday, dni, debt])
    for entry in listing:
        assert set(entry) == LISTING_KEYS
        assert entry["mine"] is False
        assert entry["subscribed"] is (entry["id"] == dni)
        assert entry["owner"] == OWNER
        assert entry["route"] == "GET /api/invoices"
    assert listing == roundtrip(marketplace.search(SUB))

    r = client.get("/fitit/marketplace/features", params={"q": "invoices"})
    assert sorted(f["id"] for f in r.json()) == sorted([friday, debt])
    r = client.get("/fitit/marketplace/features", params={"q": "FRIDAY invoices"})
    assert [f["id"] for f in r.json()] == [friday]
    r = client.get("/fitit/marketplace/features", params={"q": "friday dni"})
    assert r.json() == []

    owner_listing = login(OWNER).get("/fitit/marketplace/features").json()
    assert all(f["mine"] is True for f in owner_listing)


def test_REQ_MK2_8_listing_sort(core_env):
    zero = publish(OWNER, "zero", PASS)
    two = publish(OWNER, "two", PASS)
    one = publish(OWNER, "one", PASS)
    fake_subscribe("u1", two)
    fake_subscribe("u2", two)
    fake_subscribe("u1", one)
    client = login(SUB)

    by_subs = client.get("/fitit/marketplace/features", params={"sort": "subscribers"}).json()
    assert [f["id"] for f in by_subs] == [two, one, zero]
    assert [f["subscribers"] for f in by_subs] == [2, 1, 0]
    assert [f["id"] for f in client.get("/fitit/marketplace/features").json()] == [two, one, zero]

    hooks.save_hook(OWNER, "zero", hook("GET /api/invoices", "return data\nreturn data"))
    marketplace.publish(OWNER, "zero", "Some feature", "Does something useful")
    newest = client.get("/fitit/marketplace/features", params={"sort": "newest"}).json()
    assert [f["id"] for f in newest] == [zero, one, two]
    assert newest == roundtrip(marketplace.search(SUB, sort="newest"))

    assert_error(client.get("/fitit/marketplace/features", params={"sort": "bogus"}), 422, "invalid")


def test_REQ_MK2_8_detail(core_env):
    fid = publish(OWNER, "tagger", tag("v1"), "Tagger", "Tags the first invoice")
    client = login(SUB)
    r = client.get(f"/fitit/marketplace/features/{fid}")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == DETAIL_KEYS
    assert body["id"] == fid
    assert body["code"] == tag("v1")
    assert body["published"] is True
    assert body["title"] == "Tagger"
    assert body == roundtrip(marketplace.get_feature(SUB, fid))


def test_REQ_MK2_8_detail_not_found(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    marketplace.unpublish(OWNER, fid)
    client = login(SUB)
    for path in (f"/fitit/marketplace/features/{fid + 1000}", "/fitit/marketplace/features/abc",
                 "/fitit/marketplace/features/1.5", f"/fitit/marketplace/features/{fid}"):
        r = client.get(path)
        assert r.status_code == 404, path
        assert r.json() == {"error": "not_found"}, path


# REQ-MK2-9 -----------------------------------------------------------------

def test_REQ_MK2_9_publish_route(core_env):
    hooks.save_hook(OWNER, "tagger", tag("v1"))
    client = login(OWNER)
    body = {"hook": "tagger", "title": "Tagger", "description": "Tags the first invoice"}

    r = client.post("/fitit/marketplace/features", json=body)
    assert r.status_code == 201
    first = r.json()
    assert set(first) == {"id", "version", "created"}
    assert isinstance(first["id"], int)
    assert first["version"] == 1
    assert first["created"] is True

    r = client.post("/fitit/marketplace/features", json=body)
    assert r.status_code == 200
    assert r.json() == {"id": first["id"], "version": 1, "created": False}


def test_REQ_MK2_9_publish_route_errors(core_env):
    hooks.save_hook(OWNER, "tagger", tag("v1"))
    client = login(OWNER)
    url = "/fitit/marketplace/features"
    assert_error(client.post(url, json={"hook": "missing", "title": "T", "description": "D"}), 404, "not_found")
    assert_error(client.post(url, json={"hook": "tagger", "title": "", "description": "D"}), 422, "invalid")
    assert_error(client.post(url, json={"hook": "tagger", "title": "x" * 81, "description": "D"}), 422, "invalid")
    assert_error(client.post(url, json=["tagger", "T", "D"]), 422, "invalid")
    assert_error(client.post(url, json="tagger"), 422, "invalid")
    assert sql(core_env, "SELECT count(*) FROM features") == [(0,)]


def test_REQ_MK2_9_unpublish_route(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    assert_error(login(SUB).delete(f"/fitit/marketplace/features/{fid}"), 403, "forbidden")
    owner = login(OWNER)
    assert_error(owner.delete(f"/fitit/marketplace/features/{fid + 1000}"), 404, "not_found")
    r = owner.delete(f"/fitit/marketplace/features/{fid}")
    assert r.status_code == 204
    assert sql(core_env, "SELECT published FROM features WHERE id = %s", fid) == [(False,)]


def test_REQ_MK2_9_anonymous_401(core_env):
    client = TestClient(app)
    for r in (client.post("/fitit/marketplace/features", json={"hook": "x", "title": "T", "description": "D"}),
              client.delete("/fitit/marketplace/features/1"),
              client.get("/fitit/marketplace/features/1")):
        assert r.status_code == 401
        assert r.json() == {"error": "unauthenticated"}


# REQ-MK2-10 ----------------------------------------------------------------

@pytest.mark.parametrize("action", ["subscribe", "toggle", "unsubscribe", "accept-update", "fork", "report"])
def test_REQ_MK2_10_anonymous_401(core_env, action):
    fid = publish(OWNER, "tagger", tag("v1"))
    r = TestClient(app).post(f"/fitit/marketplace/features/{fid}/{action}", json={"reason": "bad"})
    assert r.status_code == 401
    assert r.json() == {"error": "unauthenticated"}


def test_REQ_MK2_10_subscribe_toggle_unsubscribe_success(core_env):
    fid = publish(OWNER, "tagger", tag("v1"), "Tagger", "Tags the first invoice")
    client = login(SUB)

    r = client.post(f"/fitit/marketplace/features/{fid}/subscribe")
    assert r.status_code == 200
    entry = r.json()
    assert set(entry) == SUB_KEYS
    assert entry["feature_id"] == fid
    assert entry["name"] == f"@{fid}"
    assert entry["version"] == 1
    assert entry["enabled"] is True
    assert sql(core_env, "SELECT user_id, version, enabled FROM subscriptions") == [(SUB, 1, True)]

    r = client.post(f"/fitit/marketplace/features/{fid}/toggle")
    assert r.status_code == 200
    assert r.json() == {"feature_id": fid, "enabled": False}
    r = client.post(f"/fitit/marketplace/features/{fid}/toggle")
    assert r.json() == {"feature_id": fid, "enabled": True}

    r = client.post(f"/fitit/marketplace/features/{fid}/unsubscribe")
    assert r.status_code == 204
    assert sql(core_env, "SELECT count(*) FROM subscriptions") == [(0,)]


def test_REQ_MK2_10_subscribe_errors(core_env):
    good = publish(OWNER, "good", tag("v1"))
    bad = publish(OWNER, "bad", RAISES_ON_DATA)
    gone = publish(OWNER, "gone", tag("v1"))
    marketplace.unpublish(OWNER, gone)
    sub, owner = login(SUB), login(OWNER)

    r = sub.post(f"/fitit/marketplace/features/{bad}/subscribe")
    assert_error(r, 422, "dry_run_failed")
    assert_error(owner.post(f"/fitit/marketplace/features/{good}/subscribe"), 409, "own_feature")
    assert sub.post(f"/fitit/marketplace/features/{good}/subscribe").status_code == 200
    assert_error(sub.post(f"/fitit/marketplace/features/{good}/subscribe"), 409, "already_subscribed")
    assert_error(sub.post(f"/fitit/marketplace/features/{good + 1000}/subscribe"), 404, "not_found")
    assert_error(sub.post(f"/fitit/marketplace/features/{gone}/subscribe"), 404, "not_found")
    assert_error(sub.post("/fitit/marketplace/features/abc/subscribe"), 404, "not_found")
    assert sql(core_env, "SELECT feature_id FROM subscriptions") == [(good,)]


def test_REQ_MK2_10_not_subscribed_errors(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    client = login(SUB)
    for action in ("toggle", "unsubscribe", "accept-update"):
        assert_error(client.post(f"/fitit/marketplace/features/{fid}/{action}"), 404, "not_found")


def test_REQ_MK2_10_accept_update(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    client = login(SUB)
    assert client.post(f"/fitit/marketplace/features/{fid}/subscribe").status_code == 200
    url = f"/fitit/marketplace/features/{fid}/accept-update"

    assert_error(client.post(url), 409, "up_to_date")

    hooks.save_hook(OWNER, "tagger", RAISES_ON_DATA)
    marketplace.publish(OWNER, "tagger", "Some feature", "Does something useful")
    assert_error(client.post(url), 422, "dry_run_failed")
    assert sql(core_env, "SELECT version FROM subscriptions") == [(1,)]

    hooks.save_hook(OWNER, "tagger", tag("v3"))
    marketplace.publish(OWNER, "tagger", "Some feature", "Does something useful")
    r = client.post(url)
    assert r.status_code == 200
    entry = r.json()
    assert set(entry) == SUB_KEYS
    assert entry["version"] == 3
    assert entry["latest_version"] == 3
    assert entry["update_available"] is False

    assert_error(client.post(url), 409, "up_to_date")


def test_REQ_MK2_10_fork_success(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    client = login(SUB)
    assert client.post(f"/fitit/marketplace/features/{fid}/subscribe").status_code == 200

    r = client.post(f"/fitit/marketplace/features/{fid}/fork", json={})
    assert r.status_code == 201
    body = r.json()
    assert set(body) == {"name", "route", "diff"}
    assert body["name"] == "tagger"
    assert body["route"] == "GET /api/invoices"
    assert hooks.read_hook(SUB, "tagger") == tag("v1")
    assert sql(core_env, "SELECT count(*) FROM subscriptions") == [(0,)]

    other = login(OTHER)
    r = other.post(f"/fitit/marketplace/features/{fid}/fork", json={"name": "my_copy"})
    assert r.status_code == 201
    assert r.json()["name"] == "my_copy"


def test_REQ_MK2_10_fork_errors(core_env):
    good = publish(OWNER, "good", tag("v1"))
    bad = publish(OWNER, "bad", RAISES_ON_DATA)
    gone = publish(OWNER, "gone", tag("v1"))
    marketplace.unpublish(OWNER, gone)
    hooks.save_hook(SUB, "taken", PASS)
    sub = login(SUB)

    assert_error(sub.post(f"/fitit/marketplace/features/{good}/fork", json={"name": "taken"}), 409, "name_taken")
    assert_error(sub.post(f"/fitit/marketplace/features/{good}/fork", json={"name": "Bad Name!"}), 422, "invalid")
    assert_error(login(OWNER).post(f"/fitit/marketplace/features/{good}/fork", json={}), 409, "own_feature")
    assert_error(sub.post(f"/fitit/marketplace/features/{bad}/fork", json={}), 422, "dry_run_failed")
    assert_error(sub.post(f"/fitit/marketplace/features/{gone}/fork", json={}), 404, "not_found")
    assert_error(sub.post(f"/fitit/marketplace/features/{good + 1000}/fork", json={}), 404, "not_found")
    assert [name for (name,) in sql(core_env, "SELECT name FROM hooks WHERE user_id = %s", SUB)] == ["taken"]


def test_REQ_MK2_10_report(core_env):
    fid = publish(OWNER, "tagger", tag("v1"))
    client = login(SUB)
    url = f"/fitit/marketplace/features/{fid}/report"

    r = client.post(url, json={"reason": "Shows wrong totals"})
    assert r.status_code == 204
    assert sql(core_env, "SELECT feature_id, user_id, reason FROM reports") == [(fid, SUB, "Shows wrong totals")]

    assert_error(client.post(url, json={"reason": ""}), 422, "invalid")
    assert_error(client.post(url, json={"reason": "x" * 501}), 422, "invalid")
    assert_error(client.post(f"/fitit/marketplace/features/{fid + 1000}/report", json={"reason": "bad"}), 404, "not_found")
    assert sql(core_env, "SELECT count(*) FROM reports") == [(1,)]


# REQ-MK2-11 ----------------------------------------------------------------

def test_REQ_MK2_11_my_features_anonymous_401(core_env):
    r = TestClient(app).get("/fitit/features")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthenticated"}


def test_REQ_MK2_11_my_features(core_env):
    second = publish(OWNER, "second", mark("2"))
    first = publish(OWNER, "first", mark("1"))
    subscribe(SUB, first)
    subscribe(SUB, second)
    marketplace.toggle(SUB, second)
    hooks.save_hook(SUB, "zeta", mark("z"))
    hooks.save_hook(SUB, "alpha", mark("a"))
    hooks.toggle_hook(SUB, "alpha")
    own_fid = marketplace.publish(SUB, "zeta", "Zeta", "Zeta feature")["id"]

    r = login(SUB).get("/fitit/features")
    assert r.status_code == 200
    body = r.json()
    assert body == roundtrip(marketplace.my_features(SUB))
    assert [(e["source"], e["name"]) for e in body] == [
        ("subscribed", f"@{first}"), ("subscribed", f"@{second}"), ("own", "alpha"), ("own", "zeta"),
    ]
    for e in body:
        assert set(e) == (MY_SUB_KEYS if e["source"] == "subscribed" else MY_OWN_KEYS)
    assert [e["enabled"] for e in body] == [True, False, False, True]
    assert (body[2]["feature_id"], body[2]["published"], body[2]["published_version"]) == (None, False, None)
    assert (body[3]["feature_id"], body[3]["published"], body[3]["published_version"]) == (own_fid, True, 1)


# REQ-MK2-12 ----------------------------------------------------------------

def test_REQ_MK2_12_toggle_and_delete_every_entry(core_env):
    fid = publish(OWNER, "feat", mark("F"))
    subscribe(SUB, fid)
    hooks.save_hook(SUB, "mine", mark("M"))
    client = login(SUB)

    def entries():
        return {e["name"]: e for e in client.get("/fitit/features").json()}

    def toggle(e):
        if e["source"] == "own":
            return client.post(f"/fitit/hooks/{e['name']}/toggle")
        return client.post(f"/fitit/marketplace/features/{e['feature_id']}/toggle")

    def delete(e):
        if e["source"] == "own":
            return client.delete(f"/fitit/hooks/{e['name']}")
        return client.post(f"/fitit/marketplace/features/{e['feature_id']}/unsubscribe")

    listed = entries()
    assert set(listed) == {f"@{fid}", "mine"}
    for name, e in listed.items():
        assert toggle(e).status_code == 200
        assert entries()[name]["enabled"] is False
        assert name not in names_header(client.get("/api/invoices"))
        assert toggle(e).status_code == 200
        assert entries()[name]["enabled"] is True
        assert name in names_header(client.get("/api/invoices"))

    for name, e in listed.items():
        assert delete(e).status_code == 204
        assert name not in entries()
    assert entries() == {}


# REQ-MK2-13 ----------------------------------------------------------------

def test_REQ_MK2_13_fitit_hooks_keeps_v2_shape(core_env):
    fid = publish(OWNER, "feat", mark("F"))
    subscribe(SUB, fid)
    hooks.save_hook(SUB, "mine", mark("M"))
    marketplace.publish(SUB, "mine", "Mine", "My feature")
    r = login(SUB).get("/fitit/hooks")
    assert r.status_code == 200
    assert r.json() == [{"name": "mine", "route": "GET /api/invoices", "enabled": True, "code": mark("M")}]
