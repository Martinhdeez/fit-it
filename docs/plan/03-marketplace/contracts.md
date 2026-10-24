# 03 — Contracts

Frozen for this iteration once the plan is approved. A change is a new spec version, announced to both agents.

| Contract | File | Version |
|---|---|---|
| Marketplace rules, `backend/marketplace.py` exports, shapes, HTTP routes, My features | [marketplace.md](../../specs/marketplace.md) | v1 (new) |
| Storage: tables including the marketplace tables | [storage.md](../../specs/storage.md) | v2 |
| Hook storage, guard, scope, limits, features in `run_hooks`, `backend/hooks.py` exports | [hook-contract.md](../../specs/hook-contract.md) | v3 |
| Core HTTP routes, applying hooks and features, errors | [proxy.md](../../specs/proxy.md) | v3 |
| `backend/agent.py` tools (marketplace tools), instructions, WebSocket protocol | [agent.md](../../specs/agent.md) | v3 |
| Eval harness (`setup`) and cases | [evals.md](../../specs/evals.md) | v1 + iteration 03 changes |
| Widget behaviour, Marketplace panel and My features (frontend teammate) | [embed-widget.md](../../specs/embed-widget.md) | v3 |
| `trace.emit` and event kinds | [trace-events.md](../../specs/trace-events.md) | v1 + minor additions (iteration 03) |
| Demo app routes, users, seed data | [demo-app.md](../../specs/demo-app.md) | v1 (unchanged) |

## Test environment

Everything from [iteration 02](../02-postgres/contracts.md#test-environment) holds: the `database` fixture copied into each test file, no shared `conftest.py`, `FunctionModel` only, `FITIT_AGENT_MESSAGES_PER_HOUR=100000` for WebSocket tests that do not test the rate limit. Additions:

- Tests may also import `marketplace`.
- **Seeding a feature:** `hooks.save_hook(owner, name, code)` then `marketplace.publish(owner, name, title, description)`; the returned `id` is the feature id. Owners and subscribers are demo user ids (`recepcion`, `dr_garcia`, `gerente`) when the test goes through the core, any valid id otherwise.
- **Subscribing directly:** `marketplace.subscribe(user, feature_id, api)` where `api` is any object with `get(path)`: a fake that records calls and returns fixed data, or `main.Api("session=<user>")`, which reads the demo app as that user (the demo session cookie is `session=<id>`, [demo app spec](../../specs/demo-app.md#session)).
- **Errors:** catch `marketplace.Error` and assert `.error` and `.status`; never assert on the detail text except where a requirement says "contains".
- **Several users through the core:** one `TestClient(main.app)` per user, each signed in with `GET /login?user=<id>` through the core, so each client carries its own session cookie.
- **Trace:** replace `trace.emit` with a recorder through `monkeypatch.setattr(trace, "emit", recorder)`; `marketplace.py` calls it as `trace.emit`.
- **Feature runs:** feature code follows the [hook contract](../../specs/hook-contract.md#shape); a marker can be appended with, for example, `data[0]["marks"] = data[0].get("marks", []) + ["A"]`.
- **Changing stored code** for a guard test: `UPDATE feature_versions SET code = ... WHERE feature_id = ... AND version = ...` on the test's own schema.
- **Agent tools:** a `FunctionModel` calls a marketplace tool by returning a `ToolCallPart` with the tool name and arguments of the [agent spec](../../specs/agent.md#tools); the tool's return value arrives in the next model request as a `ToolReturnPart`, where the test reads it.
- **Evals:** cases with `setup` call it with no arguments after `db.init()`; harness tests may build their own case dicts with a `setup`.
- **Iteration 01 and 02 suites** run unchanged in `make check`; no fixture migration this iteration.
