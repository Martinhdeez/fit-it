"""Fit-it core: reverse proxy, per-user hooks, widget injection. Spec: docs/specs/proxy.md."""
import json
import os
import re
import time
from urllib.parse import parse_qsl, urlsplit
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import db
import hooks
import marketplace
import trace
import transcribe


@asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(db.init)
    await run_in_threadpool(trace.startup)
    try:
        yield
    finally:
        await run_in_threadpool(trace.shutdown, 5000)


app = FastAPI(lifespan=lifespan)

EMBED = Path(__file__).parent / "static" / "embed.js"
UI = Path(__file__).parent / "static" / "ui"  # widget static export (ADR 0015)
SCRIPT = b'<script src="/fitit/embed.js" defer></script>'
DROP_REQUEST = {"host", "content-length", "accept-encoding", "connection", "x-forwarded-for", "x-forwarded-host",
                "x-forwarded-proto", "forwarded", "transfer-encoding", "te", "upgrade"}
DROP_RESPONSE = {"content-length", "content-encoding", "transfer-encoding", "connection"}


def base_url() -> str:
    return os.environ.get("BASE_URL", "http://demo:8001")


class Api:
    """`ctx.api` for hooks and the agent: read-only access to the app with the user's own cookies."""

    def __init__(self, cookie: str):
        self.cookie = cookie

    def get(self, path: str):
        if not path.startswith("/"):
            raise ValueError("path must start with '/'")
        r = httpx.get(base_url() + path, headers={"cookie": self.cookie})
        r.raise_for_status()
        return r.json()


def resolve_user(cookie: str) -> str | None:
    # ponytail: one user call per request; cache by session cookie if latency matters (ADR 0006)
    path = os.environ.get("FITIT_USER_PATH", "/api/me")
    field = os.environ.get("FITIT_USER_FIELD", "id")
    anonymous = os.environ.get("FITIT_ANONYMOUS_USERS", "").split(",")  # ADR 0027
    try:
        r = httpx.get(base_url() + path, headers={"cookie": cookie})
        user = r.json()[field] if r.status_code == 200 else None
    except Exception:
        return None
    return user if isinstance(user, str) and user not in anonymous else None


def route_of(name: str, entries: list[dict]):
    """The route of the code named `name` among features and hooks, or None if it fails the guard."""
    code = next((h["code"] for h in entries if h["name"] == name), "")
    try:
        return " ".join(hooks.check_code(code))
    except ValueError:
        return None


def request_info(method: str, path: str, query: str, body: bytes) -> dict:
    """`ctx.request`: the proxied request, with a JSON or form body parsed and JSON-looking form values decoded."""
    form = {}
    try:
        form = json.loads(body) if body[:1] in (b"{", b"[") else dict(parse_qsl(body.decode()))
        if isinstance(form, dict):
            for k, v in form.items():
                if isinstance(v, str) and v[:1] in ("{", "["):
                    try:
                        form[k] = json.loads(v)
                    except ValueError:
                        pass
    except (ValueError, UnicodeDecodeError):
        form = {}
    return {"method": method, "path": path, "query": dict(parse_qsl(query)), "form": form}


def apply_hooks(user, cookie, method, path, data, request=None):
    """Runs the user's subscribed features, then their own hooks (ADR 0023).
    Returns (response, names): the hooked JSON, a 502 on HookError, or None if nothing matched."""
    features = marketplace.active(user)
    try:
        result, applied = hooks.run_hooks(user, method, path, data, hooks.Ctx(user, Api(cookie), request), features)
    except hooks.HookError as e:
        route = route_of(e.hook, features if e.hook.startswith("@") else hooks.list_hooks(user))
        trace.emit("hook.failed", user=user, hook=e.hook, route=route, error=str(e))
        return JSONResponse({"error": "hook_failed", "hook": e.hook, "detail": str(e)}, 502), []
    for h in applied:
        trace.emit("hook.applied", user=user, hook=h["name"], route=h["route"], ms=h["ms"], logs=h["logs"])
    if not applied:
        return None, []
    names = [h["name"] for h in applied]
    return JSONResponse(result, headers={"x-fitit-hooks": ",".join(names), "cache-control": "private, no-store"}), names


