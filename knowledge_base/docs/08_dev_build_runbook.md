# 08 - Development Build Runbook

## Goal

A developer or coding agent should be able to run the local system on a workstation with no external API dependencies.

## Development assumptions

- Linux or Windows with WSL2 is preferred.
- Docker and Docker Compose are installed.
- Nvidia container runtime is configured for GPU workloads.
- Model files are already downloaded into local `models/` directory.
- Python dependencies are available through a local wheelhouse or pre-built container image.

## Repository layout

```text
grc-auditor/
  app/
  frontend/
  workers/
  migrations/
  tests/
  scripts/
  models/
  sample_docs/
  docker/
  docker-compose.dev.yml
  docker-compose.prod.yml
  Makefile
```

## Environment variables

```bash
APP_ENV=dev
APP_HOST=0.0.0.0
APP_PORT=8000
DATABASE_URL=postgresql://grc:grc@postgres:5432/grc
REDIS_URL=redis://redis:6379/0
OBJECT_STORE_MODE=filesystem
OBJECT_STORE_PATH=/data/object_store
VECTOR_BACKEND=weaviate
WEAVIATE_URL=http://weaviate:8080
LLM_PROVIDER=ollama
LLM_BASE_URL=http://ollama:11434
LLM_MODEL=granite3.3:8b
EMBEDDING_MODEL=qwen3-embedding-0.6b
RERANKER_MODEL=qwen3-reranker-0.6b
OCR_LANGUAGES=eng,deu,fra
OFFLINE_MODE=true
DISABLE_TELEMETRY=true
```

## Makefile targets

```makefile
up:
	docker compose -f docker-compose.dev.yml up -d

down:
	docker compose -f docker-compose.dev.yml down

logs:
	docker compose -f docker-compose.dev.yml logs -f

migrate:
	docker compose -f docker-compose.dev.yml exec api alembic upgrade head

test:
	docker compose -f docker-compose.dev.yml exec api pytest -q

seed:
	docker compose -f docker-compose.dev.yml exec api python scripts/seed_sample_project.py
```

## Development startup sequence

1. Start dependencies.
2. Run database migrations.
3. Start model server.
4. Verify model health.
5. Start API and workers.
6. Upload sample PDF pair.
7. Verify extraction job.
8. Verify CIR objects.
9. Run comparison.
10. Review cited changes.

## Health checks

```text
GET /health
GET /health/db
GET /health/queue
GET /health/vector
GET /health/model
GET /health/storage
```

## Sample development data

Use a small synthetic PDF pair first:

- 5 pages.
- 3 nested sections.
- 2 tables.
- 1 multi-page table.
- 5 known changes.
- Known answer file.

Then test real-world documents after the deterministic pipeline passes.

## Agent-friendly development process

Agents should implement vertical slices:

1. Upload PDF and store hash.
2. Extract pages and blocks.
3. Persist minimal CIR.
4. Display PDF page with citation highlight.
5. Extract one simple table.
6. Normalize one table row.
7. Compare one table row between versions.
8. Generate one evidence pack.
9. Generate one local LLM explanation.
10. Export result.

Avoid building all infrastructure before one cited change works end-to-end.

## Local quality gates

Run before merging:

```bash
ruff check app tests
mypy app
pytest -q
pytest tests/integration -q
```

For extraction tests, store expected snapshots:

```text
tests/fixtures/sample_emc_v1.pdf
tests/fixtures/sample_emc_v2.pdf
tests/expected/sample_emc_v1_cir.json
tests/expected/sample_emc_comparison.json
```

## Debugging table extraction

Create a table debug artifact per extraction:

```text
extract_debug/
  page_044.png
  page_044_blocks.json
  page_044_tables.json
  page_044_overlay.png
```

The overlay should show:

- Block boundaries.
- Table bounding boxes.
- Cell bounding boxes.
- Row IDs.
- Reading order.

## Development shortcuts allowed

- Use local filesystem instead of MinIO.
- Use Ollama instead of production model server.
- Use a small embedding model.
- Use synthetic PDFs.
- Use one worker process.

## Development shortcuts not allowed

- No cloud LLM fallback.
- No uncited comparison output.
- No translation-before-comparison.
- No vector-only diff.
- No deleting original PDF or raw extractor output.
