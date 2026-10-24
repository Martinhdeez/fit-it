"""Fit-it core: reverse proxy, per-user hooks, widget injection. Spec: docs/specs/proxy.md."""
import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import db
import hooks
import trace


@asynccontextmanager
async def lifespan(app):
    await run_in_threadpool(db.init)
    yield


app = FastAPI(lifespan=lifespan)

EMBED = Path(__file__).parent / "static" / "embed.js"
UI = Path(__file__).parent / "static" / "ui"  # widget static export (ADR 0015)
SCRIPT = b'<script src="/fitit/embed.js" defer></script>'
DROP_REQUEST = {"host", "content-length", "accept-encoding", "connection"}
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
    # ponytail: one /api/me call per request; cache by session cookie if latency matters (ADR 0006)
    try:
        r = httpx.get(base_url() + "/api/me", headers={"cookie": cookie})
        user = r.json()["id"] if r.status_code == 200 else None
    except Exception:
        return None
    return user if isinstance(user, str) else None


def route_of(user: str, name: str):
    return next((h["route"] for h in hooks.list_hooks(user) if h["name"] == name), None)


def apply_hooks(user, cookie, method, path, data):
    """Returns (response, applied): the hooked JSON, a 502 on HookError, or None if no hook matched."""
    ctx = hooks.Ctx(user, Api(cookie))
    start = time.perf_counter()
    try:
        result, applied = hooks.run_hooks(user, method, path, data, ctx)
    except hooks.HookError as e:
        trace.emit("hook.failed", user=user, hook=e.hook, route=route_of(user, e.hook), error=str(e))
        return JSONResponse({"error": "hook_failed", "hook": e.hook, "detail": str(e)}, 502), []
    ms = round((time.perf_counter() - start) * 1000, 1)
    # ponytail: run_hooks runs the chain as one call, so ms and logs are for the whole chain, not per hook
    for name in applied:
        trace.emit("hook.applied", user=user, hook=name, route=route_of(user, name), ms=ms, logs=list(ctx.logs))
    if not applied:
        return None, []
    return JSONResponse(result, headers={"x-fitit-hooks": ",".join(applied)}), applied


def finish(response: Response, user, method, path, start, applied) -> Response:
    trace.emit("request.proxied", user=user, method=method, path=path, status=response.status_code,
               ms=round((time.perf_counter() - start) * 1000, 1), hooks=applied)
    return response


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


@app.websocket("/fitit/ws")
async def ws(websocket: WebSocket):
    cookie = websocket.headers.get("cookie", "")
    user = await run_in_threadpool(resolve_user, cookie)
    if not user:
        await websocket.accept()
        await websocket.close(code=4401)
        return
    from agent import handle_ws  # lazy: the agent module is optional for the proxy
    await handle_ws(websocket, user, Api(cookie))


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
    up = httpx.request(method, url, headers=headers, content=body)

    applied = []
    content = up.content
    content_type = up.headers.get("content-type", "")
    if user and 200 <= up.status_code < 300 and content_type.split(";")[0].strip() == "application/json":
        hooked, applied = apply_hooks(user, cookie, method, path, up.json())
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
