import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

import db
import main


def base_url() -> str:
    return os.environ["DATABASE_URL"].split("?")[0]


def new_schema() -> tuple[str, str]:
    base = base_url()
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    return schema, f"{base}?options=-csearch_path%3D{schema}"


def drop_schema(schema: str):
    with psycopg.connect(base_url(), autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def database(monkeypatch):
    schema, url = new_schema()
    monkeypatch.setenv("DATABASE_URL", url)
    db.init()
    yield schema
    drop_schema(schema)


def columns(schema: str) -> set[tuple]:
    with db.connect() as conn:
        return set(conn.execute(
            "SELECT table_name, column_name, data_type, is_nullable FROM information_schema.columns"
            " WHERE table_schema = %s AND table_name IN ('hooks', 'hook_versions', 'messages')", (schema,)).fetchall())


def primary_keys(schema: str) -> dict[str, list[str]]:
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT tc.table_name, kcu.column_name FROM information_schema.table_constraints tc"
            " JOIN information_schema.key_column_usage kcu"
            " ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema"
            " WHERE tc.table_schema = %s AND tc.constraint_type = 'PRIMARY KEY'"
            " AND tc.table_name IN ('hooks', 'hook_versions', 'messages')"
            " ORDER BY tc.table_name, kcu.ordinal_position", (schema,)).fetchall()
    keys = {}
    for table, column in rows:
        keys.setdefault(table, []).append(column)
    return keys


def test_init_creates_the_storage_schema(database):
    tz = "timestamp with time zone"
    assert columns(database) == {
        ("hooks", "user_id", "text", "NO"), ("hooks", "name", "text", "NO"),
        ("hooks", "route", "text", "NO"), ("hooks", "code", "text", "NO"),
        ("hooks", "enabled", "boolean", "NO"), ("hooks", "version", "integer", "NO"),
        ("hooks", "updated_at", tz, "NO"),
        ("hook_versions", "user_id", "text", "NO"), ("hook_versions", "name", "text", "NO"),
        ("hook_versions", "version", "integer", "NO"), ("hook_versions", "code", "text", "NO"),
        ("hook_versions", "saved_at", tz, "NO"),
        ("messages", "id", "bigint", "NO"), ("messages", "user_id", "text", "NO"),
        ("messages", "turn", "jsonb", "NO"), ("messages", "created_at", tz, "NO"),
    }
    assert primary_keys(database) == {
        "hooks": ["user_id", "name"],
        "hook_versions": ["user_id", "name", "version"],
        "messages": ["id"],
    }


def test_defaults(database):
    with db.connect() as conn:
        conn.execute("INSERT INTO hooks (user_id, name, route, code, version) VALUES ('u', 'h', 'GET /x', 'c', 1)")
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('u', '[]')")
        assert conn.execute("SELECT enabled, updated_at IS NOT NULL FROM hooks").fetchone() == (True, True)
        assert conn.execute("SELECT id, created_at IS NOT NULL FROM messages").fetchone() == (1, True)


def test_init_is_idempotent_and_keeps_rows(database):
    with db.connect() as conn:
        conn.execute("INSERT INTO hook_versions (user_id, name, version, code) VALUES ('u', 'h', 1, 'c')")
    db.init()
    with db.connect() as conn:
        assert conn.execute("SELECT code FROM hook_versions").fetchall() == [("c",)]


def test_connect_commits_on_success_and_rolls_back_on_error(database):
    with db.connect() as conn:
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('kept', '[]')")
    with pytest.raises(RuntimeError), db.connect() as conn:
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('lost', '[]')")
        raise RuntimeError
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        assert conn.execute("SELECT user_id FROM messages").fetchall() == [("kept",)]


def test_connect_reads_database_url_at_each_call(database, monkeypatch):
    with db.connect() as conn:
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('first', '[]')")
    other, url = new_schema()
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        db.init()
        with db.connect() as conn:
            assert conn.execute("SELECT count(*) FROM messages").fetchone() == (0,)
    finally:
        drop_schema(other)


def test_core_startup_creates_the_tables(monkeypatch):
    schema, url = new_schema()
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        with TestClient(main.app):
            pass
        with db.connect() as conn:
            tables = conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (schema,)).fetchall()
        assert {"hook_versions", "hooks", "messages"} <= {t for (t,) in tables}
    finally:
        drop_schema(schema)
