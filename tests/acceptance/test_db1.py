"""Acceptance tests for DB1 (Postgres in the stack and the test gate), written from
docs/plan/02-postgres/requirements.md, contracts.md and docs/specs/storage.md only.

REQ-DB1-1 and REQ-DB1-2 are checked through the shell and Docker on the host (no Docker
inside the test container), so they are a manual checklist; the stubs below only keep
each id visible.

Manual checklist, run from the repo root on the host:

REQ-DB1-1  Compose has a private, healthy `postgres` and the core is wired to it.
  1. `docker compose config`  -> under `services.postgres`:
       - `image: postgres:17`
       - a `healthcheck:` block (spec: `pg_isready`)
       - a volume whose source is a named volume (spec: `pgdata` at
         /var/lib/postgresql/data), and `pgdata` listed under top-level `volumes:`
       - no `ports:` entry
     under `services.core`:
       - `environment:` has `DATABASE_URL`, `FITIT_HOOK_TIMEOUT_MS`,
         `FITIT_HOOK_MAX_BYTES` and `FITIT_AGENT_MESSAGES_PER_HOUR` (the last three as
         passthrough: present when exported in the shell, e.g.
         `FITIT_HOOK_TIMEOUT_MS=7 docker compose config` shows `FITIT_HOOK_TIMEOUT_MS: "7"`)
       - no `WORKSPACES` variable, no volume mounting `workspaces`
       - `depends_on.postgres.condition: service_healthy`
  2. `docker compose up -d postgres`
     `docker compose port postgres 5432`  -> no mapping (error or empty).
  3. `docker compose down`

REQ-DB1-2  POSTGRES_PASSWORD comes from the shell, default `fitit`.
  1. `POSTGRES_PASSWORD=s3cret docker compose config`  -> `services.postgres.environment`
     has `POSTGRES_PASSWORD: s3cret`, and `services.core.environment.DATABASE_URL` is
     `postgresql://fitit:s3cret@postgres:5432/fitit`.
  2. `env -u POSTGRES_PASSWORD docker compose config`  -> `POSTGRES_PASSWORD: fitit` and
     `DATABASE_URL: postgresql://fitit:fitit@postgres:5432/fitit`.
"""

import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

import db

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")


@pytest.fixture
def database(monkeypatch):
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{base}?options=-csearch_path%3D{schema}"
    monkeypatch.setenv("DATABASE_URL", url)
    db.init()
    yield url                      # connect to it with psycopg.connect(url) to read or write rows
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def empty_schema(monkeypatch):
    """Like `database`, but without `db.init()`: an empty schema for REQ-DB1-3 and 6."""
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{base}?options=-csearch_path%3D{schema}"
    monkeypatch.setenv("DATABASE_URL", url)
    yield schema, url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


# Storage spec v1: column -> (data_type as information_schema reports it, nullable).
EXPECTED = {
    "hooks": {
        "user_id": ("text", False),
        "name": ("text", False),
        "route": ("text", False),
        "code": ("text", False),
        "enabled": ("boolean", False),
        "version": ("integer", False),
        "updated_at": ("timestamp with time zone", False),
    },
    "hook_versions": {
        "user_id": ("text", False),
        "name": ("text", False),
        "version": ("integer", False),
        "code": ("text", False),
        "saved_at": ("timestamp with time zone", False),
    },
    "messages": {
        "id": ("bigint", False),
        "user_id": ("text", False),
        "turn": ("jsonb", False),
        "created_at": ("timestamp with time zone", False),
        "conversation_id": ("text", False),
    },
}

PRIMARY_KEYS = {
    "hooks": ["user_id", "name"],
    "hook_versions": ["user_id", "name", "version"],
    "messages": ["id"],
}


def tables(schema):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %s AND table_type = 'BASE TABLE'",
            (schema,),
        ).fetchall()
    return {r[0] for r in rows}


def columns(schema, table):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT column_name, data_type, is_nullable, column_default "
            "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
            (schema, table),
        ).fetchall()
    return {name: (dtype, nullable == "YES", default) for name, dtype, nullable, default in rows}


def primary_key(schema, table):
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        rows = conn.execute(
            "SELECT k.column_name FROM information_schema.table_constraints c "
            "JOIN information_schema.key_column_usage k "
            "  ON k.constraint_schema = c.constraint_schema AND k.constraint_name = c.constraint_name "
            "WHERE c.table_schema = %s AND c.table_name = %s AND c.constraint_type = 'PRIMARY KEY' "
            "ORDER BY k.ordinal_position",
            (schema, table),
        ).fetchall()
    return [r[0] for r in rows]


