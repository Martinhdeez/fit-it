# Fit-it

Every user gets an app that fits them. Zero code changes.

Fit-it is a reverse proxy that sits in front of any existing web app. Users describe a change in plain language ("apply a 15% discount to Friday invoices", "flag patients with debt over 500 EUR"), a coding agent compiles it into a small Python hook scoped to that user, and the proxy applies it to every request and response from then on. The original app is never modified.

## How it works

1. The browser talks only to the Fit-it core, never directly to the client app.
2. The core loads the user's hooks from `/workspaces/{user_id}/`.
3. The request is forwarded to the original app.
4. The app returns its normal HTML or JSON.
5. The hook runner mutates the JSON with the user's hooks, or injects the `embed.js` chat widget into the HTML.
6. The personalized response goes back to the user.

Hooks that need more data call the app's own API with the user's own session (`ctx.api`), so a hook can never see more than that user already can. The agent learns the available endpoints from the app's OpenAPI spec. Every change is traced: prompt, generated code, validation result and diff.

Architecture diagrams live in `.artifacts/pages/` (open the HTML files in a browser). `reverse-proxy-flow.html` is the current design.

## Status

Hackathon prototype (GDG Coruña, October 2026). `backend/` holds the first iteration: a per-user hook pipeline with an AST guard and fail-closed execution.

```bash
export ANTHROPIC_API_KEY=...                            # in your shell, never in a file
docker compose up --build                               # API on :8000
docker compose run --rm core uv run python check.py     # self-check
```

### Choosing the model

`FITIT_MODEL` takes any Pydantic AI `provider:model` string; export the key that provider reads ([ADR 0014](docs/adr/0014-provider-agnostic-model.md)).

```bash
export ANTHROPIC_API_KEY=...                                 # default: anthropic:claude-opus-5-5
export FITIT_MODEL=openai:gpt-5 OPENAI_API_KEY=...           # OpenAI
export FITIT_MODEL=ollama:qwen3 OLLAMA_BASE_URL=http://host.docker.internal:11434/v1   # local Ollama
```

Contribution rules (Git Flow, Conventional Commits, PRs) are in `AGENTS.md`.
