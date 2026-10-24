# 01 — Contracts

Frozen for this iteration once the plan is approved. A change is a new spec version, announced to both agents.

| Contract | File | Version |
|---|---|---|
| Hook file format, guard, scope, `backend/hooks.py` exports | [hook-contract.md](../../specs/hook-contract.md) | v1 |
| Core HTTP routes, forwarding, errors | [proxy.md](../../specs/proxy.md) | v1 |
| `backend/agent.py` exports, tools, WebSocket protocol | [agent.md](../../specs/agent.md) | v1 |
| Widget behaviour | [embed-widget.md](../../specs/embed-widget.md) | v1 |
| `trace.emit` and event kinds | [trace-events.md](../../specs/trace-events.md) | v1 |
| Demo app routes, users, seed data | [demo-app.md](../../specs/demo-app.md) | v1 |

## Test environment

- Tests use `pytest` and live in `tests/`: acceptance tests (tester) in `tests/acceptance/`, unit tests (developer) in `tests/unit/`.
- `make check` runs the whole suite inside Docker, with the `demo` service running and reachable at `http://demo:8001`.
- The core is tested in-process: `from main import app` with FastAPI's `TestClient`, and `BASE_URL=http://demo:8001`.
- The agent is tested with `agent.override(model=FunctionModel(...))`. No test calls a real model or needs an API key.
- Tests that need hooks write hook files directly into a temporary `WORKSPACES` folder, using the file format from the hook contract.
- Tests that check trace events replace `trace.emit` with a recorder (for example with `monkeypatch`).
