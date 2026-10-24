# Fit-it documentation

Start here. Every document has one job; link to it instead of repeating it.

| Folder | What it holds | Changes how |
|---|---|---|
| [`product/`](product/) | Why Fit-it exists, who it is for, the demo script | Freely, by the team |
| [`architecture/`](architecture/) | How the system fits together today | Updated with every merged ADR |
| [`adr/`](adr/) | Architecture Decision Records: one decision per file | Append-only; supersede, never rewrite |
| [`specs/`](specs/) | Contracts between components: routes, schemas, file formats, events | Versioned; frozen once an iteration plan approves them |
| [`epics/`](epics/) | Product backlog: epics, stories, acceptance criteria, owners | Freely, by the team |
| [`plan/`](plan/) | One folder per iteration for the harness agents: plan, requirements, contracts | Created by the orchestrator, approved by the user |
| [`advance/`](advance/) | Iteration checklists, ticked by reviewers on merge | Reviewers only |

Contribution rules (Git Flow, Conventional Commits, pull requests, agent rules) live in [`../AGENTS.md`](../AGENTS.md).

## Reading order

1. [`product/vision.md`](product/vision.md)
2. [`architecture/overview.md`](architecture/overview.md)
3. [`adr/README.md`](adr/README.md)
4. The spec for the component you are touching, in [`specs/`](specs/)
5. The current iteration: [`plan/03-marketplace/00-plan.md`](plan/03-marketplace/00-plan.md) (previous: [`plan/02-postgres/`](plan/02-postgres/00-plan.md), [`plan/01-mvp/`](plan/01-mvp/00-plan.md))

## Conventions

- English, plain language, short sentences.
- File names are lowercase kebab-case. ADRs are numbered `NNNN-title.md`.
- A spec names its version at the top. A breaking change bumps the version and is announced in the iteration plan.
- Requirements use ids `REQ-<ITEM>-<n>` and one testable sentence each.
- Diagrams live in `../.artifacts/pages/` as Excalidraw pages; documents link to them.
