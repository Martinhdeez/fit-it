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
