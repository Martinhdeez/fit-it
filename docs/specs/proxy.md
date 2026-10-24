# Proxy — v4

The HTTP surface of the Fit-it core. Decisions: [ADR 0002](../adr/0002-reverse-proxy-in-front-of-unmodified-app.md), [ADR 0006](../adr/0006-identify-users-through-the-app-session.md), [ADR 0017](../adr/0017-postgres-as-core-storage.md), [ADR 0020](../adr/0020-hook-time-limit-and-safety-limits.md), [ADR 0022](../adr/0022-feature-marketplace.md), [ADR 0023](../adr/0023-features-run-as-the-subscriber.md), [ADR 0024](../adr/0024-parallel-conversations.md).

## Changes from v3

Iteration 04. The v3 text is in git history (`git show 5c656ce:docs/specs/proxy.md`); v2 at `6554dff`, v1 at `a76b931`.

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | `WS /fitit/ws` takes an optional `conversation` query parameter; an invalid one closes the socket with `4400` ("Core routes"). | No (absent means `default`) | CV1 |
| 2 | New route `GET /fitit/conversations` ("Core routes"). | No (addition) | CV1 |
| 3 | `DELETE /fitit/history` takes an optional `conversation` query parameter; without it, all of the user's conversations are cleared, as in v3 ("Core routes"). | No | CV1 |
| 4 | New route `POST /fitit/transcribe`. The core sends the audio to OpenAI (`gpt-4o-mini-transcribe`) with `OPENAI_API_KEY`. | No (addition) | widget |
| 5 | Hardening: cross-origin non-GET `/fitit/*` is `403` and a cross-origin `/fitit/ws` closes with `4403` ("Origin check"); more request headers dropped and `x-forwarded-for` set ("Forwarding"); hooked responses carry `cache-control: private, no-store`; `/fitit/transcribe` caps the streamed body and shares the agent rate limit. | No (same-origin and non-browser clients unaffected) | hardening |
| 6 | New routes `GET /fitit/trace/events` and `GET /traceability` ("Core routes"); new variable `FITIT_TRACE_ADMINS` ("Configuration"). `/traceability` is reserved by the core and never proxied. | No (addition) | traceability |

Changes in v3 (iteration 03): subscribed features in the chain, `GET /fitit/features`, `/fitit/marketplace/...`, `DELETE /fitit/hooks/{name}` unpublishes.

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
| `FITIT_TRACE_ADMINS` | Comma-separated user ids that see every user's trace events in `GET /fitit/trace/events`. Demo app: e.g. `gerente`; the ERPNext overlay sets `Administrator`. | empty (each user sees only their own) |

