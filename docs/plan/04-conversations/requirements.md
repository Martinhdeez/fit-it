# 04 — Requirements and verification

Acceptance is decided by tests whose author has not seen the implementation. Each requirement is one testable sentence and names the surface it is checked through. "Own schema" means the `database` fixture of [iteration 02 contracts](../02-postgres/contracts.md#test-environment). "Held turn" means a turn kept running with the `started` / `release` events of [contracts.md](contracts.md#test-environment). "Writes nothing" means the `hooks` and `hook_versions` rows of every user are the same before and after.

## Requirements

### HK1 — Serialized hook saves, expected version, chain dry run

| Id | Requirement | Checked through |
|---|---|---|
| REQ-HK1-1 | `save_hook(user, name, code, expected_version=v)` saves exactly as without it when `v` equals the hook's `hooks.version`, or when `v` is `0` and the hook does not exist; it raises `hooks.Conflict` (an instance of `ValueError`, text starting `conflict:`, `.current` the current version or `0`) and writes nothing when the hook exists with another version, when it exists and `v` is `0`, when it never existed and `v` is `1`, and when it was deleted and `v` is its last version. | `hooks.*`, SQL |
| REQ-HK1-2 | Eight threads calling `save_hook` at once, without `expected_version`, on the same user and a name that does not exist, each with different valid code, all return without raising; `hook_versions` then holds versions 1 to 8 for that name exactly once each, and `hooks` holds version 8 with the code of `hook_versions` version 8. The same holds for a name that already exists at version 1 (versions 2 to 9). | `hooks.save_hook`, threads, SQL |
| REQ-HK1-3 | Eight threads calling `save_hook` at once with `expected_version` equal to the hook's current version: exactly one returns, the other seven raise `hooks.Conflict`, and exactly one `hook_versions` row is added. | `hooks.save_hook`, threads, SQL |
| REQ-HK1-4 | `read_hook_version` returns `(code, version)` equal to the `hooks` row, after saves and after an undo, and raises `KeyError` for a missing hook; `versions(user)` returns `{name: version}` for every hook of the user, disabled ones included, and `{}` for a user with no hooks and for an invalid user id. | `hooks.*`, SQL |
| REQ-HK1-5 | With `features` `@1`, `@2` and own enabled hooks `a_hook` and `z_hook` on `GET /api/invoices`, all appending a marker, `dry_run(..., name="m_hook", features=...)` of a marker-appending candidate on that route returns markers in the order `@1`, `@2`, `a_hook`, `m_hook`, `z_hook`; with `name="a_hook"` the candidate replaces `a_hook` (its old marker absent, the candidate's in its place); a disabled own hook does not run unless it has the candidate's name, in which case the candidate runs in its place; own hooks on another route do not run; and nothing is written. | `hooks.dry_run`, `hooks.save_hook`, `hooks.toggle_hook`, SQL |
| REQ-HK1-6 | In a chain dry run, a candidate that raises gives `HookError` with `.hook` `None`; an own hook after the candidate that raises on the candidate's output gives `HookError` with `.hook` that hook's name and its exception message in `str(e)`; a stored own hook on the route whose code no longer passes the guard (changed in SQL) gives `HookError` with `.hook` its name; each case writes nothing. | `hooks.dry_run`, SQL |
| REQ-HK1-7 | With `name="@2"` and `features` `@1`, `@2`, the candidate replaces `@2` in place; with `name="@9"` it runs after `@2` and before the own hooks; a feature after the candidate that raises gives `HookError` with `.hook` `@<its id>`. | `hooks.dry_run` |
| REQ-HK1-8 | A subscriber who owns an enabled hook on the feature's route that raises on the feature's output gets `marketplace.Error("dry_run_failed", 422)` from `subscribe`, with that hook's name in its text, and no `subscriptions` row; after disabling that hook, `subscribe` succeeds. With version 2 of a feature breaking that hook, `accept_update` raises the same error and the pin stays at 1. | `marketplace.*`, `hooks.*`, fake `api`, SQL |
| REQ-HK1-9 | `dry_run` without `name` behaves as in hook contract v3: only the candidate runs, even when the user has hooks on its route; and the acceptance tests `test_hr1.py`, `test_hs2.py`, `test_sf1.py`, `test_mk1.py` and `test_mk2.py` pass unchanged. | `hooks.dry_run`, earlier acceptance suites |

### CV1 — Parallel conversations

| Id | Requirement | Checked through |
|---|---|---|
| REQ-CV1-1 | On an empty schema `db.init()` creates `messages` with exactly `id`, `user_id`, `turn`, `created_at` and `conversation_id` (`text`, not null, default `'default'`); on a schema holding a v2 `messages` table with rows, `db.init()` adds the column, every existing row has `conversation_id` `default`, and a second `db.init()` raises nothing and keeps every row. | `db.init`, `information_schema`, SQL |
| REQ-CV1-2 | A socket without `conversation` stores its turns with `conversation_id` `default`, and the model receives the turns of rows seeded without a `conversation_id` as history. | Core WebSocket, `FunctionModel`, SQL |
| REQ-CV1-3 | On `?conversation=a` the model receives, as history, only the last 20 turns of that user's conversation `a`, oldest first, never turns of `b` or of another user's `a`; the new turn is stored with `conversation_id` `a`. | Core WebSocket, `FunctionModel`, SQL |
| REQ-CV1-4 | For a signed-in user, `conversation` values `a b`, `ü`, an empty value and 65 characters close the socket with code `4400`; 64 characters of `[A-Za-z0-9_-]` are accepted; an anonymous socket closes with `4401` whatever the parameter. | Core WebSocket |
| REQ-CV1-5 | With a held turn on conversation `a`, a message on a second socket of the same user on conversation `b` receives `text` then `done` before `a`'s turn is released, and `a` then completes with `text` then `done`. | Core WebSocket, held turn |
| REQ-CV1-6 | With a held turn on conversation `a`, a message on a second socket of the same user on `a` receives `error` whose `message` contains `busy`, then `done`; the model is not called for it, no row is stored for it, and it does not count for the rate limit; after `a`'s turn ends, a message on the second socket is answered normally. Another user's turn on their own `a` is not affected. | Core WebSocket, held turn, SQL |
| REQ-CV1-7 | With `FITIT_AGENT_MESSAGES_PER_HOUR=2`, one message on conversation `a` and one on `b` are answered and a third on `c` receives `error` containing `rate limit` then `done`. | Core WebSocket |
| REQ-CV1-8 | `GET /fitit/conversations` returns `401` anonymously, `[]` with no stored turns, and otherwise one entry per conversation with at least one stored turn, with exactly `id`, `title`, `last_activity` and `turns`: `title` the first 60 characters of the first message sent on it, `turns` its number of stored turns, `last_activity` an ISO 8601 time, sorted by latest activity first; conversations of other users and a conversation whose only message ended in `error` are absent. | Core HTTP, Core WebSocket, `FunctionModel` |
| REQ-CV1-9 | `DELETE /fitit/history?conversation=a` returns `204` and deletes only that user's rows of `a`; without the parameter it deletes all of that user's rows; other users' rows always stay; `?conversation=` empty or invalid returns `422` `{"error": "invalid", ...}` and deletes nothing; anonymous returns `401`. | Core HTTP, SQL |
| REQ-CV1-10 | `agent.message`, `agent.tool` and `agent.answer` carry `conversation` equal to the socket's conversation id (`default` without the parameter). | Core WebSocket, `FunctionModel`, recorded `trace.emit` |
| REQ-CV1-11 | The acceptance tests `test_ch1.py`, `test_ag1.py`, `test_ag2.py`, `test_ag3.py`, `test_px1.py` and `test_mk2.py` pass unchanged, and `test_db1.py` passes with `conversation_id` added to the expected `messages` columns. | Earlier acceptance suites |

### AG4 — Agent edits on a known version, chain dry run, extend the route's hook

| Id | Requirement | Checked through |
|---|---|---|
| REQ-AG4-1 | When the model calls `read_hook` for `h`, then the test saves new code for `h` directly, then the model calls `write_hook` for `h`, the client receives `hook_rejected` whose `reason` starts with `conflict:` and `h` keeps the test's code; when the model then calls `read_hook` and `write_hook` again, the save succeeds with `hook_saved`. | Core WebSocket, `FunctionModel`, `hooks.save_hook`, SQL |
| REQ-AG4-2 | `write_hook` is rejected with `conflict:` and writes nothing when, during the turn and without the model reading it, `h` was saved, undone or deleted by the test after the message started, or created by the test when it did not exist at the start. | Core WebSocket, `FunctionModel`, `hooks.*`, SQL |
| REQ-AG4-3 | Without changes by others, `write_hook` of a new hook, an edit after `read_hook`, an edit without `read_hook` of a hook that existed when the message started, and two `write_hook` calls of the same hook in one turn all save. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG4-4 | Three conflicting `write_hook` calls in one message end the turn with `error` then `done`; each sends `hook_rejected` and emits `hook.rejected`. | Core WebSocket, `FunctionModel`, recorded `trace.emit` |
| REQ-AG4-5 | With the user's hook `zz_total` on `GET /api/invoices` that reads `data[0]["amount"]` and appends a marker, a `write_hook` candidate `aa_wrap` on that route returning `{"rows": data}` is rejected with a `reason` containing `zz_total` and writes nothing; a compatible candidate `aa_mark` that appends its own marker is saved, and the tool's return (in the next model request) shows both markers, `aa_mark`'s before `zz_total`'s. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG4-6 | The instructions of every model request contain `already runs on the same route` and `read_hook`. | Core WebSocket, `FunctionModel` |
| REQ-AG4-7 | The acceptance tests `test_ag1.py`, `test_ag2.py`, `test_ag3.py`, `test_ch1.py` and `test_cv1.py` pass unchanged. | Earlier acceptance suites |

## What is verified, and how

| Surface | Items | Check |
|---|---|---|
| `backend/hooks.py`, `backend/marketplace.py`, SQL on the test's own schema | HK1 | Imported functions, threads, `psycopg` |
| Core WebSocket and HTTP | CV1, AG4 | `TestClient` on `main.app`, demo service as the app, `FunctionModel` |
| `db.init`, `information_schema` | CV1 | Own schema, a hand-made v2 `messages` table |
| Widget | WC1 | The frontend teammate, by hand against the [embed widget spec](../../specs/embed-widget.md#conversations); no REQ |

## Closed to the tester

The developer's branch and pull request, every file under `backend/`, `demo/` and `evals/` except what the contracts name, and `tests/unit/`.
