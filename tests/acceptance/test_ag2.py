"""Acceptance tests for AG2 (agent quality), written from docs/specs/agent.md v2,
hook-contract.md v2, proxy.md v2, storage.md v1, trace-events.md and the 02-postgres plan only."""

import json
import os
import subprocess
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path

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
import trace
from agent import agent

# Until DB1/HS2 land, psycopg, db and the new hooks exports are missing; keep the file collectable.
try:
    import psycopg

    import db
except ImportError:
    psycopg = db = None
import hooks
from main import app

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
USERS = ["gerente", "dr_garcia", "recepcion"]
FINAL_TEXT = "Done."

V1 = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        if datetime.fromisoformat(invoice["date"]).weekday() == 4:
            invoice["amount"] = round(invoice["amount"] * 0.85, 2)
    ctx.log("discounted Friday invoices")
    return data
'''

V2 = V1.replace("0.85", "0.80")
V1_LINE = '            invoice["amount"] = round(invoice["amount"] * 0.85, 2)'
V2_LINE = '            invoice["amount"] = round(invoice["amount"] * 0.80, 2)'

VIRTUAL = '''ROUTE = "GET /x/report"

def run(data, ctx):
    return {"total": 3}
'''

# Pass the guard, but raise in the dry run (app route with live data / virtual route with data None).
RAISES_APP = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    return data[0]["no_such_field_anywhere"]
'''

RAISES_VIRTUAL = '''ROUTE = "GET /x/boom"

def run(data, ctx):
    return data["anything"]
'''


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
def login(database, monkeypatch):
    """Returns a function that opens a core client, signed in as `user` (or anonymous)."""
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    with ExitStack() as stack:

        def _login(user=None):
            client = stack.enter_context(TestClient(app))
            if user:
                assert client.get(f"/login?user={user}").status_code < 400
            return client

        yield _login


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **fields: recorded.append({"kind": kind, **fields}))
    return recorded


class Script:
    """A FunctionModel function: plays `steps` (lists of (tool, args)), then answers FINAL_TEXT.

    Records every message list it receives in `calls`.
    """

    def __init__(self, *steps):
        self.steps = list(steps)
        self.calls = []

    def __call__(self, messages, info):
        self.calls.append(list(messages))
        i = len(self.calls) - 1
        if i < len(self.steps):
            return ModelResponse(
                parts=[ToolCallPart(tool_name=n, args=a, tool_call_id=f"c{i}_{j}") for j, (n, a) in enumerate(self.steps[i])]
            )
        return ModelResponse(parts=[TextPart(FINAL_TEXT)])

    def tool_results(self, tool_name):
        """Parts carrying a tool's result or retry back to the model, in the last call."""
        parts = [p for m in self.calls[-1] for p in m.parts]
        return [p for p in parts if getattr(p, "tool_name", None) == tool_name and p.part_kind != "tool-call"]


def chat(client, model, *texts):
    """Sends each text on one new connection; returns one event list per message."""
    turns = []
    with agent.override(model=FunctionModel(model)):
        with client.websocket_connect("/fitit/ws") as ws:
            for text in texts or ("hello",):
                ws.send_json({"type": "message", "text": text})
                got = []
                while True:
                    event = ws.receive_json()
                    got.append(event)
                    if event["type"] == "done":
                        break
                turns.append(got)
    return turns


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def sql(url, query, *params):
    with psycopg.connect(url) as conn:
        return conn.execute(query, params).fetchall()


def instructions(messages):
    return messages[-1].instructions or ""


def user_prompts(messages):
    return [p.content for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart)]


def seed_turn(url, user, question, answer):
    turn = ModelMessagesTypeAdapter.dump_python(
        [ModelRequest(parts=[UserPromptPart(question)]), ModelResponse(parts=[TextPart(answer)])], mode="json"
    )
    with psycopg.connect(url) as conn:
        conn.execute("INSERT INTO messages (user_id, turn) VALUES (%s, %s::jsonb)", (user, json.dumps(turn)))


def is_retry(part):
    return part.part_kind == "retry-prompt" or getattr(part, "outcome", None) == "retried"


# REQ-AG2-1


@pytest.mark.parametrize("user", USERS)
def test_req_ag2_1_write_hook_writes_rows_only_for_connected_user(login, database, user):
    (turn,) = chat(login(user), Script([("write_hook", {"name": "friday_discount", "code": V1})]))
    assert len(of_type(turn, "hook_saved")) == 1

    assert sql(database, "SELECT user_id, name FROM hooks") == [(user, "friday_discount")]
    assert sql(database, "SELECT user_id, name, version FROM hook_versions") == [(user, "friday_discount", 1)]


