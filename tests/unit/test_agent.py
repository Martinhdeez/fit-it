import asyncio
import json
import os
import re
import threading
import urllib.request
import uuid

import psycopg
import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import agent
import db
import hooks
import marketplace
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


@pytest.fixture(autouse=True)
def env(database, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    agent.reset_rate_limits()
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


def test_guard_rejection_is_a_retry_then_fix():
    [turn] = chat(scripted(("write_hook", {"name": "h", "code": BAD}), ("write_hook", {"name": "h", "code": GOOD}),
                           "ok"), "go")
    assert [e["type"] for e in turn] == ["tool", "hook_rejected", "tool", "hook_saved", "text", "done"]
    assert turn[1]["reason"].startswith("line 1:")


def test_dry_run_failure_saves_nothing():
    [turn] = chat(scripted(("write_hook", {"name": "h", "code": CRASH}), "gave up"), "go")
    assert [e["type"] for e in turn] == ["tool", "hook_rejected", "text", "done"]
    assert "KeyError" in turn[1]["reason"] or "TypeError" in turn[1]["reason"]
    assert hooks.list_hooks("alice") == []


def test_three_rejections_end_the_turn():
    calls = [("write_hook", {"name": "h", "code": c}) for c in (BAD, CRASH, BAD)]
    turn, second = chat(scripted(*calls, "after"), "go", "again")
    assert [e["type"] for e in turn] == ["tool", "hook_rejected"] * 3 + ["error", "done"]
    assert hooks.list_hooks("alice") == []
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


GOOD3 = GOOD.replace("x * 2", "x * 3")


def test_instructions_list_current_hooks():
    seen = []

    def fn(messages, info):
        seen.append(messages[-1].instructions)
        return ModelResponse(parts=[TextPart("ok")])

    chat(FunctionModel(fn), "hi")
    hooks.save_hook("alice", "double", GOOD)
    chat(FunctionModel(fn), "hi")
    assert "no hooks yet" in seen[0] and "list_endpoints" in seen[0]
    assert "double: GET /api/items (enabled)" in seen[1]


def test_write_hook_same_name_edits(database):
    hooks.save_hook("alice", "double", GOOD)
    [turn] = chat(scripted(("read_hook", {"name": "double"}), ("write_hook", {"name": "double", "code": GOOD3}), "ok"),
                  "make it x3")
    [saved] = [e for e in turn if e["type"] == "hook_saved"]
    assert "-    return [x * 2 for x in data]" in saved["diff"] and "+    return [x * 3 for x in data]" in saved["diff"]
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT name, version FROM hooks").fetchall() == [("double", 2)]


def test_undo_hook_tool(env):
    hooks.save_hook("alice", "double", GOOD)
    hooks.save_hook("alice", "double", GOOD3)
    seen = []

    def fn(messages, info):
        seen.append(messages[-1].parts[-1])
        if len(seen) in (1, 2):
            return ModelResponse(parts=[ToolCallPart("undo_hook", {"name": "double"})])
        return ModelResponse(parts=[TextPart("ok")])

    [turn] = chat(FunctionModel(fn), "undo")
    [saved] = [e for e in turn if e["type"] == "hook_saved"]
    assert saved["name"] == "double" and saved["route"] == "GET /api/items" and "-    return [x * 3" in saved["diff"]
    assert seen[1].content == "restored version 1"
    assert seen[2].content == "nothing to undo: no earlier version"  # plain result, not a retry
    assert hooks.read_hook("alice", "double") == GOOD
    assert ("hook.restored", {"user": "alice", "hook": "double", "version": 1}) in env


def test_rate_limit(monkeypatch, database):
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "2")
    calls = []

    def fn(messages, info):
        calls.append(1)
        return ModelResponse(parts=[TextPart("ok")])

    chat(FunctionModel(fn), "1", "2")
    [turn] = chat(FunctionModel(fn), "3")  # new connection, same user
    assert turn[0]["type"] == "error" and "rate limit" in turn[0]["message"] and turn[1:] == [{"type": "done"}]
    assert len(calls) == 2
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT count(*) FROM messages").fetchone() == (2,)


