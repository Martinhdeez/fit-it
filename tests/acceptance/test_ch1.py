"""Acceptance tests for CH1 (persistent chat history), written from docs/specs/agent.md v2,
docs/specs/storage.md v1 and the 02-postgres plan only."""

import json
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelMessagesTypeAdapter,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import FunctionModel

import agent as agent_module
from agent import agent

# Until DB1 lands, psycopg and db are missing; keep the file collectable so tests fail, not error.
try:
    import psycopg

    import db
except ImportError:
    psycopg = db = None
from main import app

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
    yield url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def client(database, monkeypatch):
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    with TestClient(app) as c:
        yield c


def login(client, user):
    r = client.get(f"/login?user={user}")
    assert r.status_code < 400
    return client


class Model:
    """A FunctionModel function that records every message list it receives.

    Answers each call with `answer`, after playing `steps` (lists of (tool, args)) first.
    With `fail=True` it raises instead, so the turn ends with `error`.
    """

    def __init__(self, answer="Here is my answer.", steps=(), fail=False):
        self.answer = answer
        self.steps = list(steps)
        self.fail = fail
        self.calls = []

    def __call__(self, messages, info):
        self.calls.append(list(messages))
        if self.fail:
            raise RuntimeError("model exploded")
        i = len(self.calls) - 1
        if i < len(self.steps):
            return ModelResponse(
                parts=[ToolCallPart(tool_name=n, args=a, tool_call_id=f"c{i}_{j}") for j, (n, a) in enumerate(self.steps[i])]
            )
        return ModelResponse(parts=[TextPart(self.answer)])


def send(client, model, *texts):
    """Sends each text as a message on one new connection; returns one event list per message."""
    turns = []
    with agent.override(model=FunctionModel(model)):
        with client.websocket_connect("/fitit/ws") as ws:
            for text in texts:
                ws.send_json({"type": "message", "text": text})
                events = []
                while True:
                    event = ws.receive_json()
                    events.append(event)
                    if event["type"] == "done":
                        break
                turns.append(events)
    return turns


def user_prompts(messages):
    return [p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)]


def answers(messages):
    return [p.content for m in messages if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, TextPart)]


def rows(url, user):
    with psycopg.connect(url) as conn:
        return conn.execute("SELECT id, turn FROM messages WHERE user_id = %s ORDER BY id", (user,)).fetchall()


def turn_json(question, answer):
    messages = [ModelRequest(parts=[UserPromptPart(question)]), ModelResponse(parts=[TextPart(answer)])]
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def seed(url, user, turn):
    with psycopg.connect(url) as conn:
        conn.execute("INSERT INTO messages (user_id, turn) VALUES (%s, %s::jsonb)", (user, json.dumps(turn)))


def as_list(turn):
    return json.loads(turn) if isinstance(turn, str) else turn


# REQ-CH1-1


def test_req_ch1_1_text_turn_stores_one_row_with_text_and_answer(client, database):
    login(client, "gerente")
    model = Model(answer="Answer number one.")
    (events,) = send(client, model, "Remember the word pineapple")
    assert [e["type"] for e in events][-2:] == ["text", "done"]

    stored = rows(database, "gerente")
    assert len(stored) == 1
    messages = ModelMessagesTypeAdapter.validate_python(as_list(stored[0][1]))
    assert "Remember the word pineapple" in user_prompts(messages)
    assert "Answer number one." in answers(messages)


def test_req_ch1_1_turn_with_tool_calls_is_still_one_row(client, database):
    login(client, "gerente")
    model = Model(answer="Listed them.", steps=[[("list_endpoints", {})]])
    (events,) = send(client, model, "What endpoints are there?")
    assert events[-2]["type"] == "text"

    stored = rows(database, "gerente")
    assert len(stored) == 1
    messages = ModelMessagesTypeAdapter.validate_python(as_list(stored[0][1]))
    assert "What endpoints are there?" in user_prompts(messages)
    assert "Listed them." in answers(messages)