# REQ-AG2-2


@pytest.mark.parametrize("code", [RAISES_APP, RAISES_VIRTUAL], ids=["app_route", "virtual_route"])
@pytest.mark.parametrize("user", USERS)
def test_req_ag2_2_dry_run_failure_writes_nothing_for_any_user(login, database, user, code):
    (turn,) = chat(login(user), Script([("write_hook", {"name": "broken", "code": code})]))

    rejected = of_type(turn, "hook_rejected")
    assert len(rejected) == 1 and rejected[0]["name"] == "broken" and rejected[0]["reason"]
    assert of_type(turn, "hook_saved") == []
    assert sql(database, "SELECT * FROM hooks") == []
    assert sql(database, "SELECT * FROM hook_versions") == []


# REQ-AG2-3


def test_req_ag2_3_write_hook_with_existing_name_edits_it(login, database):
    hooks.save_hook("gerente", "friday_discount", V1)

    (turn,) = chat(login("gerente"), Script([("write_hook", {"name": "friday_discount", "code": V2})]))

    assert sql(database, "SELECT name, version, code FROM hooks WHERE user_id = 'gerente'") == [("friday_discount", 2, V2)]
    assert sql(database, "SELECT version FROM hook_versions WHERE user_id = 'gerente' ORDER BY version") == [(1,), (2,)]
    saved = of_type(turn, "hook_saved")
    assert len(saved) == 1 and saved[0]["name"] == "friday_discount"
    lines = saved[0]["diff"].splitlines()
    assert "-" + V1_LINE in lines
    assert "+" + V2_LINE in lines


def test_req_ag2_3_two_writes_through_agent_give_version_2(login, database):
    client = login("gerente")
    chat(client, Script([("write_hook", {"name": "friday_discount", "code": V1})]))
    (turn,) = chat(client, Script([("write_hook", {"name": "friday_discount", "code": V2})]))

    assert sql(database, "SELECT name, version FROM hooks") == [("friday_discount", 2)]
    lines = of_type(turn, "hook_saved")[0]["diff"].splitlines()
    assert "-" + V1_LINE in lines and "+" + V2_LINE in lines


# REQ-AG2-4


def test_req_ag2_4_no_hooks_yet_in_every_request(login):
    script = Script([("list_endpoints", {})], [("list_hooks", {})])
    chat(login("gerente"), script)
    assert len(script.calls) == 3
    for messages in script.calls:
        assert "no hooks yet" in instructions(messages)


def test_req_ag2_4_current_hooks_name_and_route_in_every_request(login):
    hooks.save_hook("gerente", "friday_discount", V1)
    hooks.save_hook("gerente", "report", VIRTUAL)
    hooks.toggle_hook("gerente", "report")  # disabled hooks are still current hooks

    script = Script([("list_endpoints", {})], [("list_hooks", {})])
    chat(login("gerente"), script)
    assert len(script.calls) == 3
    for messages in script.calls:
        text = instructions(messages)
        assert "friday_discount" in text and "GET /api/invoices" in text
        assert "report" in text and "GET /x/report" in text
        assert "no hooks yet" not in text


def test_req_ag2_4_hook_saved_in_one_message_listed_in_the_next(login):
    client = login("gerente")
    first = Script([("write_hook", {"name": "friday_discount", "code": V1})])
    chat(client, first)
    assert "no hooks yet" in instructions(first.calls[0])

    second = Script()
    chat(client, second)
    text = instructions(second.calls[0])
    assert "friday_discount" in text and "GET /api/invoices" in text
    assert "no hooks yet" not in text


# REQ-AG2-5


def test_req_ag2_5_undo_restores_previous_version(login, database, events):
    hooks.save_hook("gerente", "friday_discount", V1)
    hooks.save_hook("gerente", "friday_discount", V2)

    script = Script([("undo_hook", {"name": "friday_discount"})])
    (turn,) = chat(login("gerente"), script)

    saved = of_type(turn, "hook_saved")
    assert len(saved) == 1
    assert saved[0]["name"] == "friday_discount"
    assert saved[0]["route"] == "GET /api/invoices"
    lines = saved[0]["diff"].splitlines()
    assert "-" + V2_LINE in lines and "+" + V1_LINE in lines

    assert sql(database, "SELECT code, version FROM hooks WHERE user_id = 'gerente'") == [(V1, 1)]
    assert hooks.read_hook("gerente", "friday_discount") == V1

    restored = [e for e in events if e["kind"] == "hook.restored"]
    assert len(restored) == 1
    assert restored[0]["user"] == "gerente"
    assert restored[0]["hook"] == "friday_discount"
    assert restored[0]["version"] == 1


