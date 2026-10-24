# 0024. Parallel conversations per user

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

Each user has one chat history ([ADR 0019](0019-persistent-chat-history.md)). The widget is getting several chat windows, so a user can ask for a report in one window while a discount is being written in another. With one history, the two windows would read and append to the same turns, and each model call would see the other window's half-finished topic.

## Decision

A user has any number of **conversations**, each with its own history. The widget names them.

- **Id.** `WS /fitit/ws?conversation=<id>`. The widget creates the id (a UUID); the core accepts any id matching `^[A-Za-z0-9_-]{1,64}$` and closes the socket with code `4400` otherwise. Without the parameter the conversation is `default`, so today's clients keep working unchanged.
- **Storage.** `messages` gains `conversation_id text NOT NULL DEFAULT 'default'`, added with `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`. Existing rows belong to `default`. At the start of every message the agent loads the last 20 turns of that `(user, conversation)`.
- **One turn at a time per conversation.** Messages on one socket are handled in order, as today. A message for a conversation whose turn is still running on another socket is **rejected** with `error` (`conversation busy`) then `done`; the model is not called and the message does not count for the rate limit. Different conversations of one user run in parallel.
- **Shared per user.** The rate limit, the user's hooks and subscriptions, and the instructions built from them stay per user. Each message reads them when it starts, as today.
- **Routes.** `GET /fitit/conversations` lists the user's conversations with a title (the first user message, cut to 60 characters), last activity and turn count, newest activity first. `DELETE /fitit/history?conversation=<id>` clears one; without the parameter it clears all of the user's conversations, as today.

## Consequences

- Several windows work at once without mixing their contexts. A reload keeps each window's conversation as long as the widget keeps its id.
- A conversation exists once it has one stored turn; there is no create call and no stored title. The title cannot be renamed.
- Parallel conversations can edit the same hook at the same moment. That is handled by [ADR 0025](0025-hook-edits-under-parallel-conversations.md).
- **Known ceiling:** the busy set is in memory and per process, like the rate limit ([ADR 0020](0020-hook-time-limit-and-safety-limits.md)). Two core processes could run two turns of one conversation at once; move both to Postgres when the core scales out.

## Alternatives considered

- **Queue the second message instead of rejecting it:** friendlier, but needs a per-conversation queue and a way to show a waiting message; two windows on one conversation is an edge case the widget avoids by design.
- **A `conversations` table with titles:** renaming and empty conversations, at the cost of a second table and a create route; nothing needs them yet.
- **Server-generated ids:** one more round trip before the first message; a client UUID cannot collide with anyone else's, because ids are scoped per user.
