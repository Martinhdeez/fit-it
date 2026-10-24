# 02 — Contracts

Frozen for this iteration once the plan is approved. A change is a new spec version, announced to both agents.

| Contract | File | Version |
|---|---|---|
| Storage: Postgres service, `backend/db.py`, schema, test isolation | [storage.md](../../specs/storage.md) | v1 |
| Hook storage, guard, scope, limits, `backend/hooks.py` exports | [hook-contract.md](../../specs/hook-contract.md) | v2 |
| Core HTTP routes, configuration, forwarding, errors | [proxy.md](../../specs/proxy.md) | v2 |
| `backend/agent.py` exports, tools, instructions, history, rate limit, WebSocket protocol | [agent.md](../../specs/agent.md) | v2 |
| Eval harness and cases | [evals.md](../../specs/evals.md) | v1 |
| Widget behaviour | [embed-widget.md](../../specs/embed-widget.md) | v2 (unchanged) |
| `trace.emit` and event kinds | [trace-events.md](../../specs/trace-events.md) | v1 + minor additions |
| Demo app routes, users, seed data | [demo-app.md](../../specs/demo-app.md) | v1 (unchanged) |

## Test environment

Everything from [iteration 01](../01-mvp/contracts.md#test-environment) holds, except that hooks are no longer files. Additions:

- `make check` runs with the `postgres` and `demo` services healthy. Inside the test container `DATABASE_URL` is `postgresql://fitit:<password>@postgres:5432/fitit`, with no query string. No test needs `POSTGRES_PASSWORD` or a model key in the shell.
- Tests may import `psycopg` (installed in the core image), `db`, `hooks`, `agent`, `main`, `trace`, and `pydantic_ai` (for `FunctionModel`, `ModelMessagesTypeAdapter` and message classes).
- **Every test that touches storage, directly or through the core, uses its own schema**, with this fixture, copied into its own test file (still no shared `conftest.py`):

```python
import os, uuid
import psycopg, pytest
import db

@pytest.fixture
def database(monkeypatch):
    base = os.environ["DATABASE_URL"]
    schema = f"t_{uuid.uuid4().hex}"
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {schema}")
    url = f"{base}?options=-csearch_path%3D{schema}"
    monkeypatch.setenv("DATABASE_URL", url)
    db.init()
    yield url                      # connect to it with psycopg.connect(url) to read or write rows
    with psycopg.connect(base, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {schema} CASCADE")
```

- Hooks are seeded with `hooks.save_hook` (and `hooks.toggle_hook` for a disabled one), or with a direct `INSERT` into `hooks` following the [storage spec](../../specs/storage.md) when a test needs a row the public functions would refuse (for example code that fails the guard).
- Storage effects are checked with SQL on the test's own schema (`psycopg.connect(url)`), against the tables and columns of the storage spec.
- History is seeded by inserting `messages` rows whose `turn` is `ModelMessagesTypeAdapter.dump_python(messages, mode="json")` of real Pydantic AI messages.
- What the model receives is checked inside the `FunctionModel` function: `messages` is the full list sent to the model, and the `instructions` field of the last `ModelRequest` holds the agent's instructions.
- **Rate limit.** Tests that send WebSocket messages and do not test the rate limit set `FITIT_AGENT_MESSAGES_PER_HOUR=100000` with `monkeypatch.setenv`. Rate-limit tests call `agent.reset_rate_limits()` first, then set the limit they need.
- **Hook limits.** Tests that check timing set `FITIT_HOOK_TIMEOUT_MS` with `monkeypatch.setenv` and measure with `time.monotonic()`; allowed slack is one second over the limit.
- **Evals.** After EV1 merges, `make check` mounts `./evals` at `/evals` and puts it on `PYTHONPATH`, so tests import `run_evals` and `cases`. Harness tests use `agent.agent.override(model=FunctionModel(...))`. A `FunctionModel` function may call `trace.emit("agent.answer", user=..., text="", input_tokens=n, output_tokens=m)` to simulate token reports, since the harness records every `trace.emit` call during a case. No harness test calls a real model.
- **Shell requirements** (DB1-1, DB1-2, EV1-7) are manual checklists in the test file, as in `test_op1.py`: no Docker inside the test container.
- Iteration 01 tests that wrote hook files into a temporary `WORKSPACES` now use the `database` fixture and seed through `hooks.save_hook`; their assertions change only where hook-contract v2 changed the behaviour (`run_hooks` returns entries, not names).
