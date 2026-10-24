# 0023. Subscribed features run as the subscriber

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

With the marketplace ([ADR 0022](0022-feature-marketplace.md)), code written by one user runs on another user's requests. Until now a hook only ever ran for the user who owned it. We must decide whose identity and session that code gets, where it sits in the chain, and what happens when it fails.

## Decision

**Security property: a subscribed feature runs exactly as if the subscriber had written it.** `ctx.user` is the subscriber's id and `ctx.api` uses the subscriber's own session cookies. The owner's identity and session are never involved in a run.

- **Data reach.** Through `ctx.api` a feature reads only what the subscriber can already read in the app ([ADR 0003](0003-api-only-access-to-client-data.md)), and only with `GET`.
- **No channel back to the owner.** A run's output goes only to the subscriber. Its `ctx.log` lines and trace events carry the subscriber as `user`. No route or tool shows the owner anything about subscribers except their count.
- **Pinned code.** A subscriber runs the version they subscribed to or last accepted, never newer code they have not dry-run.
- **Same checks as own hooks.** The pinned code passes the guard again every time it is loaded, runs with the same time limit and output cap, and fails closed ([ADR 0005](0005-fail-closed-hook-execution.md)): a failing feature makes the request `502 hook_failed`, never returns unfiltered data.
- **Dry run before it runs.** Subscribing and accepting an update dry-run the code on the subscriber's own live data with `hooks.dry_run` and are refused if it fails.
- **Order.** On a matching route, the subscriber's enabled subscriptions run first, in the order they subscribed, then the subscriber's own hooks in name order, each receiving the previous output. A feature is named `@<feature_id>` in `x-fitit-hooks`, in `HookError.hook` and in trace events, so it can never collide with a hook name (`^[a-z0-9_]{1,40}$`).

## Consequences

- Sharing code never widens anyone's data access: the worst a feature can do with data is what the subscriber could do by hand with `GET` requests.
- Features first means a feature always sees the app's own data, the same data its dry run used; the subscriber's own hooks can adjust the feature's output.
- **Known ceiling (integrity):** a feature can show the subscriber wrong data, for example hide unpaid invoices, or behave differently for one `ctx.user`. Mitigations: the code is readable by every user before subscribing, versions are pinned, users can report. Not prevented.
- **Known ceiling (side channel):** a client app `GET` route with side effects (one that stores what it receives) would let a feature write the subscriber's data where the owner can read it. That is a defect of the app's API, out of Fit-it's control; the guard cannot see it.
- Trace events of feature runs belong to the subscriber. A future trace viewer must never show them to the feature's owner.

## Alternatives considered

- **Run as the owner, with the owner's session:** the subscriber would see data with the owner's permissions; a straight privilege escalation.
- **Run with no session (`ctx.api` disabled):** safe, but most useful features (debt per doctor, mask debtors) need `ctx.api`.
- **Own hooks first, features after:** the feature's dry run would no longer match what it receives at run time.
