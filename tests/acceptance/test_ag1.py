"""Acceptance tests for AG1 (coding agent), written from docs/specs/agent.md and the 01-mvp plan only."""

import os

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from starlette.websockets import WebSocketDisconnect

from agent import agent
from main import app

DEMO_URL = os.environ.get("DEMO_URL", "http://demo:8001")

VALID_CODE = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        if datetime.fromisoformat(invoice["date"]).weekday() == 4:
            invoice["amount"] = round(invoice["amount"] * 0.85, 2)
    ctx.log("discounted Friday invoices")
    return data
'''

GUARD_REJECTED_CODE = '''import os

ROUTE = "GET /api/invoices"

def run(data, ctx):
    return data
'''

# Passes the guard, but raises KeyError on the live /api/invoices list.
DRY_RUN_FAILING_CODE = '''ROUTE = "GET /api/invoices"

def run(data, ctx):
    return data[0]["no_such_field_anywhere"]
'''

FINAL_TEXT = "Done: Friday invoices now have a 15% discount."


@pytest.fixture
def workspaces(tmp_path, monkeypatch):
    monkeypatch.setenv("BASE_URL", DEMO_URL)
    monkeypatch.setenv("WORKSPACES", str(tmp_path))
    return tmp_path


@pytest.fixture
def client(workspaces):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def gerente(client):
    r = client.get("/login?user=gerente")
    assert r.status_code < 400
    return client


class Script:
    """A FunctionModel function that plays a list of responses, then answers with FINAL_TEXT.

    Each step is a list of (tool_name, args) tuples. Every call records the messages it received.
    """

    def __init__(self, *steps, max_calls=10):
        self.steps = list(steps)
        self.calls = []
        self.max_calls = max_calls

    def __call__(self, messages, info):
        self.calls.append(list(messages))
        i = len(self.calls) - 1
        if i < len(self.steps) and i < self.max_calls:
            return ModelResponse(
                parts=[ToolCallPart(tool_name=name, args=args, tool_call_id=f"call_{i}_{j}") for j, (name, args) in enumerate(self.steps[i])]
            )
        return ModelResponse(parts=[TextPart(FINAL_TEXT)])

    def parts(self):
        """All message parts seen by the last model call."""
        return [p for m in self.calls[-1] for p in m.parts] if self.calls else []


def tool_results(script, tool_name):
    """Parts that carry a tool's result or retry back to the model (not the call itself)."""
    return [p for p in script.parts() if getattr(p, "tool_name", None) == tool_name and p.part_kind != "tool-call"]


def is_retry(part):
    return part.part_kind == "retry-prompt" or getattr(part, "outcome", None) == "retried"


def chat(client, script, text="Apply a 15% discount to Friday invoices", messages=1):
    """Sends `messages` chat messages on one connection; returns one event list per message."""
    turns = []
    with agent.override(model=FunctionModel(script)):
        with client.websocket_connect("/fitit/ws") as ws:
            for _ in range(messages):
                ws.send_json({"type": "message", "text": text})
                events = []
                while True:
                    event = ws.receive_json()
                    events.append(event)
                    if event["type"] == "done":
                        break
                turns.append(events)
    return turns


def of_type(events, kind):
    return [e for e in events if e["type"] == kind]


def saved_files(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file())


# REQ-AG1-1


def test_req_ag1_1_anonymous_ws_closed_with_4401(client):
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/fitit/ws") as ws:
            ws.send_json({"type": "message", "text": "hi"})
            ws.receive_json()
    assert exc.value.code == 4401


# REQ-AG1-2


def test_req_ag1_2_plain_answer_is_one_text_then_one_done(gerente):
    (events,) = chat(gerente, Script())
    assert events[-1]["type"] == "done"
    assert len(of_type(events, "done")) == 1
    texts = of_type(events, "text")
    assert len(texts) == 1
    assert texts[0]["text"] == FINAL_TEXT


def test_req_ag1_2_each_message_ends_with_exactly_one_done(gerente):
    # A stray second `done` for the first message would end the second turn early, without its text.
    turns = chat(gerente, Script(), messages=2)
    for events in turns:
        assert events[-1]["type"] == "done"
        assert len(of_type(events, "done")) == 1
        assert [e["text"] for e in of_type(events, "text")] == [FINAL_TEXT]


def test_req_ag1_2_text_and_done_after_tool_calls(gerente):
    script = Script([("list_endpoints", {})], [("write_hook", {"name": "friday_discount", "code": VALID_CODE})])
    (events,) = chat(gerente, script)
    assert events[-1]["type"] == "done"
    assert len(of_type(events, "done")) == 1
    assert [e["text"] for e in of_type(events, "text")] == [FINAL_TEXT]
    assert events.index(of_type(events, "text")[0]) == len(events) - 2


# REQ-AG1-3


def test_req_ag1_3_tool_events_in_call_order_before_each_call(gerente):
    write_args = {"name": "friday_discount", "code": VALID_CODE}
    script = Script(
        [("list_endpoints", {})],
        [("get_endpoint", {"path": "/api/invoices"})],
        [("write_hook", write_args)],
    )
    (events,) = chat(gerente, script)
    tools = of_type(events, "tool")
    assert [e["name"] for e in tools] == ["list_endpoints", "get_endpoint", "write_hook"]
    for e in tools:
        assert "input" in e
    assert tools[1]["input"] == {"path": "/api/invoices"}
    assert tools[2]["input"] == write_args
    # The write_hook tool event comes before the result of that call.
    assert events.index(tools[2]) < events.index(of_type(events, "hook_saved")[0])
    assert events.index(tools[-1]) < events.index(of_type(events, "text")[0])


# REQ-AG1-4


def test_req_ag1_4_list_endpoints_lines_match_openapi(gerente):
    script = Script([("list_endpoints", {})])
    (events,) = chat(gerente, script)
    assert of_type(events, "tool")[0]["name"] == "list_endpoints"

    results = tool_results(script, "list_endpoints")
    assert len(results) == 1 and not is_retry(results[0])
    lines = [line for line in str(results[0].content).splitlines() if line.strip()]

    spec = httpx.get(f"{DEMO_URL}/openapi.json").json()
    expected = {
        f"{method.upper()} {path}: {op.get('summary', '')}".rstrip()
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}
    }
    assert len(lines) == len(expected)
    assert {line.rstrip() for line in lines} == expected

    invoices = [line for line in lines if line.startswith("GET /api/invoices: ")]
    assert len(invoices) == 1
    assert invoices[0].split(": ", 1)[1].strip()


# REQ-AG1-5


def test_req_ag1_5_valid_write_hook_saves_for_user_and_sends_hook_saved(gerente, workspaces):
    script = Script([("write_hook", {"name": "friday_discount", "code": VALID_CODE})])
    (events,) = chat(gerente, script)

    assert saved_files(workspaces) == ["gerente/friday_discount.py"]
    assert (workspaces / "gerente" / "friday_discount.py").read_text(encoding="utf-8") == VALID_CODE

    saved = of_type(events, "hook_saved")
    assert len(saved) == 1
    assert saved[0]["name"] == "friday_discount"
    assert saved[0]["route"] == "GET /api/invoices"
    diff = saved[0]["diff"]
    assert "@@" in diff
    assert '+ROUTE = "GET /api/invoices"' in diff.splitlines()
    assert of_type(events, "hook_rejected") == []

    results = tool_results(script, "write_hook")
    assert len(results) == 1 and not is_retry(results[0])
    assert "saved" in str(results[0].content)


def test_req_ag1_5_saved_hook_is_not_visible_to_another_user(gerente, workspaces):
    chat(gerente, Script([("write_hook", {"name": "friday_discount", "code": VALID_CODE})]))
    assert (workspaces / "gerente" / "friday_discount.py").is_file()
    assert not (workspaces / "dr_garcia").exists()
    assert not (workspaces / "recepcion").exists()


# REQ-AG1-6


def test_req_ag1_6_guard_rejection_saves_nothing_and_retries_with_reason(gerente, workspaces):
    script = Script([("write_hook", {"name": "bad_hook", "code": GUARD_REJECTED_CODE})])
    (events,) = chat(gerente, script)

    assert saved_files(workspaces) == []
    assert of_type(events, "hook_saved") == []
    rejected = of_type(events, "hook_rejected")
    assert len(rejected) == 1
    assert rejected[0]["name"] == "bad_hook"
    reason = rejected[0]["reason"]
    assert isinstance(reason, str) and reason

    # The model's next request carries the reason as a retry of write_hook.
    results = tool_results(script, "write_hook")
    assert len(results) == 1
    assert is_retry(results[0])
    assert reason in str(results[0].content)

    assert events[-1]["type"] == "done"
    assert [e["text"] for e in of_type(events, "text")] == [FINAL_TEXT]


def test_req_ag1_6_model_can_fix_code_after_rejection(gerente, workspaces):
    script = Script(
        [("write_hook", {"name": "friday_discount", "code": GUARD_REJECTED_CODE})],
        [("write_hook", {"name": "friday_discount", "code": VALID_CODE})],
    )
    (events,) = chat(gerente, script)
    kinds = [e["type"] for e in events if e["type"] in ("hook_rejected", "hook_saved")]
    assert kinds == ["hook_rejected", "hook_saved"]
    assert saved_files(workspaces) == ["gerente/friday_discount.py"]


# REQ-AG1-7


def test_req_ag1_7_dry_run_failure_saves_nothing_and_sends_hook_rejected(gerente, workspaces):
    script = Script([("write_hook", {"name": "broken_hook", "code": DRY_RUN_FAILING_CODE})])
    (events,) = chat(gerente, script)

    assert saved_files(workspaces) == []
    assert of_type(events, "hook_saved") == []
    rejected = of_type(events, "hook_rejected")
    assert len(rejected) == 1
    assert rejected[0]["name"] == "broken_hook"
    assert rejected[0]["reason"]

    results = tool_results(script, "write_hook")
    assert len(results) == 1 and is_retry(results[0])
    assert events[-1]["type"] == "done"


# REQ-AG1-8


def test_req_ag1_8_three_rejections_end_with_error_then_done(gerente, workspaces):
    bad = [("write_hook", {"name": "bad_hook", "code": GUARD_REJECTED_CODE})]
    script = Script(*[bad] * 10)
    (events,) = chat(gerente, script)

    assert saved_files(workspaces) == []
    assert of_type(events, "hook_saved") == []
    assert len(of_type(events, "hook_rejected")) == 3
    assert len([e for e in of_type(events, "tool") if e["name"] == "write_hook"]) == 3
    assert of_type(events, "text") == []
    assert [e["type"] for e in events[-2:]] == ["error", "done"]
    assert events[-2]["message"]
    assert len(of_type(events, "done")) == 1
    # The model is not asked again after the third rejection.
    assert len(script.calls) == 3


def test_req_ag1_8_mixed_rejections_count_together(gerente, workspaces):
    script = Script(
        [("write_hook", {"name": "bad_hook", "code": GUARD_REJECTED_CODE})],
        [("write_hook", {"name": "bad_hook", "code": DRY_RUN_FAILING_CODE})],
        [("write_hook", {"name": "bad_hook", "code": GUARD_REJECTED_CODE})],
        [("write_hook", {"name": "bad_hook", "code": VALID_CODE})],
    )
    (events,) = chat(gerente, script)
    assert saved_files(workspaces) == []
    assert len(of_type(events, "hook_rejected")) == 3
    assert [e["type"] for e in events[-2:]] == ["error", "done"]
