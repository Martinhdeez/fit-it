# Storage — v1

The core's own PostgreSQL database: connection, schema, test isolation. Decision: [ADR 0017](../adr/0017-postgres-as-core-storage.md). This database never holds credentials for, or data read directly from, the client app's database ([ADR 0003](../adr/0003-api-only-access-to-client-data.md)).

## Service

| Item | Value |
|---|---|
| Compose service | `postgres`, image `postgres:17`, healthcheck with `pg_isready`, named volume `pgdata` at `/var/lib/postgresql/data`, no published port |
| User / database | `fitit` / `fitit` |
| Password | `POSTGRES_PASSWORD` from the host shell; compose default `fitit` (development only) |
| Core variable | `DATABASE_URL`, compose value `postgresql://fitit:${POSTGRES_PASSWORD:-fitit}@postgres:5432/fitit` |
| Core start | `core` depends on `postgres` being healthy |

## Module contract: `backend/db.py`

| Name | Behaviour |
|---|---|
| `connect()` | Returns a new psycopg 3 connection to `DATABASE_URL`, read from the environment at each call (default `postgresql://fitit:fitit@postgres:5432/fitit`). Used as `with db.connect() as conn:`; the transaction commits when the block ends without error and rolls back otherwise. |
| `init() -> None` | Runs `backend/schema.sql` on a new connection. Idempotent: running it again changes nothing and keeps existing rows. |

The core calls `db.init()` once at startup (FastAPI lifespan). Every query uses unqualified table names, so the tables in the first schema of `search_path` are used.

## Schema (`backend/schema.sql`)

### `hooks`: the current state of each hook

| Column | Type | Rule |
|---|---|---|
| `user_id` | `text` | not null |
| `name` | `text` | not null |
| `route` | `text` | not null. `"<METHOD> <path>"` as returned by the guard when saved. Informational: the runtime always derives the route from `code`. |
| `code` | `text` | not null |
| `enabled` | `boolean` | not null, default `true` |
| `version` | `integer` | not null. The `hook_versions.version` this code came from. |
| `updated_at` | `timestamptz` | not null, default `now()`. Set on save, undo and toggle. |

Primary key `(user_id, name)`.

### `hook_versions`: every saved code, append-only

| Column | Type | Rule |
|---|---|---|
| `user_id` | `text` | not null |
| `name` | `text` | not null |
| `version` | `integer` | not null, `1, 2, 3, ...` per `(user_id, name)` |
| `code` | `text` | not null |
| `saved_at` | `timestamptz` | not null, default `now()` |

Primary key `(user_id, name, version)`. No foreign key to `hooks`: versions outlive a deleted hook ([ADR 0018](../adr/0018-hook-versions-and-undo.md)).

### `messages`: chat history, one row per completed turn

| Column | Type | Rule |
|---|---|---|
| `id` | `bigserial` | primary key; order of turns |
| `user_id` | `text` | not null |
| `turn` | `jsonb` | not null. A JSON array: the turn's new Pydantic AI messages, as produced by `ModelMessagesTypeAdapter.dump_python(messages, mode="json")` |
| `created_at` | `timestamptz` | not null, default `now()` |

Index on `(user_id, id)`. Decision: [ADR 0019](../adr/0019-persistent-chat-history.md).

## Who writes what

| Table | Written only by |
|---|---|
| `hooks`, `hook_versions` | `backend/hooks.py` ([hook contract](hook-contract.md)) |
| `messages` | `backend/agent.py` ([agent spec](agent.md)) |

Traces are not stored here; they stay behind `trace.emit` ([trace events](trace-events.md)).

## Test isolation

- `make check` runs with the `postgres` service healthy and `DATABASE_URL` set as in compose, with no query string.
- A test that touches storage creates its own schema, sets `DATABASE_URL` to the same URL plus `?options=-csearch_path%3D<schema>`, calls `db.init()`, and drops the schema with `CASCADE` when it ends. The exact fixture is in the current plan's `contracts.md`.
- Tests never create, read or drop anything in the `public` schema.
- Because every core module reads `DATABASE_URL` at each connection, a test may switch it with `monkeypatch.setenv` at any time.
