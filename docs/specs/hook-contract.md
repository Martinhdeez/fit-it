# Hook contract — v4

What a hook is, how it is stored, validated and run. Decisions: [ADR 0004](../adr/0004-per-user-python-hooks-in-process.md), [ADR 0005](../adr/0005-fail-closed-hook-execution.md), [ADR 0017](../adr/0017-postgres-as-core-storage.md), [ADR 0018](../adr/0018-hook-versions-and-undo.md), [ADR 0020](../adr/0020-hook-time-limit-and-safety-limits.md), [ADR 0023](../adr/0023-features-run-as-the-subscriber.md), [ADR 0025](../adr/0025-hook-edits-under-parallel-conversations.md).

## Changes from v3

Iteration 04. The v3 text is in git history (`git show 5c656ce:docs/specs/hook-contract.md`); v2 at `6554dff`, v1 at `a76b931`.

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | `save_hook` serializes saves of the same `(user, name)` with an advisory lock, so concurrent saves never collide on `hook_versions` ("Concurrent saves"). | No | HK1 |
| 2 | `save_hook` takes an optional `expected_version`; a mismatch raises the new `Conflict` and writes nothing ("Concurrent saves"). Without it, behaviour is exactly v3. | No (addition) | HK1 |
| 3 | New exports `read_hook_version(user, name)` and `versions(user)`, so a caller can know which version it based an edit on. | No (addition) | HK1 |
| 4 | `dry_run` takes optional `name` and `features`: with `name`, the candidate runs inside the user's whole chain for its route ("Chain dry run"). Without `name`, behaviour is exactly v3. | No (addition) | HK1 |

Every other export keeps its v3 signature and behaviour. Changes in v3 (iteration 03): `run_hooks` `features` and `delete_hook` unpublishing.

## Storage

- A hook is one row of the `hooks` table: `user_id`, `name`, `route`, `code` (UTF-8 Python source), `enabled`, `version`, `updated_at`. Every saved code is also a row of `hook_versions`. Columns and keys: [storage spec](storage.md).
- `<name>` matches `^[a-z0-9_]{1,40}$`. `<user_id>` is the id returned by the app's `/api/me`; a user id that does not match `^[A-Za-z0-9_-]{1,64}$` gets no hooks and cannot save one.
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

## Guard (checked when saving, when restoring, and when loading)

A hook is rejected if its source:
1. is not valid Python;
2. contains `import`, `from ... import`, `global` or `nonlocal`;
3. uses any name or attribute that starts with `_`, any attribute that starts with `f_ gi_ cr_ ag_ tb_ co_`, or the attributes `format format_map mro`, or contains `try`, `with` or `async`;
4. uses any of these names: `eval exec compile open globals locals vars getattr setattr delattr input breakpoint type object print`;
5. does not define a string `ROUTE` in the format above and a callable `run`.

The rejection message starts with `line <n>: ` when a line is known.

"Loading" means every time `list_hooks`, `run_hooks` or `dry_run` reads code: a row changed directly in the database is guarded like any other code.

## Execution scope

Only these names exist when the hook runs:
- builtins: `len sum min max abs round sorted reversed any all enumerate zip range map filter str int float bool list dict set tuple isinstance`
- values: `date datetime timedelta` (from `datetime`) and `math`
- the hook's own definitions, `data` and `ctx` as arguments.

## Limits

