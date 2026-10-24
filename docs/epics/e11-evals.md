# E11 — Real-model evals

- **Item:** EV1 · **Spec:** [evals.md](../specs/evals.md) · **Requirements:** REQ-EV1-1 … 7
- **Depends on:** HS2 and CH1 (hooks and history in Postgres, so each case can run in its own schema)
- **ADR:** [0021](../adr/0021-real-model-evals-on-demand.md)

## Goal

One command tells how well the current model and prompt turn real requests into working hooks, with a pass rate to compare across changes.

## Stories

| # | Story | Acceptance |
|---|---|---|
| 1 | As a maintainer, I have a fixed set of realistic requests with automatic behavior checks. | REQ-EV1-1 |
| 2 | As a maintainer, a correct hook passes its case and a wrong or missing one fails, with a reason. | REQ-EV1-2, REQ-EV1-3 |
| 3 | As a maintainer, cases do not leak state into each other or into my database. | REQ-EV1-4 |
| 4 | As a maintainer, I get one line per case and the pass rate, and can run just a few cases to save money. | REQ-EV1-5 |
| 5 | As a contributor, `make check` never calls a real model; `make eval` does, on demand. | REQ-EV1-6, REQ-EV1-7 |

## Deliverables

`evals/cases.py`, `evals/run_evals.py`, `make eval` and the `evals/` mount in `make check` (for the harness tests).

## Out of scope

Cost in money, LLM-as-judge, running in CI, a pass-rate threshold.
