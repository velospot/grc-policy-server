# grc-policy-server

FastAPI service for GRC policy ingestion and policy comparison.

## Features

- Upload one or many policy documents in one request (`POST /documents/upload`).
- Upload via Celery workers (`POST /documents/upload/v2`) with production-oriented queue settings.
- Download a stored PDF (default or filename-specific) (`GET /documents/{document_id}/download`).
- Delete one or many documents and their Weaviate chunks (`POST /documents/delete`).
- Hybrid-search two documents for matching chunks (`POST /documents/search/hybrid`).
- Serve independent requests concurrently (no global write mutex bottleneck).
- Convert documents with Docling and chunk for retrieval pipelines.
- Persist chunk vectors in Weaviate for semantic/hybrid search.
- Persist upload metadata on disk for document listing (`GET /documents`).
- Compare two document versions and return structured GRC deltas (`POST /compare`).
- Produce summarized comparison output from streamed diff events (`POST /compare/with-summary`).
- Auto-generated OpenAPI docs via Swagger/ReDoc.

## Runtime flow (starting at `main.py`)

Entry point: `src/grc_policy_server/main.py`

1. `setup_logging(...)` configures service logging from environment settings.
2. `FastAPI(...)` app is created with OpenAPI metadata.
3. Routers are registered:
   - `health.router` (`/health`)
   - `documents.router` (`/documents`, `/documents/{document_id}/download`, `/documents/upload`, `/documents/upload/v2`, `/documents/upload/v2/{job_id}`, `/documents/delete`, `/documents/search/hybrid`)
   - `compare.router` (`/compare`)
   - `with_summary.router` (`/compare/with-summary`)
4. `run()` starts Uvicorn with configured `host`, `port`, `log_level`, and `debug` reload mode.
5. Request handling is lock-free at the app level so independent requests can run concurrently.

The dependency graph for routes is wired in `src/grc_policy_server/api/deps.py`.

## API endpoints

Authentication: all endpoints except `/health`, `/docs`, `/redoc`, and `/openapi.json` require `Authorization: Bearer <API_BEARER_TOKEN>`.
In Swagger (`/docs`), use the `Authorize` button and paste the token value (without the `Bearer ` prefix).

- `GET /health`
  - Basic service health.

- `GET /documents`
  - Lists uploaded document metadata from `upload_root`.

- `GET /documents/{document_id}/download`
  - Downloads a stored PDF for a document.
  - Optional query `filename=<pdf-name>` fetches a specific PDF file in that document directory.

- `POST /documents/upload`
  - Uploads one or more documents in a single request.
  - Use multipart form-data and repeat the `file` field for batch upload.
  - Returns per-file ingestion status.

- `POST /documents/upload/v2`
  - Same multipart request contract as `POST /documents/upload`.
  - Queues a Celery job and returns a `jobId`.
  - Fails with `503` if no active Celery worker is available.
  - Optional worker preflight ping is controlled by `CELERY_ENFORCE_WORKER_PING` (default `false`).
  - Platform-aware worker pool defaults: macOS -> `solo`, Linux -> `prefork`.

- `GET /documents/upload/v2/{job_id}`
  - Polls upload job status.
  - Returns `queued`, `running`, `finished`, or `failed`.
  - Includes final upload result when status is `finished`.

- `POST /documents/delete`
  - Deletes one or more documents by `documentIds`.
  - Removes local document artifacts and matching Weaviate chunk records.
  - Returns per-document deletion status with deleted chunk counts.

- `POST /documents/search/hybrid`
  - Runs hybrid search in Weaviate for both `documentId1` and `documentId2`.
  - Uses one shared `query` and returns matched chunks grouped by document.

- `POST /compare`
  - Compares two documents and returns structured differences.

- `POST /compare/with-summary`
  - Runs streamed comparison and returns summarized result payload.

## Upload API contract

`POST /documents/upload` and `POST /documents/upload/v2` request (multipart):

- `file`: one or more files (repeat field for multiple uploads)

Response shape:

```json
{
  "acceptedCount": 2,
  "rejectedCount": 1,
  "results": [
    {
      "filename": "policy-v1.pdf",
      "contentType": "application/pdf",
      "accepted": true,
      "documentId": "...",
      "chunksStored": 42,
      "error": null
    },
    {
      "filename": "broken.pdf",
      "contentType": "application/pdf",
      "accepted": false,
      "documentId": null,
      "chunksStored": null,
      "error": "Uploaded file is empty"
    }
  ]
}
```

