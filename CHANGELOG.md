# Changelog

All notable changes to Fit-it. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.0] — 2026-10-24

### Added

- Iteration 01 (MVP): hook runtime with AST guard (HR1), reverse proxy core (PX1), Pydantic AI coding agent (AG1), demo clinic app and `make check` gate (OP1, DA1), any Pydantic AI model provider, widget hosted as a same-origin iframe, end-to-end demo flow test.
- Iteration 02: Postgres in the stack and the test gate (DB1); hooks stored in Postgres with versions, undo, `dry_run` and per-hook timing (HS2); persistent chat history (CH1); agent edits existing hooks, undo, per-user rate limit and token counts (AG2); hook time limit and output cap (SF1); real-model eval set and `make eval` (EV1).

- Iteration 03: feature marketplace (MK1, MK2): publish a hook as a feature, subscribe pinned to a version with a dry run, accept updates, fork, toggle, unsubscribe, report; subscribed features run first, as the subscriber; agent marketplace tools (AG3) and eval cases (EV2).
- Iteration 04: parallel conversations over `WS /fitit/ws?conversation=<id>` and `GET /fitit/conversations` (CV1); serialized hook saves with version checks and a dry run of the whole route chain (HK1, AG4).
- Embed widget (frontend teammate): chat, My features, marketplace and conversation windows, resizable panel and voice dictation through `POST /fitit/transcribe` (ADR 0016), built into the core image and served at `/fitit/ui/`.
- Traceability (Álvaro): persisted trace journal, redacted OpenTelemetry export to Jaeger and `trace_view.py` timeline (ADR 0026).

### Changed

- Hooks moved from `WORKSPACES/<user>/` files to Postgres (ADR 0017); iteration 01 hook files are not migrated.

### Fixed

- Proxy accepts before closing anonymous WebSockets with 4401.
- Security: the hook guard blocks frame introspection attributes, `str.format`/`format_map`, and `try`/`with`/`async`, closing a sandbox escape and a time-limit bypass.
- Security: Origin check on non-GET `/fitit/*` requests and the WebSocket (403, 4403); transcribe checks size before reading and is rate limited; client `X-Forwarded-*` and hop-by-hop headers dropped; hooked responses are `Cache-Control: private, no-store`; `.env*` kept out of the Docker build context.
- Agent: a client disconnecting mid-turn no longer logs a traceback; the agent re-reads a hook before editing (undo is respected), replies in the user's language, and puts unrelated changes in a new hook.

### Eval

- `make eval` with `openai:gpt-5-mini`: 13/13 passed, tokens in=120773 out=19948 (2026-10-24, before the `appointments_sorted` fix). With the marketplace cases: 16/17; `fork_feature` failed and its prompt was fixed in AG4.

### Not done

- No v0.3.0 release: iteration 03 ships in v0.4.0.
- Known limits: a single long C-level call in a hook is not interrupted and hook memory is not bounded (subprocess sandbox, ADR 0020); traces store personal data in plain text.
