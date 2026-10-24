# Coding agent — v1

Turns a chat message into a validated hook. Decisions: [ADR 0007](../adr/0007-openapi-as-the-agent-catalog.md), [ADR 0008](../adr/0008-pydantic-ai-for-the-coding-agent.md).

## Module contract

`backend/agent.py` exports:
- `Deps`: a dataclass with `user: str` and `api` (the same object hooks get as `ctx.api`).
- `agent`: a Pydantic AI `Agent[Deps, str]` built with the model from `FITIT_MODEL`, read on each turn. It is any Pydantic AI `provider:model` string (`anthropic:`, `openai:`, `google:`, `groq:`, `mistral:`, `ollama:`); the provider's key variable is listed in [proxy Configuration](proxy.md#configuration). If the model cannot be built (unknown provider, missing key), the turn ends with `error` naming the variable to set, then `done`.

Tests replace the model with `agent.override(model=FunctionModel(...))` or `TestModel()`. No test needs an API key.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_endpoints` | none | One line per operation of the app's `/openapi.json`: `METHOD path: summary` |
| `get_endpoint` | `path: str` | The JSON body of `GET path` as the current user, serialized and cut to 4000 characters |
| `list_hooks` | none | The user's hooks: name, route, enabled |
| `read_hook` | `name: str` | The hook's source code |
| `write_hook` | `name: str`, `code: str` | See below |
| `delete_hook` | `name: str` | Confirmation |

`write_hook` runs, in order:
1. the guard from the [hook contract](hook-contract.md);
2. a **dry run**: for an app route without `{param}` segments and method `GET`, it calls `run` on the live `GET` response as the current user; for a virtual endpoint it calls `run(None, ctx)`; other routes skip the dry run;
3. saves the hook enabled, replacing any hook with the same name;
4. returns `saved` plus a preview of the dry-run output (cut to 2000 characters).

If step 1 or 2 fails, nothing is saved and the tool raises `ModelRetry` with the reason, so the model can fix the code. After 3 failed attempts the turn ends with an error event.

## System prompt rules

The agent is told the hook contract, to call `list_endpoints` and `get_endpoint` before writing, to change only what the user asked, to keep one hook per request, and to answer in the user's language in at most three sentences.

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
| `hook_saved` | `name`, `route`, `diff` | After a successful `write_hook`; `diff` is a unified diff against the previous version (empty previous for a new hook) |
| `hook_rejected` | `name`, `reason` | After a failed `write_hook` attempt |
| `text` | `text` | The agent's final answer |
| `error` | `message` | The turn failed (model error, retries exhausted) |
| `done` | none | Always last, once per message |

The conversation history is kept for the lifetime of the connection.
