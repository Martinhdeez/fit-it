"""Per-user hook runtime: guard, storage and execution. Spec: docs/specs/hook-contract.md."""
import ast
import difflib
import json
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path

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
    def __init__(self, hook: str, detail: str):
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
    # ponytail: AST deny-list + allow-listed builtins, not a real sandbox: no time or memory limit
    # (an infinite loop blocks the worker), and str.format can read attributes. Upgrade path: run
    # hooks in a subprocess with resource limits (ADR 0004).
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


def _dir(user: str) -> Path:
    return Path(os.environ.get("WORKSPACES", "./workspaces")) / user


def _find(user: str, name: str) -> Path:
    if USER_RE.match(user) and NAME_RE.match(name):
        for p in (_dir(user) / f"{name}.py", _dir(user) / f"{name}.py.off"):
            if p.exists():
                return p
    raise KeyError(name)


def list_hooks(user: str) -> list[dict]:
    if not USER_RE.match(user) or not _dir(user).is_dir():
        return []
    hooks = []
    for p in _dir(user).iterdir():
        enabled = p.name.endswith(".py")
        name = p.name.removesuffix(".off").removesuffix(".py")
        if not (enabled or p.name.endswith(".py.off")) or not NAME_RE.match(name):
            continue
        code = p.read_text("utf-8")
        try:
            route = " ".join(check_code(code))
        except ValueError:
            route = None
        hooks.append({"name": name, "route": route, "enabled": enabled, "code": code})
    return sorted(hooks, key=lambda h: h["name"])


def read_hook(user: str, name: str) -> str:
    return _find(user, name).read_text("utf-8")


def save_hook(user: str, name: str, code: str) -> str:
    if not USER_RE.match(user):
        raise ValueError(f"invalid user id '{user}'")
    if not NAME_RE.match(name):
        raise ValueError(f"invalid hook name '{name}': must match {NAME_RE.pattern}")
    check_code(code)
    try:
        old_path = _find(user, name)
        old = old_path.read_text("utf-8")
        old_path.unlink()
    except KeyError:
        old = ""
    _dir(user).mkdir(parents=True, exist_ok=True)
    (_dir(user) / f"{name}.py").write_text(code, "utf-8")
    return "".join(difflib.unified_diff(old.splitlines(True), code.splitlines(True), f"a/{name}.py", f"b/{name}.py"))


def toggle_hook(user: str, name: str) -> bool:
    p = _find(user, name)
    enabled = p.suffix == ".off"
    p.rename(_dir(user) / (f"{name}.py" if enabled else f"{name}.py.off"))
    return enabled


def delete_hook(user: str, name: str) -> None:
    _find(user, name).unlink()


def _matches(route_path: str, path: str) -> bool:
    want, got = route_path.split("/"), path.split("/")
    return len(want) == len(got) and all(
        w == g or (w.startswith("{") and w.endswith("}") and g != "") for w, g in zip(want, got))


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
        scope = {"__builtins__": SAFE_BUILTINS, **SCOPE_VALUES}
        try:
            exec(compile(hook["code"], f"{name}.py", "exec"), scope)
            data = scope["run"](data, ctx)
            json.dumps(data)
        except Exception as e:
            raise HookError(name, f"{type(e).__name__}: {e}")
        applied.append(name)
    return data, applied
