"""Acceptance tests for AG3 (marketplace tools and instructions in the agent), written from
docs/plan/03-marketplace/, docs/specs/agent.md v3, marketplace.md v1, hook-contract.md v3,
storage.md v2 and trace-events.md only."""

import os
import re
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

# Until MK1 lands, marketplace is missing; keep the file collectable.
try:
    import marketplace
except ImportError:
    marketplace = None

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")
FINAL_TEXT = "Done."

REPORT = '''ROUTE = "GET /x/report"

def run(data, ctx):
    return {"total": 3}
'''
REPORT_LINE = '    return {"total": 3}'
REPORT_V2 = REPORT.replace("3", "4")

STATS = '''ROUTE = "GET /x/stats"

def run(data, ctx):
    return {"count": 7}
'''

INVOICES = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    return data
'''

# Passes the guard; raises in any dry run (virtual route, data is None).
BOOM = '''ROUTE = "GET /x/boom"

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
    """Returns a function that opens a core client signed in as `user`."""
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "100000")
    with ExitStack() as stack:

        def _login(user):
            client = stack.enter_context(TestClient(app))
            assert client.get(f"/login?user={user}").status_code < 400
            return client

        yield _login


@pytest.fixture
def events(monkeypatch):
    recorded = []
    monkeypatch.setattr(trace, "emit", lambda kind, **fields: recorded.append({"kind": kind, **fields}))
    return recorded


class FakeApi:
    def __init__(self):
        self.calls = []

    def get(self, path):
        self.calls.append(path)
        return []


class Script:
    """A FunctionModel function: plays `steps` (lists of (tool, args)), then answers FINAL_TEXT."""

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

    def tool_result(self, tool_name):
        """The single tool return of `tool_name`, as text, from the last request."""
        parts = [p for m in self.calls[-1] for p in m.parts]
        found = [p for p in parts if getattr(p, "tool_name", None) == tool_name and p.part_kind == "tool-return"]
        assert len(found) == 1, f"expected one tool return for {tool_name}, got {parts!r}"
        return str(found[0].content)


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


def run_tool(client, tool, args):
    script = Script([(tool, args)])
    turn = chat(client, script)
    return turn, script.tool_result(tool)


def of_type(turn, kind):
    return [e for e in turn if e["type"] == kind]


def changed(turn):
    return of_type(turn, "feature_changed")


def sql(url, query, *params):
    with psycopg.connect(url) as conn:
        return conn.execute(query, params).fetchall()


def seed(owner, name, code, title, description="A feature."):
    hooks.save_hook(owner, name, code)
    return marketplace.publish(owner, name, title, description)["id"]


def has_id(text, fid):
    return re.search(rf"@{fid}(?!\d)", text) is not None


def line_for(text, fid):
    lines = [line for line in text.splitlines() if has_id(line, fid)]
    assert len(lines) == 1, f"expected one line with @{fid} in {text!r}"
    return lines[0]


def instructions(messages):
    return messages[-1].instructions or ""


# REQ-AG3-1


def test_req_ag3_1_search_lists_published_features_with_flags(login, database):
    plain = seed("gerente", "report", REPORT, "Monthly report")
    subscribed = seed("gerente", "stats", STATS, "Patient stats")
    mine = seed("dr_garcia", "invoices", INVOICES, "Invoice passthrough")
    hidden = seed("gerente", "boom", BOOM, "Hidden boom")
    marketplace.unpublish("gerente", hidden)
    marketplace.subscribe("dr_garcia", subscribed, FakeApi())

    turn, text = run_tool(login("dr_garcia"), "search_features", {"query": ""})

    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 3, text
    assert not has_id(text, hidden) and "Hidden boom" not in text

    line = line_for(text, plain)
    assert "Monthly report" in line and "GET /x/report" in line
    assert "[mine]" not in line and "[subscribed]" not in line

    line = line_for(text, subscribed)
    assert "Patient stats" in line and "GET /x/stats" in line
    assert line.rstrip().endswith("[subscribed]") and "[mine]" not in line

    line = line_for(text, mine)
    assert "Invoice passthrough" in line and "GET /api/invoices" in line
    assert line.rstrip().endswith("[mine]") and "[subscribed]" not in line
    assert changed(turn) == []


def test_req_ag3_1_search_query_filters(login, database):
    report = seed("gerente", "report", REPORT, "Monthly report")
    seed("gerente", "stats", STATS, "Patient stats")

    _, text = run_tool(login("dr_garcia"), "search_features", {"query": "MONTHLY"})
    lines = [line for line in text.splitlines() if line.strip()]
    assert len(lines) == 1 and has_id(lines[0], report), text


def test_req_ag3_1_search_no_match(login, database):
    seed("gerente", "report", REPORT, "Monthly report")
    _, text = run_tool(login("dr_garcia"), "search_features", {"query": "zzqqxx"})
    assert "no features found" in text


