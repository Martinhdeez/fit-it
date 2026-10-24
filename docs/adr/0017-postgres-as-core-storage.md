# 0017. PostgreSQL as the core's storage

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

The core keeps hooks as files under `WORKSPACES/<user>/` and chat history only in memory, for the lifetime of one WebSocket. Iteration 02 needs hook history (versions, undo) and chat history that survives reconnects and restarts. Files make both awkward: versions become extra files per hook, history becomes a log format of our own, and tests isolate state by swapping folders.

[ADR 0003](0003-api-only-access-to-client-data.md) still holds: hooks never touch the client app's database. This database belongs to the core only and stores the core's own state.

## Decision

The core stores its state in PostgreSQL, run as a `postgres` service in `compose.yaml`.

- **Image:** the official `postgres` image, pinned to major `17`, with a healthcheck, a named volume and no published port (only the core reaches it).
- **Driver:** psycopg 3, synchronous. Hook storage and agent tools are synchronous code already called from worker threads, so an async driver would add bridging, not speed. Async code that touches the database (the WebSocket loop) calls the sync functions through a thread.
- **No ORM.** Three small tables and a handful of queries; plain SQL in `backend/hooks.py` and `backend/agent.py` is shorter and easier to review than models.
- **Schema:** one idempotent `backend/schema.sql` (`CREATE TABLE IF NOT EXISTS ...`), applied by `db.init()` at core startup. No migration tool. When a change cannot be written idempotently, that change brings its own ADR and a migration tool.
- **Connections:** `db.connect()` opens a new connection to `DATABASE_URL`, read at call time. No pool yet.
- **Credentials:** user and database are `fitit`. The password comes from `POSTGRES_PASSWORD` in the host shell, with the compose default `fitit`. That default is a **development-only** password for a database with no published port; any deployment beyond a laptop must set its own. Nothing is written to `.env` or to a tracked file ([ADR 0010](0010-docker-compose-and-secrets-from-the-shell.md)).
- **Tests:** each test creates its own schema, points `DATABASE_URL` at it with `options=-csearch_path=<schema>` and calls `db.init()`. Exact rules in the [storage spec](../specs/storage.md).

## Consequences

- Hooks, versions and chat history live in one place, with transactions and keys instead of file conventions.
- `WORKSPACES` and the `./workspaces` bind mount disappear. Hooks from iteration 01 are not migrated; users recreate them (the agent regenerates one in seconds).
- The stack has one more service; `make check` waits for it to be healthy.
- A connection per call costs a few milliseconds per proxied request. **Known ceiling:** under real load, put `psycopg_pool` behind `db.connect()`; no caller changes.
- `make check` and local runs share one Postgres volume, separated by schema: tests never touch `public`.
- Trace storage is not part of this decision; it stays behind `trace.emit` and is the traceability owner's choice ([ADR 0012](0012-trace-seam-for-observability.md)).

## Alternatives considered

- **Keep files, add a versions folder and a JSON log per user:** works for one process, but reinvents transactions and makes test isolation a folder dance.
- **SQLite file in a volume:** no extra service, but one writer at a time, and a second engine to replace when we deploy for real.
- **SQLAlchemy or SQLModel:** an ORM and its session rules for three tables.
- **Alembic migrations:** worth it once a schema change cannot be idempotent; until then `CREATE TABLE IF NOT EXISTS` is enough.
- **asyncpg or async psycopg:** the callers are synchronous; async would force wrappers everywhere.
