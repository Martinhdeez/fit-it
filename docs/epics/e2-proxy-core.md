# E2 — Proxy core

- **Item:** PX1 · **Spec:** [proxy.md](../specs/proxy.md) · **Requirements:** REQ-PX1-1 … 10
- **Depends on:** HR1 (hook runtime), DA1 (demo app as the upstream)
- **ADRs:** [0002](../adr/0002-reverse-proxy-in-front-of-unmodified-app.md), [0005](../adr/0005-fail-closed-hook-execution.md), [0006](../adr/0006-identify-users-through-the-app-session.md)

## Goal

All traffic goes through the core, which personalizes JSON responses per user, injects the widget, serves virtual endpoints and fails closed.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As an operator, I put the core in front of my app and it behaves exactly like the app, cookies included. | REQ-PX1-1, REQ-PX1-2 |
| 2 | As a user, my hooks change the data I receive, and nobody else's. | REQ-PX1-3, REQ-PX1-4 |
| 3 | As an operator, a broken hook never leaks the unfiltered response. | REQ-PX1-5 |
| 4 | As a user, I see the Fit-it widget on every page once signed in. | REQ-PX1-6, REQ-PX1-9 |
| 5 | As a user, I can open features that exist only as hooks under `/x/`. | REQ-PX1-7 |
| 6 | As a user, I can list, toggle and delete my hooks. | REQ-PX1-8 |
| 7 | As the traceability workstream, I receive an event for every request and hook run. | REQ-PX1-10 |

## Deliverables

`backend/main.py` (FastAPI app exporting `app`), `backend/trace.py` default implementation (one JSON line per event) if TR1 has not landed yet. The v0 endpoints in `backend/main.py` are removed.

## Out of scope

Caching `/api/me`, streaming responses, WebSocket proxying to the app, compression.
