import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import hooks
import main
import marketplace
import trace

BASE = "/fitit/marketplace/features"


def hook(route="GET /api/invoices", body="return data"):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n    {body}\n'


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
def env(database, monkeypatch):
    monkeypatch.setenv("BASE_URL", "http://demo:8001")
    monkeypatch.setattr(trace, "emit", lambda kind, **f: None)


def client(user=None):
    c = TestClient(main.app, follow_redirects=False)
    if user:
        assert c.get("/login", params={"user": user}).status_code == 303
    return c


def seed(owner="gerente", name="tag", code=None):
    hooks.save_hook(owner, name, code or hook(body="return {'tagged': data}"))
    return marketplace.publish(owner, name, "Tag it", "wraps invoices")["id"]


@pytest.mark.parametrize("method,path", [
    ("GET", "/fitit/features"), ("GET", BASE), ("POST", BASE), ("GET", f"{BASE}/1"), ("DELETE", f"{BASE}/1"),
    *[("POST", f"{BASE}/1/{a}") for a in ("subscribe", "toggle", "unsubscribe", "accept-update", "fork", "report")],
])
def test_anonymous_is_401(method, path):
    r = client().request(method, path)
    assert r.status_code == 401 and r.json() == {"error": "unauthenticated"}


def test_publish_listing_detail_unpublish():
    hooks.save_hook("gerente", "tag", hook())
    g, r = client("gerente"), client("recepcion")
    body = {"hook": "tag", "title": "Tag it", "description": "wraps invoices"}
    first = g.post(BASE, json=body)
    assert first.status_code == 201 and first.json()["created"] is True
    fid = first.json()["id"]
    again = g.post(BASE, json=body)
    assert again.status_code == 200 and again.json() == {"id": fid, "version": 1, "created": False}
    assert g.post(BASE, json=body | {"hook": "nope"}).json() == {"error": "not_found"}
    for bad in ({**body, "title": " "}, {**body, "title": 5}, {"hook": "tag"}, [1], "x"):
        res = g.post(BASE, json=bad)
        assert res.status_code == 422 and res.json()["error"] == "invalid" and res.json()["detail"]
    res = g.post(BASE, content=b"{not json", headers={"content-type": "application/json"})
    assert res.status_code == 422 and res.json()["error"] == "invalid"

    listing = r.get(BASE, params={"q": "TAG", "sort": "newest"}).json()
    assert [f["id"] for f in listing] == [fid] and listing[0]["mine"] is False
    assert r.get(BASE, params={"q": "missing"}).json() == []
    res = r.get(BASE, params={"sort": "bogus"})
    assert res.status_code == 422 and res.json()["error"] == "invalid"
    detail = r.get(f"{BASE}/{fid}").json()
    assert detail["code"] == hook() and detail["published"] is True
    for bad in ("abc", "0", "-1", "99999", "1.5", str(2**70)):
        res = r.get(f"{BASE}/{bad}")
        assert res.status_code == 404 and res.json() == {"error": "not_found"}

    res = r.delete(f"{BASE}/{fid}")
    assert res.status_code == 403 and res.json()["error"] == "forbidden" and res.json()["detail"]
    assert g.delete(f"{BASE}/{fid}").status_code == 204
    assert r.get(f"{BASE}/{fid}").json() == {"error": "not_found"}
    assert g.get(f"{BASE}/{fid}").json()["published"] is False
    assert g.delete(f"{BASE}/{fid + 1}").status_code == 404