`POST /documents/upload/v2` response shape:

```json
{
  "jobId": "upload-job-123",
  "status": "queued"
}
```

`GET /documents/upload/v2/{job_id}` response shape:

```json
{
  "jobId": "upload-job-123",
  "status": "finished",
  "done": true,
  "result": {
    "acceptedCount": 1,
    "rejectedCount": 0,
    "results": []
  },
  "error": null
}
```

## Delete API contract

`POST /documents/delete` request:

```json
{
  "documentIds": ["doc-1", "doc-2"]
}
```

Response shape:

```json
{
  "deletedCount": 1,
  "failedCount": 1,
  "results": [
    {
      "documentId": "doc-1",
      "deleted": true,
      "deletedChunks": 12,
      "error": null
    },
    {
      "documentId": "doc-2",
      "deleted": false,
      "deletedChunks": 0,
      "error": "Document not found"
    }
  ]
}
```

## Environment Variables

Key runtime variables (all available in `.env.example`):

- `APP_NAME`, `ENVIRONMENT`, `LOG_LEVEL`
- `HOST`, `PORT`, `DEBUG`
- `API_BEARER_TOKEN`
- `CORS_ALLOW_ORIGINS`, `CORS_ALLOW_METHODS`, `CORS_ALLOW_HEADERS`, `CORS_ALLOW_CREDENTIALS`
- `UPLOAD_ROOT`
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_DEFAULT_QUEUE`
- `CELERY_TASK_TIMEOUT_SEC`, `CELERY_TASK_SOFT_TIME_LIMIT_SEC`, `CELERY_TASK_HARD_TIME_LIMIT_SEC`
- `CELERY_WORKER_PING_TIMEOUT_SEC`, `CELERY_ENFORCE_WORKER_PING`, `CELERY_WORKER_CONCURRENCY`, `CELERY_WORKER_POOL`
- `CELERY_WORKER_PREFETCH_MULTIPLIER`, `CELERY_WORKER_MAX_TASKS_PER_CHILD`, `CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB`
- `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP`, `CELERY_BROKER_POOL_LIMIT`, `CELERY_RESULT_EXPIRES_SEC`
- `CELERY_TASK_TRACK_STARTED`, `CELERY_TASK_REJECT_ON_WORKER_LOST`, `CELERY_WORKER_DISABLE_RATE_LIMITS`
- `DOCLING_ACCELERATOR_DEVICE`, `DOCLING_ACCELERATOR_THREADS`, `DOCLING_CUDA_USE_FLASH_ATTENTION2`
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
- `WEAVIATE_URL`, `WEAVIATE_COLLECTION`, `WEAVIATE_EMBEDDED`
- `OLLAMA_URL`, `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBED_MODEL`, `OLLAMA_TIMEOUT_SEC`
- `MONGODB_URI`, `MONGODB_DATABASE`, `MONGODB_COLLECTION`
- `DOWNLOAD_TIMEOUT_SECONDS`, `MAX_DOWNLOAD_MB`, `EMBED_BATCH_SIZE`

## Core modules

- `src/grc_policy_server/api/routes/`
  - HTTP route handlers.
- `src/grc_policy_server/services/ingestion/document_ingestion_service.py`
  - Docling conversion, chunking, vector upsert, metadata persistence.
- `src/grc_policy_server/services/vector/weaviate_client.py`
  - Weaviate integration.
- `src/grc_policy_server/services/graph/graph_neo4j_client.py`
  - Neo4j integration.
- `src/grc_policy_server/services/comparision/`
  - Policy diff/comparison engines.
- `src/grc_policy_server/respositories/documents.py`
  - Uploaded document metadata listing.

## Local development

### Prerequisites

- Python 3.13+
- `uv`
- Docker (optional, for dependencies/services)

### Install dependencies

```bash
uv sync --dev
```

### Configure environment

```bash
cp .env.example .env
```

Update values in `.env` as needed.

### Run the API

```bash
make dev
```

Or directly:

```bash
uv run uvicorn grc_policy_server.main:app --reload
```
or
```bash
./scripts/dev.sh
```
This starts both `uvicorn` and the Celery worker with platform-aware defaults and GPU-aware Docling accelerator selection (`cuda`/`mps`/`auto`).
When `CELERY_WORKER_POOL` / `CELERY_WORKER_CONCURRENCY` are unset (or `null`), defaults are selected automatically per platform.

To run only Celery worker:

```bash
POOL="${CELERY_WORKER_POOL:-$( [ "$(uname -s)" = "Darwin" ] && echo solo || echo prefork )}"
uv run celery -A grc_policy_server.worker:celery_app worker --loglevel=INFO --concurrency="${CELERY_WORKER_CONCURRENCY:-1}" --pool="${POOL}"
```

Swagger UI:

- [http://localhost:8000/docs](http://localhost:8000/docs)

## Tests and lint

```bash
make test
make lint
```

## LLM Observability (Opik)

The server and Celery workers are fully instrumented with [Opik 1.10.58](https://github.com/comet-ml/opik) for LLM tracing and accuracy monitoring.

### What is traced

Every LLM operation is captured as a named span:

| Span | Tags | Description |
|------|------|-------------|
| `extract_policy_meanings` | `llm`, `semantic-extraction` | Batch semantic clause extraction |
| `summarize_diff` | `llm`, `summarization` | Single section diff summary |
| `summarize_changes` | `llm`, `summarization` | Executive changes summary |
| `summarize_explanations` | `llm`, `summarization` | Aggregated explanation summary |
| `generate_followups` | `llm`, `generation` | Follow-up question generation |
| `generate_markdown_diff_summary` | `llm`, `generation` | Markdown-formatted diff summary |
| `ollama_generate` | `llm`, `ollama` | Raw Ollama `/api/generate` call (prompt + response) |

Workers initialize Opik automatically via the `worker_init` Celery signal, so traces from background jobs appear in the same project.

### Accessing the Opik UI

1. Start infrastructure (includes Opik):
   ```bash
   docker-compose up -d
   ```
2. Open **[http://localhost:5173](http://localhost:5173)** in your browser.
3. Select the **`grc-policy-server`** project from the left sidebar.

You will see a trace list with latency, input prompts, model responses, and nested spans showing the full call chain for each request.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPIK_ENABLED` | `true` | Set to `false` to disable all tracing |
| `OPIK_URL` | `http://localhost:5174` | Opik backend API URL (direct, bypasses nginx) |
| `OPIK_PROJECT_NAME` | `grc-policy-server` | Project name in the Opik UI |

