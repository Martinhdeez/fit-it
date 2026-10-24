# Checklist 02 — Postgres core storage, agent quality, safety limits, evals

Plan: [`docs/plan/02-postgres/00-plan.md`](../plan/02-postgres/00-plan.md). Progress: 0 / 8.

Ticked by whoever merges, with the pull request number or the tag.

- [ ] **RL0** Release `v0.3.0` (wave 0) — human — tag
- [ ] **DB1** Postgres in the stack and the test gate (wave 1) — developer + tester — PR #
- [ ] **HS2** Hook storage on Postgres, versions, undo, `dry_run`, per-hook timing (wave 2, merges first) — developer + tester — PR #
- [ ] **CH1** Persistent chat history (wave 2, after HS2) — developer + tester — PR #
- [ ] **AG2** Agent quality (wave 3) — developer + tester — PR #
- [ ] **SF1** Hook time limit and output cap (wave 3) — developer + tester — PR #
- [ ] **EV1** Real-model eval set (wave 3) — developer + tester — PR #
- [ ] **RL1** `make eval` pass rate in `CHANGELOG.md`, release `v0.4.0` (wave 4) — human — tag

Carried over from 01: **EW1** (frontend teammate), **TR1** (Álvaro).
