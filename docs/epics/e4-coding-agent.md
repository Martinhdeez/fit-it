# E4 — Coding agent

- **Item:** AG1 · **Spec:** [agent.md](../specs/agent.md) · **Requirements:** REQ-AG1-1 … 8
- **Depends on:** HR1, PX1
- **ADRs:** [0007](../adr/0007-openapi-as-the-agent-catalog.md), [0008](../adr/0008-pydantic-ai-for-the-coding-agent.md)

## Goal

A user describes a change in plain language and gets a validated, saved hook, watching each step live.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As a user, only I can talk to my agent session. | REQ-AG1-1 |
| 2 | As a user, I see each step the agent takes, and the final answer. | REQ-AG1-2, REQ-AG1-3 |
| 3 | As the agent, I know the app's endpoints and can sample their data as the user. | REQ-AG1-4 |
| 4 | As a user, my request becomes a hook that only applies to me, and I see its diff. | REQ-AG1-5 |
| 5 | As an operator, unsafe or broken code is never saved, and the agent repairs it itself. | REQ-AG1-6, REQ-AG1-7, REQ-AG1-8 |

## Deliverables

`backend/agent.py` (Pydantic AI `agent` and `Deps`), the `WS /fitit/ws` route in `backend/main.py`, `pydantic-ai` added to `backend/pyproject.toml`.

## Out of scope

Approval before saving, multi-agent flows, LangGraph, conversation history across connections.