Model credentials, set only the one the chosen provider reads: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` (plus `OPENAI_BASE_URL` for any OpenAI-compatible endpoint), `GOOGLE_API_KEY` or `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OLLAMA_BASE_URL`.

Every variable is read when it is used, never once at import. All pass through from the host shell in `compose.yaml`, except `DATABASE_URL`, which compose builds from `POSTGRES_PASSWORD`.

The core listens on port `8000` and runs `db.init()` ([storage spec](storage.md)) at startup. Paths under `/fitit/` belong to the core; every other path is proxied.

## User resolution

For every request, the core calls `GET {BASE_URL}/api/me` with the request's cookies. A `200` JSON response with a string `id` makes that id the current user. Any other result means **anonymous**.

## Forwarding

For any path not under `/fitit/` and not under `/x/`:
1. The request is sent to `BASE_URL` with the same method, path, query string, body, cookies and headers, except `host`, `content-length`, `accept-encoding`, `connection`, `x-forwarded-for`, `x-forwarded-host`, `x-forwarded-proto`, `forwarded`, `transfer-encoding`, `te` and `upgrade`. The core adds `x-forwarded-for` with the client address it sees.
2. The response status, body and headers are returned, except `content-length`, `content-encoding`, `transfer-encoding` and `connection`. Every `set-cookie` header is kept.

## Applying hooks

When the user is not anonymous, the app's response status is 2xx and its `content-type` is `application/json`:
- first the user's enabled marketplace subscriptions whose pinned code's `ROUTE` matches run, in subscription order, as the user ([ADR 0023](../adr/0023-features-run-as-the-subscriber.md)): `hooks.run_hooks(..., features=marketplace.active(user))` ([hook contract](hook-contract.md#features));
- then every enabled hook of the user whose `ROUTE` matches the request method and path runs, in name order;
- the response body is the last output, serialized as JSON, with `cache-control: private, no-store`;
- the response has the header `x-fitit-hooks` with the applied names, comma-separated in run order; a subscribed feature appears as `@<feature_id>` (for example `@3,@5,friday_discount`). Without matching hooks or features the header is absent;
- each applied hook or feature emits one `hook.applied` event with its own `route`, `ms` and `logs`, taken from the entries `run_hooks` returns ([hook contract](hook-contract.md#module-contract)); for a feature `hook` is `@<feature_id>`.

Anonymous requests, non-2xx responses and non-JSON responses are returned unchanged.

## Widget injection

When the user is not anonymous and the response `content-type` is `text/html`, the core inserts `<script src="/fitit/embed.js" defer></script>` immediately before the last `</body>`, or appends it if there is no `</body>`.

## Virtual endpoints

`GET /x/<rest>`:
- anonymous: `401` `{"error": "unauthenticated"}`;
- no enabled hook and no enabled subscription of the user matches: `404` `{"error": "not_found"}`;
- otherwise the matching features and hooks run with `data = None`, in the order of "Applying hooks", and the result is returned as `200` JSON with `x-fitit-hooks`.

`/x/` requests are never forwarded to the app.

## Errors

If a matching hook or subscribed feature raises, fails the guard when loaded, or breaches a limit (time, output size), the response is `502` with body `{"error": "hook_failed", "hook": "<name>", "detail": "<message>"}`, where `<name>` is `@<feature_id>` for a feature. The app's original body is never returned. The `hook.failed` event carries the same `hook` and the `route` of the failing code (`null` if it does not pass the guard).

## Origin check

A non-GET request under `/fitit/` whose `Origin` header is present and whose host[:port] differs from the request's `Host` is `403` `{"error": "forbidden", "detail": "cross-origin request"}`. The same check closes `WS /fitit/ws` with `4403`. A missing `Origin` (non-browser clients) passes. The scheme is not compared, so TLS terminated in front of the core needs no extra setup. The widget is served from the same origin, so it passes.

## Core routes

All of them return `401` `{"error": "unauthenticated"}` for anonymous users, except the static ones (`/fitit/embed.js`, `/fitit/ui/*`, `/traceability`).

| Route | Response |
|---|---|
| `GET /fitit/embed.js` | The widget loader ([embed widget spec](embed-widget.md)), `content-type: application/javascript`. No authentication needed. |
| `GET /fitit/ui/*` | The widget UI static export from `backend/static/ui/`, `index.html` for directories. No authentication needed. `404` `{"error": "not_found"}` if not built. |
| `GET /traceability` (and `/traceability/`) | The traceability page from the widget export (`backend/static/ui/traceability/index.html` or `traceability.html`), `content-type: text/html`. Reserved by the core, never proxied. No authentication needed (the events API enforces it). `404` `{"error": "not_found"}` if not built. |
| `GET /fitit/trace/events?after=<int>&limit=<int>` | Trace journal records with `seq > after` ([trace events spec](trace-events.md)), `after` default `0`, `limit` default `500`, at most `1000`. `200` `{"events": [record as stored], "next": int, "admin": bool}`. Users in `FITIT_TRACE_ADMINS` get every record (`admin: true`); others only records whose `user` is theirs. `next` is the `seq` of the last record scanned, filtered or not (or `after` if none), so polling with `after=next` always advances. A non-integer or out-of-range `after`/`limit` is `422` `{"error": "invalid", "detail": str}`. |
| `GET /fitit/hooks` | `200` `[{"name": str, "route": str, "enabled": bool, "code": str}]`, sorted by name |
| `POST /fitit/hooks/{name}/toggle` | `200` `{"name": str, "enabled": bool}` with the new state; `404` if the hook does not exist |
| `DELETE /fitit/hooks/{name}` | `204`; `404` if the hook does not exist. A feature published from this hook becomes unpublished ([marketplace spec](marketplace.md#rules), rule 3). |
| `POST /fitit/hooks/{name}/undo` | Calls `hooks.undo_hook`. `200` `{"name": str, "version": int, "diff": str}` with the restored version; `404` `{"error": "not_found"}` if the hook does not exist; `409` `{"error": "undo_failed", "detail": str}` if there is nothing to restore. On `200` it emits `hook.restored`. |
| `DELETE /fitit/history` | Without a query string, calls `agent.clear_history(user)`: every conversation of the user is gone for every connection ([agent spec](agent.md#history)). With `?conversation=<id>`, calls `agent.clear_history(user, id)`: only that conversation. `204`, also for an id with no rows. An id not matching `^[A-Za-z0-9_-]{1,64}$` (including an empty one) is `422` `{"error": "invalid", "detail": str}` and deletes nothing. |
| `GET /fitit/conversations` | `200` `agent.list_conversations(user)`: `[{"id": str, "title": str, "last_activity": str, "turns": int}]`, latest activity first ([agent spec](agent.md#conversations)) |
| `GET /fitit/conversations/{id}/messages` | `200` `agent.conversation_messages(user, id)`: `[{"role": "user" \| "assistant", "text": str}]`, oldest first: the stored user prompts and final text answers, without system parts, tool calls or tool returns. `[]` for an id with no rows; other users' rows are never returned. An id not matching `^[A-Za-z0-9_-]{1,64}$` is `422` `{"error": "invalid", "detail": str}`. The widget calls it to show a conversation again after a page load or from the history list. |
| `GET /fitit/features` | "My features": the user's subscriptions and own hooks in run order, with `source` `subscribed` or `own` ([marketplace spec](marketplace.md#shapes)) |
| `/fitit/marketplace/...` | Listing, detail, publish, unpublish, subscribe, toggle, unsubscribe, accept update, fork, report ([marketplace spec](marketplace.md#http-routes)) |
| `WS /fitit/ws?conversation=<id>` | The agent session for one conversation ([agent spec](agent.md#websocket-protocol-ws-fititws)). Anonymous: closed with `4401`. Parameter absent: conversation `default`. Present but not matching `^[A-Za-z0-9_-]{1,64}$`: closed with `4400`. |
| `POST /fitit/transcribe` | Body is one audio clip (at most 10 MB) with an audio `content-type`. Optional query `language` is a BCP 47 tag; the core keeps the two-letter primary subtag. `200` `{"text": str}`. `400` `{"error": "empty_audio"}` when the body is empty. `413` `{"error": "audio_too_large"}` over 10 MB, by `Content-Length` before reading or while streaming the body. `429` `{"error": "rate_limited"}` when the user is over `FITIT_AGENT_MESSAGES_PER_HOUR` (each clip counts against the agent's budget). `503` `{"error": "transcription_unavailable"}` when `OPENAI_API_KEY` is unset. `502` `{"error": "transcription_failed"}` when OpenAI fails. The key never leaves the core. |
