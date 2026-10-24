.PHONY: check up down

# Runs every test under tests/ in the core image, with the demo service up.
# ANTHROPIC_API_KEY is blanked so no test can reach a real model.
check:
	docker compose run --rm --build --no-TTY \
		-e ANTHROPIC_API_KEY= \
		-e DEMO_URL=http://demo:8001 \
		-e PYTHONPATH=/app \
		-v ./tests:/tests:ro \
		core uv run --frozen pytest -p no:cacheprovider /tests

up:
	docker compose up --build

down:
	docker compose down