def test_answer_carries_token_counts(env):
    chat(scripted("ok"), "hi")
    [answer] = [f for k, f in env if k == "agent.answer"]
    assert isinstance(answer["input_tokens"], int) and isinstance(answer["output_tokens"], int)
    assert answer["input_tokens"] > 0


def test_delete_history_route(monkeypatch, database):
    import main
    monkeypatch.setenv("BASE_URL", os.environ.get("DEMO_URL", "http://demo:8001"))
    c = TestClient(main.app, follow_redirects=False)
    assert c.delete("/fitit/history").status_code == 401
    c.get("/login", params={"user": "gerente"})
    user = c.get("/api/me").json()["id"]
    with psycopg.connect(database) as conn:
        for u in (user, "other"):
            conn.execute("INSERT INTO messages (user_id, turn) VALUES (%s, '[]')", (u,))
    assert c.delete("/fitit/history").status_code == 204
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT user_id FROM messages").fetchall() == [("other",)]


def returns(*steps):
    """Like scripted, plus the list of tool return texts the model received, in order."""
    seen, steps = [], list(steps)

    def fn(messages, info):
        part = messages[-1].parts[-1]
        if part.part_kind == "tool-return":
            seen.append(part.content)
        step = steps.pop(0)
        if isinstance(step, str):
            return ModelResponse(parts=[TextPart(step)])
        return ModelResponse(parts=[ToolCallPart(step[0], step[1])])
    return FunctionModel(fn), seen


def changes(turn):
    return [(e["feature_id"], e["action"]) for e in turn if e["type"] == "feature_changed"]


def feature(code=GOOD):
    hooks.save_hook("bob", "double", code)
    return marketplace.publish("bob", "double", "Double", "x2 items")["id"]


def test_publish_search_unpublish(env):
    hooks.save_hook("alice", "double", GOOD)
    model, seen = returns(("publish_hook", {"name": "nope", "title": "T", "description": "D"}),
                          ("publish_hook", {"name": "double", "title": "Double", "description": "x2 items"}), "ok")
    [turn] = chat(model, "publish")
    assert seen[0].startswith("not_found:")
    [fid] = [f["id"] for f in marketplace.search("alice")]
    assert seen[1] == f"published @{fid} version 1"
    assert changes(turn) == [(fid, "published")] and [e["type"] for e in turn][-2:] == ["text", "done"]

    model, seen = returns(("search_features", {"query": "X2"}), ("search_features", {"query": "zzz"}),
                          ("unpublish_feature", {"feature_id": fid}), "ok")
    [turn] = chat(model, "find and hide")
    assert seen == [f"@{fid} Double — GET /api/items — x2 items (0 subscribers, v1) [mine]", "no features found",
                    f"unpublished @{fid}"]
    assert changes(turn) == [(fid, "unpublished")] and marketplace.search("alice") == []
    assert [(k, f["user"]) for k, f in env if k.startswith("feature.")] == [
        ("feature.published", "alice"), ("feature.unpublished", "alice")]


def test_unpublish_by_other_user_is_forbidden():
    fid = feature()
    model, seen = returns(("unpublish_feature", {"feature_id": fid}), "ok")
    [turn] = chat(model, "hide")
    assert seen[0].startswith("forbidden:") and changes(turn) == []
    assert marketplace.get_feature("alice", fid)["published"]