def test_subscription_routes():
    fid = seed()
    g, r = client("gerente"), client("recepcion")
    res = r.post(f"{BASE}/{fid}/subscribe")
    assert res.status_code == 200 and res.json()["name"] == f"@{fid}" and res.json()["version"] == 1
    assert r.post(f"{BASE}/{fid}/subscribe").json()["error"] == "already_subscribed"
    res = g.post(f"{BASE}/{fid}/subscribe")
    assert res.status_code == 409 and res.json()["error"] == "own_feature"
    assert r.get("/api/invoices").json().keys() == {"tagged"}
    assert r.post(f"{BASE}/{fid}/toggle").json() == {"feature_id": fid, "enabled": False}
    assert "x-fitit-hooks" not in r.get("/api/invoices").headers

    res = r.post(f"{BASE}/{fid}/accept-update")
    assert res.status_code == 409 and res.json()["error"] == "up_to_date"
    hooks.save_hook("gerente", "tag", hook(body="return 1 / 0"))
    marketplace.publish("gerente", "tag", "Tag it", "v2")
    res = r.post(f"{BASE}/{fid}/accept-update")
    assert res.status_code == 422 and res.json()["error"] == "dry_run_failed" and "division" in res.json()["detail"]
    hooks.save_hook("gerente", "tag", hook(body="return 3"))
    marketplace.publish("gerente", "tag", "Tag it", "v3")
    res = r.post(f"{BASE}/{fid}/accept-update")
    assert res.status_code == 200 and res.json()["version"] == 3 and res.json()["enabled"] is False

    assert r.post(f"{BASE}/{fid}/report", json={"reason": "spam"}).status_code == 204
    assert r.post(f"{BASE}/{fid}/report", json={"reason": ""}).json()["error"] == "invalid"
    assert r.post(f"{BASE}/{fid}/report").json()["error"] == "invalid"
    assert r.post(f"{BASE}/{fid + 1}/report", json={"reason": "x"}).json() == {"error": "not_found"}

    assert r.post(f"{BASE}/{fid}/unsubscribe").status_code == 204
    for action in ("toggle", "unsubscribe", "accept-update"):
        res = r.post(f"{BASE}/{fid}/{action}")
        assert res.status_code == 404 and res.json() == {"error": "not_found"}


def test_dry_run_failure_refuses_subscribe():
    fid = seed(code=hook(body="return 1 / 0"))
    res = client("recepcion").post(f"{BASE}/{fid}/subscribe")
    assert res.status_code == 422 and res.json()["error"] == "dry_run_failed"
    assert marketplace.subscriptions("recepcion") == []


def test_fork_route():
    fid = seed()
    r = client("recepcion")
    r.post(f"{BASE}/{fid}/subscribe")
    res = r.post(f"{BASE}/{fid}/fork", json={"name": 1})
    assert res.status_code == 422 and res.json()["error"] == "invalid"
    res = r.post(f"{BASE}/{fid}/fork")
    assert res.status_code == 201 and res.json()["name"] == "tag" and res.json()["route"] == "GET /api/invoices"
    assert marketplace.subscriptions("recepcion") == []
    res = r.post(f"{BASE}/{fid}/fork", json={})
    assert res.status_code == 409 and res.json()["error"] == "name_taken"
    assert r.post(f"{BASE}/{fid}/fork", json={"name": "copy"}).status_code == 201
    assert client("gerente").post(f"{BASE}/{fid}/fork", json={}).json()["error"] == "own_feature"


def test_my_features_toggle_and_delete_every_entry():
    fid = seed()
    hooks.save_hook("recepcion", "mine", hook(body="return data"))
    r = client("recepcion")
    r.post(f"{BASE}/{fid}/subscribe")
    listed = r.get("/fitit/features").json()
    assert listed == marketplace.my_features("recepcion")
    assert [(e["source"], e["name"]) for e in listed] == [("subscribed", f"@{fid}"), ("own", "mine")]
    assert r.get("/api/invoices").headers["x-fitit-hooks"] == f"@{fid},mine"

    r.post(f"{BASE}/{fid}/toggle")
    r.post("/fitit/hooks/mine/toggle")
    assert [e["enabled"] for e in r.get("/fitit/features").json()] == [False, False]
    assert "x-fitit-hooks" not in r.get("/api/invoices").headers

    assert r.post(f"{BASE}/{fid}/unsubscribe").status_code == 204
    assert r.delete("/fitit/hooks/mine").status_code == 204
    assert r.get("/fitit/features").json() == []


def test_deleting_published_hook_keeps_subscribers():
    fid = seed()
    r = client("recepcion")
    r.post(f"{BASE}/{fid}/subscribe")
    g = client("gerente")
    assert g.delete("/fitit/hooks/tag").status_code == 204
    assert g.get(BASE).json() == [] and g.get("/fitit/features").json() == []
    assert client("dr_garcia").post(f"{BASE}/{fid}/subscribe").json() == {"error": "not_found"}
    assert r.get("/api/invoices").json().keys() == {"tagged"}
