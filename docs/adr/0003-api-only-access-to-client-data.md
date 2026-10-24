# 0003. API-only access to client data, no database credentials

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

Hooks often need more data than the response they modify (a patient's history, all unpaid invoices). We considered giving the core database access, either through an SDK bridge or directly.

## Decision

Hooks reach client data only through the client app's own HTTP API, using the current user's session (`ctx.api.get(path)`). The core never holds database credentials. `ctx.api` exposes `GET` only.

## Consequences

- A hook can never see more than its user already can: the app's own authentication, authorization and business rules apply to every call.
- No SDK, so the client app stays unmodified.
- Features that need data the API does not expose are not possible. Combining many records costs many requests.
- An optional read-only database adapter can be added later as an opt-in plugin, with its own ADR.

## Alternatives considered

- **SDK database bridge (outbound tunnel from the client):** safe with row scoping, but requires code in the client and one SDK per language.
- **Direct read-only database user:** simple, but bypasses the app's rules and per-user scoping.
