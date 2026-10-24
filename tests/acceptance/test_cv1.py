"""Acceptance tests for CV1 (parallel conversations), written from
docs/plan/04-conversations/requirements.md, contracts.md, docs/specs/agent.md v4,
proxy.md v4, storage.md v3, trace-events.md and ADR 0024 only."""

import asyncio
import json
import os
import subprocess
import sys
import threading
import uuid
from contextlib import ExitStack, contextmanager
from datetime import datetime
from pathlib import Path

import psycopg
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
from starlette.websockets import WebSocketDisconnect

import agent as agent_module
import db
import trace
from agent import agent
from main import app

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
HOLD = "hold this turn"
WAIT = 10  # seconds; safety net so a serial implementation fails instead of hanging


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
def empty_schema(monkeypatch):
    """Like `database`, without `db.init()`."""
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{base}?options=-csearch_path%3D{schema}"
    monkeypatch.setenv("DATABASE_URL", url)
    yield schema, url
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")


@pytest.fixture
def make_client(database, monkeypatch):
    """`make_client(user)` returns a TestClient on the core signed in as `user` (anonymous for None)."""
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    with ExitStack() as stack:

        def make(user=None):
            c = stack.enter_context(TestClient(app))
            if user:
                assert c.get(f"/login?user={user}").status_code < 400
            return c

        yield make


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **fields: recorded.append({"kind": kind, **fields}))
    return recorded


# ---------------------------------------------------------------- helpers


def user_prompts(messages):
    return [p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)]


def answers(messages):
    return [p.content for m in messages if isinstance(m, ModelResponse) for p in m.parts if isinstance(p, TextPart)]


class Script:
    """Records every message list; plays `steps` (lists of (tool, args)) then answers.

    A prompt containing `explode` raises, so its turn ends with `error`.
    A prompt equal to HOLD sets `started` and waits until `release` is set.
    """

    def __init__(self, steps=()):
        self.steps = list(steps)
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()

    def prompts(self):
        return [user_prompts(call)[-1] for call in self.calls]

    async def __call__(self, messages, info):
        self.calls.append(list(messages))
        prompt = user_prompts(messages)[-1]
        if "explode" in prompt:
            raise RuntimeError("model exploded")
        if prompt == HOLD:
            self.started.set()
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        i = len(self.calls) - 1
        if i < len(self.steps):
            return ModelResponse(
                parts=[ToolCallPart(tool_name=n, args=a, tool_call_id=f"c{i}_{j}") for j, (n, a) in enumerate(self.steps[i])]
            )
        return ModelResponse(parts=[TextPart(f"answer to {prompt}")])


def ws_url(conversation=None):
    return "/fitit/ws" if conversation is None else f"/fitit/ws?conversation={conversation}"


def until_done(ws):
    out = []
    while True:
        event = ws.receive_json()
        out.append(event)
        if event["type"] == "done":
            return out


def types(turn):
    return [e["type"] for e in turn]


def chat(client, script, conversation, *texts):
    """Sends each text on one new socket for `conversation`; returns one event list per text."""
    turns = []
    with agent.override(model=FunctionModel(script)):
        with client.websocket_connect(ws_url(conversation)) as ws:
            for text in texts:
                ws.send_json({"type": "message", "text": text})
                turns.append(until_done(ws))
    return turns


@contextmanager
def held(script):
    """Releases the held turn after WAIT seconds at the latest, and always on exit."""
    timer = threading.Timer(WAIT, script.release.set)
    timer.start()
    try:
        yield
    finally:
        script.release.set()
        timer.cancel()


def sql(url, query, params=()):
    with psycopg.connect(url) as conn:
        return conn.execute(query, params).fetchall()


def turn_json(question, answer):
    messages = [ModelRequest(parts=[UserPromptPart(question)]), ModelResponse(parts=[TextPart(answer)])]
    return ModelMessagesTypeAdapter.dump_python(messages, mode="json")


def seed(url, user, question, answer="seeded answer", conversation=None):
    with psycopg.connect(url) as conn:
        if conversation is None:
            conn.execute(
                "INSERT INTO messages (user_id, turn) VALUES (%s, %s::jsonb)",
                (user, json.dumps(turn_json(question, answer))),
            )
        else:
            conn.execute(
                "INSERT INTO messages (user_id, turn, conversation_id) VALUES (%s, %s::jsonb, %s)",
                (user, json.dumps(turn_json(question, answer)), conversation),
            )


def stored(url, user):
    """[(conversation_id, user prompts of the turn)] in id order."""
    rows = sql(url, "SELECT conversation_id, turn FROM messages WHERE user_id = %s ORDER BY id", (user,))
    out = []
    for conversation, turn in rows:
        turn = json.loads(turn) if isinstance(turn, str) else turn
        out.append((conversation, user_prompts(ModelMessagesTypeAdapter.validate_python(turn))))
    return out


