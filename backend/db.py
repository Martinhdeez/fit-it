"""Core storage on Postgres. Spec: docs/specs/storage.md."""
import os
from pathlib import Path

import psycopg

SCHEMA = Path(__file__).parent / "schema.sql"


def connect() -> psycopg.Connection:
    # ponytail: one connection per call, put psycopg_pool here if load needs it (ADR 0017)
    return psycopg.connect(os.environ.get("DATABASE_URL", "postgresql://fitit:fitit@postgres:5432/fitit"))


def init() -> None:
    with connect() as conn:
        conn.execute(SCHEMA.read_text("utf-8"))
