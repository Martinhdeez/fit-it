# Storage — v3

The core's own PostgreSQL database: connection, schema, test isolation. Decision: [ADR 0017](../adr/0017-postgres-as-core-storage.md). This database never holds credentials for, or data read directly from, the client app's database ([ADR 0003](../adr/0003-api-only-access-to-client-data.md)).

## Changes from v2

Iteration 04. The v2 text is in git history (`git show 5c656ce:docs/specs/storage.md`); v1 at `6554dff`.

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | `messages` gains `conversation_id text NOT NULL DEFAULT 'default'`, added idempotently with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`; existing rows belong to `default`. New index on `(user_id, conversation_id, id)` ([ADR 0024](../adr/0024-parallel-conversations.md)). | **Yes** for tests asserting the exact columns of `messages` (`test_db1.py`); no for rows or code | CV1 |
| 2 | `hooks.save_hook` takes an advisory lock per `(user, name)` ("Who writes what"). | No | HK1 |

Changes in v2 (iteration 03): the four marketplace tables; `hooks.delete_hook` unpublishes.

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
| `conversation_id` | `text` | not null, default `'default'`. Matches `^[A-Za-z0-9_-]{1,64}$` ([agent spec](agent.md#conversations)) |

Indexes on `(user_id, id)` and `(user_id, conversation_id, id)`. Decisions: [ADR 0019](../adr/0019-persistent-chat-history.md), [ADR 0024](../adr/0024-parallel-conversations.md).

The column is added after the `CREATE TABLE`, so a v2 database gains it at the next start:

```sql
ALTER TABLE messages ADD COLUMN IF NOT EXISTS conversation_id text NOT NULL DEFAULT 'default';
CREATE INDEX IF NOT EXISTS messages_user_conversation_id ON messages (user_id, conversation_id, id);
```

The `CREATE TABLE` statement for `messages` itself stays as in v2, so an empty and an upgraded database end up identical. Rows inserted without `conversation_id` belong to `default`.

### Marketplace tables

Decisions: [ADR 0022](../adr/0022-feature-marketplace.md), [ADR 0023](../adr/0023-features-run-as-the-subscriber.md). Rules: [marketplace spec](marketplace.md). Rows of `features` and `feature_versions` are never deleted.

```sql
CREATE TABLE IF NOT EXISTS features (
    id          bigserial   PRIMARY KEY,
    owner_id    text        NOT NULL,
    hook_name   text        NOT NULL,
    title       text        NOT NULL,
    description text        NOT NULL,
    version     integer     NOT NULL,              -- latest feature_versions.version
    published   boolean     NOT NULL DEFAULT true,
    created_at  timestamptz NOT NULL DEFAULT now(),
    UNIQUE (owner_id, hook_name)
);

CREATE TABLE IF NOT EXISTS feature_versions (
    feature_id   bigint      NOT NULL REFERENCES features (id),
    version      integer     NOT NULL,             -- 1, 2, 3, ... per feature
    code         text        NOT NULL,             -- frozen copy of the hook's code
    route        text        NOT NULL,             -- "<METHOD> <path>" from the guard at publish time
    published_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (feature_id, version)
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id         bigserial   PRIMARY KEY,            -- subscription order: ascending id
    user_id    text        NOT NULL,
    feature_id bigint      NOT NULL,
    version    integer     NOT NULL,               -- pinned version
    enabled    boolean     NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (user_id, feature_id),
    FOREIGN KEY (feature_id, version) REFERENCES feature_versions (feature_id, version)
);

CREATE TABLE IF NOT EXISTS reports (
    id         bigserial   PRIMARY KEY,
    feature_id bigint      NOT NULL REFERENCES features (id),
    user_id    text        NOT NULL,
    reason     text        NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
```

Exactly these columns, types, nullability and keys. Accepting an update changes `subscriptions.version` in place, so the subscription keeps its `id` and therefore its place in the order.

## Who writes what

| Table | Written only by |
|---|---|
| `hooks`, `hook_versions` | `backend/hooks.py` ([hook contract](hook-contract.md)); a fork writes them through `hooks.save_hook`. Every `save_hook` holds `pg_advisory_xact_lock` on a key derived from `(user_id, name)` for its transaction ([hook contract](hook-contract.md#concurrent-saves)) |
| `messages` | `backend/agent.py` ([agent spec](agent.md)) |
| `features`, `feature_versions`, `subscriptions`, `reports` | `backend/marketplace.py` ([marketplace spec](marketplace.md)), except one statement: `hooks.delete_hook` sets `features.published` to false for `(owner_id, hook_name)` in the same transaction as the delete |

Traces are not stored here; they stay behind `trace.emit` ([trace events](trace-events.md)).

## Test isolation

- `make check` runs with the `postgres` service healthy and `DATABASE_URL` set as in compose, with no query string.
- A test that touches storage creates its own schema, sets `DATABASE_URL` to the same URL plus `?options=-csearch_path%3D<schema>`, calls `db.init()`, and drops the schema with `CASCADE` when it ends. The exact fixture is in the current plan's `contracts.md`.
- Tests never create, read or drop anything in the `public` schema.
- Because every core module reads `DATABASE_URL` at each connection, a test may switch it with `monkeypatch.setenv` at any time.
