"""Acceptance tests for PX1 (proxy), written from docs/specs/proxy.md,
hook-contract.md, trace-events.md and demo-app.md only."""

import os

import httpx
import pytest
from fastapi.testclient import TestClient

import trace
from main import app

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
SCRIPT = '<script src="/fitit/embed.js" defer></script>'

ADD_FLAG = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        invoice["flag"] = "fitit"
    ctx.log("flagged", len(data))
    return data
'''

ADD_SECOND = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        invoice["second"] = invoice["flag"] + "-2"
    return data
'''

RAISES = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    return data[0]["no_such_field"]
'''

PATIENT_ONE = '''ROUTE = "GET /api/patients/{id}"

def run(data, ctx):
    data["seen_by_hook"] = True
    return data
'''

VIRTUAL = '''ROUTE = "GET /x/report"

def run(data, ctx):
    me = ctx.api.get("/api/me")
    return {"data_was_none": data is None, "me": me["id"], "total": 3}
'''


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("WORKSPACES", str(tmp_path))
    return tmp_path


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **fields: recorded.append({"kind": kind, **fields}))
    return recorded


def write_hook(ws, user, name, code, enabled=True):
    folder = ws / user
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (f"{name}.py" if enabled else f"{name}.py.off")
    path.write_text(code, encoding="utf-8")
    return path


def core(user=None):
    client = TestClient(app)
    if user:
        r = client.get(f"/login?user={user}")
        assert r.status_code == 200
        assert client.cookies.get("session") == user
    return client


def demo(path, user=None):
    cookies = {"session": user} if user else {}
    return httpx.get(DEMO_URL + path, cookies=cookies, follow_redirects=False)


# REQ-PX1-1 -----------------------------------------------------------------

@pytest.mark.parametrize("path", ["/api/me", "/api/invoices", "/api/patients", "/api/appointments", "/api/patients/1"])
def test_req_px1_1_signed_in_same_status_and_body(ws, path):
    r = core("gerente").get(path)
    expected = demo(path, "gerente")
    assert r.status_code == expected.status_code == 200
    assert r.json() == expected.json()


@pytest.mark.parametrize("path", ["/api/me", "/api/invoices", "/api/patients"])
def test_req_px1_1_anonymous_same_status_and_body(ws, path):
    r = core().get(path)
    expected = demo(path)
    assert r.status_code == expected.status_code == 401
    assert r.json() == expected.json()


def test_req_px1_1_doctor_sees_same_filtered_body(ws):
    r = core("dr_garcia").get("/api/patients")
    expected = demo("/api/patients", "dr_garcia")
    assert r.status_code == 200
    assert r.json() == expected.json()
    assert {p["doctor_id"] for p in r.json()} == {1}


def test_req_px1_1_not_found_is_forwarded(ws):
    r = core("gerente").get("/api/patients/999999")
    expected = demo("/api/patients/999999", "gerente")
    assert r.status_code == expected.status_code == 404
    assert r.json() == expected.json()


# REQ-PX1-2 -----------------------------------------------------------------

def test_req_px1_2_login_returns_app_set_cookie(ws):
    client = TestClient(app)
    r = client.get("/login?user=gerente", follow_redirects=False)
    expected = demo("/login?user=gerente")
    assert r.status_code == expected.status_code
    assert r.headers.get("location") == expected.headers.get("location")
    assert "session=gerente" in r.headers.get("set-cookie", "")
    assert r.headers.get_list("set-cookie") == expected.headers.get_list("set-cookie")


def test_req_px1_2_cookie_authenticates_later_requests(ws):
    client = TestClient(app)
    client.get("/login?user=gerente", follow_redirects=False)
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json()["id"] == "gerente"
    assert r.json() == demo("/api/me", "gerente").json()


# REQ-PX1-3 -----------------------------------------------------------------

def test_req_px1_3_hook_changes_body_and_sets_header(ws):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    r = core("gerente").get("/api/invoices")
    original = demo("/api/invoices", "gerente").json()
    assert r.status_code == 200
    assert r.headers["x-fitit-hooks"] == "add_flag"
    assert r.json() == [{**inv, "flag": "fitit"} for inv in original]


def test_req_px1_3_hooks_chain_in_name_order(ws):
    write_hook(ws, "gerente", "b_second", ADD_SECOND)
    write_hook(ws, "gerente", "a_flag", ADD_FLAG)
    r = core("gerente").get("/api/invoices")
    original = demo("/api/invoices", "gerente").json()
    assert r.status_code == 200
    assert r.headers["x-fitit-hooks"] == "a_flag,b_second"
    assert r.json() == [{**inv, "flag": "fitit", "second": "fitit-2"} for inv in original]


def test_req_px1_3_param_route_matches_one_segment(ws):
    write_hook(ws, "gerente", "patient_one", PATIENT_ONE)
    client = core("gerente")
    r = client.get("/api/patients/1")
    assert r.status_code == 200
    assert r.headers["x-fitit-hooks"] == "patient_one"
    assert r.json() == {**demo("/api/patients/1", "gerente").json(), "seen_by_hook": True}
    r = client.get("/api/patients")
    assert "x-fitit-hooks" not in r.headers
    assert r.json() == demo("/api/patients", "gerente").json()


def test_req_px1_3_disabled_hook_not_applied(ws):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG, enabled=False)
    r = core("gerente").get("/api/invoices")
    assert "x-fitit-hooks" not in r.headers
    assert r.json() == demo("/api/invoices", "gerente").json()


def test_req_px1_3_non_matching_route_unchanged(ws):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    r = core("gerente").get("/api/patients")
    assert "x-fitit-hooks" not in r.headers
    assert r.json() == demo("/api/patients", "gerente").json()


# REQ-PX1-4 -----------------------------------------------------------------

def test_req_px1_4_other_user_gets_original(ws):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    r = core("recepcion").get("/api/invoices")
    assert r.status_code == 200
    assert "x-fitit-hooks" not in r.headers
    assert r.json() == demo("/api/invoices", "recepcion").json()


def test_req_px1_4_anonymous_gets_original(ws):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    r = core().get("/api/invoices")
    expected = demo("/api/invoices")
    assert r.status_code == expected.status_code == 401
    assert "x-fitit-hooks" not in r.headers
    assert r.json() == expected.json()


# REQ-PX1-5 -----------------------------------------------------------------

def test_req_px1_5_raising_hook_fails_closed(ws):
    write_hook(ws, "gerente", "boom", RAISES)
    r = core("gerente").get("/api/invoices")
    assert r.status_code == 502
    body = r.json()
    assert set(body) == {"error", "hook", "detail"}
    assert body["error"] == "hook_failed"
    assert body["hook"] == "boom"
    assert isinstance(body["detail"], str)
    for invoice in demo("/api/invoices", "gerente").json():
        assert invoice["patient"] not in r.text


def test_req_px1_5_failure_after_successful_hook_still_fails_closed(ws):
    write_hook(ws, "gerente", "a_flag", ADD_FLAG)
    write_hook(ws, "gerente", "boom", RAISES)
    r = core("gerente").get("/api/invoices")
    assert r.status_code == 502
    assert r.json()["error"] == "hook_failed"
    assert r.json()["hook"] == "boom"
    assert "fitit" not in r.text
    for invoice in demo("/api/invoices", "gerente").json():
        assert invoice["patient"] not in r.text


# REQ-PX1-6 -----------------------------------------------------------------

def test_req_px1_6_script_injected_once_before_body_end(ws):
    r = core("gerente").get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert r.text.count(SCRIPT) == 1
    assert r.text.rfind(SCRIPT + "</body>") == r.text.rfind("</body>") - len(SCRIPT)


def test_req_px1_6_no_script_anonymously(ws):
    r = core().get("/")
    assert r.status_code == 200
    assert SCRIPT not in r.text
    assert "/fitit/embed.js" not in r.text


# REQ-PX1-7 -----------------------------------------------------------------

def test_req_px1_7_anonymous_401(ws):
    write_hook(ws, "gerente", "report", VIRTUAL)
    r = core().get("/x/report")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthenticated"}


def test_req_px1_7_no_matching_hook_404(ws):
    r = core("gerente").get("/x/report")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


def test_req_px1_7_disabled_or_other_users_hook_404(ws):
    write_hook(ws, "gerente", "report", VIRTUAL, enabled=False)
    write_hook(ws, "dr_garcia", "report", VIRTUAL)
    r = core("gerente").get("/x/report")
    assert r.status_code == 404
    assert r.json() == {"error": "not_found"}


def test_req_px1_7_hook_result_200(ws):
    write_hook(ws, "gerente", "report", VIRTUAL)
    r = core("gerente").get("/x/report")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    assert r.headers["x-fitit-hooks"] == "report"
    assert r.json() == {"data_was_none": True, "me": "gerente", "total": 3}


# REQ-PX1-8 -----------------------------------------------------------------

@pytest.mark.parametrize("method,path", [
    ("GET", "/fitit/hooks"),
    ("POST", "/fitit/hooks/add_flag/toggle"),
    ("DELETE", "/fitit/hooks/add_flag"),
])
def test_req_px1_8_anonymous_401(ws, method, path):
    hook = write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    r = core().request(method, path)
    assert r.status_code == 401
    assert r.json() == {"error": "unauthenticated"}
    assert hook.exists()


def test_req_px1_8_list_hooks(ws):
    write_hook(ws, "gerente", "b_off", VIRTUAL, enabled=False)
    write_hook(ws, "gerente", "a_flag", ADD_FLAG)
    write_hook(ws, "recepcion", "other", RAISES)
    r = core("gerente").get("/fitit/hooks")
    assert r.status_code == 200
    assert r.json() == [
        {"name": "a_flag", "route": "GET /api/invoices", "enabled": True, "code": ADD_FLAG},
        {"name": "b_off", "route": "GET /x/report", "enabled": False, "code": VIRTUAL},
    ]


def test_req_px1_8_list_empty_without_folder(ws):
    r = core("gerente").get("/fitit/hooks")
    assert r.status_code == 200
    assert r.json() == []


def test_req_px1_8_toggle_off_stops_applying_and_on_again(ws):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    client = core("gerente")
    original = demo("/api/invoices", "gerente").json()

    r = client.post("/fitit/hooks/add_flag/toggle")
    assert r.status_code == 200
    assert r.json() == {"name": "add_flag", "enabled": False}
    assert (ws / "gerente" / "add_flag.py.off").exists()
    assert not (ws / "gerente" / "add_flag.py").exists()
    r = client.get("/api/invoices")
    assert "x-fitit-hooks" not in r.headers
    assert r.json() == original

    r = client.post("/fitit/hooks/add_flag/toggle")
    assert r.status_code == 200
    assert r.json() == {"name": "add_flag", "enabled": True}
    r = client.get("/api/invoices")
    assert r.headers["x-fitit-hooks"] == "add_flag"
    assert r.json() == [{**inv, "flag": "fitit"} for inv in original]


def test_req_px1_8_toggle_missing_404(ws):
    write_hook(ws, "recepcion", "add_flag", ADD_FLAG)
    r = core("gerente").post("/fitit/hooks/add_flag/toggle")
    assert r.status_code == 404
    assert (ws / "recepcion" / "add_flag.py").exists()


def test_req_px1_8_delete(ws):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    write_hook(ws, "gerente", "b_off", VIRTUAL, enabled=False)
    client = core("gerente")

    r = client.delete("/fitit/hooks/add_flag")
    assert r.status_code == 204
    assert not (ws / "gerente" / "add_flag.py").exists()
    assert [h["name"] for h in client.get("/fitit/hooks").json()] == ["b_off"]
    r = client.get("/api/invoices")
    assert "x-fitit-hooks" not in r.headers

    r = client.delete("/fitit/hooks/b_off")
    assert r.status_code == 204
    assert not (ws / "gerente" / "b_off.py.off").exists()
    assert client.get("/fitit/hooks").json() == []


def test_req_px1_8_delete_missing_404(ws):
    write_hook(ws, "recepcion", "add_flag", ADD_FLAG)
    client = core("gerente")
    assert client.delete("/fitit/hooks/add_flag").status_code == 404
    assert (ws / "recepcion" / "add_flag.py").exists()


# REQ-PX1-9 -----------------------------------------------------------------

def test_req_px1_9_embed_js(ws):
    r = core().get("/fitit/embed.js")
    assert r.status_code == 200
    assert r.headers["content-type"].split(";")[0].strip() == "application/javascript"
    assert r.text.strip() != ""


# REQ-PX1-10 ----------------------------------------------------------------

def of_kind(events, kind):
    return [e for e in events if e["kind"] == kind]


def test_req_px1_10_applied_hook_events(ws, events):
    write_hook(ws, "gerente", "add_flag", ADD_FLAG)
    client = core("gerente")
    count = len(demo("/api/invoices", "gerente").json())
    events.clear()

    client.get("/api/invoices")

    proxied = of_kind(events, "request.proxied")
    assert len(proxied) == 1
    e = proxied[0]
    assert set(e) == {"kind", "user", "method", "path", "status", "ms", "hooks"}
    assert (e["user"], e["method"], e["path"], e["status"], e["hooks"]) == ("gerente", "GET", "/api/invoices", 200, ["add_flag"])
    assert isinstance(e["ms"], (int, float)) and e["ms"] >= 0

    applied = of_kind(events, "hook.applied")
    assert len(applied) == 1
    a = applied[0]
    assert set(a) == {"kind", "user", "hook", "route", "ms", "logs"}
    assert (a["user"], a["hook"], a["route"], a["logs"]) == ("gerente", "add_flag", "GET /api/invoices", [f"flagged {count}"])
    assert isinstance(a["ms"], (int, float)) and a["ms"] >= 0
    assert of_kind(events, "hook.failed") == []


def test_req_px1_10_two_hooks_two_applied_events(ws, events):
    write_hook(ws, "gerente", "a_flag", ADD_FLAG)
    write_hook(ws, "gerente", "b_second", ADD_SECOND)
    client = core("gerente")
    events.clear()

    client.get("/api/invoices")

    assert [e["hook"] for e in of_kind(events, "hook.applied")] == ["a_flag", "b_second"]
    assert [e["hooks"] for e in of_kind(events, "request.proxied")] == [["a_flag", "b_second"]]


def test_req_px1_10_failed_hook_event(ws, events):
    write_hook(ws, "gerente", "boom", RAISES)
    client = core("gerente")
    events.clear()

    client.get("/api/invoices")

    failed = of_kind(events, "hook.failed")
    assert len(failed) == 1
    f = failed[0]
    assert set(f) == {"kind", "user", "hook", "route", "error"}
    assert (f["user"], f["hook"], f["route"]) == ("gerente", "boom", "GET /api/invoices")
    assert isinstance(f["error"], str) and f["error"] != ""
    assert of_kind(events, "hook.applied") == []
    proxied = of_kind(events, "request.proxied")
    assert len(proxied) == 1
    assert (proxied[0]["user"], proxied[0]["path"], proxied[0]["status"]) == ("gerente", "/api/invoices", 502)
    assert isinstance(proxied[0]["hooks"], list)


def test_req_px1_10_anonymous_request_event(ws, events):
    client = core()
    events.clear()

    client.get("/api/invoices")

    proxied = of_kind(events, "request.proxied")
    assert len(proxied) == 1
    e = proxied[0]
    assert (e["user"], e["method"], e["path"], e["status"], e["hooks"]) == (None, "GET", "/api/invoices", 401, [])
    assert of_kind(events, "hook.applied") == []
    assert of_kind(events, "hook.failed") == []


def test_req_px1_10_virtual_request_event(ws, events):
    write_hook(ws, "gerente", "report", VIRTUAL)
    client = core("gerente")
    events.clear()

    client.get("/x/report")

    proxied = of_kind(events, "request.proxied")
    assert len(proxied) == 1
    e = proxied[0]
    assert (e["user"], e["method"], e["path"], e["status"], e["hooks"]) == ("gerente", "GET", "/x/report", 200, ["report"])
    applied = of_kind(events, "hook.applied")
    assert [(a["hook"], a["route"], a["logs"]) for a in applied] == [("report", "GET /x/report", [])]
