# 0020. Hook time limit, output cap and agent rate limit

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

[ADR 0004](0004-per-user-python-hooks-in-process.md) runs hooks in-process with no time limit: an infinite loop blocks a worker thread forever. A hook can also return a huge body, and nothing stops a user from sending the agent messages in a loop, each one costing model tokens.

## Decision

Three limits, each set by an environment variable with a default listed in the [proxy spec](../specs/proxy.md#configuration):

- **Time limit per hook run (`FITIT_HOOK_TIMEOUT_MS`, default `2000`).** While a hook's code runs, the hook runtime installs a `sys.settrace` function on the running thread that checks a deadline on every trace event and raises once it has passed. The thread's previous trace function is restored afterwards. A timeout is a hook failure: a request fails closed with `502 hook_failed`, and a dry run rejects the hook.
- **Output cap (`FITIT_HOOK_MAX_BYTES`, default `1000000`).** A hook whose output, serialized as JSON in UTF-8, is larger fails like any other hook.
- **Agent rate limit (`FITIT_AGENT_MESSAGES_PER_HOUR`, default `60`).** Per user, a sliding one-hour window kept in memory in the core process, counted across connections. Over the limit, the message ends with `error` then `done` and the model is not called.

## Consequences

- An accidental `while True`, or a slow loop over many records, fails closed in about two seconds instead of hanging a worker.
- Tracing slows hook code by a small factor; hooks are short, so a few milliseconds at most.
- **Known ceiling (time limit):** the deadline is checked between Python events, so a single long call in C (`sum(range(10**12))`, `10**10**8`) or a blocking wait inside `ctx.api.get` (bounded by the HTTP client's own timeout) is not interrupted. The upgrade path stays the one in ADR 0004: run hooks in a subprocess that can be killed.
- **Known ceiling (rate limit):** the window resets on restart and is per process. Enough for one core process; move the counter to Postgres when the core scales out.
- While a hook runs, `sys.settrace` replaces a debugger's or coverage tool's trace function on that thread.

## Alternatives considered

- **Worker thread with `join(timeout)`:** the request returns, but a Python thread cannot be killed, so the loop keeps burning CPU.
- **Subprocess per hook run, killed on timeout:** real enforcement including C calls, but every run pays a process start and must ship data and `ctx.api` across a process boundary. The upgrade path, not the first step.
- **`signal.alarm`:** only works on the main thread; hooks run in worker threads.
- **Instruction budget by rewriting the AST:** more code than a trace function, same C-call ceiling.
- **Rate limit in Postgres:** survives restarts, but one more table and a write per message for a limit nobody is attacking yet.