def columns(schema, table):
    rows = sql(
        os.environ["DATABASE_URL"],
        "SELECT column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = %s AND table_name = %s",
        (schema, table),
    )
    return {name: (dtype, nullable == "YES", default) for name, dtype, nullable, default in rows}


# ---------------------------------------------------------------- REQ-CV1-1


def test_req_cv1_1_empty_schema_messages_has_exactly_five_columns(empty_schema):
    schema, _ = empty_schema
    db.init()
    cols = columns(schema, "messages")
    assert {name: (dtype, nullable) for name, (dtype, nullable, _) in cols.items()} == {
        "id": ("bigint", False),
        "user_id": ("text", False),
        "turn": ("jsonb", False),
        "created_at": ("timestamp with time zone", False),
        "conversation_id": ("text", False),
    }
    default = cols["conversation_id"][2]
    assert default is not None and default.startswith("'default'")


def test_req_cv1_1_v2_table_is_upgraded_and_rows_belong_to_default(empty_schema):
    schema, url = empty_schema
    with psycopg.connect(url) as conn:
        conn.execute(
            "CREATE TABLE messages (id bigserial PRIMARY KEY, user_id text NOT NULL, "
            "turn jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now())"
        )
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('gerente', '[]'::jsonb)")
        conn.execute("INSERT INTO messages (user_id, turn) VALUES ('dr_garcia', '[1]'::jsonb)")
    before = sql(url, "SELECT id, user_id, turn, created_at FROM messages ORDER BY id")

    db.init()

    cols = columns(schema, "messages")
    assert cols["conversation_id"][:2] == ("text", False)
    assert cols["conversation_id"][2].startswith("'default'")
    assert sql(url, "SELECT id, user_id, turn, created_at FROM messages ORDER BY id") == before
    assert sql(url, "SELECT conversation_id FROM messages ORDER BY id") == [("default",), ("default",)]

    db.init()

    assert sql(url, "SELECT id, user_id, turn, created_at, conversation_id FROM messages ORDER BY id") == [
        (*row, "default") for row in before
    ]


# ---------------------------------------------------------------- REQ-CV1-2


def test_req_cv1_2_socket_without_conversation_stores_default(make_client, database):
    gerente = make_client("gerente")
    (turn,) = chat(gerente, Script(), None, "no parameter here")
    assert types(turn)[-2:] == ["text", "done"]
    assert stored(database, "gerente") == [("default", ["no parameter here"])]


def test_req_cv1_2_rows_seeded_without_conversation_are_default_history(make_client, database):
    seed(database, "gerente", "old question one", "old answer one")
    seed(database, "gerente", "old question two", "old answer two")
    gerente = make_client("gerente")
    script = Script()
    chat(gerente, script, None, "new question")
    seen = script.calls[0]
    assert user_prompts(seen) == ["old question one", "old question two", "new question"]
    assert answers(seen) == ["old answer one", "old answer two"]


# ---------------------------------------------------------------- REQ-CV1-3


def test_req_cv1_3_history_is_last_20_of_that_conversation_only(make_client, database):
    for i in range(25):
        seed(database, "gerente", f"a question {i:02d}", f"a answer {i:02d}", conversation="a")
        if i % 5 == 0:
            seed(database, "gerente", f"b question {i:02d}", conversation="b")
            seed(database, "dr_garcia", f"other user a {i:02d}", conversation="a")
    seed(database, "gerente", "default question")

    gerente = make_client("gerente")
    script = Script()
    (turn,) = chat(gerente, script, "a", "newest on a")
    assert types(turn)[-2:] == ["text", "done"]

    seen = script.calls[0]
    assert user_prompts(seen) == [f"a question {i:02d}" for i in range(5, 25)] + ["newest on a"]
    assert answers(seen) == [f"a answer {i:02d}" for i in range(5, 25)]
    assert stored(database, "gerente")[-1] == ("a", ["newest on a"])


# ---------------------------------------------------------------- REQ-CV1-4


@pytest.mark.parametrize("value", ["a%20b", "%C3%BC", "", "a" * 65], ids=["space", "u_umlaut", "empty", "65_chars"])
def test_req_cv1_4_invalid_conversation_closes_with_4400(make_client, value):
    gerente = make_client("gerente")
    with agent.override(model=FunctionModel(Script())):
        with pytest.raises(WebSocketDisconnect) as exc:
            with gerente.websocket_connect(f"/fitit/ws?conversation={value}") as ws:
                ws.send_json({"type": "message", "text": "hi"})
                ws.receive_json()
    assert exc.value.code == 4400


