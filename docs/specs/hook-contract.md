# Hook contract — v1

What a hook is, how it is stored, validated and run. Decisions: [ADR 0004](../adr/0004-per-user-python-hooks-in-process.md), [ADR 0005](../adr/0005-fail-closed-hook-execution.md).

## File and storage

- A hook is one UTF-8 Python file at `WORKSPACES/<user_id>/<name>.py` (enabled) or `WORKSPACES/<user_id>/<name>.py.off` (disabled).
- `WORKSPACES` is an environment variable; default `./workspaces`.
- `<name>` matches `^[a-z0-9_]{1,40}$`. `<user_id>` is the id returned by the app's `/api/me`; a user id that does not match `^[A-Za-z0-9_-]{1,64}$` gets no hooks.
- Enabled hooks run in ascending order of `<name>`. Each receives the previous hook's output.

## Shape

```python
ROUTE = "GET /api/invoices"

def run(data, ctx):
    for invoice in data:
        if datetime.fromisoformat(invoice["date"]).weekday() == 4:
            invoice["amount"] = round(invoice["amount"] * 0.85, 2)
    ctx.log("discounted Friday invoices")
    return data
```

- `ROUTE` is a string `"<METHOD> <path>"`. `METHOD` is one of `GET POST PUT PATCH DELETE`. `path` starts with `/` and may contain `{param}` segments, which match exactly one non-empty path segment.
- A path starting with `/x/` declares a **virtual endpoint** (see [proxy spec](proxy.md)). Only `GET` is allowed for virtual endpoints.
- `run(data, ctx)` returns a JSON-serializable value: the body the user receives.
  - For app routes, `data` is the parsed JSON body the app returned.
  - For virtual endpoints, `data` is `None`.

## `ctx`

| Member | Meaning |
|---|---|
| `ctx.user` | The current user id (`str`) |
| `ctx.api.get(path)` | `GET` on the client app as the current user (same cookies). `path` must start with `/`. Returns the parsed JSON body. Raises on a non-2xx status. No other HTTP method exists. |
| `ctx.log(*values)` | Appends one log line (values joined by a space) to this run's log, which is traced |

## Guard (checked when saving and when loading)

A hook is rejected if its source:
1. is not valid Python;
2. contains `import`, `from ... import`, `global` or `nonlocal`;
3. uses any name or attribute that starts with `_`;
4. uses any of these names: `eval exec compile open globals locals vars getattr setattr delattr input breakpoint type object print`;
5. does not define a string `ROUTE` in the format above and a callable `run`.

The rejection message starts with `line <n>: ` when a line is known.

## Execution scope

Only these names exist when the hook runs:
- builtins: `len sum min max abs round sorted reversed any all enumerate zip range map filter str int float bool list dict set tuple isinstance`
- values: `date datetime timedelta` (from `datetime`) and `math`
- the hook's own definitions, `data` and `ctx` as arguments.

## Failure

Any exception while loading or running a matching hook fails the whole request closed (see [proxy spec](proxy.md), "Errors"). No time limit in v1.

## Module contract

`backend/hooks.py` exports, and the proxy and agent use only these:

| Name | Behaviour |
|---|---|
| `check_code(code: str) -> tuple[str, str]` | Applies the guard. Returns `(method, path)` from `ROUTE`. Raises `ValueError` with the reason otherwise. |
| `Ctx(user: str, api)` | The `ctx` object. `api` is any object with `get(path)`. `ctx.logs` is the list of logged lines. |
| `list_hooks(user: str) -> list[dict]` | `[{"name", "route", "enabled", "code"}]`, sorted by name. Empty if the user has no folder. |
| `read_hook(user, name) -> str` | Source code; `KeyError` if missing |
| `save_hook(user, name, code) -> str` | Guards, then writes the hook enabled (replacing an enabled or disabled one with the same name). Returns a unified diff from the previous code (or from empty). `ValueError` on an invalid name or code, and nothing is written. |
| `toggle_hook(user, name) -> bool` | Flips enabled/disabled, returns the new state; `KeyError` if missing |
| `delete_hook(user, name) -> None` | Removes the hook; `KeyError` if missing |
| `run_hooks(user, method, path, data, ctx) -> tuple[object, list[str]]` | Runs the user's enabled matching hooks in name order, chaining outputs. Returns `(result, applied_names)`. If none match, returns `(data, [])`. |
| `HookError` | Raised by `run_hooks` when a hook fails; has `.hook` (name) and `str(error)` (detail) |
