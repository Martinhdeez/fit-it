"""Per-user hook runtime: guard, storage and execution. Spec: docs/specs/hook-contract.md."""
import ast
import difflib
import json
import math
import os
import re
import sys
import time
from datetime import date, datetime, timedelta

import db

NAME_RE = re.compile(r"^[a-z0-9_]{1,40}$")
USER_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
ROUTE_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE) (/\S*)$")

BANNED_NAMES = {"eval", "exec", "compile", "open", "globals", "locals", "vars", "getattr", "setattr",
                "delattr", "input", "breakpoint", "type", "object", "print"}
SAFE_BUILTINS = {
    f.__name__: f
    for f in (len, sum, min, max, abs, round, sorted, reversed, any, all, enumerate, zip, range, map, filter,
              str, int, float, bool, list, dict, set, tuple, isinstance)
}
SCOPE_VALUES = {"date": date, "datetime": datetime, "timedelta": timedelta, "math": math}


class HookError(Exception):
    def __init__(self, hook: str | None, detail: str):
        super().__init__(detail)
        self.hook = hook


class _Api:
    def __init__(self, api):
        self.get = api.get


class Ctx:
    def __init__(self, user: str, api):
        self.user = user
        self.api = _Api(api)  # only `get` is reachable from a hook
        self.logs: list[str] = []

    def log(self, *values):
        self.logs.append(" ".join(map(str, values)))


def check_code(code: str) -> tuple[str, str]:
    # ponytail: AST deny-list + allow-listed builtins, not a real sandbox: no memory limit, and
    # str.format can read attributes. Upgrade path: run hooks in a subprocess with resource limits (ADR 0004).
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        raise ValueError(f"line {e.lineno}: syntax error: {e.msg}")
    for node in ast.walk(tree):
        line = getattr(node, "lineno", "?")
        if isinstance(node, (ast.Import, ast.ImportFrom, ast.Global, ast.Nonlocal)):
            raise ValueError(f"line {line}: import, global and nonlocal are not allowed")
        if isinstance(node, ast.Attribute) and node.attr.startswith("_"):
            raise ValueError(f"line {line}: private attribute '{node.attr}' is not allowed")
        # Name.id, arg/keyword.arg, def/class/except-as .name
        name = getattr(node, "id", None) or getattr(node, "arg", None) or getattr(node, "name", None)
        if isinstance(name, str) and (name.startswith("_") or name in BANNED_NAMES):
            raise ValueError(f"line {line}: name '{name}' is not allowed")

    route = run = None
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "ROUTE" for t in node.targets):
            route = node
        if isinstance(node, ast.FunctionDef) and node.name == "run":
            run = node
    if route is None:
        raise ValueError("ROUTE is missing")
    if not (isinstance(route.value, ast.Constant) and isinstance(route.value.value, str)):
        raise ValueError(f"line {route.lineno}: ROUTE must be a string")
    m = ROUTE_RE.match(route.value.value)
    if not m:
        raise ValueError(f"line {route.lineno}: ROUTE must be '<GET|POST|PUT|PATCH|DELETE> /path'")
    method, path = m.groups()
    if path.startswith("/x/") and method != "GET":
        raise ValueError(f"line {route.lineno}: virtual endpoints (/x/...) only allow GET")
    if run is None:
        raise ValueError("run(data, ctx) is missing")
    return method, path


def _diff(name: str, old: str, new: str) -> str:
    return "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{name}.py", f"b/{name}.py"))


def _valid(user: str, name: str) -> bool:
    return bool(USER_RE.match(user) and NAME_RE.match(name))


def _route(code: str) -> str | None:
    try:
        return " ".join(check_code(code))
    except ValueError:
        return None


def list_hooks(user: str) -> list[dict]:
    if not USER_RE.match(user):
        return []
    with db.connect() as conn:
        rows = conn.execute("SELECT name, enabled, code FROM hooks WHERE user_id = %s ORDER BY name", (user,)).fetchall()
    return [{"name": name, "route": _route(code), "enabled": enabled, "code": code} for name, enabled, code in rows]


def read_hook(user: str, name: str) -> str:
    if _valid(user, name):
        with db.connect() as conn:
            row = conn.execute("SELECT code FROM hooks WHERE user_id = %s AND name = %s", (user, name)).fetchone()
        if row:
            return row[0]
    raise KeyError(name)


