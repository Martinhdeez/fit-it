# 0005. Fail-closed hook execution

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

Hooks can implement privacy rules (hide diagnoses, mask phone numbers). If a hook raises and the proxy returned the original response, private data would leak.

## Decision

If any matching hook raises, the core returns `502` with `{"error": "hook_failed", "hook": <name>, "detail": <message>}` and never the original body.

## Consequences

- A broken hook makes an endpoint unavailable for that user until it is fixed or disabled. The widget lets the user disable it.
- The agent's dry run catches most failures before a hook is saved.

## Alternatives considered

- **Fail open (return the original response):** leaks exactly the data privacy hooks protect.
- **Per-hook `critical` flag:** more flexible, more to explain; not needed for the MVP.
