# 0022. Trace journal and OpenTelemetry export

- **Status:** Proposed
- **Date:** 2026-10-24
- **Deciders:** Álvaro

## Context

[ADR 0012](0012-trace-seam-for-observability.md) keeps producers on `trace.emit`. Iteration 02 stores hooks in PostgreSQL. The audit log is a separate record: prompts, diffs and errors stay available to an operator, and timings go to a collector. [ADR 0015](0015-widget-as-static-iframe.md) is the widget decision and is not this one.

## Decision

One process writes `{FITIT_TRACE_DIR}/events.jsonl`. Compose mounts that directory at `/var/lib/fitit-trace`. The cursor `seq` is chosen when the line is committed, recovered from the file at startup, and increases across restarts. An incomplete tail is copied to `events.corrupt` before the next line is written. A finished JSON object that lacks a newline is closed, not discarded.

`flush` is true only when every record accepted before that call is on disk. Exporter trouble is counted apart from that. The queue holds 10,000 records. Past that, `emit` still returns and `diagnostics()["dropped"]` increases. That does not change REQ-TR1-2; loss outside this envelope is visible and not claimed as success.

FastAPI owns the server span. A processor on the same provider redacts `url.query` and status descriptions. Sink spans are internal. `ms` is exported as `fitit.duration_ms` at the emission instant, not as a reconstructed start time. `trace_id` in the journal is the active parent trace. A record with no parent has no Jaeger id.

## Consequences

- Hook listings do not see the journal. PostgreSQL stays the hook store.
- Jaeger at `127.0.0.1:16686` drops traces when it restarts. The journal volume does not.
- A second process finds `writer.lock` and does not append.
- Operators read the file from a new container. They cannot see another process's in-memory counters; damage is in `events.corrupt` and in skipped lines.

## Alternatives considered

- **Audit rows in PostgreSQL:** useful later for several writers. Not required for this single core.
- **Jaeger as the only record:** an exporter outage would drop the audit.