def test_req_cv1_4_64_chars_of_allowed_set_accepted(make_client, database):
    conversation = ("Az09_-" * 11)[:64]
    assert len(conversation) == 64
    gerente = make_client("gerente")
    (turn,) = chat(gerente, Script(), conversation, "long id")
    assert types(turn)[-2:] == ["text", "done"]
    assert stored(database, "gerente") == [(conversation, ["long id"])]


@pytest.mark.parametrize("query", ["", "?conversation=a", "?conversation=a%20b", "?conversation="])
def test_req_cv1_4_anonymous_closes_with_4401_whatever_the_parameter(make_client, query):
    anonymous = make_client(None)
    with agent.override(model=FunctionModel(Script())):
        with pytest.raises(WebSocketDisconnect) as exc:
            with anonymous.websocket_connect(f"/fitit/ws{query}") as ws:
                ws.send_json({"type": "message", "text": "hi"})
                ws.receive_json()
    assert exc.value.code == 4401


# ---------------------------------------------------------------- REQ-CV1-5


def test_req_cv1_5_other_conversation_answers_while_a_is_held(make_client, database):
    gerente = make_client("gerente")
    script = Script()
    with held(script), agent.override(model=FunctionModel(script)):
        with gerente.websocket_connect(ws_url("a")) as ws_a, gerente.websocket_connect(ws_url("b")) as ws_b:
            ws_a.send_json({"type": "message", "text": HOLD})
            assert script.started.wait(WAIT)

            ws_b.send_json({"type": "message", "text": "quick on b"})
            turn_b = until_done(ws_b)
            released_before_b = script.release.is_set()
            script.release.set()
            turn_a = until_done(ws_a)

    assert types(turn_b)[-2:] == ["text", "done"]
    assert not released_before_b, "b was only answered after a's turn was released"
    assert types(turn_a)[-2:] == ["text", "done"]
    assert sorted(stored(database, "gerente")) == [("a", [HOLD]), ("b", ["quick on b"])]


# ---------------------------------------------------------------- REQ-CV1-6


def test_req_cv1_6_same_conversation_is_busy_and_not_counted(make_client, database, monkeypatch):
    agent_module.reset_rate_limits()
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "2")
    gerente = make_client("gerente")
    script = Script()
    try:
        with held(script), agent.override(model=FunctionModel(script)):
            with gerente.websocket_connect(ws_url("a")) as ws_1, gerente.websocket_connect(ws_url("a")) as ws_2:
                ws_1.send_json({"type": "message", "text": HOLD})
                assert script.started.wait(WAIT)

                ws_2.send_json({"type": "message", "text": "busy attempt"})
                busy = until_done(ws_2)
                released_before_busy = script.release.is_set()
                script.release.set()
                turn_1 = until_done(ws_1)

                # Second of 2 allowed messages: answered only if the busy one did not count.
                ws_2.send_json({"type": "message", "text": "after release"})
                after = until_done(ws_2)
    finally:
        agent_module.reset_rate_limits()

    assert not released_before_busy
    assert types(busy) == ["error", "done"]
    assert "busy" in busy[0]["message"].lower()
    assert "busy attempt" not in script.prompts()
    assert types(turn_1)[-2:] == ["text", "done"]
    assert types(after)[-2:] == ["text", "done"]
    assert "rate limit" not in json.dumps(after).lower()
    assert stored(database, "gerente") == [("a", [HOLD]), ("a", ["after release"])]


def test_req_cv1_6_other_users_same_conversation_not_affected(make_client, database):
    gerente = make_client("gerente")
    doctor = make_client("dr_garcia")
    script = Script()
    with held(script), agent.override(model=FunctionModel(script)):
        with gerente.websocket_connect(ws_url("a")) as ws_g, doctor.websocket_connect(ws_url("a")) as ws_d:
            ws_g.send_json({"type": "message", "text": HOLD})
            assert script.started.wait(WAIT)

            ws_d.send_json({"type": "message", "text": "doctor on a"})
            turn_d = until_done(ws_d)
            released_before_d = script.release.is_set()
            script.release.set()
            turn_g = until_done(ws_g)

    assert not released_before_d
    assert types(turn_d)[-2:] == ["text", "done"]
    assert types(turn_g)[-2:] == ["text", "done"]
    assert stored(database, "dr_garcia") == [("a", ["doctor on a"])]


# ---------------------------------------------------------------- REQ-CV1-7


def test_req_cv1_7_rate_limit_is_per_user_across_conversations(make_client, database, monkeypatch):
    agent_module.reset_rate_limits()
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "2")
    gerente = make_client("gerente")
    script = Script()
    try:
        (on_a,) = chat(gerente, script, "a", "one")
        (on_b,) = chat(gerente, script, "b", "two")
        (on_c,) = chat(gerente, script, "c", "three")
    finally:
        agent_module.reset_rate_limits()

    assert types(on_a)[-2:] == ["text", "done"]
    assert types(on_b)[-2:] == ["text", "done"]
    assert types(on_c) == ["error", "done"]
    assert "rate limit" in on_c[0]["message"].lower()
    assert "three" not in script.prompts()


