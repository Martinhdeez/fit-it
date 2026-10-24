# Changelog

All notable changes to Fit-it. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased] — v0.4.0

### Added

- Iteration 01 (MVP): hook runtime with AST guard (HR1), reverse proxy core (PX1), Pydantic AI coding agent (AG1), demo clinic app and `make check` gate (OP1, DA1), any Pydantic AI model provider, widget hosted as a same-origin iframe, end-to-end demo flow test.
- Iteration 02: Postgres in the stack and the test gate (DB1); hooks stored in Postgres with versions, undo, `dry_run` and per-hook timing (HS2); persistent chat history (CH1); agent edits existing hooks, undo, per-user rate limit and token counts (AG2); hook time limit and output cap (SF1); real-model eval set and `make eval` (EV1).

### Changed

- Hooks moved from `WORKSPACES/<user>/` files to Postgres (ADR 0017); iteration 01 hook files are not migrated.

### Fixed

- Proxy accepts before closing anonymous WebSockets with 4401.

### Eval

- `make eval` with `openai:gpt-5-mini`: 13/13 passed, tokens in=120773 out=19948 (2026-10-24, before the `appointments_sorted` fix).

### Not done

- EW1 (embed widget) and TR1 (trace sink) carry over to the next iteration.
