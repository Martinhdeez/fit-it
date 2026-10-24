# 04 — Parallel conversations

## Goal of the increment

A user opens several chat windows in the widget, each with its own conversation and history, and the agent answers them in parallel. The same conversation never runs two turns at once. Parallel turns can touch the same hook safely: saves of one hook are serialized, and the agent never overwrites a change it did not see; it re-reads and reapplies. A new hook, or a new subscription, is dry-run inside the user's real chain for its route, so it is refused if it would break another hook there. The agent extends the hook already on a route instead of adding a second one.

Decisions: [ADR 0024](../../adr/0024-parallel-conversations.md) (conversations) and [ADR 0025](../../adr/0025-hook-edits-under-parallel-conversations.md) (serialized saves, expected version, chain dry run). The demo clinic app is unchanged.

## Starts from

`develop` with iteration 03's code merged: MK1 (`5c656ce`), **MK2** and **AG3**, which are being built on their branches while this plan is written. The specs below are described against their iteration 03 versions (hook contract v3, agent v3, proxy v3, storage v2, marketplace v1, embed widget v3). EV2, WM1 and RL2 do not block this plan: none of them touches a file below.

## How items run

As in iterations 01 to 03 ([ADR 0013](../../adr/0013-developer-and-tester-agents.md)): per item, a developer and a tester start together from the same brief ([epics README](../../epics/README.md#brief-for-an-agent)). The tester reads only this folder, `docs/specs/` and `docs/adr/`. A reviewer rules on failed acceptance tests. A human merges when every requirement passes. **A developer edits only the files its row in "File ownership" lists**, plus new files under the paths listed; if it needs another file, it stops and asks.

## Items

| Id | Item | Epic | Depends on | Requirements |
|---|---|---|---|---|
| IT3 | Merge MK2 and AG3 into `develop` | [E12](../../epics/e12-marketplace.md) | — | Human action |
| HK1 | Serialized hook saves, expected version, chain dry run | [E13](../../epics/e13-parallel-conversations.md) | IT3 | REQ-HK1-1 … 9 |
| CV1 | Parallel conversations: storage, socket, routes | [E13](../../epics/e13-parallel-conversations.md) | IT3 | REQ-CV1-1 … 11 |
| AG4 | Agent edits on a known version, chain dry run, extend the route's hook | [E13](../../epics/e13-parallel-conversations.md) | HK1, CV1 | REQ-AG4-1 … 7 |
| WC1 | Conversation list and chat windows in the widget | [E13](../../epics/e13-parallel-conversations.md) | CV1 (contract now, integration after merge) | **Frontend teammate**, no REQ |
| RL3 | Release `v0.6.0` | [E7](../../epics/e7-delivery.md) | all above | Human action |
| TR1 | Trace sink | [E6](../../epics/e6-traceability.md) | — | Carried over — **Álvaro**; confirm the iteration 04 trace additions |

Why these cuts:
- The four hot files are `hooks.py`, `agent.py`, `main.py` and `schema.sql`, plus `marketplace.py`. HK1 owns everything about hooks (`hooks.py`, and the two lines of `marketplace.py` that call the new dry run), so the locking, the version check and the chain are tested once, below the agent.
- CV1 owns the conversation plumbing end to end (`schema.sql`, `agent.py`, `main.py`): the column, the socket parameter, history per conversation, the busy rule and the two routes break nothing alone but mean nothing apart.
- AG4 is the only other change to `agent.py` (how `write_hook` and `read_hook` use HK1), so it waits for both HK1 (the API it calls) and CV1 (the file it edits). It is small: the agent part of ADR 0025 and one instruction.
- No new module and no new table: a conversation is a column, its title is computed from the stored turns.

## File ownership

Tests: each tester owns `tests/acceptance/test_<item>.py` for its item. Only the files below change outside that.

| Item | Developer owns | Tester owns |
|---|---|---|
| HK1 | `backend/hooks.py` (lock, `expected_version`, `Conflict`, `read_hook_version`, `versions`, chain `dry_run`), `backend/marketplace.py` (`_dry_run` for subscribe and accept update only), `tests/unit/test_hooks.py`, `tests/unit/test_marketplace.py` | `tests/acceptance/test_hk1.py` |
| CV1 | `backend/schema.sql` (`ALTER TABLE messages`, index), `backend/agent.py` (`handle_ws` conversation, history, busy set, `list_conversations`, `clear_history`, `conversation` on `agent.*` events), `backend/main.py` (WS parameter, `GET /fitit/conversations`, `DELETE /fitit/history?conversation=`), `tests/unit/test_history.py`, `tests/unit/test_agent.py`, `tests/unit/test_proxy.py`, `docs/architecture/overview.md` (conversations in the change path) | `tests/acceptance/test_cv1.py`, and `tests/acceptance/test_db1.py` (the `EXPECTED["messages"]` columns only) |
| AG4 | `backend/agent.py` (`write_hook`, `read_hook`, `undo_hook` and `delete_hook` recording versions, version snapshot when the message starts, instruction rule), `tests/unit/test_agent.py` | `tests/acceptance/test_ag4.py` |
| WC1 | Widget sources and `backend/static/ui/` (frontend teammate) | — |

In wave 1, `hooks.py` and `marketplace.py` belong to HK1 only; `agent.py`, `main.py` and `schema.sql` to CV1 only. In wave 2, `agent.py` belongs to AG4 only. No file has two owners in one wave. `tests/unit/test_agent.py` is CV1's in wave 1 and AG4's in wave 2.

## Waves

0. **IT3** (human): merge MK2 and AG3 into `develop`, then merge this plan. Testers may start as soon as this plan merges: they only need these docs.
1. **HK1 ∥ CV1.** Disjoint files; any merge order. CV1 does not call any new `hooks` export; HK1 does not touch `agent.py`. The frontend teammate may start WC1 against the embed widget spec.
2. **AG4.** Needs HK1's `save_hook(expected_version=)`, `read_hook_version`, `versions` and chain `dry_run`, and CV1's `agent.py`.
3. **RL3 — release `v0.6.0`** (human): `release/0.6.0` from `develop` → `main`, tag `v0.6.0`, merge back into `develop`. Optionally run `make eval` once and record the pass rate in `CHANGELOG.md` under `0.6.0`. WC1 may land before or after.

TR1 (Álvaro) runs alongside, against `trace-events.md`, which gains `conversation` on the `agent.*` events and new `hook.rejected` reasons (minor, additive).

## Contract changes announced

| Spec | From → to | Breaking for |
|---|---|---|
| [hook-contract.md](../../specs/hook-contract.md) | v3 → v4 | — (optional `expected_version`, `name`, `features`; new exports) |
| [storage.md](../../specs/storage.md) | v2 → v3 | `test_db1.py` exact columns of `messages` (CV1 tester updates it) |
| [agent.md](../../specs/agent.md) | v3 → v4 | — (no `conversation` means `default`) |
| [proxy.md](../../specs/proxy.md) | v3 → v4 | — (optional parameters, new route) |
| [marketplace.md](../../specs/marketplace.md) | v1 → v2 | — (subscribe and accept update stricter only with other hooks on the route) |
| [embed-widget.md](../../specs/embed-widget.md) | v3 → v4 | — (a widget without conversations keeps working) |
| [trace-events.md](../../specs/trace-events.md) | v1, minor additions | — |

## Out of scope

Manual reordering of hooks. A route returning a conversation's past messages, renaming conversations, a `conversations` table. Queuing messages for a busy conversation. Expected-version checks on undo, toggle and delete. Chain dry run for fork. Moving the busy set and the rate limit to Postgres.

## Next

A route returning a conversation's messages, so a reopened window shows its past turns. Busy set and rate limit in Postgres when the core runs more than one process. An eval case for "extend the existing hook on a route".

## Open decisions

None blocking. Recorded in ADRs 0024 and 0025 and in the specs; the ones a human should confirm are listed in the pull request.
