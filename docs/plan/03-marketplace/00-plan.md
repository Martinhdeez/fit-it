# 03 — Feature marketplace

## Goal of the increment

Users of the app share features. An owner publishes one of their hooks with a title and description, by asking the agent or from the widget. Every signed-in user can search the marketplace, read a feature's code, and subscribe after a dry run on their own data. A subscribed feature runs as the subscriber, before their own hooks, pinned to a version until they accept an update. Subscribers toggle, unsubscribe, accept updates or fork; anyone can report. One list, "My features", shows each user everything that runs for them, own and subscribed, with on, off and delete for each. The agent searches the marketplace before writing new code.

Decisions: [ADR 0022](../../adr/0022-feature-marketplace.md) (the marketplace) and [ADR 0023](../../adr/0023-features-run-as-the-subscriber.md) (the security property: a feature runs exactly as if the subscriber had written it). The demo clinic app is unchanged.

## Starts from

`develop` at `6554dff`: iteration 02 merged (DB1, HS2, CH1, AG2, SF1, EV1). RL1 (`v0.4.0`) is still open in [checklist 02](../../advance/checklist-02.md) and does not block this plan.

## How items run

As in iterations 01 and 02 ([ADR 0013](../../adr/0013-developer-and-tester-agents.md)): per item, a developer and a tester start together from the same brief ([epics README](../../epics/README.md#brief-for-an-agent)). The tester reads only this folder, `docs/specs/` and `docs/adr/`. A reviewer rules on failed acceptance tests. A human merges when every requirement passes. **A developer edits only the files its row in "File ownership" lists**, plus new files under the paths listed; if it needs another file, it stops and asks.

## Items

| Id | Item | Epic | Depends on | Requirements |
|---|---|---|---|---|
| FX0 | Merge `fix/appointments-sorted-eval` | [E11](../../epics/e11-evals.md) | — | Human action (evals spec, "Changes") |
| MK1 | Marketplace module and tables | [E12](../../epics/e12-marketplace.md) | FX0 | REQ-MK1-1 … 13 |
| MK2 | Subscribed features in the proxy, marketplace routes, My features | [E12](../../epics/e12-marketplace.md) | MK1 | REQ-MK2-1 … 13 |
| AG3 | Marketplace tools and instructions in the agent | [E12](../../epics/e12-marketplace.md) | MK1 | REQ-AG3-1 … 9 |
| EV2 | Marketplace eval cases and case `setup` | [E12](../../epics/e12-marketplace.md) | MK2, AG3 | REQ-EV2-1 … 4 |
| WM1 | Marketplace panel and My features in the widget | [E12](../../epics/e12-marketplace.md) | MK2 (contract now, integration after merge) | **Frontend teammate**, no REQ |
| RL2 | Release `v0.5.0` | [E7](../../epics/e7-delivery.md) | all above | Human action |
| TR1 | Trace sink | [E6](../../epics/e6-traceability.md) | — | Carried over — **Álvaro**; confirm the iteration 03 trace additions |

Why these cuts:
- All marketplace rules live in one new module, `backend/marketplace.py` (MK1), so routes and tools are thin callers and the rules are tested once.
- `hooks.py` only gains a `features` parameter on `run_hooks` and one `UPDATE` in `delete_hook`; it never reads marketplace tables. That change and the proxy's use of it break nothing alone but mean nothing apart, and both touch the proxy path, so they are one item with the routes: MK2 owns both hot files `hooks.py` and `main.py`.
- `agent.py` is the third hot file, owned by AG3 alone, in parallel with MK2.
- "My features" is computed in the module (`marketplace.my_features`, MK1) and served by one new route (`GET /fitit/features`, MK2). `GET /fitit/hooks` is not extended: iteration 01 tests assert its exact shape.

## File ownership

Tests: each tester owns `tests/acceptance/test_<item>.py` for its item. Only the files below change outside that.

| Item | Developer owns | Tester owns |
|---|---|---|
| MK1 | `backend/marketplace.py` (new), `backend/schema.sql` (append the four tables), `tests/unit/test_marketplace.py` (new) | `tests/acceptance/test_mk1.py` |
| MK2 | `backend/hooks.py` (`run_hooks` `features`, `delete_hook` unpublish), `backend/main.py` (`apply_hooks`, `hook.failed` route for features, `GET /fitit/features`, `/fitit/marketplace/...` routes), `tests/unit/test_hooks.py`, `tests/unit/test_proxy.py`, `tests/unit/test_marketplace_routes.py` (new), `docs/architecture/overview.md` (marketplace in components, change path and security model) | `tests/acceptance/test_mk2.py` |
| AG3 | `backend/agent.py`, `tests/unit/test_agent.py` | `tests/acceptance/test_ag3.py` |
| EV2 | `evals/cases.py`, `evals/run_evals.py`, `tests/unit/test_evals.py` | `tests/acceptance/test_ev2.py`, and `tests/acceptance/test_ev1.py` (`SPEC_CASES` for the new and changed cases) |
| WM1 | Widget sources and `backend/static/ui/` (frontend teammate) | — |

`backend/hooks.py` and `backend/main.py` have one owner each in this iteration (MK2); `backend/agent.py` one (AG3); `backend/schema.sql` one (MK1). No file has two owners in one wave.

## Waves

0. **FX0** (human): merge `fix/appointments-sorted-eval` into `develop`, so EV2 starts from the fixed `evals/cases.py`. Then merge this plan.
1. **MK1.** All testers may start in this wave: they only need these docs. The frontend teammate may start WM1 against the marketplace spec.
2. **MK2 ∥ AG3.** Disjoint files; any merge order. AG3 never imports `main`; MK2 never imports `agent` beyond the existing lazy imports.
3. **EV2.** Needs the agent tools (AG3) and the routes its checks read (MK2).
4. **RL2 — release `v0.5.0`** (human). A human runs `make eval` once with the default model and records the pass rate and tokens in `CHANGELOG.md` under `0.5.0`, then `release/0.5.0` from `develop` → `main`, tag `v0.5.0`, merge back into `develop`. WM1 may land before or after.

TR1 (Álvaro) runs alongside, against `trace-events.md`, which gains the `feature.*` events and `@<feature_id>` hook names (minor, additive).

## Contract changes announced

| Spec | From → to | Breaking for |
|---|---|---|
| [marketplace.md](../../specs/marketplace.md) | new, v1 | — |
| [storage.md](../../specs/storage.md) | v1 → v2 | — (new tables) |
| [hook-contract.md](../../specs/hook-contract.md) | v2 → v3 | — (optional `features` argument; `delete_hook` unpublishes) |
| [proxy.md](../../specs/proxy.md) | v2 → v3 | — (users without subscriptions see v2) |
| [agent.md](../../specs/agent.md) | v2 → v3 | — (new tools and event) |
| [embed-widget.md](../../specs/embed-widget.md) | v2 → v3 | — (old routes stay) |
| [evals.md](../../specs/evals.md) | v1, case changes | `test_ev1.py` `SPEC_CASES` (`appointments_sorted`, new ids) |
| [trace-events.md](../../specs/trace-events.md) | v1, minor additions | — |

## Out of scope

Review or moderation, an API to read reports, ratings and comments, paid features, multi-hook features, marketplaces across apps, auto-update and notifications, full-text search and pagination, owner analytics beyond the count, deleting feature rows, rate limits on publish or report.

## Next

Widget polish once WM1 lands. Read reports in an admin view if reports appear. Full-text search when listings pass a few hundred features.

## Open decisions

None blocking. Recorded in ADRs 0022 and 0023 and in the marketplace spec; the ones a human should confirm are listed in the pull request.
