"""Acceptance tests for AG4 (agent edits on a known version, chain dry run, extend the route's
hook), written from docs/plan/04-conversations/, docs/specs/agent.md v4, hook-contract.md v4,
trace-events.md and the ADRs only."""

import os
import subprocess
import sys
import uuid
from contextlib import ExitStack
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import db
import hooks
import trace
from agent import agent
from main import app

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
FINAL_TEXT = "Done."
USER = "gerente"
NAME = "report"


def virtual(value):
    return f'''ROUTE = "GET /x/report"

def run(data, ctx):
    return {{"by": "{value}"}}
'''


V1 = virtual("v1")
V2 = virtual("v2")
TEST_CODE = virtual("test")
AGENT_CODE = virtual("agent")
AGENT_CODE_2 = virtual("agent2")

ZZ_TOTAL = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    amount = data[0]["amount"]
    data[0]["marks"] = data[0].get("marks", []) + ["MARK_ZZ"]
    return data
'''

AA_WRAP = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    return {"rows": data}
'''

AA_MARK = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    data[0]["marks"] = data[0].get("marks", []) + ["MARK_AA"]
    return data
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
    """Returns a function that opens a core client signed in as `user`."""
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    with ExitStack() as stack:

        def _login(user=USER):
            client = stack.enter_context(TestClient(app))
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

    `before` maps a step index to a callable run inside the model function just before that
    step's response is returned: the "second writer" of the contracts' test environment.
    """

    def __init__(self, *steps, before=None):
        self.steps = list(steps)
        self.before = before or {}
        self.calls = []

    def __call__(self, messages, info):
        self.calls.append(list(messages))
        i = len(self.calls) - 1
        if i in self.before:
            self.before[i]()
        if i < len(self.steps):
            return ModelResponse(
                parts=[ToolCallPart(tool_name=n, args=a, tool_call_id=f"c{i}_{j}") for j, (n, a) in enumerate(self.steps[i])]
            )
        return ModelResponse(parts=[TextPart(FINAL_TEXT)])

    def tool_returns(self, tool_name, call=-1):
        """Contents of the parts carrying `tool_name`'s result back to the model in request `call`."""
        parts = [p for m in self.calls[call] for p in m.parts]
        return [str(p.content) for p in parts if getattr(p, "tool_name", None) == tool_name and p.part_kind != "tool-call"]


def chat(client, model, text="hello"):
    """Sends one message on a new connection; returns its events up to `done`."""
    got = []
    with agent.override(model=FunctionModel(model)):
        with client.websocket_connect("/fitit/ws") as ws:
            ws.send_json({"type": "message", "text": text})
            while True:
                event = ws.receive_json()
                got.append(event)
                if event["type"] == "done":
                    break
    return got


def of_type(turn, kind):
    return [e for e in turn if e["type"] == kind]


def sql(url, query, *params):
    with psycopg.connect(url) as conn:
        return conn.execute(query, params).fetchall()


def snapshot(url):
    return (
        sql(url, "SELECT user_id, name, route, code, enabled, version, updated_at FROM hooks ORDER BY user_id, name"),
        sql(url, "SELECT * FROM hook_versions ORDER BY 1, 2, 3"),
    )


def hook_row(url, name=NAME):
    return sql(url, "SELECT code, version FROM hooks WHERE user_id = %s AND name = %s", USER, name)


def read(name=NAME):
    return ("read_hook", {"name": name})


def write(code, name=NAME):
    return ("write_hook", {"name": name, "code": code})


def instructions(messages):
    return messages[-1].instructions or ""


# REQ-AG4-1


def test_req_ag4_1_conflict_after_read_then_reread_and_save(login, database):
    hooks.save_hook(USER, NAME, V1)
    seen = {}

    def other_conversation_saves():
        hooks.save_hook(USER, NAME, TEST_CODE)

    def after_rejection():
        seen["row"] = hook_row(database)
        seen["versions"] = sql(database, "SELECT version FROM hook_versions WHERE name = %s ORDER BY version", NAME)

    script = Script(
        [read()],
        [write(AGENT_CODE)],
        [read()],
        [write(AGENT_CODE)],
        before={1: other_conversation_saves, 2: after_rejection},
    )
    turn = chat(login(), script)

    rejected = of_type(turn, "hook_rejected")
    assert len(rejected) == 1, turn
    assert rejected[0]["name"] == NAME
    assert rejected[0]["reason"].startswith("conflict:"), rejected[0]
    assert seen["row"] == [(TEST_CODE, 2)]
    assert seen["versions"] == [(1,), (2,)]

    saved = of_type(turn, "hook_saved")
    assert len(saved) == 1 and saved[0]["name"] == NAME
    assert turn.index(rejected[0]) < turn.index(saved[0])
    assert hook_row(database) == [(AGENT_CODE, 3)]
    assert [e["type"] for e in turn[-2:]] == ["text", "done"]


# REQ-AG4-2


def _saved(user, name):
    hooks.save_hook(user, name, TEST_CODE)


def _undone(user, name):
    hooks.undo_hook(user, name)


def _deleted(user, name):
    hooks.delete_hook(user, name)


CHANGES = {
    # case: (codes saved before the message, change made by the test during the turn)
    "saved": ([V1], _saved),
    "undone": ([V1, V2], _undone),
    "deleted": ([V1], _deleted),
    "created": ([], _saved),
}


