import datetime
import os

import httpx
import pytest

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")


def client(user=None):
    c = httpx.Client(base_url=DEMO_URL)
    if user:
        assert c.get("/login", params={"user": user}).status_code == 303
    return c


def test_login_sets_session_and_me():
    c = client()
    assert c.get("/api/me").status_code == 401
    r = c.get("/login", params={"user": "gerente"})
    assert r.headers["location"] == "/"
    assert c.cookies["session"] == "gerente"
    assert c.get("/api/me").json() == {"id": "gerente", "name": "Marta (gerencia)", "role": "manager"}


def test_unknown_user_is_rejected_without_cookie():
    r = client().get("/login", params={"user": "nobody"})
    assert r.status_code == 400
    assert "set-cookie" not in r.headers


def test_logout_clears_session():
    c = client("gerente")
    c.get("/logout")
    assert c.get("/api/me").status_code == 401


@pytest.mark.parametrize("path", ["/api/patients", "/api/patients/1", "/api/invoices", "/api/appointments"])
def test_api_requires_session(path):
    assert client().get(path).status_code == 401
    assert httpx.get(DEMO_URL + path, cookies={"session": "nobody"}).status_code == 401


def test_doctor_sees_only_own_patients():
    c = client("dr_garcia")
    patients = c.get("/api/patients").json()
    assert patients and {p["doctor_id"] for p in patients} == {1}
    assert c.get("/api/patients/3").status_code == 404
    own = {p["id"] for p in patients}
    assert {i["patient_id"] for i in c.get("/api/invoices").json()} <= own
    assert {a["patient_id"] for a in c.get("/api/appointments").json()} <= own


def test_manager_sees_everything():
    c = client("gerente")
    patients = c.get("/api/patients").json()
    assert len(patients) >= 8
    assert set(patients[0]) == {"id", "name", "dni", "phone", "birth_date", "doctor_id", "doctor"}
    detail = c.get("/api/patients/1").json()
    assert detail["diagnoses"] == ["Diabetes tipo 2", "Hipertensión"]
    assert c.get("/api/patients/999").status_code == 404
    appointment = c.get("/api/appointments").json()[0]
    assert set(appointment) == {"id", "patient_id", "patient", "doctor", "starts_at"}


def test_invoices_have_dates_with_fridays():
    invoices = client("recepcion").get("/api/invoices").json()
    assert set(invoices[0]) == {"id", "patient_id", "patient", "date", "amount", "paid"}
    dates = [datetime.date.fromisoformat(i["date"]) for i in invoices]
    assert all(d.isoformat() == i["date"] for d, i in zip(dates, invoices))
    assert sum(d.weekday() == 4 for d in dates) >= 2
    assert any(not i["paid"] for i in invoices)


def test_openapi_lists_api_routes_with_summaries():
    paths = httpx.get(DEMO_URL + "/openapi.json").json()["paths"]
    for path in ["/api/me", "/api/patients", "/api/patients/{patient_id}", "/api/invoices", "/api/appointments"]:
        assert paths[path]["get"]["summary"]
    assert all(op["summary"] for ops in paths.values() for op in ops.values())


def test_home_page_has_login_links():
    html = httpx.get(DEMO_URL + "/").text
    for user in ["recepcion", "dr_garcia", "gerente"]:
        assert f"/login?user={user}" in html
    assert "/logout" in html
