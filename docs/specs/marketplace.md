# Marketplace — v2

Publishing a hook as a **feature** that every signed-in user of the app can find, subscribe to, update, fork and report; and the "My features" list that shows a user everything that runs for them. Decisions: [ADR 0022](../adr/0022-feature-marketplace.md), [ADR 0023](../adr/0023-features-run-as-the-subscriber.md), [ADR 0025](../adr/0025-hook-edits-under-parallel-conversations.md).

## Changes from v1

Iteration 04. The v1 text is in git history (`git show 5c656ce:docs/specs/marketplace.md`).

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | Subscribe and accept update dry-run the feature inside the subscriber's whole chain for its route; a failure of another hook or feature in it refuses with `dry_run_failed` naming that hook ("Terms", Dry run). Fork keeps the v1 dry run. | No for v1 tests (stricter only when the subscriber has other hooks on the route) | HK1 |

Tables: [storage spec](storage.md#marketplace-tables). How subscribed features run on requests: [hook contract](hook-contract.md#features) and [proxy spec](proxy.md#applying-hooks). Agent tools: [agent spec](agent.md#tools). Widget UI: [embed widget spec](embed-widget.md#marketplace-panel-and-my-features).

## Terms

| Term | Meaning |
|---|---|
| Feature | One hook of its owner, published. Identified by an integer `id`. At most one feature per `(owner, hook name)`: publishing the same hook again reuses it. |
| Version | A frozen copy of the hook's code at publish time, numbered `1, 2, 3, ...` per feature. The feature's `version` is its latest one. |
| Published | Listed and open to new subscriptions. Unpublished features are hidden from listings but keep running for existing subscribers. |
| Visible to a user | Published, or the user is its owner, or the user is subscribed to it. |
| Subscription | A user's link to a feature, pinned to one `version`, `enabled` or not. A user has at most one subscription per feature and can never subscribe to their own feature. |
| Feature name | `@<id>`, for example `@3`. Used wherever a hook name appears at run time (`x-fitit-hooks`, `HookError.hook`, trace events). |
| Dry run | `hooks.dry_run(user, code, data, api)` as the subscribing user, with `data` chosen as the agent's `write_hook` does: for a `GET` route without `{param}` segments, the live `GET` response through `api`; for a `/x/` route, `None`; any other route skips the run (the guard still applies). Failing it means the guard rejects the code, `api.get` raises, or `dry_run` raises. **Subscribe and accept update** run the chain dry run of the [hook contract](hook-contract.md#chain-dry-run) with `name="@<feature_id>"` and `features=active(user)`: the feature takes its place among the user's enabled subscriptions (in place when already subscribed, last when new), followed by the user's enabled own hooks on that route. If another hook or feature of that chain fails, the reason (`str(e)`) is `<hook> failed: <detail>`. **Fork** dry-runs the code alone, as in v1. |

## Rules

1. **Publish** `(owner, hook name, title, description)`: the hook must exist for the owner and pass the guard. `title` is 1–80 characters and `description` 1–500 after stripping spaces. If no feature exists for that hook, it is created with version `1`. Otherwise title and description are replaced, the feature is published again, and a new version is added **only if** the hook's code differs from the latest version's code.
2. **The owner's later edits** to the hook (save, undo, toggle) change nothing in the feature until they publish again. Toggling the hook off stops it for the owner only.
3. **Unpublish** (owner only), and **deleting the published hook** (`hooks.delete_hook`, by route or agent tool): the feature becomes unpublished. Subscribers keep their subscriptions and pinned versions, and the code keeps running for them. Publishing the same hook name again later publishes the same feature.
4. **Subscribe**: the feature must be published, not the user's own, and not already subscribed. The latest version is dry-run as the subscriber; if it fails the subscription is refused with the reason and nothing is written. Otherwise the subscription is created enabled, pinned to that version, and placed **last** in the user's subscription order.
5. **Toggle** a subscription: flips `enabled`. A disabled subscription stays in the list and keeps its pin and its place, but does not run.
6. **Unsubscribe** (the "delete" of a subscribed feature): removes the subscription. Subscribing again puts it last.
7. **Accept update**: only when the feature's `version` is greater than the pinned one. The latest version is dry-run as the subscriber; on failure nothing changes; on success the pin moves to the latest version, keeping `enabled` and the place in the order.
8. **Fork** `(user, feature, name)`: the feature must be visible to the user and not their own. The code copied is the user's pinned version if subscribed, otherwise the latest version. It is dry-run as the user, then saved with `hooks.save_hook` as a normal hook named `name` (default: the feature's hook name). `name` must not already be one of the user's hooks. If the user was subscribed, the subscription is removed in the same step, so the behaviour never runs twice. The new hook has no link to the feature.
9. **Report**: any signed-in user, on a visible feature, with a `reason` of 1–500 characters. Recorded only; no route or tool reads reports back.
10. **Subscriber count** is the number of subscriptions to the feature, enabled or not. Subscriber ids are never returned by any route or tool.
11. **Search**: `q` is split on spaces; a published feature matches when every word appears, case-insensitive, in its title or its description. Empty `q` matches all published features. At most 50 results. Sort `subscribers` (default): subscriber count descending, then `id` descending. Sort `newest`: `published_at` descending, then `id` descending.
12. Every rule applies equally to the HTTP routes, the agent tools and the module functions: the routes and tools call the module.