def assert_three_tables(schema):
    assert {"hooks", "hook_versions", "messages"} <= tables(schema)


# ---------------------------------------------------------------- REQ-DB1-1, 2 (manual)


def test_REQ_DB1_1_compose_postgres_service_and_core_wiring():
    pytest.skip("manual: shell/Docker on host, see module docstring")


def test_REQ_DB1_2_postgres_password_from_shell_default_fitit():
    pytest.skip("manual: shell/Docker on host, see module docstring")


# ---------------------------------------------------------------- REQ-DB1-3


def test_REQ_DB1_3_init_creates_the_three_tables(empty_schema):
    schema, _ = empty_schema
    assert tables(schema) == set()
    db.init()
    assert_three_tables(schema)


@pytest.mark.parametrize("table", ["hooks", "hook_versions", "messages"])
def test_REQ_DB1_3_columns_types_and_nullability(empty_schema, table):
    schema, _ = empty_schema
    db.init()
    got = {name: (dtype, nullable) for name, (dtype, nullable, _) in columns(schema, table).items()}
    assert got == EXPECTED[table]


@pytest.mark.parametrize("table", ["hooks", "hook_versions", "messages"])
def test_REQ_DB1_3_primary_keys(empty_schema, table):
    schema, _ = empty_schema
    db.init()
    assert primary_key(schema, table) == PRIMARY_KEYS[table]


def test_REQ_DB1_3_messages_id_is_bigserial(empty_schema):
    schema, _ = empty_schema
    db.init()
    dtype, _, default = columns(schema, "messages")["id"]
    assert dtype == "bigint"
    assert default is not None and default.startswith("nextval(")


# ---------------------------------------------------------------- REQ-DB1-4


def test_REQ_DB1_4_second_init_raises_nothing_and_keeps_rows(database):
    with psycopg.connect(database) as conn:
        conn.execute(
            "INSERT INTO hooks (user_id, name, route, code, version) VALUES (%s, %s, %s, %s, %s)",
            ("u1", "h", "GET /api/invoices", "code", 1),
        )
        conn.execute(
            "INSERT INTO hook_versions (user_id, name, version, code) VALUES (%s, %s, %s, %s)",
            ("u1", "h", 1, "code"),
        )
        conn.execute(
            "INSERT INTO messages (user_id, turn) VALUES (%s, %s::jsonb)",
            ("u1", "[]"),
        )

    db.init()

    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT user_id, name, route, code, version FROM hooks").fetchall() == [
            ("u1", "h", "GET /api/invoices", "code", 1)
        ]
        assert conn.execute("SELECT user_id, name, version, code FROM hook_versions").fetchall() == [
            ("u1", "h", 1, "code")
        ]
        assert conn.execute("SELECT user_id, turn FROM messages").fetchall() == [("u1", [])]


# ---------------------------------------------------------------- REQ-DB1-5


def test_REQ_DB1_5_connect_reads_database_url_at_call_time(database, monkeypatch):
    base = database.split("?", 1)[0]
    other = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {other}")
    try:
        with db.connect() as conn:
            conn.execute("INSERT INTO messages (user_id, turn) VALUES ('first', '[]'::jsonb)")

        monkeypatch.setenv("DATABASE_URL", f"{base}?options=-csearch_path%3D{other}")
        db.init()
        with db.connect() as conn:
            assert conn.execute("SELECT user_id FROM messages").fetchall() == []

        monkeypatch.setenv("DATABASE_URL", database)
        with db.connect() as conn:
            assert conn.execute("SELECT user_id FROM messages").fetchall() == [("first",)]
    finally:
        with psycopg.connect(base, autocommit=True) as conn:
            conn.execute(f"DROP SCHEMA {other} CASCADE")


def test_REQ_DB1_5_with_block_commits_on_exit(database):
    with db.connect() as conn:
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('committed', '[]'::jsonb)")
    with psycopg.connect(database) as other:
        assert other.execute("SELECT user_id FROM messages").fetchall() == [("committed",)]


def test_REQ_DB1_5_connect_returns_a_new_connection_each_call(database):
    with db.connect() as a, db.connect() as b:
        assert a is not b
        assert isinstance(a, psycopg.Connection)


# ---------------------------------------------------------------- REQ-DB1-6


def test_REQ_DB1_6_core_startup_creates_the_three_tables(empty_schema, monkeypatch):
    schema, _ = empty_schema
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    import main

    assert tables(schema) == set()
    with TestClient(main.app):
        pass
    assert_three_tables(schema)