def test_subscribe_update_unsubscribe(env):
    fid = feature()
    model, seen = returns(("subscribe_feature", {"feature_id": fid}), ("subscribe_feature", {"feature_id": fid}),
                          ("search_features", {"query": ""}), "ok")
    [turn] = chat(model, "subscribe")
    assert seen[0] == f"subscribed to @{fid} version 1" and seen[1].startswith("already_subscribed:")
    assert seen[2] == f"@{fid} Double — GET /api/items — x2 items (1 subscribers, v1) [subscribed]"
    assert changes(turn) == [(fid, "subscribed")]
    assert [s["version"] for s in marketplace.subscriptions("alice")] == [1]

    hooks.save_hook("bob", "double", GOOD3)
    marketplace.publish("bob", "double", "Double", "x3 now")
    instructions = []

    def fn(messages, info):
        instructions.append(messages[-1].instructions)
        return ModelResponse(parts=[TextPart("ok")])

    chat(FunctionModel(fn), "what do I have?")
    assert f"@{fid} Double: GET /api/items (v1, enabled, update available)" in instructions[0]

    model, seen = returns(("accept_update", {"feature_id": fid}), ("unsubscribe_feature", {"feature_id": fid}),
                          ("unsubscribe_feature", {"feature_id": fid}), "ok")
    [turn] = chat(model, "update then drop")
    assert seen[:2] == [f"@{fid} updated to version 2", f"unsubscribed from @{fid}"]
    assert seen[2].startswith("not_found:")
    assert changes(turn) == [(fid, "updated"), (fid, "unsubscribed")]
    assert [(k, f["user"]) for k, f in env if k.startswith("feature.")] == [
        ("feature.published", "bob"), ("feature.subscribed", "alice"), ("feature.published", "bob"),
        ("feature.updated", "alice"), ("feature.unsubscribed", "alice")]


def test_subscribe_dry_run_failure_writes_nothing():
    fid = feature(CRASH)
    model, seen = returns(("subscribe_feature", {"feature_id": fid}), "sorry")
    [turn] = chat(model, "subscribe")
    assert seen[0].startswith("dry_run_failed:") and changes(turn) == []
    assert [e["type"] for e in turn][-2:] == ["text", "done"]
    assert marketplace.subscriptions("alice") == []


def test_fork_sends_hook_saved_then_feature_changed(env):
    fid = feature()
    marketplace.subscribe("alice", fid, Api())
    model, seen = returns(("fork_feature", {"feature_id": fid, "name": "mine"}), "ok")
    [turn] = chat(model, "fork")
    assert seen == [f"forked @{fid} into mine"]
    events = [e for e in turn if e["type"] in ("hook_saved", "feature_changed")]
    assert [e["type"] for e in events] == ["hook_saved", "feature_changed"] and changes(turn) == [(fid, "forked")]
    assert events[0]["name"] == "mine" and events[0]["route"] == "GET /api/items" and "+++ b/mine.py" in events[0]["diff"]
    assert hooks.read_hook("alice", "mine") == GOOD and marketplace.subscriptions("alice") == []
    assert [k for k, _ in env if k in ("hook.saved", "feature.forked")] == ["hook.saved", "feature.forked"]


def test_instructions_name_marketplace_tools():
    seen = []

    def fn(messages, info):
        seen.append(messages[-1].instructions)
        return ModelResponse(parts=[TextPart("ok")])

    chat(FunctionModel(fn), "hi")
    assert "no subscriptions" in seen[0]
    assert all(t in seen[0] for t in ("search_features", "subscribe_feature", "publish_hook"))


def receive_turn(ws):
    turn = []
    while not turn or turn[-1]["type"] != "done":
        turn.append(ws.receive_json())
    return turn


