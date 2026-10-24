# Coding agent — v4

Turns a chat message into a validated hook, or finds one already published in the marketplace. Decisions: [ADR 0007](../adr/0007-openapi-as-the-agent-catalog.md), [ADR 0008](../adr/0008-pydantic-ai-for-the-coding-agent.md), [ADR 0018](../adr/0018-hook-versions-and-undo.md), [ADR 0019](../adr/0019-persistent-chat-history.md), [ADR 0020](../adr/0020-hook-time-limit-and-safety-limits.md), [ADR 0022](../adr/0022-feature-marketplace.md), [ADR 0024](../adr/0024-parallel-conversations.md), [ADR 0025](../adr/0025-hook-edits-under-parallel-conversations.md).

## Changes from v3

Iteration 04. The v3 text is in git history (`git show 5c656ce:docs/specs/agent.md`); v2 at `6554dff`, v1 at `a76b931`.

| # | Change | Breaking | Lands in |
|---|---|---|---|
| 1 | `WS /fitit/ws?conversation=<id>`: several conversations per user, each with its own history; no parameter means `default` ("Conversations", "WebSocket protocol"). | No (v3 clients use `default`) | CV1 |
| 2 | History is loaded and stored per `(user, conversation)` ("History"). | No for `default`-only clients | CV1 |
| 3 | One turn at a time per conversation: a message for a conversation busy on another socket gets `error` then `done` ("Conversations"). | No (addition) | CV1 |
| 4 | `clear_history(user, conversation=None)` and new `list_conversations(user)` ("Module contract"). | No (addition) | CV1 |
| 5 | `agent.message`, `agent.tool` and `agent.answer` carry `conversation` ([trace events](trace-events.md#minor-additions-iteration-04)). | No (addition) | CV1 |
| 6 | `write_hook` passes the version the agent last saw to `save_hook`; a conflict is a rejection ("Edits based on a known version"). | No | AG4 |
| 7 | `write_hook`'s dry run runs the candidate inside the user's whole chain for its route; a failure of another hook in it is a rejection naming that hook; the preview is the chain's output (step 2 of `write_hook`). | No | AG4 |
| 8 | Instructions: prefer editing a hook already on the target route over adding one ("Instructions"). | No | AG4 |

Changes in v3 (iteration 03): the seven marketplace tools, their instructions and `feature_changed`.

## Module contract

`backend/agent.py` exports:
- `Deps`: a dataclass with `user: str` and `api` (the same object hooks get as `ctx.api`).
- `agent`: a Pydantic AI `Agent[Deps, str]` built with the model from `FITIT_MODEL`, read on each turn. It is any Pydantic AI `provider:model` string (`anthropic:`, `openai:`, `google:`, `groq:`, `mistral:`, `ollama:`); the provider's key variable is listed in [proxy Configuration](proxy.md#configuration). If the model cannot be built (unknown provider, missing key), the turn ends with `error` naming the variable to set, then `done`.
- `clear_history(user: str, conversation: str | None = None) -> None`: deletes the `messages` rows of that user, only those of `conversation` when given; other users' and other conversations' rows stay.
- `list_conversations(user: str) -> list[dict]`: the user's conversations ("Conversations").
- `handle_ws(websocket, user: str, api, conversation: str = "default")`: runs the agent session on an accepted or accepting socket; `main.py` calls it after authenticating and validating the id.
- `reset_rate_limits() -> None`: forgets every user's counted messages ("Rate limit"). Used by tests.

Tests replace the model with `agent.override(model=FunctionModel(...))` or `TestModel()`. No test needs an API key.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `list_endpoints` | none | One line per operation of the app's `/openapi.json`: `METHOD path: summary` |
| `get_endpoint` | `path: str` | The JSON body of `GET path` as the current user, serialized and cut to 4000 characters |
| `list_hooks` | none | The user's hooks: name, route, enabled |
| `read_hook` | `name: str` | The hook's source code; records the version read ("Edits based on a known version") |
| `write_hook` | `name: str`, `code: str` | See below |
| `undo_hook` | `name: str` | See below |
| `delete_hook` | `name: str` | Confirmation |
| `search_features` | `query: str` | See "Marketplace tools" |
| `publish_hook` | `name: str`, `title: str`, `description: str` | See "Marketplace tools" |
| `unpublish_feature` | `feature_id: int` | See "Marketplace tools" |
| `subscribe_feature` | `feature_id: int` | See "Marketplace tools" |
| `unsubscribe_feature` | `feature_id: int` | See "Marketplace tools" |
| `accept_update` | `feature_id: int` | See "Marketplace tools" |
| `fork_feature` | `feature_id: int`, `name: str` | See "Marketplace tools" |

`write_hook` runs, in order:
1. the guard from the [hook contract](hook-contract.md);
2. a **chain dry run** with `hooks.dry_run(user, code, data, api, name=name, features=marketplace.active(user))` ([hook contract](hook-contract.md#chain-dry-run)): for an app route without `{param}` segments and method `GET`, `data` is the live `GET` response as the current user; for a virtual endpoint `data` is `None`; other routes skip the dry run. The candidate runs in the user's real chain for its route; if another hook or feature of the chain fails, the reason is `<hook> failed: <detail>` with that hook's name (`@<feature_id>` for a feature);
3. saves the hook enabled with `hooks.save_hook(user, name, code, expected_version=<version seen>)`, replacing any hook with the same name (this is how a hook is **edited**: same name, new code, and the diff shows the change). A `hooks.Conflict` is a failure of this step ("Edits based on a known version");
4. returns `saved` plus a preview of the chain dry run's output, the body the user will now receive (cut to 2000 characters).

If any step fails, nothing is written to the database, for any user, and the tool raises `ModelRetry` with the reason, so the model can fix the code. Each failure sends `hook_rejected` and counts as a rejection; after 3 in one message the turn ends with an error event.

### Edits based on a known version

Decision: [ADR 0025](../adr/0025-hook-edits-under-parallel-conversations.md). During one message the agent keeps, per hook name, the version it last saw:
- when the message starts, `hooks.versions(user)` (read with the hooks listed in the instructions); a name not in it has version `0`;
- `read_hook` reads with `hooks.read_hook_version` and records that version;
- a successful `write_hook` records the saved version, `undo_hook` the restored one, `delete_hook` `0`.

`write_hook` passes that version as `expected_version`. If another conversation (or the widget) changed, created or deleted the hook since, the save raises `hooks.Conflict`: the tool sends `hook_rejected` with the conflict's text (starting `conflict:`) and raises `ModelRetry` telling the model to `read_hook` and apply its change again. The recorded version is not updated by a conflict, so writing again without reading conflicts again.

`undo_hook` calls `hooks.undo_hook(user, name)`. On success it sends `hook_saved` with the hook's name, route and the undo's diff, emits the `hook.restored` trace event, and returns `restored version <n>`. If the hook does not exist it raises `ModelRetry`. If there is no earlier version it returns `nothing to undo: no earlier version` and changes nothing.

## Marketplace tools

Each calls the matching function of `backend/marketplace.py` as the connected user, with `api` the user's own `ctx.api` ([marketplace spec](marketplace.md#module-contract)); the rules and trace events are the module's. A `marketplace.Error` is **not** a `ModelRetry`: the tool returns the text `<error>: <detail>` (for example `dry_run_failed: KeyError: 'dni'`) so the model can explain it, and nothing changes.

| Tool | Calls | Returns on success | Also sends |
|---|---|---|---|
| `search_features` | `search(user, query)` | Up to 10 entries, one line each: `@<id> <title> — <route> — <description> (<subscribers> subscribers, v<version>)`, with ` [mine]` or ` [subscribed]` appended when true; or `no features found` | — |
| `publish_hook` | `publish(user, name, title, description)` | `published @<id> version <n>` | `feature_changed` `published` |
| `unpublish_feature` | `unpublish(user, feature_id)` | `unpublished @<id>` | `feature_changed` `unpublished` |
| `subscribe_feature` | `subscribe(user, feature_id, api)` | `subscribed to @<id> version <n>` | `feature_changed` `subscribed` |
| `unsubscribe_feature` | `unsubscribe(user, feature_id)` | `unsubscribed from @<id>` | `feature_changed` `unsubscribed` |
| `accept_update` | `accept_update(user, feature_id, api)` | `@<id> updated to version <n>` | `feature_changed` `updated` |
| `fork_feature` | `fork(user, feature_id, api, name)` | `forked @<id> into <name>` | `hook_saved` (name, route, diff), then `feature_changed` `forked` |

## Instructions

The prompt is passed as Pydantic AI `instructions`, so it is sent with every model request and never stored in the history. It contains:
- the hook contract (shape, `ctx`, guard rules, allowed names, fail-closed behaviour);
- the user's current hooks, one line per hook with its name, route and `enabled`/`disabled`, or the words `no hooks yet`, read when the message starts;
- the user's subscriptions, one line per subscription with `@<id>`, title, route, pinned version, `enabled`/`disabled` and `update available` when true, or the words `no subscriptions`, read when the message starts (`marketplace.subscriptions`);
- the rules: call `list_endpoints` and `get_endpoint` before writing; change only what the user asked; to change an existing behaviour, `read_hook` and `write_hook` with the **same name** instead of adding a second hook; when one of the user's hooks already runs on the same route as the change, `read_hook` it and extend it with `write_hook` under its name, instead of adding a new hook, unless the user asks for a separate one (the instructions contain the words `already runs on the same route`); if `write_hook` is rejected with `conflict:`, `read_hook` again and reapply the change; one hook per request; use `undo_hook` when the user asks to undo or go back; answer in the user's language in at most three sentences;
- the marketplace rules:
  - before writing a new hook, call `search_features`; if a feature does what the user asked, describe it and ask whether to subscribe; call `subscribe_feature` only after the user agrees in a later message;
  - to publish, read the hook, draft a short title and description from its code, show them, and call `publish_hook` only after the user confirms in a later message;
  - only the owner can change a feature; a subscriber who wants it changed can `fork_feature` it and then edit their own copy;
  - a subscriber asking to get the new version of a feature calls `accept_update`.

## History

Storage: the `messages` table of the [storage spec](storage.md). Decision: [ADR 0019](../adr/0019-persistent-chat-history.md).
- At the start of every message, the agent reads the 20 most recent rows of that user **and conversation** by `id`, oldest first, and passes their messages, concatenated, as `message_history`. Other conversations' rows are never sent.
- When a message ends with a `text` event, its new messages (`run.result.new_messages()`) are stored as one new row with the message's `conversation_id`. A message that ends with `error` stores nothing.
- `clear_history(user)` and `DELETE /fitit/history` ([proxy spec](proxy.md#core-routes)) remove all of the user's rows; `clear_history(user, conversation)` and `DELETE /fitit/history?conversation=<id>` only that conversation's. The next message of a cleared conversation starts with no history, on any connection.

## Conversations

Decision: [ADR 0024](../adr/0024-parallel-conversations.md).

- A conversation is identified per user by an id matching `^[A-Za-z0-9_-]{1,64}$`, chosen by the client (the widget uses a UUID). The id `default` is the conversation of clients that send none. A conversation exists once one of its turns is stored.
- **One turn at a time per conversation.** Messages on one socket are handled one after another, in order, as in v3. If a message arrives for a `(user, conversation)` whose turn is still running on another socket, it is answered with `error` (`message` contains `busy`) then `done`: the model is not called, nothing is stored, and it does not count for the rate limit. Turns of different conversations of the same user run in parallel. Known ceiling: the busy set is in memory, per process.
- **Per user, not per conversation:** the rate limit, the user's hooks and subscriptions, and the instructions built from them, read when each message starts.
- `list_conversations(user)` returns one entry per conversation with at least one stored turn, sorted by `last_activity` descending, then `id` ascending:

```json
[{"id": "3f1c…", "title": "Hide the DNI column on the patients page", "last_activity": "2026-10-24T10:00:00+00:00", "turns": 4}]
```

  `title` is the first 60 characters of the user's text in the conversation's oldest stored turn (`""` if that text is not a string); `last_activity` is the ISO 8601 `created_at` of its newest row; `turns` is its number of rows. Exactly these keys. `[]` when the user has none.

## Rate limit

At most `FITIT_AGENT_MESSAGES_PER_HOUR` messages (default `60`) per user in any sliding hour, counted in memory across all of that user's connections and conversations. A message over the limit is answered with `error` (`message` contains `rate limit`) then `done`; the model is not called and no history is stored. Malformed messages and messages refused as busy do not count. Checks run in this order: malformed, busy, rate limit.

## WebSocket protocol: `WS /fitit/ws`

`WS /fitit/ws?conversation=<id>`. The connection is authenticated with the user resolution from the [proxy spec](proxy.md). An anonymous connection is closed with code `4401`. Then, without the `conversation` parameter the conversation is `default`; with a value that does not match `^[A-Za-z0-9_-]{1,64}$` (including an empty one) the connection is accepted and closed with code `4400`. Every message on the socket belongs to that conversation.

Client to server:
```json
{"type": "message", "text": "Apply a 15% discount to Friday invoices"}
```

Server to client, in order, for each message:

| Event | Fields | When |
|---|---|---|
| `tool` | `name`, `input` | Before each tool call |
| `hook_saved` | `name`, `route`, `diff` | After a successful `write_hook`, `undo_hook` or `fork_feature`; `diff` is a unified diff against the previous code (empty previous for a new hook) |
| `feature_changed` | `feature_id` (int), `action` (`published`, `unpublished`, `subscribed`, `unsubscribed`, `updated`, `forked`) | After a successful marketplace tool that changes something |
| `hook_rejected` | `name`, `reason` | After a failed `write_hook` attempt |
| `text` | `text` | The agent's final answer |
| `error` | `message` | The turn failed (model error, retries exhausted, rate limit, conversation busy) |
| `done` | none | Always last, once per message |

## Trace events

As in [trace events](trace-events.md). `agent.answer` carries `input_tokens` and `output_tokens` (integers, the run's usage as Pydantic AI reports it). `agent.message`, `agent.tool` and `agent.answer` carry `conversation`, the message's conversation id.