## Module contract: `backend/marketplace.py`

All functions take the acting user id first. `api` is any object with `get(path)` (the proxy passes `main.Api(cookie)` for that user). Failures raise `marketplace.Error`, with `.error` (the code below), `.status` (the HTTP status the route returns) and `str(e)` (a human-readable detail). Nothing is written when an `Error` is raised.

| `.error` | `.status` | When |
|---|---|---|
| `not_found` | 404 | Unknown feature, feature not visible, not subscribed (toggle, unsubscribe, accept update), or hook missing (publish) |
| `forbidden` | 403 | Unpublish by a user who is not the owner |
| `own_feature` | 409 | Subscribe to or fork one's own feature |
| `already_subscribed` | 409 | Subscribe twice |
| `up_to_date` | 409 | Accept update when the pin is already the latest version |
| `name_taken` | 409 | Fork into a hook name the user already has |
| `invalid` | 422 | Title, description, reason or fork name out of bounds, or hook code that fails the guard (publish) |
| `dry_run_failed` | 422 | The dry run failed (subscribe, accept update, fork); `str(e)` is the reason |

| Name | Behaviour |
|---|---|
| `publish(user, hook, title, description) -> dict` | Rule 1. Returns `{"id": int, "version": int, "created": bool}`; `created` is true only when the feature row is new. Emits `feature.published`. |
| `unpublish(user, feature_id) -> None` | Rule 3. Unpublishing an already unpublished feature succeeds. Emits `feature.unpublished`. |
| `search(user, q="", sort="subscribers") -> list[dict]` | Rule 11. Listing entries (shape below). An unknown `sort` raises `invalid`. |
| `get_feature(user, feature_id) -> dict` | Detail entry (shape below) of a visible feature. |
| `subscribe(user, feature_id, api) -> dict` | Rule 4. Returns the subscription entry. Emits `feature.subscribed`. |
| `toggle(user, feature_id) -> bool` | Rule 5. Returns the new `enabled`. Emits `feature.toggled`. |
| `unsubscribe(user, feature_id) -> None` | Rule 6. Emits `feature.unsubscribed`. |
| `accept_update(user, feature_id, api) -> dict` | Rule 7. Returns the subscription entry. Emits `feature.updated`. |
| `fork(user, feature_id, api, name=None) -> dict` | Rule 8. Returns `{"name": str, "route": str, "diff": str}` (`diff` from `hooks.save_hook`). Emits `hook.saved` for the new hook (as `write_hook` does), then `feature.forked`. |
| `report(user, feature_id, reason) -> None` | Rule 9. Emits `feature.reported`. |
| `subscriptions(user) -> list[dict]` | The user's subscription entries in subscription order, including disabled ones and unpublished features. |
| `active(user) -> list[dict]` | `[{"name": "@<id>", "code": str}]`: the pinned code of the user's **enabled** subscriptions, in subscription order. The proxy passes it to `hooks.run_hooks` ([hook contract](hook-contract.md#features)). |
| `my_features(user) -> list[dict]` | The "My features" list (shape below). |
| `Error` | The exception above. |

