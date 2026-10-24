# 0011. Git Flow and Conventional Commits

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín, Álvaro

## Context

Humans and agents commit in parallel. History must stay readable and `main` must always be demo-ready.

## Decision

Git Flow (`main`, `develop`, `feature/*`, `release/*`, `hotfix/*`) and Conventional Commits, as defined in [`AGENTS.md`](../../AGENTS.md).

## Consequences

- Agents work on feature branches and open pull requests into `develop`; `main` only moves on a release.
- Commit types and scopes make the changelog and the review easy to scan.

## Alternatives considered

- **Trunk-based development:** faster, but riskier with several agents pushing at once.
