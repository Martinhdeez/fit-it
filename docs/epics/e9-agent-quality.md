# E9 — Agent quality

- **Item:** AG2 · **Specs:** [agent.md](../specs/agent.md) v2, [proxy.md](../specs/proxy.md) v2 · **Requirements:** REQ-AG2-1 … 9
- **Depends on:** HS2 (`dry_run`, `undo_hook`), CH1 (history in `agent.py`)
- **ADRs:** [0018](../adr/0018-hook-versions-and-undo.md), [0019](../adr/0019-persistent-chat-history.md), [0020](../adr/0020-hook-time-limit-and-safety-limits.md)

## Goal

The agent edits what exists instead of piling up hooks, can undo, tries code without side effects, and cannot be used to burn tokens without limit.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As an operator, a rejected or dry-run hook leaves no trace in the database. | REQ-AG2-1, REQ-AG2-2 |
| 2 | As a user, "make it 20% instead" changes my existing hook and I see the diff. | REQ-AG2-3, REQ-AG2-4 |
| 3 | As a user, "undo that" restores the previous version of my hook. | REQ-AG2-5 |
| 4 | As a user, I can wipe my chat history from the widget. | REQ-AG2-6 |
| 5 | As an operator, one user cannot send unlimited messages to the model. | REQ-AG2-7 |
| 6 | As an operator, I see the tokens each answer cost. | REQ-AG2-8 |
| 7 | As a user, everything the agent did in iteration 01 and CH1 still works. | REQ-AG2-9 |

## Deliverables

`backend/agent.py`: `write_hook` on `hooks.dry_run`, prompt as `instructions` with the user's hooks, `undo_hook` tool, rate limit, token fields on `agent.answer`. `backend/main.py`: `DELETE /fitit/history`.

## Out of scope

Approval before saving, multi-agent flows, summarizing old history, widget buttons for undo and clear (frontend teammate, when wanted).