# ---------------------------------------------------------------- REQ-CV1-8


def test_req_cv1_8_anonymous_is_401(make_client):
    assert make_client(None).get("/fitit/conversations").status_code == 401


def test_req_cv1_8_no_turns_is_empty_list(make_client):
    r = make_client("gerente").get("/fitit/conversations")
    assert r.status_code == 200
    assert r.json() == []


def test_req_cv1_8_lists_conversations_latest_first(make_client, database):
    seed(database, "dr_garcia", "doctor on a", conversation="a")
    seed(database, "dr_garcia", "doctor on z", conversation="z")
    gerente = make_client("gerente")
    script = Script()
    first_a = "Hide the DNI column on the patients page and also the phone column please"
    assert len(first_a) > 60

    (turn,) = chat(gerente, script, "c", "explode on c")
    assert types(turn) == ["error", "done"]
    chat(gerente, script, "a", first_a)
    chat(gerente, script, "b", "Only message on b")
    chat(gerente, script, "a", "Second message on a")

    r = gerente.get("/fitit/conversations")
    assert r.status_code == 200
    body = r.json()
    assert [c["id"] for c in body] == ["a", "b"]
    for c in body:
        assert set(c) == {"id", "title", "last_activity", "turns"}
    a, b = body
    assert a["title"] == first_a[:60]
    assert b["title"] == "Only message on b"
    assert (a["turns"], b["turns"]) == (2, 1)
    assert datetime.fromisoformat(a["last_activity"]) > datetime.fromisoformat(b["last_activity"])


# ---------------------------------------------------------------- REQ-CV1-9


def seed_for_delete(url):
    seed(url, "gerente", "g a", conversation="a")
    seed(url, "gerente", "g b", conversation="b")
    seed(url, "gerente", "g default")
    seed(url, "dr_garcia", "d a", conversation="a")


def all_rows(url):
    return sql(url, "SELECT id, user_id, conversation_id FROM messages ORDER BY id")


def test_req_cv1_9_delete_one_conversation(make_client, database):
    seed_for_delete(database)
    r = make_client("gerente").delete("/fitit/history?conversation=a")
    assert r.status_code == 204
    assert [(u, c) for _, u, c in all_rows(database)] == [
        ("gerente", "b"),
        ("gerente", "default"),
        ("dr_garcia", "a"),
    ]


def test_req_cv1_9_delete_without_parameter_clears_all_of_user(make_client, database):
    seed_for_delete(database)
    r = make_client("gerente").delete("/fitit/history")
    assert r.status_code == 204
    assert [(u, c) for _, u, c in all_rows(database)] == [("dr_garcia", "a")]


@pytest.mark.parametrize("value", ["", "a%20b", "a" * 65], ids=["empty", "space", "65_chars"])
def test_req_cv1_9_invalid_conversation_is_422_and_deletes_nothing(make_client, database, value):
    seed_for_delete(database)
    before = all_rows(database)
    r = make_client("gerente").delete(f"/fitit/history?conversation={value}")
    assert r.status_code == 422
    assert r.json()["error"] == "invalid"
    assert all_rows(database) == before


@pytest.mark.parametrize("query", ["", "?conversation=a"])
def test_req_cv1_9_anonymous_is_401(make_client, database, query):
    seed_for_delete(database)
    before = all_rows(database)
    assert make_client(None).delete(f"/fitit/history{query}").status_code == 401
    assert all_rows(database) == before


# ---------------------------------------------------------------- REQ-CV1-10


@pytest.mark.parametrize("conversation, expected", [("a", "a"), (None, "default")])
def test_req_cv1_10_agent_events_carry_conversation(make_client, events, conversation, expected):
    gerente = make_client("gerente")
    (turn,) = chat(gerente, Script([[("list_endpoints", {})]]), conversation, "list them")
    assert types(turn)[-2:] == ["text", "done"]
    agent_events = [e for e in events if e["kind"] in ("agent.message", "agent.tool", "agent.answer")]
    assert {e["kind"] for e in agent_events} == {"agent.message", "agent.tool", "agent.answer"}
    for e in agent_events:
        assert e.get("conversation") == expected, e


# ---------------------------------------------------------------- REQ-CV1-11


@pytest.mark.parametrize(
    "name", ["test_ch1.py", "test_ag1.py", "test_ag2.py", "test_ag3.py", "test_px1.py", "test_mk2.py", "test_db1.py"]
)
def test_req_cv1_11_earlier_suites_still_pass(name):
    path = Path(__file__).parent / name
    assert path.is_file(), f"{name} missing"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(path)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