def test_req_ag2_5_undo_with_one_version_is_nothing_to_undo(login, database, events):
    hooks.save_hook("gerente", "friday_discount", V1)
    before = sql(database, "SELECT code, version, enabled, updated_at FROM hooks")

    script = Script([("undo_hook", {"name": "friday_discount"})])
    (turn,) = chat(login("gerente"), script)

    results = script.tool_results("undo_hook")
    assert len(results) == 1 and not is_retry(results[0])
    assert "nothing to undo" in str(results[0].content)

    assert of_type(turn, "hook_saved") == []
    assert [e for e in events if e["kind"] == "hook.restored"] == []
    assert sql(database, "SELECT code, version, enabled, updated_at FROM hooks") == before
    assert sql(database, "SELECT version FROM hook_versions") == [(1,)]


# REQ-AG2-6


def test_req_ag2_6_delete_history_anonymous_is_401(login, database):
    seed_turn(database, "gerente", "remember PINEAPPLE", "ok PINEAPPLE")
    r = login().delete("/fitit/history")
    assert r.status_code == 401
    assert r.json() == {"error": "unauthenticated"}
    assert len(sql(database, "SELECT id FROM messages WHERE user_id = 'gerente'")) == 1


def test_req_ag2_6_delete_history_clears_only_that_user(login, database):
    seed_turn(database, "gerente", "remember PINEAPPLE", "ok PINEAPPLE")
    seed_turn(database, "gerente", "remember MANGO", "ok MANGO")
    seed_turn(database, "dr_garcia", "remember KIWI", "ok KIWI")

    client = login("gerente")
    r = client.delete("/fitit/history")
    assert r.status_code == 204

    assert sql(database, "SELECT id FROM messages WHERE user_id = 'gerente'") == []
    assert len(sql(database, "SELECT id FROM messages WHERE user_id = 'dr_garcia'")) == 1

    script = Script()
    chat(client, script, "what did I ask you to remember?")
    first = script.calls[0]
    assert user_prompts(first) == ["what did I ask you to remember?"]
    assert [m for m in first if isinstance(m, ModelResponse)] == []
    assert "PINEAPPLE" not in repr(first) and "MANGO" not in repr(first)


# REQ-AG2-7


def test_req_ag2_7_rate_limit_per_user_across_connections(login, database, monkeypatch):
    agent_module.reset_rate_limits()
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "2")
    gerente = login("gerente")
    script = Script()

    for turn in chat(gerente, script, "one", "two"):
        assert [e["text"] for e in of_type(turn, "text")] == [FINAL_TEXT]
    assert len(script.calls) == 2
    assert len(sql(database, "SELECT id FROM messages WHERE user_id = 'gerente'")) == 2

    (blocked,) = chat(gerente, script, "three")  # new connection
    assert [e["type"] for e in blocked] == ["error", "done"]
    assert "rate limit" in blocked[0]["message"].lower()
    assert len(script.calls) == 2
    assert len(sql(database, "SELECT id FROM messages WHERE user_id = 'gerente'")) == 2

    (other,) = chat(login("dr_garcia"), script, "hello")
    assert [e["text"] for e in of_type(other, "text")] == [FINAL_TEXT]
    assert len(script.calls) == 3
    agent_module.reset_rate_limits()


# REQ-AG2-8


def test_req_ag2_8_agent_answer_carries_integer_tokens(login, events):
    chat(login("gerente"), Script([("list_endpoints", {})]), "first", "second")
    answers = [e for e in events if e["kind"] == "agent.answer"]
    assert len(answers) == 2
    for e in answers:
        assert type(e.get("input_tokens")) is int
        assert type(e.get("output_tokens")) is int


# REQ-AG2-9


@pytest.mark.parametrize("name", ["test_ag1.py", "test_ch1.py"])
def test_req_ag2_9_ag1_and_ch1_acceptance_still_pass(name):
    path = Path(__file__).parent / name
    assert path.is_file(), f"{name} missing"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(path)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
