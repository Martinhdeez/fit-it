# 0012. A single trace seam for observability

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín, Álvaro

## Context

Traceability is built by Álvaro in parallel with the core. Both sides need to work without waiting for each other.

## Decision

The core only calls `trace.emit(kind: str, **fields)` from `backend/trace.py`, with the event kinds and fields fixed in [the trace events spec](../specs/trace-events.md). The traceability workstream owns `trace.py` and everything behind it (storage, OpenTelemetry, views). The default implementation prints one JSON line per event.

## Consequences

- The core and traceability can be built and tested independently.
- Adding an event kind is a spec change announced to both sides.

## Alternatives considered

- **Instrumenting with OpenTelemetry directly in the core:** couples both workstreams from the start.
