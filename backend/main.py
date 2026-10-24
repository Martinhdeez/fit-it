import ast
import sqlite3
import uuid

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

USERS = {
    "recepcion": {"name": "Lucía (recepción)", "role": "receptionist", "doctor_id": None},
    "dr_garcia": {"name": "Dr. García", "role": "doctor", "doctor_id": 1},
    "gerente": {"name": "Marta (gerencia)", "role": "manager", "doctor_id": None},
}

SEED = """
CREATE TABLE doctors (id INTEGER PRIMARY KEY, name TEXT, specialty TEXT);
CREATE TABLE patients (id INTEGER PRIMARY KEY, name TEXT, dni TEXT, phone TEXT, birth_date TEXT, doctor_id INTEGER);
CREATE TABLE appointments (id INTEGER PRIMARY KEY, patient_id INTEGER, doctor_id INTEGER, starts_at TEXT);
CREATE TABLE diagnoses (id INTEGER PRIMARY KEY, patient_id INTEGER, description TEXT, sensitive INTEGER);
CREATE TABLE invoices (id INTEGER PRIMARY KEY, patient_id INTEGER, amount REAL, paid INTEGER);

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
INSERT INTO invoices (patient_id, amount, paid) VALUES
  (1, 120, 1), (2, 80, 0), (3, 650, 0), (4, 90, 1), (5, 1200, 0), (6, 45, 0), (7, 300, 1), (8, 210, 0);
"""

PATIENTS_QUERY = """
SELECT p.id, p.name, p.dni, p.phone, p.birth_date, p.doctor_id, d.name AS doctor,
  (SELECT MIN(starts_at) FROM appointments a WHERE a.patient_id = p.id) AS next_appointment,
  (SELECT group_concat(description, '; ') FROM diagnoses x WHERE x.patient_id = p.id) AS diagnoses,
  (SELECT COALESCE(SUM(amount), 0) FROM invoices i WHERE i.patient_id = p.id AND paid = 0) AS debt
FROM patients p JOIN doctors d ON d.id = p.doctor_id ORDER BY p.id
"""

db = sqlite3.connect(":memory:", check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript(SEED)


def fetch_patients():
    return [dict(r) for r in db.execute(PATIENTS_QUERY)]


# ponytail: AST deny-list + restricted builtins, not a real sandbox (no timeout, str.format can read attributes).
# Upgrade to a subprocess with resource limits if this ever runs untrusted users.
SAFE_BUILTINS = {
    f.__name__: f
    for f in (len, sum, min, max, abs, round, sorted, reversed, any, all, enumerate, zip, range,
              str, int, float, bool, list, dict, set, tuple, isinstance)
}
BANNED_NAMES = {"eval", "exec", "compile", "open", "globals", "locals", "vars",
                "getattr", "setattr", "delattr", "input", "breakpoint", "type", "object"}


def check_code(code: str):
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"syntax error: {e.msg} (line {e.lineno})")
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            raise ValueError(f"line {node.lineno}: imports and global/nonlocal are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"line {node.lineno}: private attribute '{node.attr}' is not allowed")
        if isinstance(node, ast.Name) and (node.id.startswith("_") or node.id in BANNED_NAMES):
            raise ValueError(f"line {node.lineno}: name '{node.id}' is not allowed")


class BoxIn(BaseModel):
    name: str
    description: str = ""
    code: str


class CodeBox(BoxIn):
    id: str
    enabled: bool = True


class Toggle(BaseModel):
    enabled: bool


user_boxes: dict[str, list[CodeBox]] = {u: [] for u in USERS}


def run_pipeline(user_id: str):
    data = fetch_patients()
    context = {"user": {"id": user_id, **USERS[user_id]}, "badges": {}, "warnings": []}
    logs = []
    for box in user_boxes[user_id]:
        if not box.enabled:
            continue
        log = lambda *args, box=box: logs.append({"box": box.name, "msg": " ".join(map(str, args))})
        scope = {"__builtins__": {**SAFE_BUILTINS, "print": log}, "data": data, "context": context}
        try:
            exec(box.code, scope)
        except Exception as e:
            # Fail closed: a broken privacy rule must never let raw records through.
            logs.append({"box": box.name, "msg": f"ERROR {type(e).__name__}: {e}"})
            return {"data": [], "context": context, "logs": logs, "blocked_by": box.id}
        data = scope["data"]
        context = scope["context"]
    return {"data": data, "context": context, "logs": logs, "blocked_by": None}


app = FastAPI()


def boxes_of(user_id: str):
    if user_id not in user_boxes:
        raise HTTPException(404, f"unknown user '{user_id}'")
    return user_boxes[user_id]


def find_box(user_id: str, box_id: str):
    for box in boxes_of(user_id):
        if box.id == box_id:
            return box
    raise HTTPException(404, f"unknown box '{box_id}'")


@app.get("/api/users")
def list_users():
    return USERS


@app.get("/api/data")
def get_data(user_id: str):
    boxes_of(user_id)
    return run_pipeline(user_id)


@app.get("/api/boxes")
def list_boxes(user_id: str):
    return boxes_of(user_id)


@app.post("/api/boxes")
def add_box(user_id: str, box: BoxIn):
    try:
        check_code(box.code)
    except ValueError as e:
        raise HTTPException(400, str(e))
    new = CodeBox(id=uuid.uuid4().hex[:8], **box.model_dump())
    boxes_of(user_id).append(new)
    return new


@app.patch("/api/boxes/{box_id}")
def toggle_box(user_id: str, box_id: str, body: Toggle):
    box = find_box(user_id, box_id)
    box.enabled = body.enabled
    return box


@app.delete("/api/boxes/{box_id}")
def delete_box(user_id: str, box_id: str):
    boxes_of(user_id).remove(find_box(user_id, box_id))
    return {"ok": True}
