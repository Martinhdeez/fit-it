# 0018. Hook versions and undo

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

`save_hook` replaces a hook in place; the previous code survives only in the diff sent to the widget. A user who asks the agent to tweak a hook ("make it 20% instead") cannot go back when the tweak is wrong.

## Decision

Every successful `save_hook` appends the saved code to a `hook_versions` table, numbered `1, 2, 3, ...` per `(user_id, name)`, and records in `hooks.version` which version is current.

`undo_hook(user, name)` restores the version just before the current one: it copies that version's code into `hooks`, sets `hooks.version` to it, and writes no version row. Repeated undos walk back one version each; undo at version 1 fails. The next `save_hook` takes `max(version) + 1`, so no version is ever overwritten.

Undo is reachable from the agent (`undo_hook` tool) and from the widget (`POST /fitit/hooks/{name}/undo`).

## Consequences

- Full history per hook, at the cost of one row per save.
- Toggling creates no version (the code does not change). Deleting a hook keeps its versions; recreating the name continues the numbering. Restoring a deleted hook is not offered yet.
- The restored code passes the guard again before it becomes current, so a guard tightened since then still applies.
- No redo: after an undo, saving again is the way forward.

## Alternatives considered

- **Undo as a new save of the previous code:** simpler, but a second undo bounces back to the code just undone.
- **Event log of every hook operation (save, toggle, delete):** a full audit trail is the trace workstream's job ([ADR 0012](0012-trace-seam-for-observability.md)).
