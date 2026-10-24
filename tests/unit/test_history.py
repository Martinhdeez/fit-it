import os
import uuid

import psycopg
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from psycopg.types.json import Jsonb
from pydantic_ai import ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

import agent
import db
import trace


@pytest.fixture
def database(monkeypatch):
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{base}?options=-csearch_path%3D{schema}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    agent.reset_rate_limits()
    monkeypatch.setattr(trace, "emit", lambda kind, **f: None)
    db.init()
    yield url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


def recording(seen, fail=False):
    """A FunctionModel that records what it receives and echoes the last user text."""
    def fn(messages, info):
        seen.append(messages)
        if fail:
            raise RuntimeError("model down")
        return ModelResponse(parts=[TextPart(f"answer to {texts(messages)[-1]}")])
    return FunctionModel(fn)


def texts(messages):
    return [p.content for m in messages for p in m.parts if isinstance(p, (UserPromptPart, TextPart))]


def chat(model, user, *messages, between=None):
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await agent.handle_ws(websocket, user, None)

    turns = []
    with agent.agent.override(model=model), TestClient(app).websocket_connect("/ws") as ws:
        for i, text in enumerate(messages):
            if i and between:
                between()
            ws.send_json({"type": "message", "text": text})
            turn = []
            while not turn or turn[-1]["type"] != "done":
                turn.append(ws.receive_json())
            turns.append(turn)
    return turns


def rows(url, user):
    with psycopg.connect(url) as conn:
        return [t for (t,) in conn.execute("SELECT turn FROM messages WHERE user_id = %s ORDER BY id", (user,))]


def seed(url, user, n):
    with psycopg.connect(url) as conn:
        for i in range(n):
            turn = [ModelRequest(parts=[UserPromptPart(f"q{i}")]), ModelResponse(parts=[TextPart(f"a{i}")])]
            conn.execute("INSERT INTO messages (user_id, turn) VALUES (%s, %s)",
                         (user, Jsonb(ModelMessagesTypeAdapter.dump_python(turn, mode="json"))))


def test_completed_turn_is_one_row_without_the_prompt(database):
    [turn] = chat(recording([]), "alice", "hello")
    assert turn[-2:] == [{"type": "text", "text": "answer to hello"}, {"type": "done"}]
    [stored] = rows(database, "alice")
    assert texts(ModelMessagesTypeAdapter.validate_python(stored)) == ["hello", "answer to hello"]
    assert all(m.get("instructions") is None for m in stored)


def test_history_survives_reconnect_and_is_per_user(database):
    chat(recording([]), "alice", "hello")
    seen = []
    chat(recording(seen), "alice", "again")
    assert texts(seen[0]) == ["hello", "answer to hello", "again"]
    assert agent.SYSTEM_PROMPT.strip() in seen[0][-1].instructions
    seen = []
    chat(recording(seen), "bob", "hi")
    assert texts(seen[0]) == ["hi"]


def test_failed_turn_is_not_stored(database):
    [turn] = chat(recording([], fail=True), "alice", "hello")
    assert [e["type"] for e in turn] == ["error", "done"]
    assert rows(database, "alice") == []


def test_only_the_last_twenty_turns_are_loaded(database):
    seed(database, "alice", 25)
    seen = []
    chat(recording(seen), "alice", "now")
    assert texts(seen[0]) == [x for i in range(5, 25) for x in (f"q{i}", f"a{i}")] + ["now"]
    assert agent.SYSTEM_PROMPT.strip() in seen[0][-1].instructions


def test_history_is_read_at_every_message(database):
    seen = []
    chat(recording(seen), "alice", "one", "two", between=lambda: agent.clear_history("alice"))
    assert texts(seen[1]) == ["two"]


def test_clear_history_only_touches_that_user(database):
    seed(database, "alice", 2)
    seed(database, "bob", 1)
    agent.clear_history("alice")
    assert rows(database, "alice") == [] and len(rows(database, "bob")) == 1
