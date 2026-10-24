# Architecture overview

Diagram: [`.artifacts/pages/reverse-proxy-flow.html`](../../.artifacts/pages/reverse-proxy-flow.html) (open in a browser). Decisions behind each part are linked as ADRs.

## Context

```
 Browser ──HTTP──► Fit-it core ──HTTP──► Client app (unmodified)
    ▲                  │  ▲
    └──WebSocket───────┘  └── Claude API (coding agent)
                        │
                        └── Postgres (core only): hooks, hook versions, chat history, marketplace
```

The browser never reaches the client app directly: in Docker Compose the app has no published port ([ADR 0002](../adr/0002-reverse-proxy-in-front-of-unmodified-app.md)).

## Components

| Component | Responsibility | Spec | Epic |
|---|---|---|---|
| Proxy | Forwards every request to the client app, resolves the user, applies hooks to JSON, injects the widget into HTML | [proxy.md](../specs/proxy.md) | [E2](../epics/e2-proxy-core.md) |
| Hook runtime | Validates (AST guard), stores (with versions and undo), loads and runs per-user hooks | [hook-contract.md](../specs/hook-contract.md) | [E3](../epics/e3-hook-runtime.md) |
| Marketplace | Publishes a user's hook as a feature other users search, dry-run, subscribe to (pinned to a version), toggle, update, fork and report; "My features" lists everything that runs for a user | [marketplace.md](../specs/marketplace.md) | [E12](../epics/e12-marketplace.md) |
| Coding agent | Turns a chat message into a validated hook using Claude and tools | [agent.md](../specs/agent.md) | [E4](../epics/e4-coding-agent.md) |
| Embed widget | Chat, live agent steps, diffs and hook toggles inside the client page | [embed-widget.md](../specs/embed-widget.md) | [E5](../epics/e5-embed-widget.md) |
| Trace | Records every proxied request, hook run and agent step | [trace-events.md](../specs/trace-events.md) | [E6](../epics/e6-traceability.md) |
| Demo client app | The clinic app the demo runs on; stands in for any customer app | [demo-app.md](../specs/demo-app.md) | [E1](../epics/e1-demo-client-app.md) |

## Request path (data plane)

1. The browser sends a request to the core.
2. The core resolves the user by calling the app's `GET /api/me` with the same cookies ([ADR 0006](../adr/0006-identify-users-through-the-app-session.md)).
3. The core forwards the request to the app unchanged.
4. The app answers with its normal HTML or JSON.
5. For JSON, the hook runtime first runs the user's enabled marketplace subscriptions whose pinned code's `ROUTE` matches, in subscription order and named `@<feature_id>`, then the user's own enabled matching hooks in name order, each receiving the previous output ([ADR 0023](../adr/0023-features-run-as-the-subscriber.md)). For HTML, the core injects the widget script.
6. The core returns the result. Any hook or feature error returns `502` instead of data ([ADR 0005](../adr/0005-fail-closed-hook-execution.md)).

`GET /x/<name>` paths are virtual endpoints: they are not forwarded, and a hook builds the whole response from existing API calls.

## Change path (control plane)

1. The widget opens `WS /fitit/ws` (same origin, so the app's session cookie comes along).
2. The user sends a message. The agent reads the app's OpenAPI catalog ([ADR 0007](../adr/0007-openapi-as-the-agent-catalog.md)), samples endpoints as that user, and writes a hook.
3. Every hook goes through: AST guard, load check, dry run on live data. Only then is it saved and the diff is sent to the widget.
4. An owner publishes a hook as a marketplace feature (widget, agent or `POST /fitit/marketplace/features`): its code is frozen as a version. Publishing again adds a version only when the code changed; deleting the hook unpublishes the feature ([ADR 0022](../adr/0022-feature-marketplace.md)).
5. Another user subscribes, or accepts an update, only after the version's dry run passes on their own data; the subscription stays pinned to that version until they accept the next one. Forking copies the pinned code into a normal hook of their own.

## Security model

| Threat | Control |
|---|---|
| Hook reads data the user cannot see | Hooks only call the app's API through `ctx.api`, with the user's own session ([ADR 0003](../adr/0003-api-only-access-to-client-data.md)) |
| Hook escapes the sandbox | AST guard + restricted builtins ([ADR 0004](../adr/0004-per-user-python-hooks-in-process.md)). Known ceiling: in-process, no CPU or time limit |
| Hook breaks and leaks unfiltered data | Fail closed ([ADR 0005](../adr/0005-fail-closed-hook-execution.md)) |
| Hook writes through the API | `ctx.api` exposes `GET` only |
| User A's hook affects user B | Hooks are stored and loaded per resolved user id; another user's code runs for B only if B subscribed to it |
| A subscribed feature reaches data or identity of its owner | It runs as the subscriber: `ctx.user` and `ctx.api` are the subscriber's, the owner's session is never used, output and traces go only to the subscriber ([ADR 0023](../adr/0023-features-run-as-the-subscriber.md)) |
| An owner changes code under a subscriber | Subscriptions are pinned to a version that passed the subscriber's dry run; new versions run only after an accepted update |
| Feature code tampered in storage | Pinned code is guarded again every time it loads and fails closed like an own hook. Known ceiling: a feature can show wrong data; mitigated by readable code, pinning and reports |
| Secrets in the repo | Secrets come from the host shell through Compose ([ADR 0010](../adr/0010-docker-compose-and-secrets-from-the-shell.md)) |

## Repository layout (target for iteration 01)

```
backend/   Fit-it core (FastAPI): main.py (proxy + routes), hooks.py, marketplace.py, agent.py, trace.py, static/embed.js
demo/      Demo clinic app (FastAPI), the stand-in for a customer app
docs/      This documentation
tests/     acceptance/ (blind acceptance tests)
```