def save_hook(user: str, name: str, code: str) -> str:
    if not USER_RE.match(user):
        raise ValueError(f"invalid user id '{user}'")
    if not NAME_RE.match(name):
        raise ValueError(f"invalid hook name '{name}': must match {NAME_RE.pattern}")
    route = " ".join(check_code(code))
    with db.connect() as conn:
        old = conn.execute("SELECT code FROM hooks WHERE user_id = %s AND name = %s FOR UPDATE", (user, name)).fetchone()
        (version,) = conn.execute(
            "INSERT INTO hook_versions (user_id, name, version, code) SELECT %s, %s, coalesce(max(version), 0) + 1, %s"
            " FROM hook_versions WHERE user_id = %s AND name = %s RETURNING version",
            (user, name, code, user, name)).fetchone()
        conn.execute(
            "INSERT INTO hooks (user_id, name, route, code, enabled, version) VALUES (%s, %s, %s, %s, true, %s)"
            " ON CONFLICT (user_id, name) DO UPDATE SET route = excluded.route, code = excluded.code,"
            " enabled = true, version = excluded.version, updated_at = now()",
            (user, name, route, code, version))
    return _diff(name, old[0] if old else "", code)


def undo_hook(user: str, name: str) -> tuple[int, str]:
    if not _valid(user, name):
        raise KeyError(name)
    with db.connect() as conn:
        row = conn.execute("SELECT code, version FROM hooks WHERE user_id = %s AND name = %s FOR UPDATE",
                           (user, name)).fetchone()
        if not row:
            raise KeyError(name)
        current, version = row
        prev = conn.execute("SELECT code FROM hook_versions WHERE user_id = %s AND name = %s AND version = %s",
                            (user, name, version - 1)).fetchone()
        if not prev:
            raise ValueError("no earlier version")
        route = " ".join(check_code(prev[0]))  # a guard tightened since that save still applies
        conn.execute("UPDATE hooks SET code = %s, route = %s, version = %s, updated_at = now()"
                     " WHERE user_id = %s AND name = %s", (prev[0], route, version - 1, user, name))
    return version - 1, _diff(name, current, prev[0])


def toggle_hook(user: str, name: str) -> bool:
    if _valid(user, name):
        with db.connect() as conn:
            row = conn.execute("UPDATE hooks SET enabled = NOT enabled, updated_at = now()"
                               " WHERE user_id = %s AND name = %s RETURNING enabled", (user, name)).fetchone()
        if row:
            return row[0]
    raise KeyError(name)


def delete_hook(user: str, name: str) -> None:
    if _valid(user, name):
        with db.connect() as conn:
            if conn.execute("DELETE FROM hooks WHERE user_id = %s AND name = %s", (user, name)).rowcount:
                return
    raise KeyError(name)


def _matches(route_path: str, path: str) -> bool:
    want, got = route_path.split("/"), path.split("/")
    return len(want) == len(got) and all(
        w == g or (w.startswith("{") and w.endswith("}") and g != "") for w, g in zip(want, got))


class _TimeLimit(BaseException):
    """BaseException so an `except Exception` inside code the hook calls (ctx.api.get) cannot swallow it."""


def _run(hook: str | None, code: str, data, ctx):
    timeout_ms = int(os.environ.get("FITIT_HOOK_TIMEOUT_MS", "2000"))
    max_bytes = int(os.environ.get("FITIT_HOOK_MAX_BYTES", "1000000"))
    deadline = time.monotonic() + timeout_ms / 1000

    # ponytail: the deadline is checked between Python events only, so a single long C call
    # (sum(range(10**12))) is not interrupted, and CPython drops a tracer once it raises, so a loop in a
    # bare `except:` or `finally:` that runs after the breach is not stopped either. Upgrade path: run
    # hooks in a killable subprocess (ADR 0020).
    def tracer(frame, event, arg):
        if time.monotonic() > deadline:
            raise _TimeLimit
        return tracer

    scope = {"__builtins__": SAFE_BUILTINS, **SCOPE_VALUES}
    previous = sys.gettrace()
    sys.settrace(tracer)
    try:
        exec(compile(code, f"{hook or 'dry_run'}.py", "exec"), scope)
        data = scope["run"](data, ctx)
        size = len(json.dumps(data, ensure_ascii=False).encode())
    except _TimeLimit:
        raise HookError(hook, f"time limit of {timeout_ms} ms exceeded")
    except Exception as e:
        raise HookError(hook, f"{type(e).__name__}: {e}")
    finally:
        sys.settrace(previous)
    if size > max_bytes:
        raise HookError(hook, f"output of {size} bytes is over the limit of {max_bytes} bytes")
    return data


def run_hooks(user: str, method: str, path: str, data, ctx):
    applied = []
    for hook in list_hooks(user):
        if not hook["enabled"]:
            continue
        name = hook["name"]
        try:
            route_method, route_path = check_code(hook["code"])
        except ValueError as e:
            raise HookError(name, str(e))  # a stored hook that no longer passes the guard fails closed
        if route_method != method.upper() or not _matches(route_path, path):
            continue
        logged, start = len(ctx.logs), time.perf_counter()
        data = _run(name, hook["code"], data, ctx)
        applied.append({"name": name, "route": hook["route"], "ms": round((time.perf_counter() - start) * 1000, 3),
                        "logs": ctx.logs[logged:]})
    return data, applied


def dry_run(user: str, code: str, data, api):
    check_code(code)
    return _run(None, code, data, Ctx(user, api))
