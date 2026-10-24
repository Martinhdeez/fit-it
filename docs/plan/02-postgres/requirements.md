# 02 — Requirements and verification

Acceptance is decided by tests whose author has not seen the implementation. Each requirement is one testable sentence and names the surface it is checked through. "Own schema" means the `database` fixture of [contracts.md](contracts.md).

## Requirements

### DB1 — Postgres in the stack and the test gate

| Id | Requirement | Checked through |
|---|---|---|
| REQ-DB1-1 | `docker compose config` shows a `postgres` service with image `postgres:17`, a healthcheck and a named volume; `docker compose port postgres 5432` gives no mapping; the `core` service has `DATABASE_URL`, passes through `FITIT_HOOK_TIMEOUT_MS`, `FITIT_HOOK_MAX_BYTES` and `FITIT_AGENT_MESSAGES_PER_HOUR`, has no `WORKSPACES` variable and no `workspaces` mount, and depends on `postgres` being healthy. | Shell (manual) |
| REQ-DB1-2 | With `POSTGRES_PASSWORD=s3cret` exported, `docker compose config` shows `s3cret` as the `postgres` service's password and inside the core's `DATABASE_URL`; with it unset, both show `fitit`. | Shell (manual) |
| REQ-DB1-3 | On an empty schema, `db.init()` creates `hooks`, `hook_versions` and `messages` with the columns, types, nullability and primary keys of the storage spec. | `db.init`, `information_schema` |
| REQ-DB1-4 | Calling `db.init()` a second time raises nothing and keeps the rows already in the three tables. | `db.init`, SQL |
| REQ-DB1-5 | `db.connect()` uses the `DATABASE_URL` set at call time: a row inserted through it while pointing at one schema is not visible through it after switching `DATABASE_URL` to another schema, and a row written in a `with db.connect()` block is visible from a separate connection after the block. | `db.connect`, SQL |
| REQ-DB1-6 | Starting the core (`with TestClient(main.app):`) with `DATABASE_URL` pointing at an empty schema creates the three tables. | Core startup, `information_schema` |

### HS2 — Hook storage on Postgres, versions, undo, `dry_run`, per-hook timing

| Id | Requirement | Checked through |
|---|---|---|
| REQ-HS2-1 | `save_hook` writes one `hooks` row with the user, name, code, `route` from the guard, `enabled` true and `version` 1, and returns a unified diff from empty with labels `a/<name>.py` and `b/<name>.py`; with an invalid user id, name or code it raises `ValueError` and no row appears in `hooks` or `hook_versions`. | `hooks.save_hook`, SQL |
| REQ-HS2-2 | Each successful save of the same hook adds one `hook_versions` row with the next version number and the saved code, and sets `hooks.version` to it; `toggle_hook` and `delete_hook` add none; versions remain after `delete_hook`, and saving the name again continues the numbering. | `hooks.*`, SQL |
| REQ-HS2-3 | `list_hooks`, `read_hook`, `toggle_hook` and `delete_hook` behave as the module contract says over the table: sorted list with `enabled` flags, `[]` for a user with no hooks and for an invalid user id, `KeyError` for a missing hook, and a toggle is visible as `hooks.enabled` from a separate connection. | `hooks.*`, SQL |
| REQ-HS2-4 | A `hooks` row inserted directly with enabled code that fails the guard and a matching route makes `run_hooks` raise `HookError` whose `.hook` is that name, and `list_hooks` shows that hook with `route` `None`. | SQL, `hooks.run_hooks`, `hooks.list_hooks` |
| REQ-HS2-5 | `run_hooks` returns, per hook that ran and in run order, `{"name", "route", "ms", "logs"}` where `ms` is a non-negative number and `logs` holds only the lines that hook logged; with no matching hook it returns `(data, [])`. | `hooks.run_hooks` |
| REQ-HS2-6 | `dry_run` returns the output of the code's `run(data, ctx)` with `ctx.user` equal to the given user and `ctx.api.get` delegating to the given `api`, and adds no row to any table; it raises `ValueError` for code the guard rejects and `HookError` with `.hook` `None` for code that raises or returns a non-JSON-serializable value. | `hooks.dry_run`, SQL |
| REQ-HS2-7 | After two saves, `undo_hook` returns `(1, diff)`, `read_hook` returns the first code, `hooks.version` is 1, `enabled` is unchanged and no `hook_versions` row is added; a second `undo_hook` raises `ValueError` and changes nothing; `undo_hook` on a missing hook raises `KeyError`; the next `save_hook` creates version 3. | `hooks.undo_hook`, SQL |
| REQ-HS2-8 | Through the core, two chained matching hooks that log different lines produce `x-fitit-hooks` with both names in order, and two `hook.applied` events, each with its own hook's `route` and only its own `logs`. | Core HTTP, recorded `trace.emit` |
| REQ-HS2-9 | `POST /fitit/hooks/{name}/undo` returns `401` anonymously, `404` for a missing hook, `409` `{"error": "undo_failed", ...}` for a hook with one version, and otherwise `200` `{"name", "version", "diff"}` after which the next request through the core shows the restored behaviour; a `200` emits one `hook.restored` with `user`, `hook` and `version`. | Core HTTP, recorded `trace.emit` |
| REQ-HS2-10 | The iteration 01 acceptance tests for HR1, PX1, AG1 and the demo flow pass with their fixtures moved from hook files to the `database` fixture, with assertions changed only where hook-contract v2 changes the behaviour. | Iteration 01 acceptance suites |

