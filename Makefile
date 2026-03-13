.PHONY: dev test lint

dev:
	./scripts/dev.sh

test:
	uv run pytest

lint:
	uv run ruff check src tests
