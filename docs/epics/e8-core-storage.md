# E8 — Core storage on PostgreSQL

- **Items:** DB1, HS2, CH1 · **Specs:** [storage.md](../specs/storage.md), [hook-contract.md](../specs/hook-contract.md) v2, [proxy.md](../specs/proxy.md) v2, [agent.md](../specs/agent.md) v2 · **Requirements:** REQ-DB1-1 … 6, REQ-HS2-1 … 10, REQ-CH1-1 … 7
- **Depends on:** iteration 01 (HR1, PX1, AG1)
- **ADRs:** [0017](../adr/0017-postgres-as-core-storage.md), [0018](../adr/0018-hook-versions-and-undo.md), [0019](../adr/0019-persistent-chat-history.md)

## Goal

The core keeps its state in its own Postgres: hooks with their full version history, and each user's chat, so nothing is lost on reload or restart and any change can be undone.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As an operator, `docker compose up` starts Postgres next to the core, reachable only by the core, with no secret in the repo. | REQ-DB1-1, REQ-DB1-2 |
| 2 | As a developer, the core creates its tables itself, and every test gets its own empty database schema. | REQ-DB1-3, REQ-DB1-4, REQ-DB1-5, REQ-DB1-6 |
| 3 | As a user, my hooks survive restarts and behave exactly as before. | REQ-HS2-1, REQ-HS2-3, REQ-HS2-4, REQ-HS2-10 |
| 4 | As a user, every version of my hook is kept and I can undo the last change. | REQ-HS2-2, REQ-HS2-7, REQ-HS2-9 |
| 5 | As the traceability workstream, I get the time and logs of each hook, not of the whole chain. | REQ-HS2-5, REQ-HS2-8 |
| 6 | As the agent, I can try a hook on live data without saving it anywhere. | REQ-HS2-6 |
| 7 | As a user, the agent remembers our conversation after a reload, and I can wipe it. | REQ-CH1-1 … 7 |

## Deliverables

- DB1: `postgres` service in `compose.yaml`, `backend/db.py`, `backend/schema.sql`, `psycopg[binary]` in `backend/pyproject.toml`, `db.init()` in the core's startup.
- HS2: `backend/hooks.py` on Postgres with the v2 module contract, the proxy adopting the new `run_hooks` result and the undo route, iteration 01 tests moved from hook files to the database.
- CH1: history in `backend/agent.py`.

## Out of scope

ORM, migration tool, connection pool, storing traces in Postgres (traceability owner's choice), migrating iteration 01 hook files, restoring deleted hooks, redo.
