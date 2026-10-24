# Epics

Each epic is a slice of the product with stories and their acceptance criteria. Iteration plans pick items from epics; the testable form of each acceptance criterion is the `REQ-*` id in the iteration's `requirements.md`.

| Epic | Item (iteration 01) | Owner | Status |
|---|---|---|---|
| [E1 Demo client app](e1-demo-client-app.md) | DA1 | Agents | Planned |
| [E2 Proxy core](e2-proxy-core.md) | PX1 | Agents | Planned |
| [E3 Hook runtime](e3-hook-runtime.md) | HR1 | Agents | Planned |
| [E4 Coding agent](e4-coding-agent.md) | AG1 | Agents | Planned |
| [E5 Embed widget](e5-embed-widget.md) | EW1 | Frontend teammate | Planned |
| [E6 Traceability](e6-traceability.md) | TR1 | Álvaro | Planned |
| [E7 Delivery](e7-delivery.md) | OP1 | Agents | Planned |

## Brief for an agent

Each item is handed to the developer and the tester with the same brief:

```
Item: <ID> <title>  —  Epic: docs/epics/<file>
Read first: AGENTS.md, docs/plan/01-mvp/00-plan.md, contracts.md, requirements.md (your REQ ids), the specs the epic links.
Branch: feature/<id-lowercase>-<slug> from develop (developer) | test/<id-lowercase>-<slug> (tester).
Developer: implement only this item, unit tests in tests/unit/, `make check` green, PR into develop.
Tester: acceptance tests in tests/acceptance/, one or more per REQ id, named after it; never read backend/, demo/ or the developer's branch; report pass/fail per REQ.
On a failed test: the reviewer rules code / test / spec (ADR 0013).
```