Trace events are emitted inside these functions with `trace.emit` (called as `trace.emit`, so a test can replace it), only after the change is committed. Kinds and fields: [trace events](trace-events.md#minor-additions-iteration-03).

## Shapes

**Listing entry** (search, listing route):

```json
{"id": 3, "title": "Friday discount", "description": "15% off invoices issued on a Friday",
 "route": "GET /api/invoices", "owner": "gerente", "version": 2, "subscribers": 5,
 "published_at": "2026-10-24T10:00:00+00:00", "mine": false, "subscribed": true}
```

`route` is the latest version's route. `published_at` is the ISO 8601 time the latest version was created. `mine`: the user is the owner. `subscribed`: the user has a subscription to it. Exactly these keys.

**Detail entry**: the listing entry plus `"published": bool` and `"code": str` (the latest version's code).

**Subscription entry**:

```json
{"feature_id": 3, "name": "@3", "title": "Friday discount", "route": "GET /api/invoices", "owner": "gerente",
 "version": 1, "latest_version": 2, "update_available": true, "enabled": true, "published": true}
```

`version` is the pinned version and `route` is that version's route. `update_available` is `latest_version > version`. Exactly these keys.

**My features entry**: one list of everything that can run for the user, in run order ([ADR 0023](../adr/0023-features-run-as-the-subscriber.md)): first the subscriptions in subscription order, then the user's own hooks in name order.

```json
[
  {"source": "subscribed", "name": "@3", "route": "GET /api/invoices", "enabled": true,
   "feature_id": 3, "title": "Friday discount", "owner": "gerente",
   "version": 1, "latest_version": 2, "update_available": true, "published": true},
  {"source": "own", "name": "hide_dni", "route": "GET /api/patients", "enabled": false, "code": "ROUTE = ...",
   "feature_id": 7, "published": true, "published_version": 1}
]
```

- `source` `"subscribed"`: the subscription entry's keys plus `"source"`. Exactly these keys.
- `source` `"own"`: the keys of a `GET /fitit/hooks` entry (`name`, `route`, `enabled`, `code`) plus `"source"`, `"feature_id"` (the feature published from this hook, or `null` if never published), `"published"` (that feature is currently published; `false` when there is none) and `"published_version"` (that feature's latest version, or `null`). Exactly these keys.

## HTTP routes

Owned by the core ([proxy spec](proxy.md#core-routes)). Every route returns `401` `{"error": "unauthenticated"}` for anonymous users. `{id}` that is not a positive integer is `404` `{"error": "not_found"}`. Request bodies are JSON objects; a body that is not a JSON object, or a missing or non-string field, is `422` `{"error": "invalid", "detail": str}`. Every `marketplace.Error` becomes status `.status` with body `{"error": .error, "detail": str}`, except `not_found`, whose body is `{"error": "not_found"}`.

| Route | Request | Success | Errors |
|---|---|---|---|
| `GET /fitit/features` | — | `200` My features list | — |
| `GET /fitit/marketplace/features?q=&sort=` | `q` optional, `sort` `subscribers` (default) or `newest` | `200` list of listing entries | `422 invalid` (unknown `sort`) |
| `GET /fitit/marketplace/features/{id}` | — | `200` detail entry | `404` |
| `POST /fitit/marketplace/features` | `{"hook": str, "title": str, "description": str}` | `201` `{"id", "version", "created": true}` for a new feature; `200` with `created` false when publishing again | `404` (no such hook), `422 invalid` |
| `DELETE /fitit/marketplace/features/{id}` | — | `204` (unpublish) | `404`, `403 forbidden` |
| `POST /fitit/marketplace/features/{id}/subscribe` | — | `200` subscription entry | `404` (unknown or unpublished), `409 own_feature`, `409 already_subscribed`, `422 dry_run_failed` |
| `POST /fitit/marketplace/features/{id}/toggle` | — | `200` `{"feature_id": int, "enabled": bool}` | `404` (not subscribed) |
| `POST /fitit/marketplace/features/{id}/unsubscribe` | — | `204` | `404` (not subscribed) |
| `POST /fitit/marketplace/features/{id}/accept-update` | — | `200` subscription entry | `404` (not subscribed), `409 up_to_date`, `422 dry_run_failed` |
| `POST /fitit/marketplace/features/{id}/fork` | `{}` or `{"name": str}` | `201` `{"name", "route", "diff"}` | `404`, `409 own_feature`, `409 name_taken`, `422 invalid` (bad name), `422 dry_run_failed` |
| `POST /fitit/marketplace/features/{id}/report` | `{"reason": str}` | `204` | `404`, `422 invalid` |

Own hooks keep their routes, unchanged: `GET /fitit/hooks`, `POST /fitit/hooks/{name}/toggle`, `DELETE /fitit/hooks/{name}` (which also unpublishes the hook's feature, rule 3), `POST /fitit/hooks/{name}/undo`. Together with the subscription routes above, a user can turn on, turn off and delete every entry of `GET /fitit/features`:

| Entry `source` | Turn on / off | Delete |
|---|---|---|
| `own` | `POST /fitit/hooks/{name}/toggle` | `DELETE /fitit/hooks/{name}` |
| `subscribed` | `POST /fitit/marketplace/features/{feature_id}/toggle` | `POST /fitit/marketplace/features/{feature_id}/unsubscribe` |