def finish(response: Response, user, method, path, start, applied) -> Response:
    trace.emit("request.proxied", user=user, method=method, path=path, status=response.status_code,
               ms=round((time.perf_counter() - start) * 1000, 1), hooks=applied)
    return response


def cross_origin(headers) -> bool:
    """True when a browser sent an `Origin` whose host[:port] differs from the request's `Host`.
    ponytail: the scheme is not compared, so TLS terminated in front of the core needs no proxy-headers setup."""
    origin = headers.get("origin")
    return origin is not None and urlsplit(origin).netloc != headers.get("host")


@app.middleware("http")
async def same_origin_writes(request: Request, call_next):
    if request.url.path.startswith("/fitit/") and request.method != "GET" and cross_origin(request.headers):
        return JSONResponse({"error": "forbidden", "detail": "cross-origin request"}, 403)
    return await call_next(request)


def unauthenticated() -> JSONResponse:
    return JSONResponse({"error": "unauthenticated"}, 401)


def not_found() -> JSONResponse:
    return JSONResponse({"error": "not_found"}, 404)


@app.get("/fitit/embed.js")
def embed_js():
    body = EMBED.read_text("utf-8") if EMBED.exists() else "// Fit-it widget not built yet\n"
    return Response(body, media_type="application/javascript")


if UI.is_dir():  # not built: /fitit/ui/ falls through to the core-namespace 404
    app.mount("/fitit/ui", StaticFiles(directory=UI, html=True))


@app.get("/fitit/trace/events")
def trace_events(request: Request, after: str = "0", limit: str = "500"):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    try:
        after_n, limit_n = int(after), int(limit)
        if after_n < 0 or not 0 < limit_n <= 1000:
            raise ValueError("after must be >= 0 and limit in 1..1000")
    except ValueError as e:
        return JSONResponse({"error": "invalid", "detail": str(e)}, 422)
    records = trace.read_events(after=after_n, limit=limit_n)
    admin = user in os.environ.get("FITIT_TRACE_ADMINS", "").split(",")
    return {"events": [r for r in records if admin or r.get("user") == user],
            "next": records[-1]["seq"] if records else after_n, "admin": admin}


@app.get("/traceability")
@app.get("/traceability/")
def traceability():
    for page in (UI / "traceability" / "index.html", UI / "traceability.html"):
        if page.is_file():
            return Response(page.read_bytes(), media_type="text/html")
    return not_found()