### CH1 — Persistent chat history

| Id | Requirement | Checked through |
|---|---|---|
| REQ-CH1-1 | After a message that ends with `text`, exactly one new `messages` row exists for that user, and its `turn` validates with `ModelMessagesTypeAdapter` and contains the user's text and the model's answer. | Core WebSocket, `FunctionModel`, SQL |
| REQ-CH1-2 | On a new WebSocket connection for the same user, the model receives the earlier user text and answer in `messages` for the next message. | Core WebSocket, `FunctionModel` |
| REQ-CH1-3 | Another user's first message reaches the model with no earlier turns from the first user. | Core WebSocket, `FunctionModel` |
| REQ-CH1-4 | A message that ends with `error` (the model raises) adds no `messages` row. | Core WebSocket, `FunctionModel`, SQL |
| REQ-CH1-5 | With 25 turns seeded for a user, the model receives the 20 most recent in order and none of the 5 oldest. | SQL seed, Core WebSocket, `FunctionModel` |
| REQ-CH1-6 | When the user's rows are deleted between two messages on the same connection, the second message reaches the model with no earlier turns. | Core WebSocket, SQL |
| REQ-CH1-7 | `agent.clear_history(user)` deletes every `messages` row of that user and none of another user's. | `agent.clear_history`, SQL |

### AG2 — Agent quality

| Id | Requirement | Checked through |
|---|---|---|
| REQ-AG2-1 | After a `write_hook` with valid code, `hooks` and `hook_versions` hold rows only for the connected user. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG2-2 | A `write_hook` whose code raises during the dry run sends `hook_rejected` and leaves `hooks` and `hook_versions` empty, for every user id. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG2-3 | A `write_hook` with the name of an existing hook leaves one hook of that name with `version` 2, and its `hook_saved.diff` has a `-` line from the old code and a `+` line from the new. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG2-4 | The instructions of every model request contain each of the user's current hooks' name and route, or `no hooks yet` when there are none. | Core WebSocket, `FunctionModel` |
| REQ-AG2-5 | After two saves of a hook, an `undo_hook` call sends `hook_saved` with that hook's name, route and a diff, restores the first code, and emits `hook.restored` with `version` 1; for a hook with one version it returns text containing `nothing to undo` and changes nothing. | Core WebSocket, `FunctionModel`, SQL, recorded `trace.emit` |
| REQ-AG2-6 | `DELETE /fitit/history` returns `401` anonymously and `204` signed in, after which the user has no `messages` rows, other users' rows remain, and the user's next message reaches the model with no earlier turns. | Core HTTP, Core WebSocket, SQL |
| REQ-AG2-7 | After `agent.reset_rate_limits()` and with `FITIT_AGENT_MESSAGES_PER_HOUR=2`, a user's third message within the hour, even on a new connection, gets `error` with `rate limit` in the message then `done`, without calling the model or adding a `messages` row, while another user's message still reaches the model. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG2-8 | Each `agent.answer` event carries integer `input_tokens` and `output_tokens`. | Core WebSocket, `FunctionModel`, recorded `trace.emit` |
| REQ-AG2-9 | The acceptance tests for AG1 and CH1 still pass. | `test_ag1.py`, `test_ch1.py` |

