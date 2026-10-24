"""Coding agent: turns a chat message into a validated hook, or finds a published one. Spec: docs/specs/agent.md."""
import json
import logging
import os
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from psycopg.types.json import Jsonb
from pydantic_ai import (Agent, CallToolsNode, FunctionToolCallEvent, FunctionToolResultEvent, ModelMessagesTypeAdapter,
                         ModelRetry, RunContext)
from starlette.websockets import WebSocketDisconnect, WebSocketState

import db
import hooks
import marketplace
import trace

MAX_REJECTIONS = 3
HISTORY_TURNS = 20

SYSTEM_PROMPT = """You are Fit-it, a coding agent that personalizes a web app for one user by writing hooks.
A hook rewrites the JSON body of one endpoint of the app, for this user only. The app itself is never changed.

How to work:
- Always call list_endpoints, then get_endpoint on the relevant GET endpoint to see real data, before writing a hook.
- Change only what the user asked for. One hook per request.
- To change an existing behaviour, read_hook it, then write_hook with the SAME name and the new code. Never add a
  second hook for something an existing hook already does.
- Before changing an existing hook, always read_hook it: the stored code is the source of truth and may have been
  changed or undone outside this chat. Never re-apply values from earlier turns without reading it.
- When one of the user's hooks already runs on the same route, edit it only when the request changes what that hook
  does. An unrelated change goes in a new hook with its own name (hooks on a route chain in name order).
- If write_hook is rejected with conflict:, the hook changed since you saw it: read_hook it again and reapply your
  change.
- write_hook validates and dry-runs the code. If it is rejected, read the reason, fix the code and call it again.
- When the user asks to undo or go back, call undo_hook with the hook's name.
- Reply in the language of the user's latest message, in at most three sentences.

Marketplace. Other users of the app publish their hooks as features (named @<id>) that this user can subscribe to.
- Before writing a new hook, call search_features. If a feature does what the user asked, describe it and ask whether
  to subscribe. Call subscribe_feature only after the user agrees in a later message.
- To publish a hook, read_hook it, draft a short title and description from its code, show them to the user, and call
  publish_hook only after the user confirms in a later message. unpublish_feature hides it again.
- When the user asks to copy (fork) a feature into their own hooks, call search_features and then fork_feature right
  away: no subscription or confirmation needed. Name the hook in snake_case after the feature. To change a feature
  they subscribe to, a subscriber forks it too, then edits the copy. unsubscribe_feature removes a subscription.
- When a subscriber asks for the new version of a feature, call accept_update.

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
    hooks: str = ""  # the user's hooks when the message started, for the instructions
    subscriptions: str = ""  # likewise, the user's subscriptions
    versions: dict = field(default_factory=dict)  # name -> hook version this turn last saw; missing is 0 (ADR 0025)


# ponytail: in memory, per process; Postgres-backed if the core scales out (plan 02, Next)
_sent: dict[str, deque] = defaultdict(deque)
_busy: set[tuple[str, str]] = set()  # (user, conversation) with a message running; same ceiling (ADR 0024)


def reset_rate_limits() -> None:
    _sent.clear()


def rate_limited(user: str) -> bool:
    """Counts this message, unless the user already sent FITIT_AGENT_MESSAGES_PER_HOUR in the last hour."""
    limit = int(os.environ.get("FITIT_AGENT_MESSAGES_PER_HOUR", "60"))
    now, sent = time.monotonic(), _sent[user]
    while sent and sent[0] <= now - 3600:
        sent.popleft()
    if len(sent) >= limit:
        return True
    sent.append(now)
    return False


def hooks_summary(user: str) -> str:
    versions = hooks.versions(user)
    return "\n".join(f"- {h['name']}: {h['route']} (v{versions.get(h['name'])}, {'enabled' if h['enabled'] else 'disabled'})"
                     for h in hooks.list_hooks(user)) or "no hooks yet"


# No model here: FITIT_MODEL is read at run time (handle_ws), so importing needs no API key.
# instructions, not system_prompt: sent with every request, so it survives the history window (ADR 0019).
agent = Agent(deps_type=Deps, output_type=str, instructions=SYSTEM_PROMPT, retries=3)


@agent.instructions
def current_hooks(ctx: RunContext[Deps]) -> str:
    if not ctx.deps.hooks:  # read once, at the message's first model request
        ctx.deps.hooks = hooks_summary(ctx.deps.user)
    return "The user's current hooks (name: route):\n" + ctx.deps.hooks


def subscriptions_summary(user: str) -> str:
    return "\n".join(
        f"- @{s['feature_id']} {s['title']}: {s['route']} (v{s['version']}, {'enabled' if s['enabled'] else 'disabled'}"
        f"{', update available' if s['update_available'] else ''})"
        for s in marketplace.subscriptions(user)) or "no subscriptions"


@agent.instructions
def current_subscriptions(ctx: RunContext[Deps]) -> str:
    if not ctx.deps.subscriptions:
        ctx.deps.subscriptions = subscriptions_summary(ctx.deps.user)
    return "The user's subscriptions (@id title: route):\n" + ctx.deps.subscriptions


def model_name() -> str:
    return os.environ.get("FITIT_MODEL", "anthropic:claude-opus-5-5")


STALE_CODE = "<omitted: call read_hook for the current code>"


def load_history(user: str, conversation: str = "default") -> list:
    """The conversation's last HISTORY_TURNS turns, oldest first, as one message list."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT turn FROM (SELECT id, turn FROM messages WHERE user_id = %s AND conversation_id = %s"
            " ORDER BY id DESC LIMIT %s) t ORDER BY id", (user, conversation, HISTORY_TURNS)).fetchall()
    messages = [m for (turn,) in rows for m in turn]
    for m in messages:  # old hook code is stale after an undo or another turn's save: the model must read_hook again
        for part in m.get("parts", []):
            if part.get("part_kind") == "tool-call" and part.get("tool_name") == "write_hook":
                args = part.get("args")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        continue
                if isinstance(args, dict) and "code" in args:
                    part["args"] = {**args, "code": STALE_CODE}
            elif part.get("part_kind") == "tool-return" and part.get("tool_name") == "read_hook":
                part["content"] = STALE_CODE
    return ModelMessagesTypeAdapter.validate_python(messages)


