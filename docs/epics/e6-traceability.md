# E6 — Traceability

- **Item:** TR1 · **Spec:** [trace-events.md](../specs/trace-events.md) · **Requirements:** REQ-TR1-1 … 3
- **Owner:** Álvaro
- **Depends on:** nothing (works against the `trace.emit` contract)
- **ADR:** [0012](../adr/0012-trace-seam-for-observability.md)

## Goal

Every prompt, generated hook, validation and hook execution is recorded and can be shown, so an operator can audit what changed, for whom and why.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As the core, I can emit events without ever failing or slowing a request. | REQ-TR1-1, REQ-TR1-3 |
| 2 | As an operator, I can read every event in order. | REQ-TR1-2 |
| 3 | As a presenter, I can show the trace of the demo (view defined by Álvaro). | Defined by the owner |

## Deliverables

`backend/trace.py` and whatever stands behind it (storage, OpenTelemetry, a view), decided by the owner and recorded in a new ADR if it adds a component.

## Out of scope for the core team

Everything behind `trace.emit`.
