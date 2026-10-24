"""Coding agent: turns a chat message into a validated hook. Spec: docs/specs/agent.md."""
import json
import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic_ai import Agent, CallToolsNode, FunctionToolCallEvent, FunctionToolResultEvent, ModelRetry, RunContext
from starlette.websockets import WebSocketDisconnect, WebSocketState

import hooks
import trace

MAX_REJECTIONS = 3

SYSTEM_PROMPT = """You are Fit-it, a coding agent that personalizes a web app for one user by writing hooks.
A hook rewrites the JSON body of one endpoint of the app, for this user only. The app itself is never changed.

How to work:
- Always call list_endpoints, then get_endpoint on the relevant GET endpoint to see real data, before writing a hook.
- Change only what the user asked for. One hook per request. Use list_hooks and read_hook to edit an existing hook
  instead of adding a second one for the same change.
- write_hook validates and dry-runs the code. If it is rejected, read the reason, fix the code and call it again.
- Answer in the user's language, in at most three sentences.

Hook contract. A hook is one Python file with exactly this shape:

ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        if datetime.fromisoformat(invoice["date"]).weekday() == 4:
            invoice["amount"] = round(invoice["amount"] * 0.85, 2)
    ctx.log("discounted Friday invoices")
    return data

- ROUTE is a string "<METHOD> <path>", METHOD one of GET POST PUT PATCH DELETE. path starts with / and may contain
  {param} segments that match exactly one path segment.
- A path starting with /x/ is a virtual endpoint that does not exist in the app (GET only); there data is None and
  run builds the body itself, for example from ctx.api.get calls.
- run(data, ctx) receives the parsed JSON body the app returned and returns a JSON-serializable value: the body the
  user receives.
- ctx.user is the current user id. ctx.api.get(path) does a GET on the app as the current user and returns parsed
  JSON (path starts with /; raises on non-2xx; no other HTTP method exists). ctx.log(*values) writes a trace line.
- Not allowed: import, from ... import, global, nonlocal, any name or attribute starting with _, and the names
  eval exec compile open globals locals vars getattr setattr delattr input breakpoint type object print.
- Only these names exist: len sum min max abs round sorted reversed any all enumerate zip range map filter str int
  float bool list dict set tuple isinstance, plus date, datetime, timedelta (from the datetime module) and math.
- If the hook raises, the user's request fails closed, so handle missing keys and None values.
"""


class TooManyRejections(Exception):
    pass


@dataclass
class Deps:
    user: str
    api: object
    events: list = field(default_factory=list)  # hook_saved / hook_rejected, flushed after each tool result
    rejections: int = 0


# No model here: FITIT_MODEL is read at run time (handle_ws), so importing needs no API key.
agent = Agent(deps_type=Deps, output_type=str, system_prompt=SYSTEM_PROMPT, retries=3)


def model_name() -> str:
    return os.environ.get("FITIT_MODEL", "anthropic:claude-opus-5-5")


@agent.tool
def list_endpoints(ctx: RunContext[Deps]) -> str:
    """List the app's endpoints, one `METHOD path: summary` line per operation."""
    spec = ctx.deps.api.get("/openapi.json")
    return "\n".join(
        f"{method.upper()} {path}: {op.get('summary', '')}"
        for path, ops in spec.get("paths", {}).items()
        for method, op in ops.items()
        if isinstance(op, dict))


@agent.tool
def get_endpoint(ctx: RunContext[Deps], path: str) -> str:
    """GET an app endpoint as the current user and return its JSON body (cut to 4000 characters)."""
    try:
        body = ctx.deps.api.get(path)
    except Exception as e:
        raise ModelRetry(f"GET {path} failed: {e}")
    return json.dumps(body, ensure_ascii=False)[:4000]


@agent.tool
def list_hooks(ctx: RunContext[Deps]) -> list[dict]:
    """List the user's hooks: name, route, enabled."""
    return [{k: h[k] for k in ("name", "route", "enabled")} for h in hooks.list_hooks(ctx.deps.user)]


@agent.tool
def read_hook(ctx: RunContext[Deps], name: str) -> str:
    """Return the source code of one of the user's hooks."""
    try:
        return hooks.read_hook(ctx.deps.user, name)
    except KeyError:
        raise ModelRetry(f"no hook named '{name}'")


