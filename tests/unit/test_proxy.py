import importlib
import os
import shutil
import sys
import types
import uuid

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import db
import hooks
import main
import trace
import transcribe


def hook(route, body="return data"):
    return f'ROUTE = "{route}"\n\ndef run(data, ctx):\n    {body}\n'


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
    monkeypatch.setenv("BASE_URL", "http://demo:8001")
    events = []
    monkeypatch.setattr(trace, "emit", lambda kind, **f: events.append({"kind": kind, **f}))
    return events


def write(user, name, code, enabled=True):
    hooks.save_hook(user, name, code)
    if not enabled:
        hooks.toggle_hook(user, name)


def client(user=None):
    c = TestClient(main.app, follow_redirects=False)
    if user:
        assert c.get("/login", params={"user": user}).status_code == 303
    return c


def demo(user):
    c = httpx.Client(base_url="http://demo:8001")
    c.get("/login", params={"user": user})
    return c


def test_forwards_status_body_and_cookies():
    assert client().get("/api/invoices").status_code == 401
    r = client("gerente").get("/api/invoices")
    assert r.status_code == 200
    assert r.json() == demo("gerente").get("/api/invoices").json()
    assert "x-fitit-hooks" not in r.headers


def test_login_cookie_passes_through():
    c = client()
    r = c.get("/login?user=gerente")
    assert r.status_code == 303 and "session=gerente" in r.headers["set-cookie"]
    assert c.get("/api/me").json()["id"] == "gerente"


def test_hook_applies_only_to_its_user(env):
    write("gerente", "a_count", hook("GET /api/invoices", 'ctx.log("hi")\n    return len(data)'))
    write("gerente", "b_wrap", hook("GET /api/invoices", 'ctx.log("bye")\n    return {"n": data}'))
    write("gerente", "c_off", hook("GET /api/invoices", "return 0"), enabled=False)
    n = len(demo("gerente").get("/api/invoices").json())
    r = client("gerente").get("/api/invoices?x=1")
    assert r.json() == {"n": n} and r.headers["x-fitit-hooks"] == "a_count,b_wrap"
    applied = [e for e in env if e["kind"] == "hook.applied"]
    assert [e["hook"] for e in applied] == ["a_count", "b_wrap"]
    assert applied[0]["route"] == "GET /api/invoices" and applied[0]["logs"] == ["hi"]
    assert applied[1]["logs"] == ["bye"] and all(e["ms"] >= 0 for e in applied)
    proxied = [e for e in env if e["kind"] == "request.proxied"]
    assert proxied[-1] == {"kind": "request.proxied", "user": "gerente", "method": "GET", "path": "/api/invoices",
                           "status": 200, "ms": proxied[-1]["ms"], "hooks": ["a_count", "b_wrap"]}

    r = client("recepcion").get("/api/invoices")
    assert "x-fitit-hooks" not in r.headers and isinstance(r.json(), list)
    assert client().get("/api/invoices").status_code == 401
    assert env[-1]["user"] is None


def test_failing_hook_fails_closed(env):
    write("gerente", "boom", hook("GET /api/patients", 'return 1 / 0'))
    r = client("gerente").get("/api/patients")
    assert r.status_code == 502
    assert r.json() == {"error": "hook_failed", "hook": "boom", "detail": "ZeroDivisionError: division by zero"}
    failed = [e for e in env if e["kind"] == "hook.failed"]
    assert failed == [{"kind": "hook.failed", "user": "gerente", "hook": "boom", "route": "GET /api/patients",
                       "error": "ZeroDivisionError: division by zero"}]


def test_widget_injected_once_for_signed_in_html():
    tag = '<script src="/fitit/embed.js" defer></script>'
    body = client("gerente").get("/").text
    assert body.count(tag) == 1 and body.rindex(tag) < body.rindex("</body>")
    assert tag not in client().get("/").text


def test_virtual_endpoints(monkeypatch):
    assert client().get("/x/report").status_code == 401
    assert client("gerente").get("/x/report").json() == {"error": "not_found"}
    write("gerente", "report", hook("GET /x/report", 'return {"n": len(ctx.api.get("/api/invoices"))}'))
    c = client("gerente")
    monkeypatch.setattr(main.httpx, "request", None)  # nothing may be forwarded
    r = c.get("/x/report")
    assert r.status_code == 200 and r.json()["n"] > 0 and r.headers["x-fitit-hooks"] == "report"


