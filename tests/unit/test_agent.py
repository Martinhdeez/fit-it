import json
import os
import re
import urllib.request

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import agent
import hooks
import trace

GOOD = 'ROUTE = "GET /api/items"\n\ndef run(data, ctx):\n    ctx.log("hi")\n    return [x * 2 for x in data]\n'
BAD = 'import os\nROUTE = "GET /api/items"\n\ndef run(data, ctx):\n    return data\n'
CRASH = 'ROUTE = "GET /api/items"\n\ndef run(data, ctx):\n    return data["missing"]\n'


class Api:
    def get(self, path):
        if path == "/openapi.json":
            return {"paths": {"/api/items": {"get": {"summary": "List items"}, "post": {"summary": "Add item"}}}}
        if path == "/api/items":
            return [1, 2, 3]
        raise RuntimeError(f"404 {path}")


def scripted(*steps):
    """A FunctionModel that answers each model request with the next step: (tool, args) or a final text."""
    steps = list(steps)

    def fn(messages, info):
        step = steps.pop(0)
        if isinstance(step, str):
            return ModelResponse(parts=[TextPart(step)])
        return ModelResponse(parts=[ToolCallPart(step[0], step[1])])
    return FunctionModel(fn)


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACES", str(tmp_path))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    events = []
    monkeypatch.setattr(trace, "emit", lambda kind, **f: events.append((kind, f)))
    return events


def chat(model, *texts, api=None):
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await agent.handle_ws(websocket, "alice", api or Api())

    turns = []
    with agent.agent.override(model=model), TestClient(app).websocket_connect("/ws") as ws:
        for text in texts:
            ws.send_json({"type": "message", "text": text})
            turn = []
            while not turn or turn[-1]["type"] != "done":
                turn.append(ws.receive_json())
            turns.append(turn)
    return turns


def test_model_is_read_at_run_time(monkeypatch):
    assert agent.agent.model is None
    monkeypatch.setenv("FITIT_MODEL", "test")
    assert agent.model_name() == "test"


def test_saves_hook_and_streams_events(env):
    [turn] = chat(scripted(("list_endpoints", {}), ("write_hook", {"name": "double", "code": GOOD}), "Done."), "x2")
    assert [e["type"] for e in turn] == ["tool", "tool", "hook_saved", "text", "done"]
    assert turn[0] == {"type": "tool", "name": "list_endpoints", "input": {}}
    assert turn[1]["input"] == {"name": "double", "code": GOOD}
    assert turn[2]["route"] == "GET /api/items" and "+++ b/double.py" in turn[2]["diff"]
    assert turn[3] == {"type": "text", "text": "Done."}
    assert hooks.list_hooks("alice")[0]["enabled"]
    assert [k for k, _ in env] == ["agent.message", "agent.tool", "agent.tool", "hook.saved", "agent.answer"]


def test_tool_results_reach_the_model():
    seen = []

    def fn(messages, info):
        seen.append(messages[-1].parts[-1])
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("list_endpoints", {})])
        if len(seen) == 2:
            return ModelResponse(parts=[ToolCallPart("write_hook", {"name": "double", "code": GOOD})])
        return ModelResponse(parts=[TextPart("ok")])

    chat(FunctionModel(fn), "x2")
    assert seen[1].content == "GET /api/items: List items\nPOST /api/items: Add item"
    assert seen[2].content == "saved, dry-run output: [2, 4, 6]"


def test_guard_rejection_is_a_retry_then_fix(tmp_path):
    [turn] = chat(scripted(("write_hook", {"name": "h", "code": BAD}), ("write_hook", {"name": "h", "code": GOOD}),
                           "ok"), "go")
    assert [e["type"] for e in turn] == ["tool", "hook_rejected", "tool", "hook_saved", "text", "done"]
    assert turn[1]["reason"].startswith("line 1:")


def test_dry_run_failure_saves_nothing(tmp_path):
    [turn] = chat(scripted(("write_hook", {"name": "h", "code": CRASH}), "gave up"), "go")
    assert [e["type"] for e in turn] == ["tool", "hook_rejected", "text", "done"]
    assert "KeyError" in turn[1]["reason"] or "TypeError" in turn[1]["reason"]
    assert hooks.list_hooks("alice") == [] and list(tmp_path.iterdir()) == []