@agent.tool
def delete_hook(ctx: RunContext[Deps], name: str) -> str:
    """Delete one of the user's hooks."""
    try:
        hooks.delete_hook(ctx.deps.user, name)
    except KeyError:
        raise ModelRetry(f"no hook named '{name}'")
    trace.emit("hook.deleted", user=ctx.deps.user, hook=name)
    return f"deleted {name}"


def dry_run(deps: Deps, name: str, code: str, method: str, path: str):
    """Run the candidate hook the way the runtime does. Returns its output, or None if the route is skipped."""
    virtual = path.startswith("/x/")
    if not virtual and (method != "GET" or "{" in path):
        return None
    data = None if virtual else deps.api.get(path)
    # hooks.py exports no "run this code" helper, so the candidate is saved under a throwaway user id and run
    # through run_hooks, then removed. The real user's hooks are never touched.
    temp = f"dryrun-{uuid.uuid4().hex}"
    try:
        hooks.save_hook(temp, name, code)
        result, _ = hooks.run_hooks(temp, method, path, data, hooks.Ctx(deps.user, deps.api))
        return result
    finally:
        shutil.rmtree(Path(os.environ.get("WORKSPACES", "./workspaces")) / temp, ignore_errors=True)


@agent.tool(retries=MAX_REJECTIONS + 1)
def write_hook(ctx: RunContext[Deps], name: str, code: str) -> str:
    """Validate, dry-run and save a hook (enabled), replacing any hook with the same name."""
    deps = ctx.deps
    try:
        method, path = hooks.check_code(code)
        preview = dry_run(deps, name, code, method, path)
        diff = hooks.save_hook(deps.user, name, code)
    except Exception as e:
        reason = str(e)
        deps.rejections += 1
        deps.events.append({"type": "hook_rejected", "name": name, "reason": reason})
        trace.emit("hook.rejected", user=deps.user, hook=name, reason=reason)
        if deps.rejections >= MAX_REJECTIONS:
            raise TooManyRejections(f"hook rejected {MAX_REJECTIONS} times, last reason: {reason}")
        raise ModelRetry(reason)
    route = f"{method} {path}"
    deps.events.append({"type": "hook_saved", "name": name, "route": route, "diff": diff})
    trace.emit("hook.saved", user=deps.user, hook=name, route=route, diff=diff)
    if preview is None:
        return "saved (no dry run for this route)"
    return "saved, dry-run output: " + json.dumps(preview, ensure_ascii=False)[:2000]


async def handle_ws(websocket, user: str, api) -> None:
    if websocket.client_state == WebSocketState.CONNECTING:
        await websocket.accept()
    history = []
    while True:
        try:
            msg = await websocket.receive_json()
        except WebSocketDisconnect:
            return
        text = msg.get("text") if isinstance(msg, dict) and msg.get("type") == "message" else None
        if not isinstance(text, str):
            await websocket.send_json({"type": "error", "message": 'expected {"type": "message", "text": "..."}'})
            await websocket.send_json({"type": "done"})
            continue
        trace.emit("agent.message", user=user, text=text)
        deps = Deps(user=user, api=api)

        async def flush():
            while deps.events:
                await websocket.send_json(deps.events.pop(0))

        try:
            async with agent.iter(text, deps=deps, message_history=history, model=model_name()) as run:
                async for node in run:
                    if not isinstance(node, CallToolsNode):
                        continue
                    async with node.stream(run.ctx) as events:
                        async for event in events:
                            if isinstance(event, FunctionToolCallEvent):
                                name, args = event.part.tool_name, event.part.args_as_dict()
                                trace.emit("agent.tool", user=user, tool=name, input=args)
                                await websocket.send_json({"type": "tool", "name": name, "input": args})
                            elif isinstance(event, FunctionToolResultEvent):
                                await flush()
            await flush()
            history = run.result.all_messages()
            trace.emit("agent.answer", user=user, text=run.result.output)
            await websocket.send_json({"type": "text", "text": run.result.output})
        except Exception as e:
            await flush()
            await websocket.send_json({"type": "error", "message": str(e) or type(e).__name__})
        await websocket.send_json({"type": "done"})
