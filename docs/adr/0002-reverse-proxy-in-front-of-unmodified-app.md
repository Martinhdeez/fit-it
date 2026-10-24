# 0002. Reverse proxy in front of an unmodified app

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín, Álvaro

## Context

Fit-it must let each user change how an existing app behaves for them. The operator should not have to change, rebuild or redeploy the app. The app can be written in any language.

## Decision

The Fit-it core is a reverse proxy. All browser traffic goes to the core, which forwards it to the client app, applies the user's hooks to the response, and injects the widget script into HTML pages. The client app is not reachable directly: it has no published port in the deployment.

## Consequences

- Zero changes to the client app, in any language.
- The core sees every request, which makes per-user hooks, virtual endpoints and tracing possible.
- Every request pays the proxy's latency, and the core is a single point of failure.
- Apps that render in the browser from JSON are the best fit; server-rendered HTML can only be changed through injected scripts.

## Alternatives considered

- **SDK inside the client backend (sidecar model):** needs code changes and one SDK per language.
- **Browser extension (as Tweeks does):** changes only the page, installed by each user, no server-side logic or governance.
- **Patching the app's source per user:** races between users, needs a build per user, language-specific.
- **v0 design, a data pipeline inside our own backend:** only worked for data we owned; superseded by this ADR.