def test_req_ch1_1_each_text_message_adds_exactly_one_row(client, database):
    login(client, "gerente")
    send(client, Model(), "first")
    assert len(rows(database, "gerente")) == 1
    send(client, Model(), "second")
    assert len(rows(database, "gerente")) == 2


# REQ-CH1-2


def test_req_ch1_2_new_connection_sees_earlier_text_and_answer(client, database):
    login(client, "gerente")
    send(client, Model(answer="The secret is pineapple."), "My secret word is pineapple")

    model = Model(answer="You said pineapple.")
    send(client, model, "What was my secret word?")
    seen = model.calls[0]
    assert user_prompts(seen) == ["My secret word is pineapple", "What was my secret word?"]
    assert "The secret is pineapple." in answers(seen)


# REQ-CH1-3


def test_req_ch1_3_other_user_starts_with_no_history(client, database):
    login(client, "gerente")
    send(client, Model(answer="Gerente answer."), "Gerente private question")

    login(client, "dr_garcia")
    model = Model()
    send(client, model, "Doctor first question")
    seen = model.calls[0]
    assert user_prompts(seen) == ["Doctor first question"]
    assert answers(seen) == []
    assert not any(isinstance(m, ModelResponse) for m in seen)


# REQ-CH1-4


def test_req_ch1_4_error_turn_stores_nothing(client, database):
    login(client, "gerente")
    (events,) = send(client, Model(fail=True), "This will fail")
    assert [e["type"] for e in events][-2:] == ["error", "done"]
    assert rows(database, "gerente") == []


def test_req_ch1_4_error_turn_is_not_sent_to_the_model_later(client, database):
    login(client, "gerente")
    send(client, Model(fail=True), "This will fail")
    model = Model()
    send(client, model, "Try again")
    assert user_prompts(model.calls[0]) == ["Try again"]


# REQ-CH1-5


def test_req_ch1_5_only_20_most_recent_turns_in_order(client, database):
    for i in range(25):
        seed(database, "gerente", turn_json(f"seeded question {i:02d}", f"seeded answer {i:02d}"))

    login(client, "gerente")
    model = Model()
    send(client, model, "newest question")
    seen = model.calls[0]
    assert user_prompts(seen) == [f"seeded question {i:02d}" for i in range(5, 25)] + ["newest question"]
    assert answers(seen) == [f"seeded answer {i:02d}" for i in range(5, 25)]


# REQ-CH1-6


def test_req_ch1_6_rows_deleted_mid_connection_are_forgotten(client, database):
    login(client, "gerente")
    model = Model()
    with agent.override(model=FunctionModel(model)):
        with client.websocket_connect("/fitit/ws") as ws:
            ws.send_json({"type": "message", "text": "first on this connection"})
            while ws.receive_json()["type"] != "done":
                pass
            assert len(rows(database, "gerente")) == 1

            with psycopg.connect(database) as conn:
                conn.execute("DELETE FROM messages WHERE user_id = %s", ("gerente",))

            calls_before = len(model.calls)
            ws.send_json({"type": "message", "text": "second on this connection"})
            while ws.receive_json()["type"] != "done":
                pass

    seen = model.calls[calls_before]
    assert user_prompts(seen) == ["second on this connection"]
    assert not any(isinstance(m, ModelResponse) for m in seen)


# REQ-CH1-7


def test_req_ch1_7_clear_history_deletes_only_that_user(database):
    for i in range(3):
        seed(database, "gerente", turn_json(f"g{i}", f"ga{i}"))
    for i in range(2):
        seed(database, "dr_garcia", turn_json(f"d{i}", f"da{i}"))
    before = rows(database, "dr_garcia")

    assert agent_module.clear_history("gerente") is None

    assert rows(database, "gerente") == []
    assert rows(database, "dr_garcia") == before


def test_req_ch1_7_clear_history_for_user_without_rows_keeps_others(database):
    seed(database, "dr_garcia", turn_json("d", "da"))
    agent_module.clear_history("gerente")
    assert len(rows(database, "dr_garcia")) == 1
