.PHONY: dev test lint install-table-extraction install-cuda build-gpu

dev:
	./scripts/dev.sh

test:
	uv run pytest

lint:
	uv run ruff check src tests

install-table-extraction:
	uv sync --extra table-extraction

install-cuda:
	uv sync --extra cuda

build-gpu:
	docker build -f Dockerfile.gpu -t grc-policy-server:gpu .
