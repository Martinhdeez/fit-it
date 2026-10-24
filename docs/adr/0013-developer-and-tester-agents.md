# 0013. Developer, tester and reviewer agents

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

We want AI agents to build the MVP, with independent verification, inside one day. A full pipeline (test author, builder, reviewer, designer for every item) is too slow. But when a blind test fails, someone neutral must decide who is right: the code, the test or the spec.

## Decision

Three roles per item:
- **Developer:** implements one item from its epic and spec, with unit tests in `tests/unit/`, on a `feature/*` branch, and opens a pull request into `develop`.
- **Tester:** writes acceptance tests in `tests/acceptance/` from the requirements and specs only, without reading the implementation, runs them against the developer's branch, and reports pass or fail per requirement.
- **Reviewer:** called only when an acceptance test fails. Reads the requirement, the spec, the failing test and the code, and rules on each failure:
  - **code wrong:** the developer fixes it;
  - **test wrong:** the tester fixes it;
  - **spec ambiguous or wrong:** the reviewer proposes a spec change, and a human approves it before either side continues.

A human (Martín or Álvaro) merges when every requirement passes. After two rounds of review without agreement, the reviewer escalates to the human.

## Consequences

- Independent tests, with a referee only when needed, so the common path stays fast.
- Specs and requirements must be precise enough for a tester who never sees the code. A failure the reviewer traces to the spec is a defect in the docs and is fixed there first.
- The reviewer's rulings are written in the pull request, so later readers see why a test or the code changed.

## Alternatives considered

- **Full harness (test author, builder, reviewer, designer for every item):** more rigorous, too slow for the hackathon.
- **One agent that builds and tests its own work:** tests end up confirming what was built, not what was asked.
- **No referee; the developer decides when a test is wrong:** the developer would weaken tests to pass them.
