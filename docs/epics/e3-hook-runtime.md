# E3 — Hook runtime

- **Item:** HR1 · **Spec:** [hook-contract.md](../specs/hook-contract.md) · **Requirements:** REQ-HR1-1 … 9
- **Depends on:** OP1 (test gate)
- **ADRs:** [0004](../adr/0004-per-user-python-hooks-in-process.md), [0005](../adr/0005-fail-closed-hook-execution.md)

## Goal

Store, validate and run per-user hooks safely. This is the security core of Fit-it: every rule in the guard has a test.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As an operator, generated code with imports, private attributes or dangerous builtins is rejected before it is stored or run. | REQ-HR1-1, REQ-HR1-2, REQ-HR1-3 |
| 2 | As the agent, I save a hook and get the diff against the previous version. | REQ-HR1-4 |
| 3 | As the proxy, I run exactly the user's enabled hooks that match a request, in a defined order. | REQ-HR1-5, REQ-HR1-6, REQ-HR1-7 |
| 4 | As the proxy, I know which hook failed, so I can fail closed and report it. | REQ-HR1-8 |
| 5 | As a hook author, I have `ctx.user`, `ctx.api.get`, `ctx.log` and date helpers. | REQ-HR1-9 |

## Deliverables

`backend/hooks.py` with exactly the exports of the module contract. Reuse the guard from the v0 `backend/main.py`; `backend/check.py` is replaced by tests under `tests/`.

## Out of scope

Time or memory limits, subprocess isolation, hook versioning beyond the diff.