def save_turn(user: str, messages: list, conversation: str = "default") -> None:
    turn = ModelMessagesTypeAdapter.dump_python(messages, mode="json")
    for m in turn:
        if m.get("kind") == "request":
            m["instructions"] = None  # the prompt is never stored (ADR 0019)
    with db.connect() as conn:
        conn.execute("INSERT INTO messages (user_id, turn, conversation_id) VALUES (%s, %s, %s)",
                     (user, Jsonb(turn), conversation))


def clear_history(user: str, conversation: str | None = None) -> None:
    with db.connect() as conn:
        if conversation is None:
            conn.execute("DELETE FROM messages WHERE user_id = %s", (user,))
        else:
            conn.execute("DELETE FROM messages WHERE user_id = %s AND conversation_id = %s", (user, conversation))


def list_conversations(user: str) -> list[dict]:
    """The user's conversations with a stored turn, latest activity first (agent spec, "Conversations")."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT c.conversation_id, c.last, c.n, f.turn FROM (SELECT conversation_id, max(created_at) last,"
            " count(*) n, min(id) first FROM messages WHERE user_id = %s GROUP BY conversation_id) c"
            " JOIN messages f ON f.id = c.first ORDER BY c.last DESC, c.conversation_id", (user,)).fetchall()
    result = []
    for cid, last, n, turn in rows:
        title = next((p.get("content") for m in turn for p in m.get("parts", [])
                      if p.get("part_kind") == "user-prompt"), "")
        result.append({"id": cid, "title": title[:60] if isinstance(title, str) else "",
                       "last_activity": last.isoformat(), "turns": n})
    return result


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
        code, ctx.deps.versions[name] = hooks.read_hook_version(ctx.deps.user, name)
    except KeyError:
        raise ModelRetry(f"no hook named '{name}'")
    return code


@agent.tool
def delete_hook(ctx: RunContext[Deps], name: str) -> str:
    """Delete one of the user's hooks."""
    try:
        hooks.delete_hook(ctx.deps.user, name)
    except KeyError:
        raise ModelRetry(f"no hook named '{name}'")
    ctx.deps.versions[name] = 0
    trace.emit("hook.deleted", user=ctx.deps.user, hook=name)
    return f"deleted {name}"


@agent.tool
def undo_hook(ctx: RunContext[Deps], name: str) -> str:
    """Restore the previous version of one of the user's hooks."""
    deps = ctx.deps
    try:
        version, diff = hooks.undo_hook(deps.user, name)
    except KeyError:
        raise ModelRetry(f"no hook named '{name}'")
    except ValueError:
        return "nothing to undo: no earlier version"
    deps.versions[name] = version
    route = next(h["route"] for h in hooks.list_hooks(deps.user) if h["name"] == name)
    deps.events.append({"type": "hook_saved", "name": name, "route": route, "diff": diff})
    trace.emit("hook.restored", user=deps.user, hook=name, version=version)
    return f"restored version {version}"