@pytest.mark.parametrize("case", list(CHANGES))
def test_req_ag4_2_change_by_other_since_message_start_conflicts(login, database, case):
    initial, change = CHANGES[case]
    for code in initial:
        hooks.save_hook(USER, NAME, code)
    state = {}

    def other_conversation_changes():
        change(USER, NAME)
        state["before"] = snapshot(database)

    script = Script([write(AGENT_CODE)], before={0: other_conversation_changes})
    turn = chat(login(), script)

    rejected = of_type(turn, "hook_rejected")
    assert len(rejected) == 1, turn
    assert rejected[0]["name"] == NAME
    assert rejected[0]["reason"].startswith("conflict:"), rejected[0]
    assert of_type(turn, "hook_saved") == []
    assert snapshot(database) == state["before"]


# REQ-AG4-3


def test_req_ag4_3_new_hook_saves(login, database):
    turn = chat(login(), Script([write(AGENT_CODE)]))
    assert of_type(turn, "hook_rejected") == []
    assert len(of_type(turn, "hook_saved")) == 1
    assert hook_row(database) == [(AGENT_CODE, 1)]


def test_req_ag4_3_edit_after_read_saves(login, database):
    hooks.save_hook(USER, NAME, V1)
    turn = chat(login(), Script([read()], [write(AGENT_CODE)]))
    assert of_type(turn, "hook_rejected") == []
    assert len(of_type(turn, "hook_saved")) == 1
    assert hook_row(database) == [(AGENT_CODE, 2)]


def test_req_ag4_3_edit_without_read_of_existing_hook_saves(login, database):
    hooks.save_hook(USER, NAME, V1)
    turn = chat(login(), Script([write(AGENT_CODE)]))
    assert of_type(turn, "hook_rejected") == []
    assert len(of_type(turn, "hook_saved")) == 1
    assert hook_row(database) == [(AGENT_CODE, 2)]


@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing"])
def test_req_ag4_3_two_writes_in_one_turn_both_save(login, database, existing):
    start = 0
    if existing:
        hooks.save_hook(USER, NAME, V1)
        start = 1
    turn = chat(login(), Script([write(AGENT_CODE)], [write(AGENT_CODE_2)]))
    assert of_type(turn, "hook_rejected") == []
    assert len(of_type(turn, "hook_saved")) == 2
    assert hook_row(database) == [(AGENT_CODE_2, start + 2)]
    versions = sql(database, "SELECT version FROM hook_versions WHERE name = %s ORDER BY version", NAME)
    assert versions == [(v,) for v in range(1, start + 3)]


# REQ-AG4-4


def test_req_ag4_4_three_conflicts_end_turn_with_error(login, database, events):
    hooks.save_hook(USER, NAME, V1)
    state = {}

    def other_conversation_saves():
        hooks.save_hook(USER, NAME, TEST_CODE)
        state["before"] = snapshot(database)

    script = Script(
        [write(AGENT_CODE)],
        [write(AGENT_CODE)],
        [write(AGENT_CODE)],
        before={0: other_conversation_saves},
    )
    turn = chat(login(), script)

    assert [e["type"] for e in turn[-2:]] == ["error", "done"], turn
    assert of_type(turn, "text") == []
    rejected = of_type(turn, "hook_rejected")
    assert len(rejected) == 3
    for e in rejected:
        assert e["name"] == NAME and e["reason"].startswith("conflict:")
    traced = [e for e in events if e["kind"] == "hook.rejected"]
    assert len(traced) == 3
    for e in traced:
        assert e["user"] == USER and e["hook"] == NAME and e["reason"].startswith("conflict:")
    assert of_type(turn, "hook_saved") == []
    assert snapshot(database) == state["before"]


# REQ-AG4-5


def test_req_ag4_5_candidate_breaking_later_hook_is_rejected_naming_it(login, database):
    hooks.save_hook(USER, "zz_total", ZZ_TOTAL)
    before = snapshot(database)

    turn = chat(login(), Script([write(AA_WRAP, "aa_wrap")]))

    rejected = of_type(turn, "hook_rejected")
    assert len(rejected) == 1, turn
    assert rejected[0]["name"] == "aa_wrap"
    assert "zz_total" in rejected[0]["reason"], rejected[0]
    assert of_type(turn, "hook_saved") == []
    assert snapshot(database) == before


def test_req_ag4_5_compatible_candidate_saved_and_preview_shows_chain_output(login, database):
    hooks.save_hook(USER, "zz_total", ZZ_TOTAL)

    script = Script([write(AA_MARK, "aa_mark")])
    turn = chat(login(), script)

    assert of_type(turn, "hook_rejected") == []
    saved = of_type(turn, "hook_saved")
    assert len(saved) == 1 and saved[0]["name"] == "aa_mark"
    assert hook_row(database, "aa_mark") == [(AA_MARK, 1)]

    (result,) = script.tool_returns("write_hook")
    assert "MARK_AA" in result and "MARK_ZZ" in result, result
    assert result.index("MARK_AA") < result.index("MARK_ZZ"), result


# REQ-AG4-6


@pytest.mark.parametrize("with_hooks", [False, True], ids=["no_hooks", "with_hooks"])
def test_req_ag4_6_instructions_tell_to_extend_route_hook(login, database, with_hooks):
    if with_hooks:
        hooks.save_hook(USER, "zz_total", ZZ_TOTAL)
    script = Script([("list_endpoints", {})], [("list_hooks", {})])
    chat(login(), script)
    assert len(script.calls) == 3
    for messages in script.calls:
        text = instructions(messages)
        assert "already runs on the same route" in text
        assert "read_hook" in text


# REQ-AG4-7


@pytest.mark.parametrize("name", ["test_ag1.py", "test_ag2.py", "test_ag3.py", "test_ch1.py", "test_cv1.py"])
def test_req_ag4_7_earlier_acceptance_suites_still_pass(name):
    path = Path(__file__).parent / name
    assert path.is_file(), f"{name} missing"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(path)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
