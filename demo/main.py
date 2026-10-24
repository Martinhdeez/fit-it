import sqlite3

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

USERS = {
    "recepcion": {"name": "Lucía (recepción)", "role": "receptionist", "doctor_id": None},
    "dr_garcia": {"name": "Dr. García", "role": "doctor", "doctor_id": 1},
    "gerente": {"name": "Marta (gerencia)", "role": "manager", "doctor_id": None},
}

# Copied from the v0 prototype (backend/main.py), with invoice dates added.
SEED = """
CREATE TABLE doctors (id INTEGER PRIMARY KEY, name TEXT, specialty TEXT);
CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT, dni TEXT, phone TEXT, birth_date TEXT, doctor_id INTEGER);
CREATE TABLE appointments (id INTEGER PRIMARY KEY, patient_id INTEGER, doctor_id INTEGER, starts_at TEXT);
CREATE TABLE diagnoses (id INTEGER PRIMARY KEY, patient_id INTEGER, description TEXT, sensitive INTEGER);
CREATE TABLE invoices (id INTEGER PRIMARY KEY, patient_id INTEGER, date TEXT, amount REAL, paid INTEGER);

INSERT INTO doctors VALUES
  (1, 'Dr. García', 'Medicina general'), (2, 'Dra. Pereira', 'Oncología'), (3, 'Dr. Souto', 'Cardiología');
INSERT INTO patients VALUES
  (1, 'María López', '12345678A', '+34 612 345 678', '1958-03-12', 1),
  (2, 'Xoán Rodríguez', '23456789B', '+34 623 456 789', '1984-07-01', 1),
  (3, 'Ana Fernández', '34567890C', '+34 634 567 890', '1972-11-23', 2),
  (4, 'Pablo Vázquez', '45678901D', '+34 645 678 901', '1990-05-30', 3),
  (5, 'Carmen Iglesias', '56789012E', '+34 656 789 012', '1949-01-17', 2),
  (6, 'Iago Castro', '67890123F', '+34 667 890 123', '2001-09-09', 1),
  (7, 'Lucía Otero', '78901234G', '+34 678 901 234', '1966-12-04', 3),
  (8, 'Manuel Rey', '89012345H', '+34 689 012 345', '1979-04-15', 2);
INSERT INTO appointments (patient_id, doctor_id, starts_at) VALUES
  (1, 1, '2026-10-26 09:00'), (2, 1, '2026-10-26 09:30'), (3, 2, '2026-10-27 11:00'),
  (4, 3, '2026-10-28 16:00'), (5, 2, '2026-10-26 12:00'), (6, 1, '2026-11-02 10:00'),
  (7, 3, '2026-10-29 17:30'), (8, 2, '2026-10-30 13:00');
INSERT INTO diagnoses (patient_id, description, sensitive) VALUES
  (1, 'Diabetes tipo 2', 0), (1, 'Hipertensión', 0), (2, 'Ansiedad generalizada', 1),
  (3, 'Cáncer de mama', 1), (4, 'Arritmia', 0), (5, 'Linfoma', 1),
  (6, 'Asma', 0), (7, 'Insuficiencia cardíaca', 0), (8, 'VIH', 1);
INSERT INTO invoices (patient_id, date, amount, paid) VALUES
  (1, '2026-10-02', 120, 1), (2, '2026-10-05', 80, 0), (3, '2026-10-09', 650, 0),
  (4, '2026-10-13', 90, 1), (5, '2026-10-16', 1200, 0), (6, '2026-10-21', 45, 0),
  (7, '2026-10-22', 300, 1), (8, '2026-10-23', 210, 0);
"""

