# Specs

Contracts between components. A tester writes acceptance tests from these files alone, so every rule here must be checkable from outside the implementation.

| Spec | Version | Owner |
|---|---|---|
| [hook-contract.md](hook-contract.md) | v4 | Core |
| [proxy.md](proxy.md) | v4 | Core |
| [agent.md](agent.md) | v4 | Core |
| [marketplace.md](marketplace.md) | v2 | Core |
| [storage.md](storage.md) | v3 | Core |
| [evals.md](evals.md) | v1 (cases and `setup` added in iteration 03) | Core |
| [embed-widget.md](embed-widget.md) | v4 | Core (built by the frontend teammate) |
| [trace-events.md](trace-events.md) | v1 (minor additions in iterations 02, 03 and 04) | Traceability (Álvaro) |
| [demo-app.md](demo-app.md) | v1 | Core |

Versioning: a change that breaks a test written against the current version bumps the major version and is announced in the iteration plan. Additions that break nothing are a minor note at the bottom of the spec. A new major version starts with a "Changes from v<n>" table and points to the previous text in git history.
