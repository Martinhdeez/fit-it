# 0021. Real-model evaluation set, run on demand

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

`make check` proves the plumbing with scripted models and never calls a real model ([ADR 0013](0013-developer-and-tester-agents.md)). It says nothing about whether a real model turns "hide the phone of patients who owe money" into a working hook. Prompt changes and model switches ([ADR 0014](0014-provider-agnostic-model.md)) need a number to compare.

## Decision

An evaluation set in `evals/`: realistic requests against the demo clinic, each with an automatic check of the resulting behavior through the proxy (for example, after the turn, `GET /api/invoices` as that user and compare it with the body before). `make eval` runs it inside Docker with whatever `FITIT_MODEL` and key are in the shell, and prints a line per case, the pass rate and token counts. Cases and the harness contract are in the [evals spec](../specs/evals.md).

- Never part of `make check`: it costs money and its result depends on the model.
- Each case runs in its own database schema, so cases never see each other's hooks or history.
- The harness itself is tested in `make check` with scripted models; the pass rate is not a requirement.

## Consequences

- A prompt or model change can be compared with a number before and after.
- Running it costs model tokens; a human decides when, at least once per release, and records the pass rate in `CHANGELOG.md`.
- Checks verify behavior, not code, so any hook that does the right thing passes.

## Alternatives considered

- **Assert on the generated code:** brittle; many correct hooks look different.
- **LLM-as-judge:** costs more, and its verdict is itself a model output.
- **Run in CI on every pull request:** recurring cost for every docs change.
