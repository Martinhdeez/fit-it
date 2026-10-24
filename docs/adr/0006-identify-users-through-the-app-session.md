# 0006. Identify users through the app's own session

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

Hooks are per user, so the core must know who is making each request. With zero changes to the app, the core cannot decode the app's session itself.

## Decision

On each request the core calls the app's `GET /api/me` with the incoming cookies. A `200` JSON response with a string `id` identifies the user. Anything else means anonymous: the request is proxied with no hooks and no widget.

## Consequences

- Works with any app that exposes a "current user" endpoint, with no changes.
- One extra request per proxied request. Caching by session cookie is the upgrade path if latency matters.
- The `/api/me` path is a requirement on supported apps; it becomes configurable if another app names it differently.

## Alternatives considered

- **The core runs its own login:** duplicates the app's authentication.
- **The app sends a user header:** a one-line change, but breaks "zero modifications".
