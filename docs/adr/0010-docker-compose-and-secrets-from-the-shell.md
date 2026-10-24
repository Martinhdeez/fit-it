# 0010. Docker Compose, secrets from the host shell

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín, Álvaro

## Context

Two developers and several agents must run the same stack. Secrets (the model API key) must never reach the repository.

## Decision

The stack runs with `docker compose`. Secrets are declared in `compose.yaml` as `environment:` entries without values, so they pass through from the host shell. No `.env` file is part of the workflow.

## Consequences

- One command starts everything; no local Python setup is needed.
- Each developer exports the key in their shell before `docker compose up`.

## Alternatives considered

- **`.env` file:** easy to commit by mistake.
- **Docker secrets:** more setup than one key needs.
