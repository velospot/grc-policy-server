.PHONY: dev test lint

dev:
	uv sync
	uv run uvicorn grc-policy-server.main:app --reload

test:
	uv run pytest

lint:
	uv run ruff check src tests
