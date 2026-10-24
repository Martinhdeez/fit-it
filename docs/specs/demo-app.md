# Demo client app — v1

A small clinic app that stands in for a customer's app. It knows nothing about Fit-it. It lives in `demo/` and listens on port `8001`, with no published port in Compose.

## Users

| id | name | role |
|---|---|---|
| `recepcion` | Lucía (recepción) | receptionist |
| `dr_garcia` | Dr. García | doctor (doctor id 1) |
| `gerente` | Marta (gerencia) | manager |

## Session

- `GET /login?user=<id>` sets the cookie `session=<id>` and redirects to `/`. An unknown id returns `400`.
- `GET /logout` clears the cookie and redirects to `/`.

## API

All `/api/*` routes except `/api/me` return `401` without a valid session. FastAPI serves `GET /openapi.json`, and every route has a summary.

| Route | Returns |
|---|---|
| `GET /api/me` | `{"id", "name", "role"}`; `401` without a session |
| `GET /api/patients` | `[{"id", "name", "dni", "phone", "birth_date", "doctor_id", "doctor"}]`. A doctor only gets their own patients. |
| `GET /api/patients/{id}` | One patient with `"diagnoses": [str]`; `404` if unknown or not visible to the user |
| `GET /api/invoices` | `[{"id", "patient_id", "patient", "date", "amount", "paid"}]`. `date` is `YYYY-MM-DD`; at least two invoices fall on a Friday. A doctor only gets invoices of their own patients. |
| `GET /api/appointments` | `[{"id", "patient_id", "patient", "doctor", "starts_at"}]` |

Seed data: 3 doctors, at least 8 patients, appointments, diagnoses (some sensitive) and invoices, some unpaid. Data is in-memory SQLite and resets on restart.

## Page

`GET /` returns an HTML page with:
- links to log in as each user, and to log out;
- when logged in, the user's name and two tables, invoices and patients, filled by `fetch` from the API;
- table columns taken from the keys of the JSON objects, so fields a hook adds appear as new columns and removed fields disappear.