When running the API or worker on the host (outside Docker), set `OPIK_URL=http://localhost:5174` in your `.env`.
When running in Docker with the shared network, set `OPIK_URL=http://opik-backend:8080`.

### Evaluating LLM accuracy

From the Opik UI you can:
- Browse individual traces to inspect prompts and model outputs side-by-side.
- Filter by tag (`semantic-extraction`, `summarization`, `generation`) to focus on a specific operation type.
- Compare latency across runs to detect regressions.
- Add manual annotations or automated evaluations against a golden dataset.

## Infrastructure

`docker-compose.yml` includes:

- `redis`
- `weaviate`
- `opik` (MySQL + ClickHouse + backend + frontend)

Start with:

```bash
docker-compose up -d
```

## TODOs

- Re-enable and validate Neo4j chunk upsert in ingestion once graph schema is finalized.
- Add integration tests for upload+ingestion against local Weaviate and Neo4j containers.
- Improve metadata persistence abstraction (allow pluggable storage backend).
- Add request/response validation tests for error-path upload outcomes in batch mode.

## Contributing

1. Create a branch from `main` (recommended prefix: `codex/`).
2. Run setup and checks:
   - `uv sync --dev`
   - `make test`
3. Keep changes scoped, add/adjust tests, and update README/API contract when behavior changes.
4. Open a PR with a concise change summary and verification notes.

## Acknowledgements

- [FastAPI](https://fastapi.tiangolo.com/) for API framework and OpenAPI tooling.
- [Docling](https://github.com/docling-project/docling) for document conversion/chunking pipeline building blocks.
- [Weaviate](https://weaviate.io/) for vector storage and retrieval.
- [Neo4j](https://neo4j.com/) for graph-based document relation modeling.
- [Ollama](https://ollama.com/) for local model serving.
