# 0022. Feature marketplace inside one app

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

Every user of the app asks the agent for their own hooks, and many ask for the same thing: the Friday discount, hiding the DNI, an age column. Each request costs model tokens and a few minutes, and each copy is a slightly different hook. Users of the same app ([ADR 0009](0009-one-client-app-per-core.md): one app per core) could share what already works.

## Decision

A marketplace of **features** inside the core. One feature is one hook of its owner, published with a title and a description.

- **Publish.** The owner asks the agent ("publish my Friday discount"): the agent drafts a title and description from the code, shows them, and publishes only after the owner confirms. The widget publishes directly through `POST /fitit/marketplace/features`. Publishing copies the hook's current code into a numbered **feature version**; the feature is a snapshot, not a link to the live hook.
- **Visibility.** Every signed-in user of the app sees every published feature, searchable by title and description. There is no review step: the guard, the time limit and the output cap already apply to every run ([ADR 0020](0020-hook-time-limit-and-safety-limits.md)). Any user can report a feature; reports are only recorded.
- **Subscribe.** A user subscribes to a feature after a dry run of it on their own data; a failing dry run refuses the subscription with the reason. How a subscribed feature runs is [ADR 0023](0023-features-run-as-the-subscriber.md).
- **Versions.** A subscriber is pinned to the version they subscribed to. When the owner publishes again with different code, the feature gets the next version and subscribers see "update available"; accepting it dry-runs the new version first. The owner's edits to their own hook change nothing for subscribers until the owner publishes again.
- **Only the owner edits.** A subscriber can toggle, unsubscribe, accept an update, or **fork**: copy the code into their own hooks as a normal hook they own, unlinked from the feature.
- **Unpublish.** Unpublishing, or deleting the hook, hides the feature from listings and from new subscriptions. Existing subscribers keep running their pinned version.
- **Listings** show title, description, route, owner id, current version and subscriber count; sorted by subscribers or newest. Subscriber identities are never exposed, only the count.
- **Storage** in the core's Postgres ([ADR 0017](0017-postgres-as-core-storage.md)): tables `features`, `feature_versions`, `subscriptions`, `reports` in the [storage spec](../specs/storage.md). All marketplace logic lives in a new module, `backend/marketplace.py`, so `hooks.py` changes by a parameter and one statement.

## Consequences

- A common request is answered in seconds by subscribing, without a model call writing new code. The agent searches the marketplace before writing.
- Pinned versions mean an owner can never change code running on someone else's data without that person accepting, and a dry run, first.
- Features are snapshots, so one owner hook can diverge from its published version; the owner sees it only when publishing again.
- No moderation. A misleading or broken feature stays listed until its owner unpublishes it; reports are the record a human reads later, in SQL.
- **Known ceiling:** search is word-by-word substring matching over at most 50 results, no ranking or pagination. Enough for one app's users; full-text search when listings grow.
- Feature rows are never deleted, because subscriptions and reports point to them.

## Alternatives considered

- **Subscribers follow the owner's live hook:** zero version bookkeeping, but the owner could change code on other people's data at any time.
- **Auto-update to the latest version:** convenient, same problem with one step of delay.
- **Review before listing:** safer content, but needs a reviewer role and a queue nobody staffs during a hackathon.
- **Share by copying only (fork, no subscriptions):** simplest, but owners' fixes never reach anyone.
- **A feature as a bundle of several hooks:** more expressive; one hook per feature covers every request we have seen.
