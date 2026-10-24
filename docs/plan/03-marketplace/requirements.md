# 03 — Requirements and verification

Acceptance is decided by tests whose author has not seen the implementation. Each requirement is one testable sentence and names the surface it is checked through. "Own schema" means the `database` fixture of [iteration 02 contracts](../02-postgres/contracts.md#test-environment). "Seeded feature" means a hook saved with `hooks.save_hook` and published with `marketplace.publish`, as in [contracts.md](contracts.md#test-environment). `Error(x, n)` means `marketplace.Error` with `.error == x` and `.status == n`.

## Requirements

### MK1 — Marketplace module and tables

| Id | Requirement | Checked through |
|---|---|---|
| REQ-MK1-1 | On an empty schema, `db.init()` creates `features`, `feature_versions`, `subscriptions` and `reports` with the columns, types, nullability, primary keys, unique keys and foreign keys of the storage spec, next to the unchanged v1 tables; a second `db.init()` raises nothing and keeps rows in all seven tables. | `db.init`, `information_schema`, SQL |
| REQ-MK1-2 | The first `publish` of a hook returns `{"id": <int>, "version": 1, "created": True}`, writes one `features` row (owner, hook name, title, description, `version` 1, `published` true) and one `feature_versions` row with the hook's current code and route, and emits one `feature.published` with `user`, `feature_id`, `hook`, `version` 1 and `created` true. | `marketplace.publish`, SQL, recorded `trace.emit` |
| REQ-MK1-3 | `publish` raises `Error("not_found", 404)` for a hook the user does not have, and `Error("invalid", 422)` for an empty or blank title, a title of 81 characters, an empty description, a description of 501 characters, or a hook row (inserted directly) whose code fails the guard; in each case no row is added to any marketplace table. | `marketplace.publish`, SQL |
| REQ-MK1-4 | After the owner saves new code for a published hook, `get_feature` still returns the old code and `version` 1; publishing again returns `version` 2 and `created` false with the new code, title and description; publishing a third time with unchanged code returns `version` 2, adds no `feature_versions` row and updates the title. | `marketplace.*`, `hooks.save_hook`, SQL |
| REQ-MK1-5 | `search` by any user returns only published features, each with exactly the listing keys of the marketplace spec and correct `mine` and `subscribed` flags; `q` matches only features containing every word of it, ignoring case, in title or description; `sort="subscribers"` orders by subscriber count descending and `sort="newest"` by latest version time descending; an unknown sort raises `Error("invalid", 422)`. | `marketplace.search`, `marketplace.subscribe` |
| REQ-MK1-6 | `subscribe` by another user, with feature code that raises unless `ctx.user` is that user and that calls `ctx.api.get` on the given `api`, succeeds: the `api` received the call, one `subscriptions` row exists pinned to the latest version and enabled, the return value is a subscription entry with exactly the spec's keys, the feature's `subscribers` in `search` grows by one, and `feature.subscribed` is emitted with `user`, `feature_id` and `version`. | `marketplace.subscribe`, fake `api`, SQL, recorded `trace.emit` |
| REQ-MK1-7 | `subscribe` raises `Error("dry_run_failed", 422)` whose text contains the exception message when the code raises on the subscriber's data, `Error("own_feature", 409)` for the owner, `Error("already_subscribed", 409)` the second time, and `Error("not_found", 404)` for an unknown or unpublished feature; none of them adds a `subscriptions` row. | `marketplace.subscribe`, SQL |
| REQ-MK1-8 | After the owner publishes version 2, the subscriber's `active()` still returns version 1's code and `subscriptions()` shows `version` 1, `latest_version` 2, `update_available` true; `accept_update` with version 2 failing the dry run raises `Error("dry_run_failed", 422)` and keeps the pin at 1; with version 2 passing it returns the entry with `version` 2, keeps the subscription's `id` and emits `feature.updated`; a further `accept_update` raises `Error("up_to_date", 409)`. | `marketplace.*`, SQL, recorded `trace.emit` |
| REQ-MK1-9 | `unpublish` by a non-owner raises `Error("forbidden", 403)`; by the owner it sets `published` false and emits `feature.unpublished`, after which `search` omits the feature for every user, a new `subscribe` raises `Error("not_found", 404)`, an existing subscriber's `active()` still returns the pinned code and `subscriptions()` shows `published` false, and `get_feature` works for the owner and the subscriber but raises `not_found` for anyone else. | `marketplace.*`, recorded `trace.emit` |
| REQ-MK1-10 | `toggle` returns false then true and emits `feature.toggled`; a disabled subscription is absent from `active()` and still present in `subscriptions()`; `unsubscribe` removes the row and emits `feature.unsubscribed`; subscribing again places the feature after the user's other subscriptions in `active()`; `toggle`, `unsubscribe` and `accept_update` without a subscription raise `Error("not_found", 404)`. | `marketplace.*`, SQL, recorded `trace.emit` |
| REQ-MK1-11 | `fork` by a subscriber pinned to version 1 while version 2 exists creates the user's hook named after the feature's hook with version 1's code (`hooks.version` 1), removes the subscription, returns `{"name", "route", "diff"}` and emits `hook.saved` then `feature.forked`; with `name` of an existing hook it raises `Error("name_taken", 409)`, with an invalid name `Error("invalid", 422)`, by the owner `Error("own_feature", 409)`, with code failing the dry run `Error("dry_run_failed", 422)`, and for an unpublished feature the user is not subscribed to `Error("not_found", 404)`, writing nothing in each case. | `marketplace.fork`, `hooks.read_hook`, SQL, recorded `trace.emit` |
| REQ-MK1-12 | `report` writes one `reports` row with the feature, the reporting user and the reason and emits `feature.reported`; an empty reason or one of 501 characters raises `Error("invalid", 422)` and an unknown feature `Error("not_found", 404)`, writing nothing. | `marketplace.report`, SQL, recorded `trace.emit` |
| REQ-MK1-13 | `my_features(user)` returns the user's subscriptions in subscription order, disabled ones included, then their own hooks in name order, disabled ones included, each with exactly the keys of its `source` in the marketplace spec; an own hook's `feature_id`, `published` and `published_version` reflect its feature (or `null`, false, `null` when never published). | `marketplace.my_features` |

### MK2 — Subscribed features in the proxy, routes, My features

| Id | Requirement | Checked through |
|---|---|---|
| REQ-MK2-1 | A seeded feature `GET /x/whoami` returning `{"user": ctx.user, "me": ctx.api.get("/api/me")["id"]}`, subscribed by another user, answers that user's `GET /x/whoami` through the core with `200` and both values equal to the subscriber's id. | Core HTTP |
| REQ-MK2-2 | With a user subscribed to features A then B and owning hook `own`, all on `GET /api/invoices` and each appending a marker to the first invoice, the response shows the markers in the order A, B, `own`, `x-fitit-hooks` is `@<A>,@<B>,own`, and `hook.applied` events (with `user` the subscriber) and the `request.proxied` `hooks` list use the same names in the same order. | Core HTTP, recorded `trace.emit` |
| REQ-MK2-3 | After the owner changes and republishes the hook, the subscriber's responses keep version 1's behaviour until `POST .../accept-update`, then show version 2's; the owner's unpublished edits never reach the subscriber; a toggled-off subscription does not run and does not appear in `x-fitit-hooks`. | Core HTTP |
| REQ-MK2-4 | A subscribed feature that raises on the subscriber's request gives `502` `{"error": "hook_failed", "hook": "@<id>", "detail": ...}` with none of the app's data, and one `hook.failed` with `hook` `@<id>` and that code's route. | Core HTTP, recorded `trace.emit` |
| REQ-MK2-5 | A subscribed feature whose pinned `feature_versions.code` is changed directly in SQL to code that fails the guard gives `502` with `hook` `@<id>` on its route. | SQL, Core HTTP |
| REQ-MK2-6 | A `/x/` path matched only by a subscribed feature returns `200` with the feature's output; after unsubscribing it returns `404` `{"error": "not_found"}`. | Core HTTP |
| REQ-MK2-7 | After the owner deletes a published hook with `DELETE /fitit/hooks/{name}`, the feature is missing from every user's listing and from the owner's `GET /fitit/features`, a new user's subscribe gets `404`, and an existing subscriber's request still shows the pinned behaviour. | Core HTTP |
| REQ-MK2-8 | `GET /fitit/marketplace/features` returns `401` anonymously and otherwise `200` with listing entries honouring `q` and `sort` as in REQ-MK1-5, `422` `{"error": "invalid", ...}` for an unknown `sort`; `GET /fitit/marketplace/features/{id}` returns the detail entry with `code` and `published`, and `404` `{"error": "not_found"}` for an unknown id, a non-integer id and an unpublished feature of another owner. | Core HTTP |
| REQ-MK2-9 | `POST /fitit/marketplace/features` returns `201` with `created` true the first time and `200` with `created` false the next, `404` for a missing hook, and `422` `{"error": "invalid", ...}` for a bad title or a body that is not a JSON object; `DELETE /fitit/marketplace/features/{id}` returns `204` for the owner, `403` `{"error": "forbidden", ...}` for another user and `404` for an unknown id. | Core HTTP |
| REQ-MK2-10 | The routes `subscribe`, `toggle`, `unsubscribe`, `accept-update`, `fork` and `report` return `401` anonymously and the success status and body of the marketplace spec (`200` subscription entry, `200` `{"feature_id", "enabled"}`, `204`, `200` entry, `201` `{"name", "route", "diff"}`, `204`), and map each error of REQ-MK1-7, -8, -10, -11 and -12 to its status with body `{"error", "detail"}` (`{"error": "not_found"}` for 404). | Core HTTP |
| REQ-MK2-11 | `GET /fitit/features` returns `401` anonymously and otherwise `200` with the same list as `marketplace.my_features` for that user: subscriptions first in subscription order, then own hooks by name, each with exactly the keys of its `source`. | Core HTTP |
| REQ-MK2-12 | For every entry of `GET /fitit/features`, the toggle route of its `source` (`own`: `POST /fitit/hooks/{name}/toggle`; `subscribed`: `POST /fitit/marketplace/features/{feature_id}/toggle`) flips its `enabled` in the next `GET /fitit/features` and stops or restarts it in `x-fitit-hooks`, and the delete route of its `source` (`DELETE /fitit/hooks/{name}`; `POST .../{feature_id}/unsubscribe`) removes it from the next list. | Core HTTP |
| REQ-MK2-13 | The acceptance tests of iterations 01 and 02 (`test_hr1.py`, `test_px1.py`, `test_hs2.py`, `test_sf1.py`, `test_demo_flow.py`) pass unchanged; in particular `GET /fitit/hooks` keeps exactly its v2 shape. | Earlier acceptance suites |

### AG3 — Marketplace in the agent

| Id | Requirement | Checked through |
|---|---|---|
| REQ-AG3-1 | A `search_features` call returns one line per matching published feature containing `@<id>`, its title and its route, with ` [mine]` or ` [subscribed]` when true, and `no features found` when nothing matches. | Core WebSocket, `FunctionModel` (tool return in the next request) |
| REQ-AG3-2 | A `publish_hook` call for an existing hook creates the feature for the connected user and sends `feature_changed` `{"feature_id": <id>, "action": "published"}` before `done`; for a missing hook its return starts with `not_found:`, no feature row exists and no `feature_changed` is sent. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG3-3 | A `subscribe_feature` call creates a subscription only for the connected user and sends `feature_changed` `subscribed`; when the dry run fails the return contains `dry_run_failed`, no row is written, no `feature_changed` is sent and the turn still ends with `text` then `done`. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG3-4 | `unsubscribe_feature` removes the subscription and sends `feature_changed` `unsubscribed`; `accept_update` moves the pin to the latest version and sends `feature_changed` `updated`. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG3-5 | `fork_feature` creates the connected user's hook with the feature's pinned code, removes the subscription, and sends `hook_saved` (name, route, diff) then `feature_changed` `forked`. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG3-6 | `unpublish_feature` by the owner sets `published` false and sends `feature_changed` `unpublished`; by another user its return starts with `forbidden:` and nothing changes. | Core WebSocket, `FunctionModel`, SQL |
| REQ-AG3-7 | The instructions of every model request contain, for each of the user's subscriptions, `@<id>` and its title, plus `update available` when one is, or `no subscriptions` when there are none; and they name the tools `search_features`, `subscribe_feature` and `publish_hook`. | Core WebSocket, `FunctionModel` |
| REQ-AG3-8 | Each successful marketplace tool call emits the matching `feature.*` event once, with `user` the connected user. | Core WebSocket, `FunctionModel`, recorded `trace.emit` |
| REQ-AG3-9 | The acceptance tests for AG1, AG2 and CH1 still pass. | `test_ag1.py`, `test_ag2.py`, `test_ch1.py` |

### EV2 — Marketplace eval cases

| Id | Requirement | Checked through |
|---|---|---|
| REQ-EV2-1 | `run_evals.CASES` has 17 cases with unique ids, including every id of the evals spec; the four marketplace cases except `publish_feature` have a callable `setup`; `appointments_sorted`'s turn contains `latest first` and its `check` raises `AssertionError` when `after` equals the demo app's own `GET /api/appointments` body. | `run_evals.CASES`, demo HTTP |
| REQ-EV2-2 | `run_case` on a case whose `setup` saves a hook for the case's user on its route makes that hook apply to the case's `before` request, and after the case no schema starting with `eval_` remains. | `run_evals.run_case`, `FunctionModel`, SQL |
| REQ-EV2-3 | `run_case` on a case whose `setup` raises returns `passed` false with `detail` starting `setup:`, raises nothing and leaves no `eval_` schema. | `run_evals.run_case` |
| REQ-EV2-4 | With a `FunctionModel` that calls `search_features` and then `subscribe_feature` with the id found, `run_case` on `subscribe_hide_dni` returns `passed` true; with one that only answers text it returns `passed` false. | `run_evals.run_case`, `FunctionModel` |

## What is verified, and how

| Surface | Items | Check |
|---|---|---|
| `backend/marketplace.py`, SQL on the test's own schema | MK1 | Imported functions, `psycopg`, recorded `trace.emit` |
| Core HTTP | MK2 | `TestClient` on `main.app`, demo service as the app, one client per signed-in user |
| Core WebSocket | AG3 | `TestClient` WebSocket, `FunctionModel` |
| `evals/run_evals.py` | EV2 | Imported functions, `FunctionModel` |
| Widget | WM1 | The frontend teammate, by hand against the [embed widget spec](../../specs/embed-widget.md#marketplace-panel-and-my-features); no REQ |
| Real model | EV2 (pass rate) | `make eval` by a human in RL2; not a requirement |

## Closed to the tester

The developer's branch and pull request, every file under `backend/`, `demo/` and `evals/` except what the contracts name, and `tests/unit/`.
