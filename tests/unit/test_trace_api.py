import pytest
from fastapi.testclient import TestClient

import main
import trace


@pytest.fixture
def journal(tmp_path, monkeypatch):
    monkeypatch.setenv("FITIT_TRACE_DIR", str(tmp_path))
    monkeypatch.delenv("FITIT_TRACE_ADMINS", raising=False)
    trace.startup()
    for user in ("ana", "bob", "ana", "bob"):
        trace.emit("request.proxied", user=user, path="/api/x")
    assert trace.flush(timeout_ms=3000)
    return tmp_path


def get(monkeypatch, user, **params):
    monkeypatch.setattr(main, "resolve_user", lambda cookie: user)
    return TestClient(main.app).get("/fitit/trace/events", params=params)


def test_anonymous_is_401(journal, monkeypatch):
    r = get(monkeypatch, None)
    assert r.status_code == 401 and r.json() == {"error": "unauthenticated"}


def test_user_sees_only_own_records_and_next_skips_filtered(journal, monkeypatch):
    body = get(monkeypatch, "ana").json()
    assert body["admin"] is False
    assert [e["user"] for e in body["events"]] == ["ana", "ana"]
    seqs = [e["seq"] for e in trace.read_events(after=0, limit=1000)]
    assert body["next"] == seqs[-1]
    page = get(monkeypatch, "ana", after=seqs[0], limit=1).json()
    assert page["events"] == [] and page["next"] == seqs[1]
    assert get(monkeypatch, "ana", after=seqs[-1]).json() == {"events": [], "next": seqs[-1], "admin": False}


def test_admin_sees_all(journal, monkeypatch):
    monkeypatch.setenv("FITIT_TRACE_ADMINS", "root,bob")
    body = get(monkeypatch, "bob").json()
    assert body["admin"] is True and [e["user"] for e in body["events"]] == ["ana", "bob", "ana", "bob"]


@pytest.mark.parametrize("params", [{"after": "x"}, {"limit": "0"}, {"limit": "1001"}, {"after": "-1"}])
def test_bad_params_are_422(journal, monkeypatch, params):
    r = get(monkeypatch, "ana", **params)
    assert r.status_code == 422 and r.json()["error"] == "invalid"


def test_traceability_page(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "UI", tmp_path)
    c = TestClient(main.app)
    r = c.get("/traceability")
    assert r.status_code == 404 and r.json() == {"error": "not_found"}
    (tmp_path / "traceability.html").write_text("<p>trace</p>")
    for path in ("/traceability", "/traceability/"):
        r = c.get(path)
        assert r.status_code == 200 and r.text == "<p>trace</p>" and r.headers["content-type"].startswith("text/html")
