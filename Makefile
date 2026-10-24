.PHONY: check eval up down

# Runs every test under tests/ in the core image, with the demo service up.
# Model keys are blanked so no test can reach a real model.
check:
	MSYS_NO_PATHCONV=1 docker compose run --rm --build --no-TTY \
		-e ANTHROPIC_API_KEY= -e OPENAI_API_KEY= -e GOOGLE_API_KEY= -e GEMINI_API_KEY= \
		-e GROQ_API_KEY= -e MISTRAL_API_KEY= \
		-e DEMO_URL=http://demo:8001 \
		-e OTEL_EXPORTER_OTLP_ENDPOINT= -e OTEL_EXPORTER_OTLP_TRACES_ENDPOINT= \
		-e PYTHONPATH=/app:/evals \
		-v ./tests:/tests:ro -v ./evals:/evals:ro \
		core uv run --frozen pytest -p no:cacheprovider /tests

# Runs the real-model eval set (docs/specs/evals.md). Costs tokens; never part of `check`.
# FITIT_MODEL and the model keys come from the shell through compose.yaml; EVAL_CASES picks case ids.
eval:
	docker compose run --rm --build --no-TTY \
		-e EVAL_CASES="$(EVAL_CASES)" \
		-e PYTHONPATH=/app:/evals \
		-v ./evals:/evals:ro \
		core uv run --frozen python /evals/run_evals.py

up:
	docker compose up --build

down:
	docker compose down
