# 0019. Persistent chat history per user

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

The agent's conversation lives in memory for one WebSocket. A page reload, which the widget triggers after every saved hook, starts the conversation over, so "make that discount 20% instead" loses its referent.

## Decision

Each completed turn (one user message and everything the model produced for it) is stored as one row of the `messages` table: the turn's new Pydantic AI messages, serialized with `ModelMessagesTypeAdapter` into a `jsonb` array.

At the start of **every** message, the agent loads the user's 20 most recent turns, oldest first, as `message_history`. Nothing is cached per connection, so reconnects, restarts, several tabs and a cleared history all behave the same. Only turns that end with a `text` answer are stored. `DELETE /fitit/history` removes all of a user's turns.

The prompt is passed as Pydantic AI `instructions` (sent with every request, never stored), so stored turns hold only the conversation and a prompt change applies to old conversations too.

## Consequences

- The conversation survives reloads and restarts, and the user can wipe it.
- History holds whatever the tools returned, including samples of client data (`get_endpoint` output, up to 4000 characters each). It is the same user's data, in the core's database, and cleared with the user's history. Operators must treat the core database as holding client data.
- Cutting at turn boundaries keeps tool calls and their results paired, which Pydantic AI requires. **Known ceiling:** 20 long turns can still be many tokens; summarizing old turns is the upgrade path.
- Failed turns are not remembered; the user repeats the request.

## Alternatives considered

- **One row per model message:** cutting by message count can split a tool call from its result.
- **Load once per connection:** a cleared history or a second tab would see stale state.
- **Keep history in the browser and send it with each message:** the client would control the model's context and could inject fake tool results.
