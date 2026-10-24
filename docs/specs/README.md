# Specs

Contracts between components. A tester writes acceptance tests from these files alone, so every rule here must be checkable from outside the implementation.

| Spec | Version | Owner |
|---|---|---|
| [hook-contract.md](hook-contract.md) | v1 | Core |
| [proxy.md](proxy.md) | v1 | Core |
| [agent.md](agent.md) | v1 | Core |
| [embed-widget.md](embed-widget.md) | v1 | Core |
| [trace-events.md](trace-events.md) | v1 | Traceability (Álvaro) |
| [demo-app.md](demo-app.md) | v1 | Core |

Versioning: a change that breaks a test written against the current version bumps the major version and is announced in the iteration plan. Additions that break nothing are a minor note at the bottom of the spec.
