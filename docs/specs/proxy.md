# Proxy — v2

The HTTP surface of the Fit-it core. Decisions: [ADR 0002](../adr/0002-reverse-proxy-in-front-of-unmodified-app.md), [ADR 0006](../adr/0006-identify-users-through-the-app-session.md), [ADR 0017](../adr/0017-postgres-as-core-storage.md), [ADR 0020](../adr/0020-hook-time-limit-and-safety-limits.md).

## Changes from v1

Iteration 02. The v1 text is in git history (`git show a76b931:docs/specs/proxy.md`).

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | `DATABASE_URL` replaces `WORKSPACES`; the core applies the storage schema at startup. | Yes (configuration) | DB1 |
| 2 | One `hook.applied` event per hook with that hook's own `ms` and `logs` (v1 sent the whole chain's values on every event). | No (now matches the trace spec) | HS2 |
| 3 | New route `POST /fitit/hooks/{name}/undo`. | No (addition) | HS2 |
| 4 | New route `DELETE /fitit/history`. | No (addition) | AG2 |
| 5 | New variables `FITIT_HOOK_TIMEOUT_MS`, `FITIT_HOOK_MAX_BYTES`, `FITIT_AGENT_MESSAGES_PER_HOUR`; a breached hook limit fails closed like a raising hook. | No | SF1, AG2 |

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `BASE_URL` | Client app origin | `http://demo:8001` |
| `DATABASE_URL` | The core's Postgres ([storage spec](storage.md)) | `postgresql://fitit:fitit@postgres:5432/fitit` |
| `POSTGRES_PASSWORD` | Compose only: password of the `postgres` service, also put into `DATABASE_URL` | `fitit` (development only) |
| `FITIT_MODEL` | Agent model: any Pydantic AI `provider:model` string ([ADR 0014](../adr/0014-provider-agnostic-model.md)) | `anthropic:claude-opus-5-5` |
| `FITIT_HOOK_TIMEOUT_MS` | Time limit of one hook run ([hook contract](hook-contract.md#limits)) | `2000` |
| `FITIT_HOOK_MAX_BYTES` | Maximum size of one hook's output, as UTF-8 JSON | `1000000` |
| `FITIT_AGENT_MESSAGES_PER_HOUR` | Agent messages per user per sliding hour ([agent spec](agent.md#rate-limit)) | `60` |

Model credentials, set only the one the chosen provider reads: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (plus `OPENAI_BASE_URL` for any OpenAI-compatible endpoint), `GOOGLE_API_KEY` or `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OLLAMA_BASE_URL`.

Every variable is read when it is used, never once at import. All pass through from the host shell in `compose.yaml`, except `DATABASE_URL`, which compose builds from `POSTGRES_PASSWORD`.

The core listens on port `8000` and runs `db.init()` ([storage spec](storage.md)) at startup. Paths under `/fitit/` belong to the core; every other path is proxied.

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
- the response has the header `x-fitit-hooks` with the applied hook names, comma-separated in run order. Without matching hooks the header is absent;
- each applied hook emits one `hook.applied` event with its own `route`, `ms` and `logs`, taken from the entries `run_hooks` returns ([hook contract](hook-contract.md#module-contract)).

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

If a matching hook raises or breaches a limit (time, output size), the response is `502` with body `{"error": "hook_failed", "hook": "<name>", "detail": "<message>"}`. The app's original body is never returned.

## Core routes

All of them return `401` `{"error": "unauthenticated"}` for anonymous users, except the two static ones.

| Route | Response |
|---|---|
| `GET /fitit/embed.js` | The widget loader ([embed widget spec](embed-widget.md)), `content-type: application/javascript`. No authentication needed. |
| `GET /fitit/ui/*` | The widget UI static export from `backend/static/ui/`, `index.html` for directories. No authentication needed. `404` `{"error": "not_found"}` if not built. |
| `GET /fitit/hooks` | `200` `[{"name": str, "route": str, "enabled": bool, "code": str}]`, sorted by name |
| `POST /fitit/hooks/{name}/toggle` | `200` `{"name": str, "enabled": bool}` with the new state; `404` if the hook does not exist |
| `DELETE /fitit/hooks/{name}` | `204`; `404` if the hook does not exist |
| `POST /fitit/hooks/{name}/undo` | Calls `hooks.undo_hook`. `200` `{"name": str, "version": int, "diff": str}` with the restored version; `404` `{"error": "not_found"}` if the hook does not exist; `409` `{"error": "undo_failed", "detail": str}` if there is nothing to restore. On `200` it emits `hook.restored`. |
| `DELETE /fitit/history` | Calls `agent.clear_history(user)`: the user's chat history is gone for every connection ([agent spec](agent.md#history)). `204`. |
| `WS /fitit/ws` | The agent session ([agent spec](agent.md)) |
