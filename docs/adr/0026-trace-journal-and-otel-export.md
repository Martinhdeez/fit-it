# 0026. Trace journal and OpenTelemetry export

- **Status:** Proposed
- **Date:** 2026-10-24
- **Deciders:** Álvaro

## Context

[ADR 0012](0012-trace-seam-for-observability.md) keeps producers on `trace.emit`. Hooks live in PostgreSQL. The audit log is separate: prompts, diffs and errors stay available to an operator, and timings go to a collector. [ADR 0015](0015-widget-as-static-iframe.md) is the widget. Numbers 0022–0025 are other accepted decisions.

## Decision

One process writes `{FITIT_TRACE_DIR}/events.jsonl`. Compose mounts that directory at `/var/lib/fitit-trace`. `seq` is chosen when the line is committed and recovered by reading valid records, including when other lines are not valid UTF-8. An incomplete tail is copied to `events.corrupt` and removed with `os.truncate`, so the bytes before it are not rewritten. A finished JSON object that lacks a newline is closed, not discarded. The writer holds the file with `fcntl.flock` until the process exits.

`flush` is true only when every record accepted before that call is on disk. Exporter trouble is counted apart from that. `shutdown` uses one deadline: the journal wait and a single exporter flush share it. The provider is not shut down, because tests open the app more than once and a tracer provider cannot be replaced.

The queue holds 10,000 records. This narrows REQ-TR1-2: every event accepted while the sink is open and the queue has room is recorded and readable in order. Events refused because the queue is full or the sink is closed are counted in `diagnostics()["dropped"]` and are not part of that guarantee. A blocked writer and 1,000 small events still return in well under five seconds, so the queue absorbs a short stall, not an unbounded outage. There is no rotation.

FastAPI owns the server span. Export redacts `url.query`, status text, and numeric path segments (`/api/patients/4242` becomes `/api/patients/{id}`). Sink spans are internal. `ms` is `fitit.duration_ms` at the emission instant. Marketplace fields map to `fitit.feature.id`, `fitit.feature.version` and `fitit.feature.created`. `trace_id` in the journal is the active parent trace. A record with no parent has no Jaeger id. One WebSocket is one trace until the agent opens a span per turn.

Stdout gets `seq kind user` only. The full record stays in the journal. `python trace_view.py` prints the timeline for the demo. Evals replace `trace.emit` and do not write the journal. `make check` uses `FITIT_TRACE_DIR=/tmp/fitit-trace`.

## Consequences

- Hook listings do not see the journal. PostgreSQL stays the hook store.
- Jaeger at `127.0.0.1:16686` drops traces when it restarts. The journal volume does not.
- A second process on the same host cannot append while the lock is held. The lock disappears when the holder exits, including after a crash.
- Operators read the volume from a new container. In-memory counters stay in the writer process. Damage is in `events.corrupt` and in skipped lines.

## Alternatives considered

- **Audit rows in PostgreSQL:** useful later for several writers. Not required for this single core.
- **Jaeger as the only record:** an exporter outage would drop the audit.
- **Blocking `emit` when the queue is full:** would miss the latency requirement under load.