def test_req_ag3_1_search_empty_marketplace(login, database):
    _, text = run_tool(login("dr_garcia"), "search_features", {"query": ""})
    assert "no features found" in text


# REQ-AG3-2


def test_req_ag3_2_publish_hook_creates_feature(login, database):
    hooks.save_hook("gerente", "report", REPORT)

    turn, _ = run_tool(
        login("gerente"), "publish_hook", {"name": "report", "title": "Monthly report", "description": "Totals."}
    )

    rows = sql(database, "SELECT id, owner_id, hook_name, title, description, version, published FROM features")
    assert len(rows) == 1
    fid, owner, hook, title, description, version, published = rows[0]
    assert (owner, hook, title, description, version, published) == ("gerente", "report", "Monthly report", "Totals.", 1, True)

    assert changed(turn) == [{"type": "feature_changed", "feature_id": fid, "action": "published"}]
    assert turn.index(changed(turn)[0]) < len(turn) - 1
    assert turn[-1]["type"] == "done"


def test_req_ag3_2_publish_missing_hook_is_not_found(login, database):
    turn, text = run_tool(
        login("gerente"), "publish_hook", {"name": "nope", "title": "Monthly report", "description": "Totals."}
    )
    assert text.startswith("not_found:"), text
    assert sql(database, "SELECT * FROM features") == []
    assert changed(turn) == []


# REQ-AG3-3


def test_req_ag3_3_subscribe_only_for_connected_user(login, database):
    fid = seed("gerente", "report", REPORT, "Monthly report")

    turn, _ = run_tool(login("recepcion"), "subscribe_feature", {"feature_id": fid})

    assert sql(database, "SELECT user_id, feature_id, version, enabled FROM subscriptions") == [("recepcion", fid, 1, True)]
    assert changed(turn) == [{"type": "feature_changed", "feature_id": fid, "action": "subscribed"}]


def test_req_ag3_3_subscribe_dry_run_failure(login, database):
    fid = seed("gerente", "boom", BOOM, "Boom")

    turn, text = run_tool(login("recepcion"), "subscribe_feature", {"feature_id": fid})

    assert "dry_run_failed" in text
    assert sql(database, "SELECT * FROM subscriptions") == []
    assert changed(turn) == []
    assert [e["type"] for e in turn[-2:]] == ["text", "done"]
    assert of_type(turn, "error") == []


# REQ-AG3-4


def test_req_ag3_4_unsubscribe(login, database):
    fid = seed("gerente", "report", REPORT, "Monthly report")
    marketplace.subscribe("dr_garcia", fid, FakeApi())

    turn, _ = run_tool(login("dr_garcia"), "unsubscribe_feature", {"feature_id": fid})

    assert sql(database, "SELECT * FROM subscriptions") == []
    assert changed(turn) == [{"type": "feature_changed", "feature_id": fid, "action": "unsubscribed"}]


def test_req_ag3_4_accept_update(login, database):
    fid = seed("gerente", "report", REPORT, "Monthly report")
    marketplace.subscribe("dr_garcia", fid, FakeApi())
    hooks.save_hook("gerente", "report", REPORT_V2)
    assert marketplace.publish("gerente", "report", "Monthly report", "A feature.")["version"] == 2

    turn, _ = run_tool(login("dr_garcia"), "accept_update", {"feature_id": fid})

    assert sql(database, "SELECT user_id, version FROM subscriptions WHERE feature_id = %s", fid) == [("dr_garcia", 2)]
    assert changed(turn) == [{"type": "feature_changed", "feature_id": fid, "action": "updated"}]


# REQ-AG3-5


def test_req_ag3_5_fork_copies_pinned_code(login, database):
    fid = seed("gerente", "report", REPORT, "Monthly report")
    marketplace.subscribe("dr_garcia", fid, FakeApi())
    hooks.save_hook("gerente", "report", REPORT_V2)
    marketplace.publish("gerente", "report", "Monthly report", "A feature.")

    turn, _ = run_tool(login("dr_garcia"), "fork_feature", {"feature_id": fid, "name": "my_report"})

    assert sql(database, "SELECT name, code FROM hooks WHERE user_id = 'dr_garcia'") == [("my_report", REPORT)]
    assert sql(database, "SELECT * FROM subscriptions") == []

    saved = of_type(turn, "hook_saved")
    assert len(saved) == 1
    assert saved[0]["name"] == "my_report" and saved[0]["route"] == "GET /x/report"
    assert "+" + REPORT_LINE in saved[0]["diff"].splitlines()
    assert changed(turn) == [{"type": "feature_changed", "feature_id": fid, "action": "forked"}]
    assert turn.index(saved[0]) < turn.index(changed(turn)[0])