@app.get("/fitit/hooks")
def list_hooks(request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    return hooks.list_hooks(user) if user else unauthenticated()


@app.post("/fitit/hooks/{name}/toggle")
def toggle_hook(name: str, request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    try:
        enabled = hooks.toggle_hook(user, name)
    except KeyError:
        return not_found()
    trace.emit("hook.toggled", user=user, hook=name, enabled=enabled)
    return {"name": name, "enabled": enabled}


@app.delete("/fitit/hooks/{name}")
def delete_hook(name: str, request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    try:
        hooks.delete_hook(user, name)
    except KeyError:
        return not_found()
    trace.emit("hook.deleted", user=user, hook=name)
    return Response(status_code=204)


@app.post("/fitit/hooks/{name}/undo")
def undo_hook(name: str, request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    try:
        version, diff = hooks.undo_hook(user, name)
    except KeyError:
        return not_found()
    except ValueError as e:
        return JSONResponse({"error": "undo_failed", "detail": str(e)}, 409)
    trace.emit("hook.restored", user=user, hook=name, version=version)
    return {"name": name, "version": version, "diff": diff}


def valid_conversation(id: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9_-]{1,64}", id) is not None


@app.delete("/fitit/history")
def clear_history(request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    conversation = request.query_params.get("conversation")
    if conversation is not None and not valid_conversation(conversation):
        return JSONResponse({"error": "invalid", "detail": "conversation must match [A-Za-z0-9_-]{1,64}"}, 422)
    from agent import clear_history
    clear_history(user, conversation)
    return Response(status_code=204)


@app.get("/fitit/conversations")
def list_conversations(request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    from agent import list_conversations
    return list_conversations(user)


@app.get("/fitit/conversations/{conversation}/messages")
def conversation_messages(conversation: str, request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    if not valid_conversation(conversation):
        return JSONResponse({"error": "invalid", "detail": "conversation must match [A-Za-z0-9_-]{1,64}"}, 422)
    from agent import conversation_messages
    return conversation_messages(user, conversation)


@app.get("/fitit/features")
def my_features(request: Request):
    user = resolve_user(request.headers.get("cookie", ""))
    return marketplace.my_features(user) if user else unauthenticated()


def market(request: Request, call, id: str | None = None):
    """Marketplace routes: 401 if anonymous, 404 for a bad `id`, then `call(user[, feature_id])`, with
    `marketplace.Error` mapped to its status and a `None` result to 204 (marketplace spec, "HTTP routes")."""
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    args = [user]
    if id is not None:
        if not (id.isascii() and id.isdigit() and 0 < int(id) < 2**63):
            return not_found()
        args.append(int(id))
    try:
        result = call(*args)
    except marketplace.Error as e:
        return not_found() if e.error == "not_found" else JSONResponse({"error": e.error, "detail": str(e)}, e.status)
    return Response(status_code=204) if result is None else result


async def json_body(request: Request):
    """The request's JSON object (`{}` for an empty body), or None when it is not a JSON object."""
    raw = await request.body()
    try:
        body = json.loads(raw) if raw.strip() else {}
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def fields(body, *names, optional=()):
    if body is None:
        raise marketplace.Error("invalid", "the body must be a JSON object")
    for name in names + optional:
        if (name in names or name in body) and not isinstance(body.get(name), str):
            raise marketplace.Error("invalid", f"'{name}' must be a string")
    return [body.get(name) for name in names + optional]


def api_of(request: Request) -> Api:
    return Api(request.headers.get("cookie", ""))


@app.get("/fitit/marketplace/features")
def search_features(request: Request, q: str = "", sort: str = "subscribers"):
    return market(request, lambda user: marketplace.search(user, q, sort))


@app.post("/fitit/marketplace/features")
async def publish_feature(request: Request):
    body = await json_body(request)

    def publish(user):
        result = marketplace.publish(user, *fields(body, "hook", "title", "description"))
        return JSONResponse(result, 201 if result["created"] else 200)
    return await run_in_threadpool(market, request, publish)


@app.get("/fitit/marketplace/features/{id}")
def get_feature(id: str, request: Request):
    return market(request, marketplace.get_feature, id)


@app.delete("/fitit/marketplace/features/{id}")
def unpublish_feature(id: str, request: Request):
    return market(request, marketplace.unpublish, id)


@app.post("/fitit/marketplace/features/{id}/subscribe")
def subscribe_feature(id: str, request: Request):
    return market(request, lambda user, fid: marketplace.subscribe(user, fid, api_of(request)), id)


@app.post("/fitit/marketplace/features/{id}/toggle")
def toggle_feature(id: str, request: Request):
    return market(request, lambda user, fid: {"feature_id": fid, "enabled": marketplace.toggle(user, fid)}, id)


@app.post("/fitit/marketplace/features/{id}/unsubscribe")
def unsubscribe_feature(id: str, request: Request):
    return market(request, marketplace.unsubscribe, id)


@app.post("/fitit/marketplace/features/{id}/accept-update")
def accept_update(id: str, request: Request):
    return market(request, lambda user, fid: marketplace.accept_update(user, fid, api_of(request)), id)


@app.post("/fitit/marketplace/features/{id}/fork")
async def fork_feature(id: str, request: Request):
    body = await json_body(request)

    def fork(user, fid):
        (name,) = fields(body, optional=("name",))
        return JSONResponse(marketplace.fork(user, fid, api_of(request), name), 201)
    return await run_in_threadpool(market, request, fork, id)


@app.post("/fitit/marketplace/features/{id}/report")
async def report_feature(id: str, request: Request):
    body = await json_body(request)
    return await run_in_threadpool(
        market, request, lambda user, fid: marketplace.report(user, fid, *fields(body, "reason")), id)


@app.post("/fitit/transcribe")
async def transcribe_audio(request: Request, language: str = ""):
    user = resolve_user(request.headers.get("cookie", ""))
    if not user:
        return unauthenticated()
    too_large = JSONResponse({"error": "audio_too_large"}, 413)
    if int(request.headers.get("content-length") or 0) > transcribe.MAX_BYTES:
        return too_large
    from agent import rate_limited  # lazy, as in ws(); shares the agent's per-user hourly budget
    if rate_limited(user):
        return JSONResponse({"error": "rate_limited"}, 429)
    audio = b""
    async for chunk in request.stream():
        audio += chunk
        if len(audio) > transcribe.MAX_BYTES:
            return too_large
    try:
        text = await run_in_threadpool(
            transcribe.transcribe, audio, request.headers.get("content-type", ""), language
        )
    except transcribe.TranscriptionError as error:
        return JSONResponse({"error": error.code}, error.status)
    return {"text": text}


@app.websocket("/fitit/ws")
async def ws(websocket: WebSocket):
    if cross_origin(websocket.headers):
        await websocket.accept()
        await websocket.close(code=4403)
        return
    cookie = websocket.headers.get("cookie", "")
    user = await run_in_threadpool(resolve_user, cookie)
    if not user:
        await websocket.accept()
        await websocket.close(code=4401)
        return
    conversation = websocket.query_params.get("conversation", "default")
    if not valid_conversation(conversation):
        await websocket.accept()
        await websocket.close(code=4400)
        return
    from agent import handle_ws  # lazy: the agent module is optional for the proxy
    await handle_ws(websocket, user, Api(cookie), conversation)


@app.api_route("/x/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def virtual(request: Request):
    start = time.perf_counter()
    method, path = request.method, request.url.path
    cookie = request.headers.get("cookie", "")
    user = resolve_user(cookie)
    applied = []
    if not user:
        response = unauthenticated()
    else:
        response, applied = apply_hooks(user, cookie, method, path, None)
        response = response or not_found()
    return finish(response, user, method, path, start, applied)


@app.api_route("/{rest:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def proxy(request: Request):
    if request.url.path.startswith("/fitit/"):
        return not_found()  # core namespace: never forwarded
    body = await request.body()
    return await run_in_threadpool(forward, request, body)


def forward(request: Request, body: bytes) -> Response:
    start = time.perf_counter()
    method, path = request.method, request.url.path
    cookie = request.headers.get("cookie", "")
    user = resolve_user(cookie)
    url = base_url() + request.scope["raw_path"].decode() + (f"?{request.url.query}" if request.url.query else "")
    headers = [(k, v) for k, v in request.headers.items() if k not in DROP_REQUEST]
    if request.client:
        headers.append(("x-forwarded-for", request.client.host))
    up = httpx.request(method, url, headers=headers, content=body)

    applied = []
    content = up.content
    content_type = up.headers.get("content-type", "")
    if user and 200 <= up.status_code < 300 and content_type.split(";")[0].strip() == "application/json":
        info = request_info(method, path, request.url.query, body)
        hooked, applied = apply_hooks(user, cookie, method, path, up.json(), info)
        if hooked:
            return finish(hooked, user, method, path, start, applied)
    if user and content_type.startswith("text/html"):
        i = content.rfind(b"</body>")
        content = content[:i] + SCRIPT + content[i:] if i >= 0 else content + SCRIPT

    response = Response(content, up.status_code)
    for k, v in up.headers.multi_items():
        if k.lower() not in DROP_RESPONSE:
            response.headers.append(k, v)
    return finish(response, user, method, path, start, applied)
