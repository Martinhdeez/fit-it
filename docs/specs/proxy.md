# Proxy — v1

The HTTP surface of the Fit-it core. Decisions: [ADR 0002](../adr/0002-reverse-proxy-in-front-of-unmodified-app.md), [ADR 0006](../adr/0006-identify-users-through-the-app-session.md).

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `BASE_URL` | Client app origin | `http://demo:8001` |
| `WORKSPACES` | Hooks folder | `./workspaces` |
| `FITIT_MODEL` | Agent model: any Pydantic AI `provider:model` string ([ADR 0014](../adr/0014-provider-agnostic-model.md)) | `anthropic:claude-opus-5-5` |

Model credentials, set only the one the chosen provider reads: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (plus `OPENAI_BASE_URL` for any OpenAI-compatible endpoint), `GOOGLE_API_KEY` or `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OLLAMA_BASE_URL`. All pass through from the host shell.

The core listens on port `8000`. Paths under `/fitit/` belong to the core; every other path is proxied.

## User resolution

For every request, the core calls `GET {BASE_URL}/api/me` with the request's cookies. A `200` JSON response with a string `id` makes that id the current user. Any other result means **anonymous**.

## Forwarding

For any path not under `/fitit/` and not under `/x/`:
1. The request is sent to `BASE_URL` with the same method, path, query string, body, cookies and headers, except `host`, `content-length`, `accept-encoding` and `connection`.
2. The response status, body and headers are returned, except `content-length`, `content-encoding`, `transfer-encoding` and `connection`. Every `set-cookie` header is kept.

## Applying hooks

When the user is not anonymous, the app's response status is 2xx and its `content-type` is `application/json`:
- every enabled hook of the user whose `ROUTE` matches the request method and path runs, in name order;
- the response body is the last hook's output, serialized as JSON;
- the response has the header `x-fitit-hooks` with the applied hook names, comma-separated in run order. Without matching hooks the header is absent.

Anonymous requests, non-2xx responses and non-JSON responses are returned unchanged.

## Widget injection

When the user is not anonymous and the response `content-type` is `text/html`, the core inserts `<script src="/fitit/embed.js" defer></script>` immediately before the last `</body>`, or appends it if there is no `</body>`.

## Virtual endpoints

`GET /x/<rest>`:
- anonymous: `401` `{"error": "unauthenticated"}`;
- no enabled hook of the user matches: `404` `{"error": "not_found"}`;
- otherwise the matching hooks run with `data = None`, and the result is returned as `200` JSON with `x-fitit-hooks`.

`/x/` requests are never forwarded to the app.

## Errors

If a matching hook raises, the response is `502` with body `{"error": "hook_failed", "hook": "<name>", "detail": "<message>"}`. The app's original body is never returned.

## Core routes

All of them return `401` `{"error": "unauthenticated"}` for anonymous users.

| Route | Response |
|---|---|
| `GET /fitit/embed.js` | The widget script, `content-type: application/javascript`. No authentication needed. |
| `GET /fitit/hooks` | `200` `[{"name": str, "route": str, "enabled": bool, "code": str}]`, sorted by name |
| `POST /fitit/hooks/{name}/toggle` | `200` `{"name": str, "enabled": bool}` with the new state; `404` if the hook does not exist |
| `DELETE /fitit/hooks/{name}` | `204`; `404` if the hook does not exist |
| `WS /fitit/ws` | The agent session ([agent spec](agent.md)) |
