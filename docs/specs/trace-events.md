# Trace events — v1

The only interface between the core and the traceability workstream. Decision: [ADR 0012](../adr/0012-trace-seam-for-observability.md).

## Interface

```python
# backend/trace.py — owned by the traceability workstream
def emit(kind: str, **fields) -> None: ...
```

- The core calls `emit` and nothing else from `trace`.
- `emit` must never raise and must return quickly; slow work happens elsewhere.
- Default implementation: print one JSON line `{"ts": <unix seconds>, "kind": ..., **fields}` to stdout.

## Events

Every event carries `user` (string, or `null` if anonymous).

| Kind | Fields | Emitted when |
|---|---|---|
| `request.proxied` | `user`, `method`, `path`, `status`, `ms`, `hooks` (list of names) | Every proxied or virtual request, after the response is built |
| `hook.applied` | `user`, `hook`, `route`, `ms`, `logs` (list of strings) | Each hook that ran without error |
| `hook.failed` | `user`, `hook`, `route`, `error` | A hook raised; the request failed closed |
| `agent.message` | `user`, `text` | A chat message arrives |
| `agent.tool` | `user`, `tool`, `input` | Before each tool call |
| `hook.saved` | `user`, `hook`, `route`, `diff` | `write_hook` saved a hook |
| `hook.rejected` | `user`, `hook`, `reason` | `write_hook` rejected a hook |
| `hook.toggled` | `user`, `hook`, `enabled` | Through `POST /fitit/hooks/{name}/toggle` |
| `hook.deleted` | `user`, `hook` | Through `DELETE /fitit/hooks/{name}` or the `delete_hook` tool |
| `agent.answer` | `user`, `text` | The agent's final answer for a message |

## Minor additions (iteration 02)

Additive; nothing in v1 changes. To be confirmed by the traceability owner before the items that emit them merge.

| Kind | Fields | Emitted when | Item |
|---|---|---|---|
| `hook.restored` (new) | `user`, `hook`, `version` | `undo_hook` restored a version, through `POST /fitit/hooks/{name}/undo` or the agent's `undo_hook` tool | HS2, AG2 |
| `agent.answer` (new fields) | `input_tokens`, `output_tokens` (integers) | As before | AG2 |

From iteration 02, `hook.applied` carries the `ms` and `logs` of that hook alone, as this table always said; v1 of the core sent the chain's totals.

## Minor additions (iteration 03)

Additive; nothing above changes, so this stays v1 with a minor note (spec versioning rule in the [specs README](README.md)). **To be confirmed by the traceability owner (Álvaro)** before MK1 merges. Decisions: [ADR 0022](../adr/0022-feature-marketplace.md), [ADR 0023](../adr/0023-features-run-as-the-subscriber.md).

All `feature.*` events are emitted by `backend/marketplace.py` after the change is committed, whether it came from an HTTP route or an agent tool ([marketplace spec](marketplace.md#module-contract)). `user` is the acting user. `feature_id` is an integer. None of them ever carries a subscriber's id other than the acting user's.

| Kind | Fields | Emitted when | Item |
|---|---|---|---|
| `feature.published` (new) | `user`, `feature_id`, `hook`, `version`, `created` (bool) | A hook was published, first time or again (`version` is the feature's latest, new or unchanged) | MK1 |
| `feature.unpublished` (new) | `user`, `feature_id` | The owner unpublished the feature. Deleting the hook unpublishes too, but emits only `hook.deleted`. | MK1 |
| `feature.subscribed` (new) | `user`, `feature_id`, `version` | A subscription was created after a passing dry run | MK1 |
| `feature.toggled` (new) | `user`, `feature_id`, `enabled` | A subscription was turned on or off | MK1 |
| `feature.unsubscribed` (new) | `user`, `feature_id` | A subscription was removed (not by a fork) | MK1 |
| `feature.updated` (new) | `user`, `feature_id`, `version` | A subscriber accepted an update; `version` is the new pin | MK1 |
| `feature.forked` (new) | `user`, `feature_id`, `version`, `hook` | A feature's code was copied into the user's hook `hook`; preceded by `hook.saved` for that hook | MK1 |
| `feature.reported` (new) | `user`, `feature_id`, `reason` | A user reported a feature | MK1 |
| `hook.applied`, `hook.failed`, `request.proxied` (new values) | `hook` (or each entry of `hooks`) may be `@<feature_id>` | A subscribed feature ran or failed on the subscriber's request; `user` is the subscriber | MK2 |

A refused subscribe, update or fork (failed dry run) emits nothing new: it changed nothing.

## Minor additions (iteration 04)

Additive; nothing above changes. **To be confirmed by the traceability owner (Álvaro)** before CV1 merges. Decision: [ADR 0024](../adr/0024-parallel-conversations.md).

| Kind | Fields | Emitted when | Item |
|---|---|---|---|
| `agent.message`, `agent.tool`, `agent.answer` (new field) | `conversation` (string, the conversation id; `default` for clients that send none) | As before | CV1 |
| `hook.rejected` (new reasons) | `reason` may start with `conflict:` (the hook changed since the agent read it) or name another hook of the chain (`<hook> failed: ...`) | As before | AG4 |

A message refused as busy emits `agent.message` and nothing else.
