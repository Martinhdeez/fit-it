# 0008. Pydantic AI for the coding agent

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

The agent runs a tool loop: read the catalog, sample endpoints, write a hook, validate it, repair it on failure. It must stream its steps to the widget, be testable offline, and should not lock us to one model provider (the next hackathon may require Llama).

## Decision

Use Pydantic AI, without LangGraph. Tools are typed functions with a dependency object (user, API client). A validation failure in `write_hook` raises `ModelRetry`, so the model repairs the hook itself. The model is set by the `FITIT_MODEL` environment variable, default `anthropic:claude-opus-5-5`.

## Consequences

- Typed, validated tool arguments.
- Offline tests with Pydantic AI's `TestModel` and `FunctionModel`.
- Changing provider is one setting.
- Provider-specific features (such as Anthropic server-side refusal fallbacks) are not used.

## Alternatives considered

- **LangGraph (alone or with Pydantic AI):** worth it for durable multi-agent graphs or human approval steps; one agent with six tools does not need it.
- **A hand-written loop on the Anthropic SDK:** fewer dependencies, but ties us to one provider and needs our own test doubles.
