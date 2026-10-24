# 0009. One client app per core instance

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

The core could front several apps, each with its own catalog and workspaces. The demo needs one.

## Decision

One core instance fronts one app, set by the `BASE_URL` environment variable. Hooks live in `WORKSPACES/<user_id>/`.

Update (2026-10-24): storage moved to Postgres, see [ADR 0017](0017-postgres-as-core-storage.md).

## Consequences

- Simpler configuration, paths and tests.
- Several apps means several core instances until a multi-app ADR supersedes this one; adding an app level to the workspace path is the expected migration.

## Alternatives considered

- **Multi-app from day one (`apps: {app_id: base_url}`):** more configuration and routing for no demo value.