### SF1 — Hook time limit and output cap

| Id | Requirement | Checked through |
|---|---|---|
| REQ-SF1-1 | With `FITIT_HOOK_TIMEOUT_MS=200`, a matching hook that loops forever makes `run_hooks` raise `HookError` with that hook's name and `time limit` in the detail, within 1.2 seconds. | `hooks.run_hooks` |
| REQ-SF1-2 | Through the core, the same hook gives `502` `{"error": "hook_failed", "hook": <name>, ...}` with none of the app's data, within 1.2 seconds of the hook starting. | Core HTTP |
| REQ-SF1-3 | `dry_run` of code that loops forever raises `HookError` with `.hook` `None` within the limit plus one second. | `hooks.dry_run` |
| REQ-SF1-4 | With `FITIT_HOOK_MAX_BYTES=1000`, a hook returning a value larger than 1000 bytes as UTF-8 JSON makes `run_hooks` raise `HookError` with `output` in the detail, and a hook returning less runs normally. | `hooks.run_hooks` |
| REQ-SF1-5 | With both variables unset, a hook that busy-waits one second returns normally, and a hook that loops forever fails within three seconds. | `hooks.run_hooks` |
| REQ-SF1-6 | A trace function installed with `sys.settrace` before `run_hooks` is still `sys.gettrace()` after it, both after a normal run and after a timeout. | `hooks.run_hooks`, `sys.gettrace` |

### EV1 — Real-model eval set

| Id | Requirement | Checked through |
|---|---|---|
| REQ-EV1-1 | `run_evals.CASES` has between 10 and 20 cases with unique ids, including every id in the evals spec, each with a demo user, at least one turn, a route starting with `/api/` or `/x/`, and a callable `check`. | `run_evals.CASES` |
| REQ-EV1-2 | With a `FunctionModel` that writes the hook-contract example hook as `write_hook`, `run_case` on `friday_discount` returns `passed` true. | `run_evals.run_case`, `FunctionModel` |
| REQ-EV1-3 | With a `FunctionModel` that only answers text, `run_case` on `friday_discount` returns `passed` false with a non-empty `detail`; with one that raises, it also returns `passed` false and raises nothing. | `run_evals.run_case`, `FunctionModel` |
| REQ-EV1-4 | After `run_case`, no schema whose name starts with `eval_` remains, and `DATABASE_URL` and `trace.emit` are what they were before the call. | `run_evals.run_case`, SQL |
| REQ-EV1-5 | `main()` with `EVAL_CASES` set to two ids runs only those, prints one line per case starting with `PASS` or `FAIL` followed by the id, then a line starting with `pass rate: <k>/2`, and returns `0` only if both passed. | `run_evals.main`, captured stdout |
| REQ-EV1-6 | `input_tokens` and `output_tokens` are `None` (printed `n/a`) when no `agent.answer` event carries them, and the sums over the case's `agent.answer` events otherwise. | `run_evals.run_case`, `FunctionModel` |
| REQ-EV1-7 | `make check` runs no eval case and needs no model key; `make eval` runs `run_evals.py` with `FITIT_MODEL`, the model keys and `EVAL_CASES` passed through from the shell. | Shell (manual) |

## What is verified, and how

| Surface | Items | Check |
|---|---|---|
| Shell, Docker | DB1, EV1 | Tester runs the commands (manual checklist) |
| `backend/db.py`, SQL on the test's own schema | DB1, HS2, CH1, AG2 | Imported functions, `psycopg` |
| `backend/hooks.py` | HS2, SF1 | Imported functions, own schema |
| Core HTTP / WebSocket | HS2, CH1, AG2, SF1 | `TestClient` on `main.app`, demo service as the app, `FunctionModel` |
| `evals/run_evals.py` | EV1 | Imported functions, `FunctionModel` |
| Real model | EV1 (pass rate) | `make eval` by a human in RL1; not a requirement |

## Closed to the tester

The developer's branch and pull request, every file under `backend/`, `demo/` and `evals/` except what the contracts name, and `tests/unit/`.
