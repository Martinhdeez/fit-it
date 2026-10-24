"""End-to-end rehearsal of docs/product/demo-script.md, in process, with a scripted model.

Written from the demo script, docs/plan/01-mvp/ and docs/specs/ only. One test per beat.
Iteration 02 (HS2): fixtures moved from hook files to the database (hook-contract v2, storage v1).
"""

import os
import uuid
from datetime import date

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import hooks
import trace
from agent import agent
from main import app

# Until DB1 lands, psycopg and db are missing; keep the file collectable so tests fail, not error.
try:
    import psycopg

    import db
except ImportError:
    psycopg = db = None

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
WIDGET = '<script src="/fitit/embed.js" defer></script>'

FRIDAY_DISCOUNT = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        if datetime.fromisoformat(invoice["date"]).weekday() == 4:
            invoice["amount"] = round(invoice["amount"] * 0.85, 2)
    ctx.log("discounted Friday invoices")
    return data
'''

MASK_DEBTOR_PHONES = '''ROUTE = "GET /api/patients"

def run(data, ctx):
    debtors = set(i["patient_id"] for i in ctx.api.get("/api/invoices") if not i["paid"])
    for patient in data:
        if patient["id"] in debtors:
            patient["phone"] = "***"
    return data
'''

DEBT_PER_DOCTOR = '''ROUTE = "GET /x/debt-per-doctor"

def run(data, ctx):
    doctor_of = dict((p["id"], p["doctor"]) for p in ctx.api.get("/api/patients"))
    totals = {}
    for invoice in ctx.api.get("/api/invoices"):
        if not invoice["paid"]:
            doctor = doctor_of[invoice["patient_id"]]
            totals[doctor] = round(totals.get(doctor, 0) + invoice["amount"], 2)
    return totals
'''

NEEDS_OS = '''import os

ROUTE = "GET /api/invoices"

def run(data, ctx):
    ctx.log(os.getcwd())
    return data
'''

BROKEN = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    return data[0]["no_such_field"]
'''

ANSWER = "Done."


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
def workspaces(database, monkeypatch):
    """The test's own schema URL (iteration 01 used a temporary hook-file folder)."""
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    return database


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **fields: recorded.append({"kind": kind, **fields}))
    return recorded


def login(user):
    client = TestClient(app)
    assert client.get(f"/login?user={user}").status_code == 200
    return client


def demo(path, user):
    return httpx.get(DEMO_URL + path, cookies={"session": user}).json()


def write_file(workspaces, user, name, code):
    hooks.save_hook(user, name, code)


def sql(url, query, *args):
    with psycopg.connect(url) as conn:
        return conn.execute(query, args).fetchall()


def demo_model(steps):
    """A scripted model: plays `steps` (one tool call per model request), then answers ANSWER."""
    calls = []

    def model(messages, info):
        calls.append(messages)
        i = len(calls) - 1
        if i < len(steps):
            name, args = steps[i]
            return ModelResponse(parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=f"call_{i}")])
        return ModelResponse(parts=[TextPart(ANSWER)])

    return FunctionModel(model)


def agent_writes(name, code, sample="/api/invoices"):
    return [("list_endpoints", {}), ("get_endpoint", {"path": sample}), ("write_hook", {"name": name, "code": code})]


def chat(client, text, steps):
    events = []
    with agent.override(model=demo_model(steps)):
        with client.websocket_connect("/fitit/ws") as ws:
            ws.send_json({"type": "message", "text": text})
            while not events or events[-1]["type"] != "done":
                events.append(ws.receive_json())
    return events


def types(events):
    return [e["type"] for e in events]


def is_friday(invoice):
    return date.fromisoformat(invoice["date"]).weekday() == 4


# Beat 1: the same app twice, identical, with the widget injected.