# REQ-AG3-6


def test_req_ag3_6_unpublish_by_owner(login, database):
    fid = seed("gerente", "report", REPORT, "Monthly report")

    turn, _ = run_tool(login("gerente"), "unpublish_feature", {"feature_id": fid})

    assert sql(database, "SELECT published FROM features WHERE id = %s", fid) == [(False,)]
    assert changed(turn) == [{"type": "feature_changed", "feature_id": fid, "action": "unpublished"}]


def test_req_ag3_6_unpublish_by_other_user_is_forbidden(login, database):
    fid = seed("gerente", "report", REPORT, "Monthly report")
    before = sql(database, "SELECT * FROM features")

    turn, text = run_tool(login("dr_garcia"), "unpublish_feature", {"feature_id": fid})

    assert text.startswith("forbidden:"), text
    assert sql(database, "SELECT * FROM features") == before
    assert changed(turn) == []


# REQ-AG3-7


TOOL_NAMES = ["search_features", "subscribe_feature", "publish_hook"]


def test_req_ag3_7_no_subscriptions_in_every_request(login, database):
    script = Script([("search_features", {"query": ""})], [("list_hooks", {})])
    chat(login("dr_garcia"), script)
    assert len(script.calls) == 3
    for messages in script.calls:
        text = instructions(messages)
        assert "no subscriptions" in text
        for name in TOOL_NAMES:
            assert name in text


def test_req_ag3_7_subscriptions_listed_in_every_request(login, database):
    report = seed("gerente", "report", REPORT, "Monthly report")
    stats = seed("gerente", "stats", STATS, "Patient stats")
    marketplace.subscribe("dr_garcia", report, FakeApi())
    marketplace.subscribe("dr_garcia", stats, FakeApi())
    marketplace.toggle("dr_garcia", stats)  # disabled subscriptions are still subscriptions
    hooks.save_hook("gerente", "report", REPORT_V2)
    marketplace.publish("gerente", "report", "Monthly report", "A feature.")

    script = Script([("search_features", {"query": ""})], [("list_hooks", {})])
    chat(login("dr_garcia"), script)
    assert len(script.calls) == 3
    for messages in script.calls:
        text = instructions(messages)
        assert "no subscriptions" not in text
        line = line_for(text, report)
        assert "Monthly report" in line and "update available" in line
        line = line_for(text, stats)
        assert "Patient stats" in line and "update available" not in line
        for name in TOOL_NAMES:
            assert name in text


# REQ-AG3-8


def _setup_published(database):
    hooks.save_hook("gerente", "report", REPORT)
    return None


def _setup_feature(database):
    return seed("gerente", "report", REPORT, "Monthly report")


def _setup_subscribed(database):
    fid = _setup_feature(database)
    marketplace.subscribe("dr_garcia", fid, FakeApi())
    return fid


def _setup_update(database):
    fid = _setup_subscribed(database)
    hooks.save_hook("gerente", "report", REPORT_V2)
    marketplace.publish("gerente", "report", "Monthly report", "A feature.")
    return fid


CASES = {
    "publish_hook": ("gerente", _setup_published, "feature.published"),
    "unpublish_feature": ("gerente", _setup_feature, "feature.unpublished"),
    "subscribe_feature": ("dr_garcia", _setup_feature, "feature.subscribed"),
    "unsubscribe_feature": ("dr_garcia", _setup_subscribed, "feature.unsubscribed"),
    "accept_update": ("dr_garcia", _setup_update, "feature.updated"),
    "fork_feature": ("dr_garcia", _setup_update, "feature.forked"),
}


@pytest.mark.parametrize("tool", list(CASES))
def test_req_ag3_8_tool_emits_matching_event_once(login, database, events, tool):
    user, setup, kind = CASES[tool]
    fid = setup(database)
    if tool == "publish_hook":
        args = {"name": "report", "title": "Monthly report", "description": "Totals."}
    elif tool == "fork_feature":
        args = {"feature_id": fid, "name": "my_report"}
    else:
        args = {"feature_id": fid}
    client = login(user)
    events.clear()

    turn, _ = run_tool(client, tool, args)

    assert len(changed(turn)) == 1
    feature_events = [e for e in events if e["kind"].startswith("feature.")]
    assert [e["kind"] for e in feature_events] == [kind]
    assert feature_events[0]["user"] == user
    if fid is not None:
        assert feature_events[0]["feature_id"] == fid


# REQ-AG3-9


@pytest.mark.parametrize("name", ["test_ag1.py", "test_ag2.py", "test_ch1.py"])
def test_req_ag3_9_ag1_ag2_and_ch1_acceptance_still_pass(name):
    path = Path(__file__).parent / name
    assert path.is_file(), f"{name} missing"
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", str(path)], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
