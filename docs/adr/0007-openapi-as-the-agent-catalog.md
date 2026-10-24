# 0007. OpenAPI as the agent's endpoint catalog

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

The coding agent must know which endpoints the app has and what they return, or it will guess.

## Decision

The catalog is the app's OpenAPI document, read from `GET /openapi.json` on the app. The agent receives it as a compact list of `METHOD path: summary` lines and can sample any `GET` endpoint as the current user to see real data.

## Consequences

- Standard and free for frameworks that generate it (FastAPI, NestJS, Spring with springdoc).
- Apps without an OpenAPI document are not supported yet.

## Alternatives considered

- **Learning the catalog from proxied traffic:** works without a spec, but adds a component; parked.
- **Manual endpoint registration:** extra work for the operator.