def test_hook_management(env):
    for method, path in [("GET", "/fitit/hooks"), ("POST", "/fitit/hooks/a/toggle"), ("DELETE", "/fitit/hooks/a"),
                         ("POST", "/fitit/hooks/a/undo")]:
        assert client().request(method, path).status_code == 401
    write("gerente", "a", hook("GET /api/invoices", "return 1"))
    c = client("gerente")
    assert c.get("/fitit/hooks").json()[0] | {"code": ""} == {"name": "a", "route": "GET /api/invoices",
                                                              "enabled": True, "code": ""}
    assert c.post("/fitit/hooks/a/toggle").json() == {"name": "a", "enabled": False}
    assert "x-fitit-hooks" not in c.get("/api/invoices").headers
    assert c.post("/fitit/hooks/zz/toggle").status_code == 404
    assert c.delete("/fitit/hooks/a").status_code == 204
    assert c.delete("/fitit/hooks/a").status_code == 404
    assert c.get("/fitit/hooks").json() == []
    kinds = [(e["kind"], e.get("enabled")) for e in env if e["kind"].startswith("hook.")]
    assert kinds == [("hook.toggled", False), ("hook.deleted", None)]


def test_undo_route(env):
    c = client("gerente")
    assert c.post("/fitit/hooks/a/undo").json() == {"error": "not_found"}
    write("gerente", "a", hook("GET /api/invoices", "return 1"))
    r = c.post("/fitit/hooks/a/undo")
    assert r.status_code == 409 and r.json() == {"error": "undo_failed", "detail": "no earlier version"}
    write("gerente", "a", hook("GET /api/invoices", "return 2"))
    assert c.get("/api/invoices").json() == 2
    r = c.post("/fitit/hooks/a/undo")
    assert r.status_code == 200 and r.json()["version"] == 1 and "+    return 1" in r.json()["diff"]
    assert c.get("/api/invoices").json() == 1
    assert [e for e in env if e["kind"] == "hook.restored"] == [
        {"kind": "hook.restored", "user": "gerente", "hook": "a", "version": 1}]


def test_embed_js_and_core_namespace():
    r = client().get("/fitit/embed.js")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/javascript")
    assert "/fitit/ui/" in r.text and r.text == main.EMBED.read_text()
    assert client("gerente").get("/fitit/unknown").status_code == 404


def test_widget_ui_served_only_when_built(monkeypatch):
    if main.UI.exists():
        pytest.skip("a real widget build is present; not touching it")
    c = client("gerente")
    monkeypatch.setattr(main.httpx, "request", None)  # nothing may be forwarded
    r = c.get("/fitit/ui/")
    assert r.status_code == 404 and r.json() == {"error": "not_found"}
    monkeypatch.undo()
    main.UI.mkdir(parents=True)
    try:
        (main.UI / "index.html").write_text("<p>fitit-ui</p>")
        importlib.reload(main)  # the mount is decided at import
        r = client().get("/fitit/ui/")
        assert r.status_code == 200 and r.text == "<p>fitit-ui</p>"
        assert r.headers["content-type"].startswith("text/html")
    finally:
        shutil.rmtree(main.UI)
        importlib.reload(main)


def test_api_get():
    api = main.Api("session=gerente")
    assert api.get("/api/me")["id"] == "gerente"
    with pytest.raises(ValueError):
        api.get("api/me")
    with pytest.raises(httpx.HTTPStatusError):
        main.Api("").get("/api/invoices")


def test_ws_rejects_anonymous_and_delegates(monkeypatch):
    with pytest.raises(WebSocketDisconnect) as e, client().websocket_connect("/fitit/ws") as ws:
        ws.receive_text()
    assert e.value.code == 4401

    async def handle_ws(websocket, user, api, conversation):
        await websocket.accept()
        await websocket.send_json({"user": user, "me": api.get("/api/me")["id"], "conversation": conversation})
        await websocket.close()

    monkeypatch.setitem(sys.modules, "agent", types.SimpleNamespace(handle_ws=handle_ws))
    c = client("gerente")
    with c.websocket_connect("/fitit/ws") as ws:
        assert ws.receive_json() == {"user": "gerente", "me": "gerente", "conversation": "default"}
    ok = "a-Z_9" * 12 + "abcd"  # 64 characters
    with c.websocket_connect(f"/fitit/ws?conversation={ok}") as ws:
        assert ws.receive_json()["conversation"] == ok
    for bad in ("a%20b", "%C3%BC", "", ok + "x"):
        with pytest.raises(WebSocketDisconnect) as e, c.websocket_connect(f"/fitit/ws?conversation={bad}") as ws:
            ws.receive_text()
        assert e.value.code == 4400, bad
    with pytest.raises(WebSocketDisconnect) as e, client().websocket_connect("/fitit/ws?conversation=a%20b") as ws:
        ws.receive_text()
    assert e.value.code == 4401


def test_ws_anonymous_is_accepted_then_closed_with_4401():
    # Closing before accept() makes a real client see HTTP 403, not 4401.
    with client().websocket_connect("/fitit/ws") as ws:
        with pytest.raises(WebSocketDisconnect) as e:
            ws.receive_text()
    assert e.value.code == 4401