def test_three_rejections_end_the_turn(tmp_path):
    calls = [("write_hook", {"name": "h", "code": c}) for c in (BAD, CRASH, BAD)]
    turn, second = chat(scripted(*calls, "after"), "go", "again")
    assert [e["type"] for e in turn] == ["tool", "hook_rejected"] * 3 + ["error", "done"]
    assert list(tmp_path.iterdir()) == []
    assert second == [{"type": "text", "text": "after"}, {"type": "done"}]  # connection survives, counter resets


def test_history_is_kept_between_messages():
    counts = []

    def fn(messages, info):
        counts.append(len(messages))
        return ModelResponse(parts=[TextPart("hi")])

    chat(FunctionModel(fn), "one", "two")
    assert counts[1] > counts[0]


def test_hooks_tools():
    hooks.save_hook("alice", "old", GOOD)
    steps = [("list_hooks", {}), ("read_hook", {"name": "old"}), ("delete_hook", {"name": "old"}), "ok"]
    [turn] = chat(scripted(*steps), "clean")
    assert [e["name"] for e in turn if e["type"] == "tool"] == ["list_hooks", "read_hook", "delete_hook"]
    assert hooks.list_hooks("alice") == []


def test_bad_client_message_gets_error_and_done():
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await agent.handle_ws(websocket, "alice", Api())

    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "nope"})
        assert ws.receive_json()["type"] == "error"
        assert ws.receive_json() == {"type": "done"}


@pytest.mark.parametrize("model, var", [("openai:gpt-5", "OPENAI_API_KEY"), ("nope:x", "FITIT_MODEL")])
def test_unusable_model_gets_error_and_done(monkeypatch, model, var):
    monkeypatch.setenv("FITIT_MODEL", model)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await agent.handle_ws(websocket, "alice", Api())

    with TestClient(app).websocket_connect("/ws") as ws:
        ws.send_json({"type": "message", "text": "hi"})
        error = ws.receive_json()
        assert error["type"] == "error" and var in error["message"]
        assert ws.receive_json() == {"type": "done"}


class DemoApi:
    def get(self, path):
        with urllib.request.urlopen(os.environ.get("DEMO_URL", "http://demo:8001") + path) as r:
            return json.load(r)


def test_list_endpoints_on_demo_openapi():
    seen = []

    def fn(messages, info):
        seen.append(messages[-1].parts[-1])
        if len(seen) == 1:
            return ModelResponse(parts=[ToolCallPart("list_endpoints", {})])
        return ModelResponse(parts=[TextPart("ok")])

    api = DemoApi()
    chat(FunctionModel(fn), "endpoints?", api=api)
    ops = sum(len(v) for v in api.get("/openapi.json")["paths"].values())
    lines = seen[1].content.splitlines()
    assert len(lines) == ops and all(re.match(r"^(GET|POST|PUT|PATCH|DELETE) /\S*: ", l) for l in lines)


def test_main_route_end_to_end(monkeypatch):
    import main
    monkeypatch.setenv("BASE_URL", os.environ.get("DEMO_URL", "http://demo:8001"))
    code = 'ROUTE = "GET /api/invoices"\n\ndef run(data, ctx):\n    return data[:1]\n'
    model = scripted(("get_endpoint", {"path": "/api/invoices"}), ("write_hook", {"name": "one", "code": code}), "ok")
    c = TestClient(main.app, follow_redirects=False)
    c.get("/login", params={"user": "gerente"})
    user = c.get("/api/me").json()["id"]
    with agent.agent.override(model=model), c.websocket_connect("/fitit/ws") as ws:
        ws.send_json({"type": "message", "text": "only one invoice"})
        turn = []
        while not turn or turn[-1]["type"] != "done":
            turn.append(ws.receive_json())
    assert [e["type"] for e in turn] == ["tool", "tool", "hook_saved", "text", "done"]
    assert [h["name"] for h in hooks.list_hooks(user)] == ["one"]
