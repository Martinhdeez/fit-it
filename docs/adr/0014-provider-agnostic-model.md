# 0014. Provider-agnostic model through `FITIT_MODEL`

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** Martín

## Context

[ADR 0008](0008-pydantic-ai-for-the-coding-agent.md) picked Pydantic AI so that changing provider is one setting, but only the Anthropic client was installed and only `ANTHROPIC_API_KEY` reached the container. Teams running Fit-it may have keys for another provider, or none and a local model.

## Decision

`FITIT_MODEL` accepts any Pydantic AI 2.x `provider:model` string. The core installs `pydantic-ai-slim[anthropic,openai,google,groq,mistral]` and `compose.yaml` passes through, without values, the variables those providers read: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `GOOGLE_API_KEY`, `GEMINI_API_KEY`, `GROQ_API_KEY`, `MISTRAL_API_KEY`, `OLLAMA_BASE_URL`. The default stays `anthropic:claude-opus-5-5`.

## Consequences

- Switching provider is two exports in the shell; no code change.
- The `openai` extra also covers OpenAI-compatible endpoints (`ollama:`, `openrouter:`, or `openai:` with `OPENAI_BASE_URL`).
- A missing key or unknown provider ends the turn with an `error` naming the variable to set, as for any other failure.
- Hook quality depends on the model; only the default is tested end to end.

## Alternatives considered

- **Full `pydantic-ai` package:** pulls every provider plus extras we do not use (evals, MCP, CLI), a heavier image for no gain.
- **Anthropic only:** contradicts ADR 0008 and blocks teams without an Anthropic key.