def test_subscribed_features_run_first_as_the_subscriber(env, database):
    import marketplace
    mark = 'data[0]["marks"] = data[0].get("marks", []) + ["{}"]\n    return data'
    write("gerente", "who", hook("GET /x/who", 'return [ctx.user, ctx.api.get("/api/me")["id"]]'))
    write("gerente", "a", hook("GET /api/invoices", mark.format("A")))
    write("gerente", "b", hook("GET /api/invoices", mark.format("B")))
    who, a, b = (marketplace.publish("gerente", n, n, n)["id"] for n in ("who", "a", "b"))
    for fid in (who, b, a):
        marketplace.subscribe("recepcion", fid, main.Api("session=recepcion"))
    write("recepcion", "own", hook("GET /api/invoices", mark.format("own")))
    c = client("recepcion")
    assert c.get("/x/who").json() == ["recepcion", "recepcion"]
    del env[:]
    r = c.get("/api/invoices")
    assert r.json()[0]["marks"] == ["B", "A", "own"] and r.headers["x-fitit-hooks"] == f"@{b},@{a},own"
    assert [(e["hook"], e["user"]) for e in env if e["kind"] == "hook.applied"] == [
        (f"@{b}", "recepcion"), (f"@{a}", "recepcion"), ("own", "recepcion")]
    assert env[-1]["hooks"] == [f"@{b}", f"@{a}", "own"]

    marketplace.toggle("recepcion", b)
    assert c.get("/api/invoices").headers["x-fitit-hooks"] == f"@{a},own"
    with psycopg.connect(database) as conn:
        conn.execute("UPDATE feature_versions SET code = %s WHERE feature_id = %s",
                     (hook("GET /api/invoices", "return 1 / 0"), a))
    del env[:]
    r = c.get("/api/invoices")
    assert r.status_code == 502 and r.json()["hook"] == f"@{a}" and "marks" not in r.text
    assert [e for e in env if e["kind"] == "hook.failed"] == [{
        "kind": "hook.failed", "user": "recepcion", "hook": f"@{a}", "route": "GET /api/invoices",
        "error": "ZeroDivisionError: division by zero"}]
    with psycopg.connect(database) as conn:
        conn.execute("UPDATE feature_versions SET code = 'import os' WHERE feature_id = %s", (a,))
    r = c.get("/api/invoices")
    assert r.status_code == 502 and r.json()["hook"] == f"@{a}"
    assert [e["route"] for e in env if e["kind"] == "hook.failed"][-1] is None
    assert client("gerente").get("/x/who").json() == ["gerente", "gerente"]


def audio(body=b"audio-bytes", language="es-ES"):
    return client().post("/fitit/transcribe", content=body, headers={"content-type": "audio/webm"}, params={"language": language})


def test_transcribe_anonymous():
    r = audio()
    assert r.status_code == 401 and r.json() == {"error": "unauthenticated"}


def test_transcribe_returns_openai_text(monkeypatch):
    monkeypatch.setattr(main, "resolve_user", lambda cookie: "gerente")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    seen = {}

    def fake_post(url, *, headers, files, data, timeout):
        seen["url"] = url
        seen["auth"] = headers["Authorization"]
        seen["model"] = data["model"]
        seen["language"] = data["language"]
        seen["type"] = files["file"][2]
        seen["audio"] = files["file"][1]
        seen["timeout"] = timeout
        return httpx.Response(200, json={"text": " hide the phones "})

    monkeypatch.setattr(httpx, "post", fake_post)
    r = audio()
    assert r.status_code == 200 and r.json() == {"text": "hide the phones"}
    assert seen == {
        "url": "https://example.test/v1/audio/transcriptions",
        "auth": "Bearer sk-test",
        "model": transcribe.MODEL,
        "language": "es",
        "type": "audio/webm",
        "audio": b"audio-bytes",
        "timeout": 60,
    }
    assert transcribe.MODEL == "gpt-4o-mini-transcribe"


def test_transcribe_without_key(monkeypatch):
    monkeypatch.setattr(main, "resolve_user", lambda cookie: "gerente")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    assert audio().status_code == 503 and audio().json() == {"error": "transcription_unavailable"}


def test_transcribe_openai_error(monkeypatch):
    monkeypatch.setattr(main, "resolve_user", lambda cookie: "gerente")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    def fail(*args, **kwargs):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(httpx, "post", fail)
    r = audio()
    assert r.status_code == 502 and r.json() == {"error": "transcription_failed"}


def test_transcribe_rejects_large_audio(monkeypatch):
    monkeypatch.setattr(main, "resolve_user", lambda cookie: "gerente")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(transcribe, "MAX_BYTES", 4)
    r = audio(b"12345")
    assert r.status_code == 413 and r.json() == {"error": "audio_too_large"}
