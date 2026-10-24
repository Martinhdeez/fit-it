# 0025. Hook edits under parallel conversations

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

With parallel conversations ([ADR 0024](0024-parallel-conversations.md)) two agent turns of one user can write the same hook at the same moment. Three things go wrong today:

1. `save_hook` numbers the version as `max + 1` and locks only an existing `hooks` row. Two first saves of a new name both pick version `1` and one fails on the `hook_versions` primary key.
2. A turn that read a hook, then writes its edit, silently overwrites what another turn saved in between.
3. The dry run tries the candidate alone. A hook that works alone can break the user's next hook on the same route, which then fails closed on every request ([ADR 0005](0005-fail-closed-hook-execution.md)).

## Decision

**Serialized saves.** `hooks.save_hook` takes a transaction-scoped Postgres advisory lock on `(user, name)` before it reads anything. Saves of the same hook run one after the other; versions stay `1, 2, 3, ...` without gaps or collisions.

**Optimistic check.** `save_hook` gains an optional `expected_version`: the `hooks.version` the caller based its edit on, `0` for "must not exist yet". Under the lock, if the current state differs, it raises `hooks.Conflict` and writes nothing. The agent remembers, per hook name, the version it saw in this turn: from the user's hooks when the message started, from each `read_hook`, and from its own saves and undos. `write_hook` always passes that version. A conflict is a rejection: the model is told to `read_hook` again and retry, and it counts towards the three rejections of a turn. Callers that pass no `expected_version` (forks, tests) keep today's behaviour.

**Dry run on the whole chain.** `hooks.dry_run` gains an optional `name` and `features`. With a name, it runs the user's real chain for the candidate's route, in run order ([ADR 0023](0023-features-run-as-the-subscriber.md)): enabled subscribed features first, then enabled own hooks by name, with the candidate replacing the hook of the same name or inserted at its name's position, on the same live data, as the user. A failure of the candidate is reported as today; a failure of any other hook in that chain also rejects, naming that hook. The agent's `write_hook` and the marketplace's subscribe and accept-update use it.

## Consequences

- Concurrent saves never raise a database error, and an edit is never based on code the agent did not see.
- A hook is accepted only if the user's whole route still works with it, on their own data.
- **Known ceiling (stale chain):** a hook already failing on the route, before the candidate, also rejects the candidate. The route already returns `502` for the user, so the agent reports it instead of piling on code; the user disables or fixes the failing hook first.
- **Known ceiling (time):** a chain dry run runs every hook of the route once, each within its own time limit. A long chain makes `write_hook` slower by that much.
- Undo, toggle and delete stay unchecked: they are explicit user actions on the current state.
- The marketplace fork keeps the candidate-only dry run: forking removes the subscription in the same step, so the chain would contain the code twice.

## Alternatives considered

- **`SERIALIZABLE` transactions with retry:** correct, but every caller needs a retry loop for a case one lock covers.
- **Compare code instead of version numbers:** works too, but sends the whole code twice through every tool call; the version is one integer the core already stores.
- **Last write wins:** simplest, and loses work silently, which is what parallel windows make likely.
- **Dry-run the chain only when the candidate is last:** misses hooks after it in name order, the case that breaks.
