# Embed widget — v3

The chat widget a signed-in user sees on every HTML page. Decisions: [ADR 0015](../adr/0015-widget-as-static-iframe.md), [ADR 0022](../adr/0022-feature-marketplace.md). Built by the frontend teammate, not by agents.

## Changes from v2

Iteration 03. The v2 text is in git history (`git show 6554dff:docs/specs/embed-widget.md`).

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | "My features" replaces the hook list: one list of own hooks and subscribed features, from `GET /fitit/features` ("My features"). | No (the old routes stay) | WM1 |
| 2 | A marketplace panel: search, detail, subscribe, fork, report, publish, unpublish ("Marketplace panel"). | No (addition) | WM1 |
| 3 | The app also reloads after a `done` that follows a `feature_changed` event. | No | WM1 |

Delivery and the sizing contract are unchanged.

## Delivery

1. The core injects `<script src="/fitit/embed.js" defer></script>` into HTML pages ([proxy spec](proxy.md#widget-injection)).
2. `embed.js` is a plain-JS loader, no build step. It adds one `<iframe src="/fitit/ui/" title="Fit-it">`, fixed bottom-right, no border, transparent background, 64 x 64 px, top z-index. A second run of the script does nothing.
3. `/fitit/ui/` serves the widget UI: a Next.js static export (`output: "export"`, `basePath: "/fitit/ui"`) built into `backend/static/ui/`. Same origin as the app, so the iframe sends the app's cookies. If it is not built, `/fitit/ui/*` returns `404` `{"error": "not_found"}`.

## Sizing contract

The iframe posts to its parent (`window.parent.postMessage(msg, location.origin)`):

| Message | Loader resizes the iframe to |
|---|---|
| `{"fitit": "open"}` | `min(420px, 100vw - 32px)` x `min(640px, 100vh - 32px)` |
| `{"fitit": "close"}` | 64 x 64 px |

The loader ignores messages whose `source` is not its iframe or whose origin is not the page's origin.

## Widget UI behaviour

- Chat over `WS /fitit/ws`; protocol and events in the [agent spec](agent.md#websocket-protocol-ws-fititws).
- After a `done` that follows at least one `hook_saved` or `feature_changed`, and after any action in "My features" or the marketplace panel that changes what runs (toggle, delete, unsubscribe, subscribe, accept update, fork), it reloads the app with `window.top.location.reload()`.
- If the WebSocket closes with `4401`, or any route returns `401`, it shows a sign-in message instead of the chat.

## Marketplace panel and My features

Guidance for the frontend teammate. The HTTP contract is the [marketplace spec](marketplace.md#http-routes): exact routes, shapes and status codes. Every error body is `{"error": str, "detail"?: str}`; show `detail` (or a plain sentence for `error` when there is no detail) inline, next to the control that failed, never in a dialog.

Three views, for example as tabs: **Chat**, **My features**, **Marketplace**.

### My features

The one place where a user turns on, turns off and deletes everything that runs for them.

- Source: `GET /fitit/features`. Show the entries in the order received: that is the order they run in. Reload the list after every action.
- Each row: name (`title` for subscribed, `name` for own), `route`, an on/off switch, a delete button, and a badge: **Subscribed** (with `owner` and `v<version>`) or **Mine**; for an own hook with `published` true, a **Published v<published_version>** badge.

| Action | `source: "own"` | `source: "subscribed"` |
|---|---|---|
| On / off | `POST /fitit/hooks/{name}/toggle` | `POST /fitit/marketplace/features/{feature_id}/toggle` |
| Delete | `DELETE /fitit/hooks/{name}` | `POST /fitit/marketplace/features/{feature_id}/unsubscribe` |
| Undo | `POST /fitit/hooks/{name}/undo` (optional button; `409` means nothing to undo) | — |
| Update | — | When `update_available`: button "Update to v<latest_version>" → `POST .../{feature_id}/accept-update`; on `422 dry_run_failed` show the reason and keep the current version |
| Publish | Button "Publish" (or "Publish again" when `feature_id` is set) opening a small form: title (1–80), description (1–500) → `POST /fitit/marketplace/features` `{"hook": name, "title", "description"}`; prefill from the feature's detail when `feature_id` is set | — |
| Unpublish | When `published`: "Unpublish" → `DELETE /fitit/marketplace/features/{feature_id}` | — |
| Fork | — | "Make a copy I can edit" → `POST .../{feature_id}/fork` `{}`; on `409 name_taken` ask for another name in an inline field and retry with `{"name": ...}` |

- Deleting a published own hook unpublishes its feature; the delete button's inline confirmation says so ("Subscribers keep their copy"). Inline confirmation is a second click on the same button, not `confirm()`.
- A subscribed row whose `published` is false shows "No longer listed" but works normally.

### Marketplace

- Search box → `GET /fitit/marketplace/features?q=<text>&sort=<sort>`, debounced about 300 ms; a sort switch **Most used** (`subscribers`) / **Newest** (`newest`). Empty result: "Nothing yet. Ask the chat to build it, then publish it."
- Each card: `title`, `description`, `route`, `owner`, `v<version>`, `<subscribers> users`. Badges **Mine** (`mine`) and **Subscribed** (`subscribed`).
- Opening a card → `GET /fitit/marketplace/features/{id}`: the same fields plus the `code`, read-only and monospaced, so the user can see what will run on their data.
- Buttons on the detail, hidden when `mine`:
  - **Subscribe** (when not `subscribed`) → `POST .../{id}/subscribe`. While waiting show "Trying it on your data…". On `422 dry_run_failed` show "It does not work with your data:" and the `detail`.
  - **Make a copy** → `POST .../{id}/fork`, as in My features.
  - **Report** → an inline textarea (1–500 characters) → `POST .../{id}/report` `{"reason"}`; on `204` show "Thanks, reported."
- When `mine`: **Unpublish** instead of the buttons above.
- Never show who subscribed; the API does not return it.

## Constraints

- No `alert`, `confirm` or `prompt`. No external requests.
- Readable at 360 px width.
