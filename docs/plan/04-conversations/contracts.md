# 04 — Contracts

Frozen for this iteration once the plan is approved. A change is a new spec version, announced to both agents.

| Contract | File | Version |
|---|---|---|
| Hook storage, guard, limits, concurrent saves, chain dry run, `backend/hooks.py` exports | [hook-contract.md](../../specs/hook-contract.md) | v4 |
| Storage: tables, `messages.conversation_id` | [storage.md](../../specs/storage.md) | v3 |
| `backend/agent.py` exports, conversations, history, `write_hook`, instructions, WebSocket protocol | [agent.md](../../specs/agent.md) | v4 |
| Core HTTP routes, `GET /fitit/conversations`, `DELETE /fitit/history?conversation=`, `WS /fitit/ws?conversation=` | [proxy.md](../../specs/proxy.md) | v4 |
| Marketplace rules (chain dry run on subscribe and accept update) | [marketplace.md](../../specs/marketplace.md) | v2 |
| Widget behaviour, Conversations (frontend teammate) | [embed-widget.md](../../specs/embed-widget.md) | v4 |
| `trace.emit` and event kinds | [trace-events.md](../../specs/trace-events.md) | v1 + minor additions (iteration 04) |
| Eval harness and cases | [evals.md](../../specs/evals.md) | unchanged |
| Demo app routes, users, seed data | [demo-app.md](../../specs/demo-app.md) | v1 (unchanged) |

## Test environment

Everything from [iteration 03](../03-marketplace/contracts.md#test-environment) and [iteration 02](../02-postgres/contracts.md#test-environment) holds: the `database` fixture copied into each test file, no shared `conftest.py`, `FunctionModel` only, `FITIT_AGENT_MESSAGES_PER_HOUR=100000` for WebSocket tests that do not test the rate limit. Additions:

- **Conversation ids** in tests are short literals (`"a"`, `"b"`) or `str(uuid.uuid4())`. A socket for a conversation is `client.websocket_connect("/fitit/ws?conversation=a")`; without the query string it is `default`.
- **A v2 database** (for the upgrade requirement): on an empty schema, create the v2 `messages` table by hand exactly as in the v2 storage spec (`id bigserial PRIMARY KEY, user_id text NOT NULL, turn jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()`), insert rows, then call `db.init()`.
- **Seeding history** for a conversation: insert `messages` rows with `conversation_id` set, `turn` built as in iteration 02 (`ModelMessagesTypeAdapter.dump_python([...], mode="json")`). A row inserted without `conversation_id` belongs to `default`.
- **Holding a turn open.** A `FunctionModel` may be built from an `async` function. To keep a turn running, the function sets a `threading.Event` `started`, then loops `await asyncio.sleep(0.01)` until a `threading.Event` `release` is set, then answers. The test thread waits on `started` (with a timeout) before sending on the second socket, and sets `release` at the end. Use one `TestClient` (one event loop) with two `websocket_connect` contexts; do not use a blocking `time.sleep` in the model function, which may stall both sockets.
- **A second writer during a turn.** Inside the `FunctionModel` function, between two model responses, the test may call `hooks.save_hook`, `hooks.undo_hook` or `hooks.delete_hook` directly for the same user: this is "another conversation" changing the hook.
- **Concurrent saves.** Use `concurrent.futures.ThreadPoolExecutor` with 8 workers and a `threading.Barrier(8)` that each worker waits on just before calling `hooks.save_hook`. Every worker reads `DATABASE_URL` from the environment, so the `database` fixture applies to all of them.
- **Chain dry run directly.** `hooks.dry_run(user, code, data, api, name=..., features=[{"name": "@<id>", "code": ...}])`; features may be literal code, no marketplace rows needed. Markers as in iteration 03: `data[0]["marks"] = data[0].get("marks", []) + ["A"]`.
- **Errors:** catch `hooks.Conflict` and assert `isinstance(e, ValueError)`, `str(e).startswith("conflict:")` and `.current`; never assert on the rest of the text. For `HookError` assert `.hook` and that `str(e)` contains the failing exception's message.
- **Trace:** replace `trace.emit` with a recorder through `monkeypatch.setattr(trace, "emit", recorder)`, as in iteration 03.
- **Earlier suites** run unchanged in `make check`, except `test_db1.py`, whose `EXPECTED["messages"]` gains `"conversation_id": ("text", False)` (the CV1 tester's change).