Apply to every run of `run` by `run_hooks` and `dry_run`. Variables are read at each run; defaults in [proxy Configuration](proxy.md#configuration).

| Limit | Variable | Default | On breach |
|---|---|---|---|
| Wall time of one hook's `run` call, `ctx.api.get` calls included | `FITIT_HOOK_TIMEOUT_MS` | `2000` | The run fails with a detail containing `time limit` |
| Size of one hook's output, serialized as JSON in UTF-8 | `FITIT_HOOK_MAX_BYTES` | `1000000` | The run fails with a detail containing `output` |

The time limit is enforced with a trace function on the running thread ([ADR 0020](../adr/0020-hook-time-limit-and-safety-limits.md)); after a run, success or failure, the thread's trace function (`sys.gettrace()`) is the one it had before. Known ceiling: a single long call in C is not interrupted.

## Features

A subscribed marketplace feature ([marketplace spec](marketplace.md)) is code the user did not write, run as if they had ([ADR 0023](../adr/0023-features-run-as-the-subscriber.md)). `run_hooks` receives them as `features`, a list of `{"name": "@<feature_id>", "code": str}` (what `marketplace.active(user)` returns), and:
- runs them **before** the user's own enabled hooks, in list order, then the own hooks in name order, chaining every output into the next input;
- treats each one exactly like an own hook: guard at load, route matching, same `ctx` (so `ctx.user` is the current user and `ctx.api` their session), same limits, same failure;
- names it `@<feature_id>` in its `applied` entry and in `HookError.hook`.

`hooks.py` never reads the marketplace tables; it only runs what it is given.

## Concurrent saves

Decision: [ADR 0025](../adr/0025-hook-edits-under-parallel-conversations.md).

- `save_hook` takes a transaction-scoped advisory lock keyed on `(user, name)` (`pg_advisory_xact_lock`) before reading anything. Concurrent saves of one hook run one after the other: each succeeds, versions are consecutive and unique, and `hooks` ends with the code and version of the last one.
- `expected_version` is the `hooks.version` the caller based its edit on, or `0` for "this hook must not exist yet". Checked under the lock. It **conflicts** when the hook exists with another version, exists while `0` was given, or does not exist while a positive number was given (never created, or deleted since). On a conflict `save_hook` raises `Conflict` and writes nothing.
- `Conflict` is a subclass of `ValueError` (so v3 callers that catch `ValueError` still catch it). `str(e)` starts with `conflict: ` and names the hook, the expected version and the current one, for example `conflict: hook 'discount' is at version 4, not 3; read it again`; `.current` is the current version, or `0` when the hook does not exist.
- `undo_hook`, `toggle_hook` and `delete_hook` take no expected version.

## Chain dry run

`dry_run(user, code, data, api, name=..., features=...)` tries the candidate the way it will really run, inside the user's chain for the candidate's route ([ADR 0025](../adr/0025-hook-edits-under-parallel-conversations.md)). The method and path are the candidate's `ROUTE`; the chain is what `run_hooks` would run for them, with the candidate in place:

1. the matching entries of `features` (a list like `marketplace.active(user)`), in list order. If `name` starts with `@`, the candidate is a feature: it replaces the entry with that `name` in place, or is appended after the last entry if none has it;
2. then the user's **enabled** own hooks whose route matches, in name order. If `name` is a hook name, the candidate replaces the hook with that name (whether that hook is enabled or not, because saving enables it), or is inserted at its name's position;
3. everything chained as in `run_hooks`: one `Ctx(user, api)`, the same guard at load, the same limits, `data` as the first input.

It returns the **last output of the chain** and writes nothing. If the candidate fails, `HookError.hook` is `None` (as in v3). If any other hook or feature of the chain fails, or a stored hook in it no longer passes the guard, `HookError.hook` is that hook's name (or `@<feature_id>`) and `str(e)` its detail. Callers report that as `<hook> failed: <detail>`.

## Failure

Any exception while loading or running a matching hook or feature, and any breached limit, fails the whole request closed (see [proxy spec](proxy.md), "Errors").

## Module contract

`backend/hooks.py` exports, and the proxy and agent use only these:

| Name | Behaviour |
|---|---|
| `check_code(code: str) -> tuple[str, str]` | Applies the guard. Returns `(method, path)` from `ROUTE`. Raises `ValueError` with the reason otherwise. |
| `Ctx(user: str, api)` | The `ctx` object. `api` is any object with `get(path)`. `ctx.logs` is the list of all lines logged through this `ctx`. |
| `list_hooks(user: str) -> list[dict]` | `[{"name", "route", "enabled", "code"}]`, sorted by name. `route` is `"<METHOD> <path>"` from the guard, or `None` if the stored code no longer passes it. `[]` if the user has no hooks or the user id is invalid. |
| `read_hook(user, name) -> str` | Source code; `KeyError` if missing |
| `read_hook_version(user, name) -> tuple[str, int]` | `(code, version)` of the hook, read in one query, so the version is the one that code belongs to; `KeyError` if missing |
| `versions(user) -> dict[str, int]` | `{name: hooks.version}` for every hook of the user, enabled or not; `{}` if none or the user id is invalid |
| `save_hook(user, name, code, expected_version=None) -> str` | Takes the lock and checks `expected_version` when given ("Concurrent saves"; `Conflict` on a mismatch). Then, as in v3: guards, then in one transaction writes the hook enabled (replacing an enabled or disabled one with the same name), appends a `hook_versions` row with version `max(existing versions of that name) + 1` (or `1`), and sets `hooks.version` to it. Returns a unified diff from the previous code (or from empty), with file labels `a/<name>.py` and `b/<name>.py`. `ValueError` on an invalid user id, name or code, and nothing is written. |
| `undo_hook(user, name) -> tuple[int, str]` | Restores the code of version `hooks.version - 1` from `hook_versions`: guards it, writes it to `hooks`, sets `hooks.version` to that number, keeps `enabled`. Writes no `hook_versions` row. Returns `(restored_version, diff)`, the diff going from the code before the undo to the restored code. `KeyError` if the hook does not exist; `ValueError` (`"no earlier version"`, or the guard's reason) if there is nothing to restore, and nothing changes. |
| `toggle_hook(user, name) -> bool` | Flips enabled/disabled, returns the new state; `KeyError` if missing. Writes no version. |
| `delete_hook(user, name) -> None` | Removes the hook from `hooks`; its `hook_versions` rows stay. In the same transaction, sets `published` to false on the `features` row with that `owner_id` and `hook_name`, if any. `KeyError` if missing. |
| `run_hooks(user, method, path, data, ctx, features=()) -> tuple[object, list[dict]]` | Runs the matching entries of `features` in list order ("Features"), then the user's enabled matching hooks in name order, chaining outputs. Returns `(result, applied)`: one entry per hook or feature that ran, in run order, `{"name": str, "route": str, "ms": float, "logs": list[str]}`, where `name` is the hook name or `@<feature_id>`, `ms` is its run time in milliseconds and `logs` the lines it logged (only its own). If none match, returns `(data, [])`. |
| `dry_run(user, code, data, api, name=None, features=()) -> object` | With `name`: the chain dry run ("Chain dry run"). Without `name`, as in v3: guards `code` (`ValueError` with the reason), then runs its `run(data, Ctx(user, api))` exactly as `run_hooks` would, limits included, and returns the output. Raises `HookError` with `.hook` `None` if `run` raises, breaches a limit or returns a value that is not JSON-serializable. Writes nothing to the database. Does not check that `ROUTE` matches anything. |
| `HookError` | Raised by `run_hooks` and `dry_run` when a hook fails; has `.hook` (hook name, `@<feature_id>` for a feature, or `None` for the candidate of `dry_run`) and `str(error)` (detail) |
| `Conflict` | Subclass of `ValueError` raised by `save_hook` on an `expected_version` mismatch; has `.current` ("Concurrent saves") |