db = sqlite3.connect(":memory:", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(SEED)

# Rows are visible when the user is not a doctor (doctor_id NULL) or is the patient's doctor.
VISIBLE = "(:doctor_id IS NULL OR p.doctor_id = :doctor_id)"


def rows(sql, **params):
    return [dict(r) for r in db.execute(sql, params)]


def current_user(request: Request):
    user_id = request.cookies.get("session")
    if user_id not in USERS:
        raise HTTPException(401, "not logged in")
    return {"id": user_id, **USERS[user_id]}


app = FastAPI(title="Demo clinic")


@app.get("/login", summary="Log in as a demo user")
def login(user: str):
    if user not in USERS:
        raise HTTPException(400, f"unknown user '{user}'")
    response = RedirectResponse("/", status_code=303)
    response.set_cookie("session", user, httponly=True, samesite="lax")
    return response


@app.get("/logout", summary="Log out")
def logout():
    response = RedirectResponse("/", status_code=303)
    response.delete_cookie("session")
    return response


@app.get("/api/me", summary="Current user")
def me(user=Depends(current_user)):
    return {"id": user["id"], "name": user["name"], "role": user["role"]}


@app.get("/api/patients", summary="List patients")
def patients(user=Depends(current_user)):
    return rows(f"""
        SELECT p.id, p.name, p.dni, p.phone, p.birth_date, p.doctor_id, d.name AS doctor
        FROM patients p JOIN doctors d ON d.id = p.doctor_id
        WHERE {VISIBLE} ORDER BY p.id""", doctor_id=user["doctor_id"])


@app.get("/api/patients/{patient_id}", summary="Get one patient with diagnoses")
def patient(patient_id: int, user=Depends(current_user)):
    found = rows(f"""
        SELECT p.id, p.name, p.dni, p.phone, p.birth_date, p.doctor_id, d.name AS doctor
        FROM patients p JOIN doctors d ON d.id = p.doctor_id
        WHERE p.id = :id AND {VISIBLE}""", id=patient_id, doctor_id=user["doctor_id"])
    if not found:
        raise HTTPException(404, "patient not found")
    diagnoses = db.execute("SELECT description FROM diagnoses WHERE patient_id = ? ORDER BY id", (patient_id,))
    return {**found[0], "diagnoses": [r["description"] for r in diagnoses]}


@app.get("/api/invoices", summary="List invoices")
def invoices(user=Depends(current_user)):
    result = rows(f"""
        SELECT i.id, i.patient_id, p.name AS patient, i.date, i.amount, i.paid
        FROM invoices i JOIN patients p ON p.id = i.patient_id
        WHERE {VISIBLE} ORDER BY i.id""", doctor_id=user["doctor_id"])
    return [{**r, "paid": bool(r["paid"])} for r in result]


@app.get("/api/appointments", summary="List appointments")
def appointments(user=Depends(current_user)):
    return rows(f"""
        SELECT a.id, a.patient_id, p.name AS patient, d.name AS doctor, a.starts_at
        FROM appointments a JOIN patients p ON p.id = a.patient_id JOIN doctors d ON d.id = a.doctor_id
        WHERE {VISIBLE} ORDER BY a.starts_at""", doctor_id=user["doctor_id"])


PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demo clinic</title>
<style>
  body { font-family: system-ui, sans-serif; margin: 2rem; }
  table { border-collapse: collapse; margin-bottom: 2rem; }
  th, td { border: 1px solid #ccc; padding: .3rem .6rem; text-align: left; }
  th { background: #f3f3f3; }
</style>
</head>
<body>
<h1>Demo clinic</h1>
<nav>
  Log in as: <a href="/login?user=recepcion">recepcion</a> ·
  <a href="/login?user=dr_garcia">dr_garcia</a> ·
  <a href="/login?user=gerente">gerente</a> · <a href="/logout">Log out</a>
</nav>
<p id="who">Not logged in.</p>
<div id="tables"></div>
<script>
function table(title, items) {
  const section = document.createElement("section");
  const h2 = document.createElement("h2");
  h2.textContent = title;
  section.append(h2);
  const keys = [...new Set(items.flatMap(Object.keys))];
  const t = document.createElement("table");
  const head = t.insertRow();
  for (const k of keys) {
    const th = document.createElement("th");
    th.textContent = k;
    head.append(th);
  }
  for (const item of items) {
    const row = t.insertRow();
    for (const k of keys) {
      const v = item[k];
      row.insertCell().textContent = v === undefined || v === null ? "" : typeof v === "object" ? JSON.stringify(v) : v;
    }
  }
  section.append(t);
  document.getElementById("tables").append(section);
}

async function load() {
  const me = await fetch("/api/me");
  if (!me.ok) return;
  document.getElementById("who").textContent = "Logged in as " + (await me.json()).name + ".";
  for (const [title, url] of [["Invoices", "/api/invoices"], ["Patients", "/api/patients"]]) {
    const r = await fetch(url);
    if (r.ok) table(title, await r.json());
  }
}
load();
</script>
</body>
</html>
"""


@app.get("/", summary="Clinic home page", response_class=HTMLResponse)
def home():
    return PAGE
