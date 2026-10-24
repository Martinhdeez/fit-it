# E12 — Feature marketplace

- **Items:** MK1, MK2, AG3, EV2, WM1 · **Specs:** [marketplace.md](../specs/marketplace.md) v1, [storage.md](../specs/storage.md) v2, [hook-contract.md](../specs/hook-contract.md) v3, [proxy.md](../specs/proxy.md) v3, [agent.md](../specs/agent.md) v3, [embed-widget.md](../specs/embed-widget.md) v3, [evals.md](../specs/evals.md), [trace-events.md](../specs/trace-events.md) · **Requirements:** REQ-MK1-1 … 13, REQ-MK2-1 … 13, REQ-AG3-1 … 9, REQ-EV2-1 … 4
- **Depends on:** iteration 02 (hooks in Postgres, `dry_run`, limits, evals)
- **ADRs:** [0022](../adr/0022-feature-marketplace.md), [0023](../adr/0023-features-run-as-the-subscriber.md)

## Goal

Users of one app share what works: an owner publishes a hook as a feature, anyone finds it, tries it on their own data and subscribes, pinned to a version, running with their own session. Each user turns on, turns off and deletes everything that runs for them, own or subscribed, from one list.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As an owner, I publish one of my hooks with a title and description, publish again to release a new version, and unpublish it. | REQ-MK1-2, REQ-MK1-3, REQ-MK1-4, REQ-MK1-9, REQ-MK2-9 |
| 2 | As a user, I search the marketplace and see what each feature does and how many use it, never who. | REQ-MK1-5, REQ-MK2-8 |
| 3 | As a user, I subscribe only after the feature worked on my own data. | REQ-MK1-6, REQ-MK1-7, REQ-MK2-10 |
| 4 | As a user, a subscribed feature runs with my identity and my session, before my own hooks, and fails closed. | REQ-MK2-1 … 6 |
| 5 | As a subscriber, the owner's changes reach me only when I accept them. | REQ-MK1-8, REQ-MK2-3 |
| 6 | As a subscriber, I turn a feature on or off, remove it, or fork it into my own hook. | REQ-MK1-10, REQ-MK1-11, REQ-MK2-10 |
| 7 | As a user, one list shows all my features, own and subscribed, in run order, and I can toggle and delete each. | REQ-MK1-13, REQ-MK2-11, REQ-MK2-12 |
| 8 | As an owner, deleting my published hook hides it from new users and leaves subscribers running. | REQ-MK2-7 |
| 9 | As anyone, I can report a feature. | REQ-MK1-12, REQ-MK2-10 |
| 10 | As a user, the agent finds an existing feature before writing a new one, and publishes, subscribes, updates and forks for me after I confirm. | REQ-AG3-1 … 9 |
| 11 | As a maintainer, the eval set measures the marketplace flow. | REQ-EV2-1 … 4 |
| 12 | As a user, the widget has a marketplace panel and a "My features" view. | WM1 (frontend teammate, no REQ) |

## Deliverables

- MK1: `backend/marketplace.py` (new), the four tables in `backend/schema.sql`.
- MK2: `backend/hooks.py` (`run_hooks(..., features)`, `delete_hook` unpublishes), `backend/main.py` (features in `apply_hooks`, `GET /fitit/features`, `/fitit/marketplace/...`), `docs/architecture/overview.md`.
- AG3: marketplace tools, instructions and `feature_changed` in `backend/agent.py`.
- EV2: `setup` and four cases in `evals/`, plus the `appointments_sorted` fix.
- WM1: the widget views (frontend teammate).

## Out of scope

Review or moderation, reading reports through an API, ratings, comments, paid features, features made of several hooks, cross-app marketplaces, auto-update, notifications, full-text search and pagination, owner analytics beyond the subscriber count.