def dry_run(deps: Deps, name: str, code: str, method: str, path: str):
    """Run the candidate inside the user's chain for its route. Returns the chain's output, or None if skipped."""
    virtual = path.startswith("/x/")
    if not virtual and (method != "GET" or "{" in path):
        return None
    data = None if virtual else deps.api.get(path)
    try:
        return hooks.dry_run(deps.user, code, data, deps.api, name=name, features=marketplace.active(deps.user))
    except hooks.HookError as e:
        if e.hook is None:
            raise
        raise ValueError(f"{e.hook} failed: {e}")


@agent.tool(retries=MAX_REJECTIONS + 1)
def write_hook(ctx: RunContext[Deps], name: str, code: str) -> str:
    """Validate, dry-run and save a hook (enabled), replacing any hook with the same name."""
    deps = ctx.deps
    try:
        method, path = hooks.check_code(code)
        preview = dry_run(deps, name, code, method, path)
        diff = hooks.save_hook(deps.user, name, code, expected_version=deps.versions.get(name, 0))
    except Exception as e:
        reason = str(e)
        retry = reason + (". Call read_hook and apply your change again." if isinstance(e, hooks.Conflict) else "")
        deps.rejections += 1
        deps.events.append({"type": "hook_rejected", "name": name, "reason": reason})
        trace.emit("hook.rejected", user=deps.user, hook=name, reason=reason)
        if deps.rejections >= MAX_REJECTIONS:
            raise TooManyRejections(f"hook rejected {MAX_REJECTIONS} times, last reason: {reason}")
        raise ModelRetry(retry)
    try:  # the saved version; if another turn saved again in between, keep the stale one so the next write conflicts
        current, version = hooks.read_hook_version(deps.user, name)
        if current == code:
            deps.versions[name] = version
    except KeyError:
        pass
    route = f"{method} {path}"
    deps.events.append({"type": "hook_saved", "name": name, "route": route, "diff": diff})
    trace.emit("hook.saved", user=deps.user, hook=name, route=route, diff=diff)
    if preview is None:
        return "saved (no dry run for this route)"
    return "saved, dry-run output: " + json.dumps(preview, ensure_ascii=False)[:2000]


def changed(deps: Deps, feature_id: int, action: str) -> None:
    deps.events.append({"type": "feature_changed", "feature_id": feature_id, "action": action})


# Marketplace tools: the rules and feature.* trace events live in marketplace.py. A marketplace.Error is returned as
# text, not raised as ModelRetry, so the model can explain it (agent spec, "Marketplace tools").
@agent.tool
def search_features(ctx: RunContext[Deps], query: str) -> str:
    """Search published features by words in their title or description; an empty query lists the most used."""
    try:
        found = marketplace.search(ctx.deps.user, query)[:10]
    except marketplace.Error as e:
        return f"{e.error}: {e}"
    return "\n".join(
        f"@{f['id']} {f['title']} — {f['route']} — {f['description']} ({f['subscribers']} subscribers, v{f['version']})"
        + (" [mine]" if f["mine"] else "") + (" [subscribed]" if f["subscribed"] else "")
        for f in found) or "no features found"


@agent.tool
def publish_hook(ctx: RunContext[Deps], name: str, title: str, description: str) -> str:
    """Publish one of the user's hooks as a feature. Only after the user confirmed the title and description."""
    try:
        f = marketplace.publish(ctx.deps.user, name, title, description)
    except marketplace.Error as e:
        return f"{e.error}: {e}"
    changed(ctx.deps, f["id"], "published")
    return f"published @{f['id']} version {f['version']}"


@agent.tool
def unpublish_feature(ctx: RunContext[Deps], feature_id: int) -> str:
    """Hide one of the user's own features from the marketplace; existing subscribers keep it."""
    try:
        marketplace.unpublish(ctx.deps.user, feature_id)
    except marketplace.Error as e:
        return f"{e.error}: {e}"
    changed(ctx.deps, feature_id, "unpublished")
    return f"unpublished @{feature_id}"


@agent.tool
def subscribe_feature(ctx: RunContext[Deps], feature_id: int) -> str:
    """Subscribe the user to a feature, after a dry run on their data. Only after the user agreed."""
    try:
        s = marketplace.subscribe(ctx.deps.user, feature_id, ctx.deps.api)
    except marketplace.Error as e:
        return f"{e.error}: {e}"
    changed(ctx.deps, feature_id, "subscribed")
    return f"subscribed to @{feature_id} version {s['version']}"


