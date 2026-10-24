# E7 — Delivery

- **Item:** OP1 · **Requirements:** REQ-OP1-1 … 3
- **Depends on:** nothing
- **ADRs:** [0010](../adr/0010-docker-compose-and-secrets-from-the-shell.md), [0013](../adr/0013-developer-and-tester-agents.md)

## Goal

One command to run the stack and one command that says "this is correct", the same for humans and agents.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As a contributor, `make check` tells me whether everything passes, inside Docker. | REQ-OP1-1 |
| 2 | As a presenter, `docker compose up` starts the demo, with the app only reachable through the core. | REQ-OP1-2 |
| 3 | As an agent, I can run all tests without an API key. | REQ-OP1-3 |

## Deliverables

`Makefile` with `check` (and `up`, `down`), `pytest` as a dev dependency, `tests/unit/` and `tests/acceptance/` folders, the test environment from [contracts.md](../plan/01-mvp/contracts.md), `workspaces/` and `.scratch/` in `.gitignore`.

## Out of scope

CI workflows, deployment to a server.
