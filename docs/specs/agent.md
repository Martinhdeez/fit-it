# Coding agent — v2

Turns a chat message into a validated hook. Decisions: [ADR 0007](../adr/0007-openapi-as-the-agent-catalog.md), [ADR 0008](../adr/0008-pydantic-ai-for-the-coding-agent.md), [ADR 0018](../adr/0018-hook-versions-and-undo.md), [ADR 0019](../adr/0019-persistent-chat-history.md), [ADR 0020](../adr/0020-hook-time-limit-and-safety-limits.md).

## Changes from v1

Iteration 02. The v1 text is in git history (`git show a76b931:docs/specs/agent.md`).

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | Conversation history is stored per user in Postgres and read at the start of every message, instead of kept per connection ("History"). New export `clear_history(user)`. | No for the protocol | CH1 |
| 2 | `write_hook`'s dry run uses `hooks.dry_run`; it never writes anything for another user id. | No | AG2 |
| 3 | The prompt is Pydantic AI `instructions` and lists the user's current hooks ("Instructions"). | No | AG2 |
| 4 | New tool `undo_hook`. | No (addition) | AG2 |
| 5 | Per-user message rate limit ("Rate limit"); new export `reset_rate_limits()`. | No below the limit | AG2 |
| 6 | The `agent.answer` trace event carries `input_tokens` and `output_tokens`. | No (addition) | AG2 |

## Module contract

`backend/agent.py` exports:
- `Deps`: a dataclass with `user: str` and `api` (the same object hooks get as `ctx.api`).
- `agent`: a Pydantic AI `Agent[Deps, str]` built with the model from `FITIT_MODEL`, read on each turn. It is any Pydantic AI `provider:model` string (`anthropic:`, `openai:`, `google:`, `groq:`, `mistral:`, `ollama:`); the provider's key variable is listed in [proxy Configuration](proxy.md#configuration). If the model cannot be built (unknown provider, missing key), the turn ends with `error` naming the variable to set, then `done`.
- `clear_history(user: str) -> None`: deletes every `messages` row of that user; other users' rows stay.
- `reset_rate_limits() -> None`: forgets every user's counted messages ("Rate limit"). Used by tests.

Tests replace the model with `agent.override(model=FunctionModel(...))` or `TestModel()`. No test needs an API key.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_endpoints` | none | One line per operation of the app's `/openapi.json`: `METHOD path: summary` |
| `get_endpoint` | `path: str` | The JSON body of `GET path` as the current user, serialized and cut to 4000 characters |
| `list_hooks` | none | The user's hooks: name, route, enabled |
| `read_hook` | `name: str` | The hook's source code |
| `write_hook` | `name: str`, `code: str` | See below |
| `undo_hook` | `name: str` | See below |
| `delete_hook` | `name: str` | Confirmation |

`write_hook` runs, in order:
1. the guard from the [hook contract](hook-contract.md);
2. a **dry run** with `hooks.dry_run(user, code, data, api)`: for an app route without `{param}` segments and method `GET`, `data` is the live `GET` response as the current user; for a virtual endpoint `data` is `None`; other routes skip the dry run;
3. saves the hook enabled with `hooks.save_hook`, replacing any hook with the same name (this is how a hook is **edited**: same name, new code, and the diff shows the change);
4. returns `saved` plus a preview of the dry-run output (cut to 2000 characters).

If step 1 or 2 fails, nothing is written to the database, for any user, and the tool raises `ModelRetry` with the reason, so the model can fix the code. After 3 failed attempts in one message the turn ends with an error event.

`undo_hook` calls `hooks.undo_hook(user, name)`. On success it sends `hook_saved` with the hook's name, route and the undo's diff, emits the `hook.restored` trace event, and returns `restored version <n>`. If the hook does not exist it raises `ModelRetry`. If there is no earlier version it returns `nothing to undo: no earlier version` and changes nothing.

## Instructions

The prompt is passed as Pydantic AI `instructions`, so it is sent with every model request and never stored in the history. It contains:
- the hook contract (shape, `ctx`, guard rules, allowed names, fail-closed behaviour);
- the user's current hooks, one line per hook with its name, route and `enabled`/`disabled`, or the words `no hooks yet`, read when the message starts;
- the rules: call `list_endpoints` and `get_endpoint` before writing; change only what the user asked; to change an existing behaviour, `read_hook` and `write_hook` with the **same name** instead of adding a second hook; one hook per request; use `undo_hook` when the user asks to undo or go back; answer in the user's language in at most three sentences.

## History

Storage: the `messages` table of the [storage spec](storage.md). Decision: [ADR 0019](../adr/0019-persistent-chat-history.md).
- At the start of every message, the agent reads the user's 20 most recent rows by `id`, oldest first, and passes their messages, concatenated, as `message_history`.
- When a message ends with a `text` event, its new messages (`run.result.new_messages()`) are stored as one new row. A message that ends with `error` stores nothing.
- `clear_history(user)` and `DELETE /fitit/history` ([proxy spec](proxy.md#core-routes)) remove all of the user's rows; the next message starts with no history, on any connection.

## Rate limit

At most `FITIT_AGENT_MESSAGES_PER_HOUR` messages (default `60`) per user in any sliding hour, counted in memory across all of that user's connections. A message over the limit is answered with `error` (`message` contains `rate limit`) then `done`; the model is not called and no history is stored. Malformed messages do not count.

## WebSocket protocol: `WS /fitit/ws`

The connection is authenticated with the user resolution from the [proxy spec](proxy.md). An anonymous connection is closed with code `4401`.

Client to server:
```json
{"type": "message", "text": "Apply a 15% discount to Friday invoices"}
```

Server to client, in order, for each message:

| Event | Fields | When |
|---|---|---|
| `tool` | `name`, `input` | Before each tool call |
| `hook_saved` | `name`, `route`, `diff` | After a successful `write_hook` or `undo_hook`; `diff` is a unified diff against the previous code (empty previous for a new hook) |
| `hook_rejected` | `name`, `reason` | After a failed `write_hook` attempt |
| `text` | `text` | The agent's final answer |
| `error` | `message` | The turn failed (model error, retries exhausted, rate limit) |
| `done` | none | Always last, once per message |

## Trace events

As in [trace events](trace-events.md). `agent.answer` carries `input_tokens` and `output_tokens` (integers, the run's usage as Pydantic AI reports it).
