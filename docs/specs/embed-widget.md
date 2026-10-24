# Embed widget — v2

The chat widget a signed-in user sees on every HTML page. Decision: [ADR 0015](../adr/0015-widget-as-static-iframe.md).

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

- Chat over `WS /fitit/ws`; protocol and events unchanged, see the [agent spec](agent.md#websocket-protocol-ws-fititws).
- Hook list, toggle and delete through `GET /fitit/hooks`, `POST /fitit/hooks/{name}/toggle`, `DELETE /fitit/hooks/{name}`, unchanged, see the [proxy spec](proxy.md#core-routes).
- After a `done` that follows at least one `hook_saved`, and after a toggle or delete, it reloads the app with `window.top.location.reload()`.
- If the WebSocket closes with `4401`, it shows a sign-in message instead of the chat.

## Constraints

- No `alert`, `confirm` or `prompt`. No external requests.
- Readable at 360 px width.
