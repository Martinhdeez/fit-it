# E13 — Parallel conversations

- **Items:** HK1, CV1, AG4, WC1 · **Specs:** [agent.md](../specs/agent.md) v4, [hook-contract.md](../specs/hook-contract.md) v4, [storage.md](../specs/storage.md) v3, [proxy.md](../specs/proxy.md) v4, [marketplace.md](../specs/marketplace.md) v2, [embed-widget.md](../specs/embed-widget.md) v4, [trace-events.md](../specs/trace-events.md) · **Requirements:** REQ-HK1-1 … 9, REQ-CV1-1 … 11, REQ-AG4-1 … 7
- **Depends on:** iteration 03 (MK2, AG3 merged)
- **ADRs:** [0024](../adr/0024-parallel-conversations.md), [0025](../adr/0025-hook-edits-under-parallel-conversations.md)

## Goal

A user works in several chat windows at once, each with its own conversation, without the windows mixing their context or overwriting each other's hooks; and a new hook is accepted only if the user's whole route still works with it.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As a user, each chat window remembers only its own conversation, and old clients keep their single conversation. | REQ-CV1-1 … 4 |
| 2 | As a user, two windows answer at the same time; the same conversation never runs two turns at once. | REQ-CV1-5, REQ-CV1-6 |
| 3 | As a user, I see my conversations with a title and when I last used them, and delete one or all. | REQ-CV1-8, REQ-CV1-9 |
| 4 | As an operator, the rate limit still counts all of a user's windows, and traces say which conversation a message belongs to. | REQ-CV1-7, REQ-CV1-10 |
| 5 | As a user, two windows saving the same hook never lose a save or crash. | REQ-HK1-1 … 4 |
| 6 | As a user, the agent never overwrites a change it did not see; it re-reads and reapplies. | REQ-AG4-1 … 3 |
| 7 | As a user, a new hook, or a subscription, that would break another of my hooks on the same route is refused, naming that hook. | REQ-HK1-5 … 8, REQ-AG4-4, REQ-AG4-5 |
| 8 | As a user, the agent extends my existing hook on a route when my request changes what that hook does, and puts an unrelated change in a new hook on that route. | REQ-AG4-6 |
| 9 | As a user, the widget shows a list of conversations and opens each in its own window. | WC1 (frontend teammate, no REQ) |

## Deliverables

- HK1: `backend/hooks.py` (`save_hook` lock and `expected_version`, `Conflict`, `read_hook_version`, `versions`, chain `dry_run`), `backend/marketplace.py` (chain dry run for subscribe and accept update).
- CV1: `backend/schema.sql` (`conversation_id`), `backend/agent.py` (conversation per socket, history per conversation, busy set, `list_conversations`, `clear_history(user, conversation)`), `backend/main.py` (WS parameter, `GET /fitit/conversations`, `DELETE /fitit/history?conversation=`), `docs/architecture/overview.md`.
- AG4: `backend/agent.py` (`write_hook` with expected version and chain dry run, `read_hook` records the version, instruction rule).
- WC1: the widget's conversation list and windows (frontend teammate).

## Out of scope

Manual reordering of hooks, a route returning a conversation's past messages, renaming conversations, queuing a message for a busy conversation, summarizing old turns, sharing a conversation between users, expected-version checks on undo, toggle and delete, chain dry run for fork.
