# Architecture Decision Records

One decision per file, in the [MADR](https://adr.github.io/madr/) style: context, decision, consequences, alternatives.

Rules:
- Numbered `NNNN-title-in-kebab-case.md`, never renumbered.
- Status is one of `Proposed`, `Accepted`, `Superseded by NNNN`, `Rejected`.
- An accepted ADR is not rewritten. A new decision supersedes it and both link to each other.
- Copy [`template.md`](template.md) to start a new one.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-reverse-proxy-in-front-of-unmodified-app.md) | Reverse proxy in front of an unmodified app | Accepted |
| [0003](0003-api-only-access-to-client-data.md) | API-only access to client data, no database credentials | Accepted |
| [0004](0004-per-user-python-hooks-in-process.md) | Per-user Python hooks executed in-process behind an AST guard | Accepted |
| [0005](0005-fail-closed-hook-execution.md) | Fail-closed hook execution | Accepted |
| [0006](0006-identify-users-through-the-app-session.md) | Identify users through the app's own session | Accepted |
| [0007](0007-openapi-as-the-agent-catalog.md) | OpenAPI as the agent's endpoint catalog | Accepted |
| [0008](0008-pydantic-ai-for-the-coding-agent.md) | Pydantic AI for the coding agent | Accepted |
| [0009](0009-one-client-app-per-core.md) | One client app per core instance | Accepted |
| [0010](0010-docker-compose-and-secrets-from-the-shell.md) | Docker Compose, secrets from the host shell | Accepted |
| [0011](0011-git-flow-and-conventional-commits.md) | Git Flow and Conventional Commits | Accepted |
| [0012](0012-trace-seam-for-observability.md) | A single trace seam for observability | Accepted |
| [0013](0013-developer-and-tester-agents.md) | Developer, tester and reviewer agents | Accepted |
| [0014](0014-provider-agnostic-model.md) | Provider-agnostic model through `FITIT_MODEL` | Accepted |
| [0015](0015-widget-as-static-iframe.md) | Widget as a static Next.js export in a same-origin iframe | Accepted |
| [0017](0017-postgres-as-core-storage.md) | PostgreSQL as the core's storage | Accepted |
| [0018](0018-hook-versions-and-undo.md) | Hook versions and undo | Accepted |
| [0019](0019-persistent-chat-history.md) | Persistent chat history per user | Accepted |
| [0020](0020-hook-time-limit-and-safety-limits.md) | Hook time limit, output cap and agent rate limit | Accepted |
| [0021](0021-real-model-evals-on-demand.md) | Real-model evaluation set, run on demand | Accepted |
