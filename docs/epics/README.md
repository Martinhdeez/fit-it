# Epics

Each epic is a slice of the product with stories and their acceptance criteria. Iteration plans pick items from epics; the testable form of each acceptance criterion is the `REQ-*` id in the iteration's `requirements.md`.

| Epic | Items | Iteration | Owner | Status |
|---|---|---|---|---|
| [E1 Demo client app](e1-demo-client-app.md) | DA1 | 01 | Agents | Done |
| [E2 Proxy core](e2-proxy-core.md) | PX1 | 01 | Agents | Done |
| [E3 Hook runtime](e3-hook-runtime.md) | HR1 | 01 | Agents | Done |
| [E4 Coding agent](e4-coding-agent.md) | AG1 | 01 | Agents | Done |
| [E5 Embed widget](e5-embed-widget.md) | EW1 | 01 | Frontend teammate | Planned |
| [E6 Traceability](e6-traceability.md) | TR1 | 01 | Álvaro | Planned |
| [E7 Delivery](e7-delivery.md) | OP1 | 01 | Agents | Done |
| [E8 Core storage on PostgreSQL](e8-core-storage.md) | DB1, HS2, CH1 | 02 | Agents | Planned |
| [E9 Agent quality](e9-agent-quality.md) | AG2 | 02 | Agents | Planned |
| [E10 Safety limits for hooks](e10-safety-limits.md) | SF1 | 02 | Agents | Planned |
| [E11 Real-model evals](e11-evals.md) | EV1 | 02 | Agents | Planned |
| [E12 Feature marketplace](e12-marketplace.md) | MK1, MK2, AG3, EV2, WM1 | 03 | Agents; WM1 frontend teammate | Planned |
| [E13 Parallel conversations](e13-parallel-conversations.md) | HK1, CV1, AG4, WC1 | 04 | Agents; WC1 frontend teammate | Planned |

## Brief for an agent

Each item is handed to the developer and the tester with the same brief:

```
Item: <ID> <title>  —  Epic: docs/epics/<file>
Read first: AGENTS.md, docs/plan/<current>/00-plan.md, contracts.md, requirements.md (your REQ ids), the specs the epic links.
Branch: feature/<id-lowercase>-<slug> from develop (developer) | test/<id-lowercase>-<slug> (tester).
Developer: implement only this item, edit only the files the plan gives it, unit tests in tests/unit/, `make check` green, PR into develop.
Tester: acceptance tests in tests/acceptance/, one or more per REQ id, named after it; never read backend/, demo/, evals/ or the developer's branch; report pass/fail per REQ.
On a failed test: the reviewer rules code / test / spec (ADR 0013).
```
