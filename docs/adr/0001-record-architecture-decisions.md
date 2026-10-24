# 0001. Record architecture decisions

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín, Álvaro

## Context

The architecture changed several times on the first day (data pipeline, sidecar with SDK, reverse proxy). Agents and new contributors need to know what was decided and why, without rereading chat history.

## Decision

Every significant decision is recorded as an ADR in `docs/adr/`, using the MADR structure in [`template.md`](template.md). Accepted ADRs are never rewritten; new decisions supersede them.

## Consequences

- Specs and epics can cite a decision by number.
- Reversing a decision costs one new file, which makes reversals visible.

## Alternatives considered

- **Decisions in the README or chat:** lost or overwritten as the project moves.
