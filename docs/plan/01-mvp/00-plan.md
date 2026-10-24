# 01 — MVP: personalize the demo clinic by chat

## Goal of the increment

A signed-in user of the demo clinic, reached through Fit-it, asks in the widget for a change ("15% discount on Friday invoices"). The agent writes a hook, and after a page reload that user, and only that user, sees the change. Hooks can be toggled and deleted, broken hooks fail closed, and every step is traced.

## Starts from

`v0.1.0`: Docker setup, `AGENTS.md`, docs. `backend/main.py` and `backend/check.py` are the v0 prototype (clinic data plus guard); its guard code is reused in HR1 and its clinic data in DA1, then the v0 files are removed.

## How items run

Per [ADR 0013](../../adr/0013-developer-and-tester-agents.md): for each item, a **developer** agent and a **tester** agent start together from the same brief. The tester reads only this folder, `docs/specs/` and `docs/adr/`, never the developer's branch. When an acceptance test fails, a **reviewer** agent rules whether the code, the test or the spec is wrong, and the right side fixes it. A human merges when every requirement passes.

## Items

| Id | Item | Epic | Depends on | Requirements |
|---|---|---|---|---|
| OP1 | Test gate and Compose stack | [E7](../../epics/e7-delivery.md) | — | REQ-OP1-1 … 3 |
| DA1 | Demo clinic app | [E1](../../epics/e1-demo-client-app.md) | — | REQ-DA1-1 … 6 |
| HR1 | Hook runtime | [E3](../../epics/e3-hook-runtime.md) | OP1 | REQ-HR1-1 … 9 |
| PX1 | Proxy | [E2](../../epics/e2-proxy-core.md) | HR1, DA1 | REQ-PX1-1 … 10 |
| AG1 | Coding agent | [E4](../../epics/e4-coding-agent.md) | HR1, PX1 | REQ-AG1-1 … 8 |
| EW1 | Embed widget | [E5](../../epics/e5-embed-widget.md) | AG1 | REQ-EW1-1 … 5 (manual) |
| TR1 | Trace sink | [E6](../../epics/e6-traceability.md) | — | REQ-TR1-1 … 3 — **Álvaro** |

## Waves

1. **OP1, DA1** in parallel. TR1 starts here and runs alongside everything.
2. **HR1.**
3. **PX1.**
4. **AG1.**
5. **EW1**, then the demo rehearsal from [`demo-script.md`](../../product/demo-script.md).

## Out of scope

Endpoint discovery from traffic, several apps per core, direct database access, hook time limits, sharing hooks between users, authentication of the core itself.

## Open decisions

None blocking. Recorded: Pydantic AI only, OpenAPI as the catalog, one app, developer + tester agents with a reviewer on failed tests.
