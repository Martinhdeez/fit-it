"""Acceptance tests for DA1 (demo clinic app), written from docs/specs/demo-app.md v1
and docs/plan/01-mvp/requirements.md only. HTTP to DEMO_URL (default http://demo:8001)."""

import os
import re
from datetime import date

import httpx
import pytest

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")

API_ROUTES = ["/api/patients", "/api/patients/1", "/api/invoices", "/api/appointments"]
SPEC_API_PATHS = ["/api/me", "/api/patients", "/api/patients/{id}", "/api/invoices", "/api/appointments"]
PATIENT_FIELDS = {"id", "name", "dni", "phone", "birth_date", "doctor_id", "doctor"}
INVOICE_FIELDS = {"id", "patient_id", "patient", "date", "amount", "paid"}


@pytest.fixture
def client():
    with httpx.Client(base_url=DEMO_URL, timeout=10) as c:
        yield c


def login(client, user):
    r = client.get("/login", params={"user": user})
    assert 300 <= r.status_code < 400, r.status_code
    assert r.headers["location"] in ("/", f"{DEMO_URL}/")
    assert client.cookies.get("session") == user


# REQ-DA1-1

def test_REQ_DA1_1_me_without_session_is_401(client):
    assert client.get("/api/me").status_code == 401


def test_REQ_DA1_1_me_after_login_is_gerente(client):
    login(client, "gerente")
    r = client.get("/api/me")
    assert r.status_code == 200
    assert r.json() == {"id": "gerente", "name": "Marta (gerencia)", "role": "manager"}


def test_REQ_DA1_1_me_after_logout_is_401(client):
    login(client, "gerente")
    r = client.get("/logout")
    assert 300 <= r.status_code < 400
    assert client.get("/api/me").status_code == 401


# REQ-DA1-2

def test_REQ_DA1_2_unknown_user_login_is_400_without_cookie(client):
    r = client.get("/login", params={"user": "nobody"})
    assert r.status_code == 400
    assert "set-cookie" not in r.headers
    assert "session" not in client.cookies


# REQ-DA1-3

@pytest.mark.parametrize("path", API_ROUTES)
def test_REQ_DA1_3_api_route_without_session_is_401(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", API_ROUTES)
def test_REQ_DA1_3_api_route_with_invalid_session_is_401(client, path):
    assert client.get(path, cookies={"session": "nobody"}).status_code == 401


# REQ-DA1-4

def test_REQ_DA1_4_doctor_sees_only_own_patients(client):
    login(client, "dr_garcia")
    r = client.get("/api/patients")
    assert r.status_code == 200
    patients = r.json()
    assert patients
    assert all(p["doctor_id"] == 1 for p in patients)


def test_REQ_DA1_4_manager_sees_at_least_8_patients(client):
    login(client, "gerente")
    r = client.get("/api/patients")
    assert r.status_code == 200
    patients = r.json()
    assert len(patients) >= 8
    assert all(set(p) == PATIENT_FIELDS for p in patients)
    assert len({p["doctor_id"] for p in patients}) > 1


def test_REQ_DA1_4_doctor_cannot_open_other_doctors_patient(client):
    login(client, "gerente")
    others = [p["id"] for p in client.get("/api/patients").json() if p["doctor_id"] != 1]
    assert others
    client.cookies.clear()
    login(client, "dr_garcia")
    assert client.get(f"/api/patients/{others[0]}").status_code == 404


# REQ-DA1-5

def test_REQ_DA1_5_invoices_fields_dates_and_fridays(client):
    login(client, "gerente")
    r = client.get("/api/invoices")
    assert r.status_code == 200
    invoices = r.json()
    assert invoices
    for inv in invoices:
        assert set(inv) == INVOICE_FIELDS
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", inv["date"])
    fridays = [inv for inv in invoices if date.fromisoformat(inv["date"]).weekday() == 4]
    assert len(fridays) >= 2
    assert any(inv["paid"] is False for inv in invoices)


# REQ-DA1-6

def test_REQ_DA1_6_openapi_lists_every_api_route_with_summary(client):
    r = client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for path in SPEC_API_PATHS:
        assert path in paths, path
        op = paths[path].get("get")
        assert op is not None, path
        assert op.get("summary", "").strip(), path