def test_one_turn_at_a_time_per_conversation(env, database, monkeypatch):
    monkeypatch.setenv("FITIT_AGENT_MESSAGES_PER_HOUR", "4")
    started, release, calls = threading.Event(), threading.Event(), []

    async def fn(messages, info):
        text = messages[-1].parts[-1].content
        calls.append(text)
        if text == "hold":
            started.set()
            while not release.is_set():
                await asyncio.sleep(0.01)
        return ModelResponse(parts=[TextPart(f"re {text}")])

    app = FastAPI()

    @app.websocket("/ws/{user}/{conversation}")
    async def ws(websocket: WebSocket, user: str, conversation: str):
        await agent.handle_ws(websocket, user, Api(), conversation)

    with agent.agent.override(model=FunctionModel(fn)), TestClient(app) as c, \
            c.websocket_connect("/ws/alice/a") as a1, c.websocket_connect("/ws/alice/a") as a2, \
            c.websocket_connect("/ws/alice/b") as b, c.websocket_connect("/ws/bob/a") as bob:
        a1.send_json({"type": "message", "text": "hold"})
        assert started.wait(5)
        a2.send_json({"type": "message", "text": "same"})
        busy = receive_turn(a2)
        assert busy[0]["type"] == "error" and "busy" in busy[0]["message"] and busy[1:] == [{"type": "done"}]
        for other in (b, bob):
            other.send_json({"type": "message", "text": "other"})
            assert [e["type"] for e in receive_turn(other)] == ["text", "done"]
        release.set()
        assert receive_turn(a1)[-2]["text"] == "re hold"
        a2.send_json({"type": "message", "text": "now"})  # busy did not count: alice's 3rd counted message
        assert receive_turn(a2)[-2]["text"] == "re now"
        b.send_json({"type": "message", "text": "over"})  # 4th counted, the limit is per user across conversations
        receive_turn(b)
        a2.send_json({"type": "message", "text": "over"})
        assert "rate limit" in receive_turn(a2)[0]["message"]
    assert calls == ["hold", "other", "other", "now", "over"]
    with psycopg.connect(database) as conn:
        assert conn.execute("SELECT user_id, conversation_id FROM messages ORDER BY id").fetchall() == [
            ("alice", "b"), ("bob", "a"), ("alice", "a"), ("alice", "a"), ("alice", "b")]
    busy_events = [f for k, f in env if k == "agent.message" and f["text"] == "same"]
    assert busy_events == [{"user": "alice", "text": "same", "conversation": "a"}]


def test_agent_events_carry_the_conversation(env):
    app = FastAPI()

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await agent.handle_ws(websocket, "alice", Api(), "c1")

    with agent.agent.override(model=scripted(("list_hooks", {}), "ok")), TestClient(app).websocket_connect("/ws") as w:
        w.send_json({"type": "message", "text": "hi"})
        receive_turn(w)
    chat(scripted("ok"), "hi")
    got = [(k, f["conversation"]) for k, f in env if k.startswith("agent.")]
    assert got == [("agent.message", "c1"), ("agent.tool", "c1"), ("agent.answer", "c1"),
                   ("agent.message", "default"), ("agent.answer", "default")]


def test_conversation_routes(monkeypatch, database):
    import main
    monkeypatch.setenv("BASE_URL", os.environ.get("DEMO_URL", "http://demo:8001"))
    c = TestClient(main.app, follow_redirects=False)
    assert c.get("/fitit/conversations").status_code == 401
    assert c.delete("/fitit/history?conversation=a").status_code == 401
    c.get("/login", params={"user": "gerente"})
    user = c.get("/api/me").json()["id"]
    assert c.get("/fitit/conversations").json() == []
    for u, cid in ((user, "a"), (user, "b"), ("other", "a")):
        with psycopg.connect(database) as conn:
            conn.execute("INSERT INTO messages (user_id, turn, conversation_id) VALUES (%s, '[]', %s)", (u, cid))
    assert [x["id"] for x in c.get("/fitit/conversations").json()] == ["b", "a"]
    for bad in ("", "a%20b", "x" * 65):
        r = c.delete(f"/fitit/history?conversation={bad}")
        assert r.status_code == 422 and r.json()["error"] == "invalid"
    assert c.delete("/fitit/history?conversation=a").status_code == 204
    with psycopg.connect(database) as conn:
        assert sorted(conn.execute("SELECT user_id, conversation_id FROM messages").fetchall()) == sorted([
            ("other", "a"), (user, "b")])
