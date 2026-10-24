# 02 — Postgres core storage, agent quality, safety limits, evals

## Goal of the increment

The core keeps its state in its own Postgres. A user's hooks keep every version and can be undone, from the widget or by asking the agent; the agent remembers the conversation across reloads and edits existing hooks instead of piling up new ones. Runaway hooks fail closed in bounded time, chat is rate limited, and one command measures how well a real model handles a fixed set of realistic requests.

The demo clinic app is unchanged. [ADR 0003](../../adr/0003-api-only-access-to-client-data.md) still holds: the new database belongs to the core only.

## Starts from

`v0.3.0` (wave 0): iteration 01 as merged into `develop` at `a76b931`, with OP1, DA1, HR1, PX1 and AG1 done, the provider-agnostic model and the iframe widget host. Hooks are files under `WORKSPACES`, chat history lives per connection, hooks have no time limit.

## How items run

As in iteration 01 ([ADR 0013](../../adr/0013-developer-and-tester-agents.md)): per item, a developer and a tester start together from the same brief ([epics README](../../epics/README.md#brief-for-an-agent)). The tester reads only this folder, `docs/specs/` and `docs/adr/`. A reviewer rules on failed acceptance tests. A human merges when every requirement passes.

New rule for this iteration: **a developer edits only the files its row in "File ownership" lists**, plus new files it creates under the paths listed. If an item needs another file, it stops and asks; it never edits a file another item in the same wave owns.

## Items

| Id | Item | Epic | Depends on | Requirements |
|---|---|---|---|---|
| RL0 | Release `v0.3.0` | [E7](../../epics/e7-delivery.md) | — | Human action |
| DB1 | Postgres in the stack and the test gate | [E8](../../epics/e8-core-storage.md) | RL0 | REQ-DB1-1 … 6 |
| HS2 | Hook storage on Postgres, versions, undo, `dry_run`, per-hook timing | [E8](../../epics/e8-core-storage.md) | DB1 | REQ-HS2-1 … 10 |
| CH1 | Persistent chat history | [E8](../../epics/e8-core-storage.md) | DB1 | REQ-CH1-1 … 7 |
| AG2 | Agent quality: `dry_run`, edit, undo tool, instructions, rate limit, history route | [E9](../../epics/e9-agent-quality.md) | HS2, CH1 | REQ-AG2-1 … 9 |
| SF1 | Hook time limit and output cap | [E10](../../epics/e10-safety-limits.md) | HS2 | REQ-SF1-1 … 6 |
| EV1 | Real-model eval set and `make eval` | [E11](../../epics/e11-evals.md) | HS2, CH1 | REQ-EV1-1 … 7 |
| RL1 | Release `v0.4.0` | [E7](../../epics/e7-delivery.md) | all above | Human action |
| TR1 | Trace sink | [E6](../../epics/e6-traceability.md) | — | Carried over — **Álvaro** |

Why these cuts: the proxy's adoption of the new `run_hooks` result (once planned as PX2) is five lines in `main.py` and breaks `make check` unless it lands with the contract change, so it is part of HS2. The agent's adoption of `dry_run` and the agent-quality work both live in `backend/agent.py`, so they are one item, AG2. The agent rate limit also lives in `agent.py`, so it is in AG2, not SF1.

## File ownership

Tests: each tester owns `tests/acceptance/test_<item>.py` for its item. Only the files below change outside that.

| Item | Developer owns | Tester owns |
|---|---|---|
| DB1 | `compose.yaml`, `backend/pyproject.toml`, `backend/uv.lock`, `backend/db.py` (new), `backend/schema.sql` (new, all three tables of the storage spec), `backend/main.py` (startup only: call `db.init()`), `tests/unit/test_db.py` (new) | `tests/acceptance/test_db1.py` |
| HS2 | `backend/hooks.py`, `backend/main.py` (`apply_hooks`, undo route), `tests/unit/test_hooks.py`, `tests/unit/test_proxy.py`, `tests/unit/test_agent.py` (fixtures only: hook files → database), `README.md`, `.gitignore`, `docs/architecture/overview.md`, `docs/product/demo-script.md` (remove `workspaces/` mentions) | `tests/acceptance/test_hs2.py`, and the fixture migration of `test_hr1.py`, `test_px1.py`, `test_ag1.py`, `test_demo_flow.py` |
| CH1 | `backend/agent.py` (history and `clear_history` only), `tests/unit/test_history.py` (new) | `tests/acceptance/test_ch1.py` |
| AG2 | `backend/agent.py`, `backend/main.py` (`DELETE /fitit/history`), `tests/unit/test_agent.py` | `tests/acceptance/test_ag2.py` |
| SF1 | `backend/hooks.py`, `tests/unit/test_limits.py` (new) | `tests/acceptance/test_sf1.py` |
| EV1 | `evals/cases.py` (new), `evals/run_evals.py` (new), `Makefile`, `tests/unit/test_evals.py` (new) | `tests/acceptance/test_ev1.py` |

`backend/agent.py` is owned by CH1 in wave 2 and AG2 in wave 3; `backend/hooks.py` by HS2 in wave 2 and SF1 in wave 3; `backend/main.py` by DB1, HS2 and AG2 in three different waves. No file has two owners in one wave.

## Waves

0. **RL0 — release `v0.3.0`** (human). Before this plan merges into `develop`: branch `release/0.3.0` from `develop` at `a76b931`, add `CHANGELOG.md` (sections `0.3.0`, `0.2.0`, `0.1.0` from the tags and merged PRs), merge into `main`, tag `v0.3.0`, merge back into `develop`. Then merge this plan.
1. **DB1.** All testers may start in this wave: they only need these docs.
2. **HS2 ∥ CH1.** Built in parallel; **HS2 merges first**. CH1 then rebases on `develop` and runs `make check` again before merging: after HS2, the agent tests run against a test schema, which CH1's history writes need.
3. **AG2 ∥ SF1 ∥ EV1.** Any merge order. After AG2 merges, EV1's tester re-runs REQ-EV1-6 (token sums).
4. **RL1 — release `v0.4.0`** (human). A human runs `make eval` once with the default model and records the pass rate and tokens in `CHANGELOG.md` under `0.4.0`, then `release/0.4.0` from `develop` → `main`, tag `v0.4.0`, merge back into `develop`.

TR1 (Álvaro) runs alongside, against `trace-events.md`, which gains `hook.restored` and token fields on `agent.answer` (minor, additive).

## Contract changes announced

| Spec | From → to | Breaking for |
|---|---|---|
| [hook-contract.md](../../specs/hook-contract.md) | v1 → v2 | Tests that wrote hook files; callers of `run_hooks` (the proxy, inside HS2) |
| [proxy.md](../../specs/proxy.md) | v1 → v2 | Configuration (`WORKSPACES` gone) |
| [agent.md](../../specs/agent.md) | v1 → v2 | Nothing on the WebSocket protocol |
| [storage.md](../../specs/storage.md) | new, v1 | — |
| [evals.md](../../specs/evals.md) | new, v1 | — |
| [trace-events.md](../../specs/trace-events.md) | v1, minor additions | — |

## Out of scope

Storing traces in Postgres (traceability owner's choice), an ORM, a migration tool, a connection pool, migrating iteration 01 hook files, restoring deleted hooks, redo, summarizing old history, subprocess isolation and memory limits for hooks, cost in money for evals, running evals in CI, widget buttons for undo and clear history (the routes exist; the frontend teammate adds them when wanted).

## Next

Demo polish: clinic UI and a recorded fallback for the demo. Widget undo and clear-history buttons. Connection pool and Postgres-backed rate limit if the core scales out.

## Open decisions

None blocking. Recorded in ADRs 0017–0021; the ones a human should confirm are listed in the pull request.