def test_beat_1_both_users_see_the_unchanged_app_with_the_widget(workspaces):
    for user in ("recepcion", "gerente"):
        client = login(user)
        page = client.get("/")
        assert page.status_code == 200
        assert page.text.count(WIDGET) == 1
        assert page.text.index(WIDGET) < page.text.rindex("</body>")
        r = client.get("/api/invoices")
        assert r.json() == demo("/api/invoices", user)
        assert "x-fitit-hooks" not in r.headers
    assert WIDGET not in TestClient(app).get("/").text
    assert TestClient(app).get("/fitit/embed.js").headers["content-type"].startswith("application/javascript")


# Beat 2: gerente asks for the Friday discount; agent steps stream in.

def test_beat_2_chat_streams_tools_hook_saved_text_done(workspaces):
    gerente = login("gerente")
    events = chat(gerente, "Apply a 15% discount to invoices issued on a Friday",
                  agent_writes("friday_discount", FRIDAY_DISCOUNT))

    assert types(events) == ["tool", "tool", "tool", "hook_saved", "text", "done"]
    assert [e["name"] for e in events[:3]] == ["list_endpoints", "get_endpoint", "write_hook"]
    saved = events[3]
    assert saved["name"] == "friday_discount"
    assert saved["route"] == "GET /api/invoices"
    assert '+ROUTE = "GET /api/invoices"' in saved["diff"].splitlines()
    assert events[4]["text"] == ANSWER
    assert sql(workspaces, "SELECT name FROM hooks WHERE user_id = 'gerente'") == [("friday_discount",)]


# Beat 3: after reload, Friday invoices are discounted for gerente only.

def test_beat_3_reload_shows_discount_for_gerente_only(workspaces):
    gerente, recepcion = login("gerente"), login("recepcion")
    chat(gerente, "Apply a 15% discount to invoices issued on a Friday",
         agent_writes("friday_discount", FRIDAY_DISCOUNT))

    original = demo("/api/invoices", "gerente")
    r = gerente.get("/api/invoices")
    assert r.status_code == 200
    assert r.headers["x-fitit-hooks"] == "friday_discount"
    expected = [dict(i, amount=round(i["amount"] * 0.85, 2)) if is_friday(i) else i for i in original]
    assert r.json() == expected
    assert sum(is_friday(i) for i in original) >= 2

    other = recepcion.get("/api/invoices")
    assert other.json() == demo("/api/invoices", "recepcion")
    assert "x-fitit-hooks" not in other.headers


# Beat 4: recepcion hides phones of patients who owe money, only for herself.

def test_beat_4_recepcion_masks_debtor_phones_only_for_herself(workspaces):
    recepcion, gerente = login("recepcion"), login("gerente")
    events = chat(recepcion, "Hide the phone number of patients who owe money",
                  agent_writes("mask_debtor_phones", MASK_DEBTOR_PHONES, sample="/api/patients"))
    assert types(events)[-3:] == ["hook_saved", "text", "done"]

    debtors = {i["patient_id"] for i in demo("/api/invoices", "recepcion") if not i["paid"]}
    assert debtors
    r = recepcion.get("/api/patients")
    assert r.headers["x-fitit-hooks"] == "mask_debtor_phones"
    for patient in r.json():
        assert (patient["phone"] == "***") == (patient["id"] in debtors)

    assert gerente.get("/api/patients").json() == demo("/api/patients", "gerente")


# Beat 5: a virtual endpoint built from existing API calls; the app gets no new route.

def test_beat_5_virtual_endpoint_debt_per_doctor(workspaces):
    gerente = login("gerente")
    events = chat(gerente, "Give me a page with total unpaid debt per doctor",
                  agent_writes("debt_per_doctor", DEBT_PER_DOCTOR))
    assert [e for e in events if e["type"] == "hook_saved"][0]["route"] == "GET /x/debt-per-doctor"

    doctor_of = {p["id"]: p["doctor"] for p in demo("/api/patients", "gerente")}
    expected = {}
    for i in demo("/api/invoices", "gerente"):
        if not i["paid"]:
            d = doctor_of[i["patient_id"]]
            expected[d] = round(expected.get(d, 0) + i["amount"], 2)

    r = gerente.get("/x/debt-per-doctor")
    assert r.status_code == 200
    assert r.headers["x-fitit-hooks"] == "debt_per_doctor"
    assert r.json() == expected
    assert login("recepcion").get("/x/debt-per-doctor").status_code == 404
    assert TestClient(app).get("/x/debt-per-doctor").status_code == 401