@agent.tool
def unsubscribe_feature(ctx: RunContext[Deps], feature_id: int) -> str:
    """Remove the user's subscription to a feature."""
    try:
        marketplace.unsubscribe(ctx.deps.user, feature_id)
    except marketplace.Error as e:
        return f"{e.error}: {e}"
    changed(ctx.deps, feature_id, "unsubscribed")
    return f"unsubscribed from @{feature_id}"


@agent.tool
def accept_update(ctx: RunContext[Deps], feature_id: int) -> str:
    """Move the user's subscription to the feature's latest version, after a dry run."""
    try:
        s = marketplace.accept_update(ctx.deps.user, feature_id, ctx.deps.api)
    except marketplace.Error as e:
        return f"{e.error}: {e}"
    changed(ctx.deps, feature_id, "updated")
    return f"@{feature_id} updated to version {s['version']}"


@agent.tool
def fork_feature(ctx: RunContext[Deps], feature_id: int, name: str | None = None) -> str:
    """Copy a feature's code into a new hook of the user named `name` (default: the feature's hook name), ending any
    subscription to it."""
    deps = ctx.deps
    try:
        h = marketplace.fork(deps.user, feature_id, deps.api, name)
    except marketplace.Error as e:
        return f"{e.error}: {e}"
    deps.events.append({"type": "hook_saved", "name": h["name"], "route": h["route"], "diff": h["diff"]})
    changed(deps, feature_id, "forked")
    return f"forked @{feature_id} into {h['name']}"


async def handle_ws(websocket, user: str, api, conversation: str = "default") -> None:
    if websocket.client_state == WebSocketState.CONNECTING:
        await websocket.accept()
    try:
        while True:
            msg = await websocket.receive_json()
            text = msg.get("text") if isinstance(msg, dict) and msg.get("type") == "message" else None
            if not isinstance(text, str):
                await websocket.send_json({"type": "error", "message": 'expected {"type": "message", "text": "..."}'})
                await websocket.send_json({"type": "done"})
                continue
            trace.emit("agent.message", user=user, text=text, conversation=conversation)
            key = (user, conversation)
            if key in _busy:
                await websocket.send_json({"type": "error", "message": "conversation busy: a message is still running"})
                await websocket.send_json({"type": "done"})
                continue
            if rate_limited(user):
                limit = os.environ.get("FITIT_AGENT_MESSAGES_PER_HOUR", "60")
                message = f"rate limit: at most {limit} messages per hour"
                await websocket.send_json({"type": "error", "message": message})
                await websocket.send_json({"type": "done"})
                continue
            deps = Deps(user=user, api=api)

            async def flush():
                while deps.events:
                    await websocket.send_json(deps.events.pop(0))

            _busy.add(key)  # no await since the check above, so check-and-add is atomic on the event loop
            try:
                history = load_history(user, conversation)  # every message: nothing cached (ADR 0019)
                deps.versions = hooks.versions(user)  # the versions this message starts from (ADR 0025)
                async with agent.iter(text, deps=deps, message_history=history, model=model_name()) as run:
                    async for node in run:
                        if not isinstance(node, CallToolsNode):
                            continue
                        async with node.stream(run.ctx) as events:
                            async for event in events:
                                if isinstance(event, FunctionToolCallEvent):
                                    name, args = event.part.tool_name, event.part.args_as_dict()
                                    trace.emit("agent.tool", user=user, tool=name, input=args,
                                               conversation=conversation)
                                    await websocket.send_json({"type": "tool", "name": name, "input": args})
                                elif isinstance(event, FunctionToolResultEvent):
                                    await flush()
                await flush()
                save_turn(user, run.result.new_messages(), conversation)
                usage = run.result.usage
                trace.emit("agent.answer", user=user, text=run.result.output, conversation=conversation,
                           input_tokens=usage.input_tokens, output_tokens=usage.output_tokens)
                await websocket.send_json({"type": "text", "text": run.result.output})
            except WebSocketDisconnect:
                raise
            except Exception as e:
                await flush()
                if isinstance(e, TooManyRejections):
                    message = str(e)
                else:  # provider and config errors name env vars and internals: log them, show a generic text
                    logging.exception("agent turn failed")
                    message = "The assistant is unavailable right now. Try again later."
                await websocket.send_json({"type": "error", "message": message})
            finally:
                _busy.discard(key)
            await websocket.send_json({"type": "done"})
    except WebSocketDisconnect:
        pass  # the client left, maybe mid-turn: nothing more to send
