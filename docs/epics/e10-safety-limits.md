# E10 — Safety limits for hooks

- **Item:** SF1 · **Spec:** [hook-contract.md](../specs/hook-contract.md#limits) v2 · **Requirements:** REQ-SF1-1 … 6
- **Depends on:** HS2 (`hooks.py` on Postgres, `dry_run`)
- **ADRs:** [0004](../adr/0004-per-user-python-hooks-in-process.md), [0005](../adr/0005-fail-closed-hook-execution.md), [0020](../adr/0020-hook-time-limit-and-safety-limits.md)

## Goal

A slow or runaway hook fails closed in bounded time, and no hook can return an unbounded body.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As an operator, a hook stuck in a loop fails in about two seconds instead of hanging a worker. | REQ-SF1-1, REQ-SF1-2, REQ-SF1-5 |
| 2 | As the agent, a looping candidate is rejected by the dry run, so it is never saved. | REQ-SF1-3 |
| 3 | As an operator, a hook cannot return a giant body. | REQ-SF1-4 |
| 4 | As a developer, the limit does not leave the thread's tracer changed. | REQ-SF1-6 |

## Deliverables

Limits in `backend/hooks.py`, applied in `run_hooks` and `dry_run`. The proxy needs no change: a breached limit is a `HookError`.

## Out of scope

Subprocess isolation, memory limits, interrupting long C calls (known ceiling, ADR 0020). The agent rate limit is in AG2, because it lives in `backend/agent.py`.