# Beat 6: toggle off restores the original instantly; toggle on brings it back.

def test_beat_6_toggle_off_restores_original_and_on_returns(workspaces):
    gerente = login("gerente")
    write_file(workspaces, "gerente", "friday_discount", FRIDAY_DISCOUNT)
    original = demo("/api/invoices", "gerente")
    assert gerente.get("/api/invoices").json() != original

    assert gerente.get("/fitit/hooks").json()[0]["enabled"] is True
    assert gerente.post("/fitit/hooks/friday_discount/toggle").json() == {"name": "friday_discount", "enabled": False}
    r = gerente.get("/api/invoices")
    assert r.json() == original
    assert "x-fitit-hooks" not in r.headers

    assert gerente.post("/fitit/hooks/friday_discount/toggle").json()["enabled"] is True
    r = gerente.get("/api/invoices")
    assert r.headers["x-fitit-hooks"] == "friday_discount"
    assert r.json() != original


# Beat 7: the trace records prompt, tools, saved hook and execution with timings.

def test_beat_7_trace_records_every_step(workspaces, events):
    gerente = login("gerente")
    chat(gerente, "Apply a 15% discount to invoices issued on a Friday",
         agent_writes("friday_discount", FRIDAY_DISCOUNT))
    gerente.get("/api/invoices")

    kinds = [e["kind"] for e in events]
    for kind in ("agent.message", "agent.tool", "hook.saved", "agent.answer", "hook.applied", "request.proxied"):
        assert kind in kinds, kind
    assert [e["tool"] for e in events if e["kind"] == "agent.tool"] == ["list_endpoints", "get_endpoint", "write_hook"]
    (applied,) = [e for e in events if e["kind"] == "hook.applied"]
    assert applied["user"] == "gerente" and applied["hook"] == "friday_discount"
    assert applied["logs"] == ["discounted Friday invoices"]
    assert isinstance(applied["ms"], (int, float))
    proxied = [e for e in events if e["kind"] == "request.proxied" and e["path"] == "/api/invoices"]
    assert proxied[-1]["hooks"] == ["friday_discount"]
    assert isinstance(proxied[-1]["ms"], (int, float))


# Beat 8: a hook that needs `import os` is rejected by the guard; nothing is saved.

def test_beat_8_guard_rejects_import_os_three_times_and_saves_nothing(workspaces):
    gerente = login("gerente")
    write = ("write_hook", {"name": "server_info", "code": NEEDS_OS})
    events = chat(gerente, "Show the server's working directory", [write] * 5)

    assert types(events) == ["tool", "hook_rejected"] * 3 + ["error", "done"]
    assert all(e["name"] == "server_info" and e["reason"] for e in events if e["type"] == "hook_rejected")
    assert sql(workspaces, "SELECT 1 FROM hooks") == [] and sql(workspaces, "SELECT 1 FROM hook_versions") == []
    assert gerente.get("/fitit/hooks").json() == []


# Safety beat: a broken hook fails closed with 502, never leaking the original data.

def test_beat_broken_hook_fails_closed_with_502(workspaces):
    gerente = login("gerente")
    write_file(workspaces, "gerente", "broken", BROKEN)
    r = gerente.get("/api/invoices")
    assert r.status_code == 502
    body = r.json()
    assert body["error"] == "hook_failed" and body["hook"] == "broken" and body["detail"]
    assert set(body) == {"error", "hook", "detail"}
    assert login("recepcion").get("/api/invoices").status_code == 200


# Fallback beat: rehearsal hooks kept in the database still apply with no model at all.

def test_beat_fallback_rehearsal_hooks_work_without_the_model(workspaces):
    write_file(workspaces, "gerente", "friday_discount", FRIDAY_DISCOUNT)
    r = login("gerente").get("/api/invoices")
    assert r.status_code == 200
    assert r.headers["x-fitit-hooks"] == "friday_discount"
