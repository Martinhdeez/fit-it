# 0004. Per-user Python hooks executed in-process behind an AST guard

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

The agent generates code that runs on every matching request. It must start instantly, reload without a restart, and be easy to trace. It must not escape into the host.

## Decision

A hook is a small Python file stored per user. Before it is saved and every time it is loaded, an AST guard rejects imports, `global`/`nonlocal`, any name or attribute starting with `_`, and a deny-list of builtins (`eval`, `exec`, `open`, `getattr`, ...). It runs with `exec` in a scope whose builtins are an allow-list, plus a few safe values (`date`, `datetime`, `timedelta`, `math`). The exact rules are in [the hook contract](../specs/hook-contract.md).

## Consequences

- No cold start; a new hook applies on the next request.
- Full trace of what ran.
- **Known ceiling:** this is not a security boundary like a container. There is no CPU or time limit, so an infinite loop blocks a worker thread. The upgrade path is running hooks in a subprocess with resource limits.

## Alternatives considered

- **A container or VM per user:** strong isolation, but seconds of latency and heavy resource use.
- **Client-side JavaScript:** raw data still reaches the browser, so it cannot enforce privacy.
- **WASM sandbox:** strong isolation, too much tooling for the time available.
