# E1 — Demo client app

- **Item:** DA1 · **Spec:** [demo-app.md](../specs/demo-app.md) · **Requirements:** REQ-DA1-1 … 6
- **Depends on:** nothing

## Goal

A small clinic app that behaves like a real customer app and knows nothing about Fit-it, so the demo proves "zero modifications".

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As a demo presenter, I log in as any of the three users with one link. | REQ-DA1-1, REQ-DA1-2 |
| 2 | As the app, I only serve data to signed-in users, and doctors only see their own patients. | REQ-DA1-3, REQ-DA1-4 |
| 3 | As the agent, I can read invoices with dates to build a Friday discount. | REQ-DA1-5 |
| 4 | As the agent, I can discover every endpoint from `/openapi.json`. | REQ-DA1-6 |
| 5 | As a user, I see invoices and patients in tables whose columns follow the JSON keys. | Manual check |

## Deliverables

`demo/main.py`, `demo/Dockerfile`, a `demo` service in `compose.yaml` with no published port. Reuse the seed data in the v0 `backend/main.py`, adding invoice dates.

## Out of scope

Writes (POST/PUT/DELETE), real authentication, styling beyond readable tables.
