# grc-policy-server

FastAPI service for GRC policy ingestion and policy comparison.

## Features

- Upload one or many policy documents in one request (`POST /documents/upload`).
- Ingest documents from remote sources (HTTP/S, S3, Azure Blob, Google Drive) (`POST /documents/ingest/sources`).
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
   - `documents.router` (`/documents`, `/documents/{document_id}/download`, `/documents/upload`, `/documents/ingest/sources`, `/documents/upload/v2`, `/documents/upload/v2/{job_id}`, `/documents/delete`, `/documents/search/hybrid`)
   - `compare.router` (`/compare`)
   - `with_summary.router` (`/compare/with-summary`)
   - `storage_providers.router` (`/storage/providers`)
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

- `POST /documents/ingest/sources`
  - Ingests one or more documents from remote URIs (for example `https://...`, `s3://...`, `azblob://...`, Google Drive share links).
  - Optional `providerId` can be supplied per source to use stored credentials/config.

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

- `POST /v4/compare/stream`
  - Department-specific streaming comparison (SSE). Preferred endpoint for interactive frontends.
  - Two-stage stream: per-diff table rows (Stage 1) then an aggregated narrative summary (Stage 2).
  - See [Compare V4 streaming API](#compare-v4-streaming-api) below for the full contract.

- `GET /storage/providers`, `POST /storage/providers`, `PUT /storage/providers/{provider_id}`, `DELETE /storage/providers/{provider_id}`
  - Create/list/update/delete storage provider configurations (used by `/documents/ingest/sources`).

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

## Compare V4 streaming API

`POST /v4/compare/stream` streams a two-stage department-aware comparison as SSE.

### Request

```json
{
  "doc1Id": "<uuid>",
  "doc2Id": "<uuid>",
  "testingDepartment": "EMC",
  "forceReExtract": false
}
```

`testingDepartment` must be one of `"EMC"`, `"Safety"`, or `"Environment"`.

### curl example

```bash
curl -N -X POST http://localhost:8000/v4/compare/stream \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"doc1Id":"<id1>","doc2Id":"<id2>","testingDepartment":"EMC"}'
```

### Event reference

All events are `data: {json}\n\n` SSE lines.

| Event type | Key fields | Description |
|---|---|---|
| `payload` | `doc1_id`, `doc2_id`, `testing_department` | First event; echoes request context |
| `progress` | `stage`, `message`, `total?` | Stage transitions: `loading` → `streaming_diffs` → `summarizing` |
| `diff_start` | `change_id`, `section`, `page`, `change_type`, `node_type`, `doc1_preview`, `doc2_preview` | Emitted before LLM call for each diff |
| `diff_token` | `change_id`, `token` | One event per LLM output token for this diff row |
| `diff_complete` | `change_id`, `row_markdown`, `requires_review: bool`, `skipped: bool` | Final row result; `skipped=true` means no semantic change found |
| `table_complete` | `markdown`, `rows_analyzed`, `rows_skipped`, `review_count` | Full assembled markdown diff table |
| `summary_token` | `token` | One event per LLM output token for the aggregated summary |
| `summary_complete` | `text` | Complete aggregated summary text |
| `done` | `total_diffs`, `analyzed`, `skipped`, `review_count`, `requires_human_review`, `accuracy_metrics` | Final event |
| `error` | `error` | Emitted if the comparison fails |

### Table format

`table_complete.markdown` and individual `diff_complete.row_markdown` values use this format:

```markdown
| Section | Page | Change | Semantic Difference |
|---------|------|--------|---------------------|
| 4.2.1 Emissionsgrenzwerte | p.23 | Modified | Conducted emissions limit tightened from 79 dBµA to 73 dBµA across 0.1–2 MHz. Retest required. |
| 5.1 Test Setup            | p.31 | Added    | Pre-conditioning at 40 °C for 2 h before testing is now mandatory. |
| —                         | —    | Modified | ⚠ HUMAN_REVIEW: Overlapping requirement between sections 3.4 and 3.8 — cannot determine precedence. |
```

Rows prefixed with `⚠` require human review. Cosmetic/whitespace-only diffs are silently omitted from the table.

### Compared to V3

| Feature | `/v3/compare/stream` | `/v4/compare/stream` |
|---|---|---|
| Department-aware prompting | No | Yes (`testingDepartment`) |
| LLM output per diff | Markdown summary + JSON change record | Single semantic sentence (table row) |
| Skip cosmetic diffs | No | Yes |
| Human-review flagging | Via change record `status` field | Inline `⚠` + `requires_review: true` |
| Aggregated summary | At `done` event | Streamed via `summary_token` events |
| Client table assembly | Not required | Rows streamed individually; full table at `table_complete` |

## Environment Variables

Key runtime variables (all available in `.env.example`):

- `APP_NAME`, `ENVIRONMENT`, `LOG_LEVEL`
- `HOST`, `PORT`, `DEBUG`
- `API_BEARER_TOKEN`
- `CORS_ALLOW_ORIGINS`, `CORS_ALLOW_METHODS`, `CORS_ALLOW_HEADERS`, `CORS_ALLOW_CREDENTIALS`
- `UPLOAD_ROOT`
- `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `DATABASE_URL`
- `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CELERY_DEFAULT_QUEUE`
- `CELERY_TASK_TIMEOUT_SEC`, `CELERY_TASK_SOFT_TIME_LIMIT_SEC`, `CELERY_TASK_HARD_TIME_LIMIT_SEC`
- `CELERY_WORKER_PING_TIMEOUT_SEC`, `CELERY_ENFORCE_WORKER_PING`, `CELERY_WORKER_CONCURRENCY`, `CELERY_WORKER_POOL`
- `CELERY_WORKER_PREFETCH_MULTIPLIER`, `CELERY_WORKER_MAX_TASKS_PER_CHILD`, `CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB`
- `CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP`, `CELERY_BROKER_POOL_LIMIT`, `CELERY_RESULT_EXPIRES_SEC`
- `CELERY_TASK_TRACK_STARTED`, `CELERY_TASK_REJECT_ON_WORKER_LOST`, `CELERY_WORKER_DISABLE_RATE_LIMITS`
- `DOCLING_ACCELERATOR_DEVICE`, `DOCLING_ACCELERATOR_THREADS`, `DOCLING_CUDA_USE_FLASH_ATTENTION2`
- `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`
- `WEAVIATE_URL`, `WEAVIATE_COLLECTION`, `WEAVIATE_EMBEDDED`
- `WEAVIATE_VECTORIZER`, `WEAVIATE_HUGGINGFACE_MODEL`, `WEAVIATE_HUGGINGFACE_ENDPOINT_URL`
- `OLLAMA_URL`, `OLLAMA_CHAT_MODEL`, `OLLAMA_EMBED_MODEL`, `OLLAMA_TIMEOUT_SEC`
- `LLM_PRIMARY_PROVIDER`, `VLLM_CHAT_URL`, `VLLM_EMBED_URL`, `VLLM_CHAT_MODEL`, `VLLM_EMBED_MODEL`, `VLLM_TIMEOUT_SEC`
- `OPIK_ENABLED`, `OPIK_URL_OVERRIDE`, `OPIK_PROJECT_NAME`, `OPIK_WORKSPACE`
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

### Optional extras

The project has three optional dependency groups. None are installed by default.

| Extra | Packages | When to use |
|---|---|---|
| `table-extraction` | `camelot-py`, `pdf2image`, `opencv-python-headless` | Better PDF table extraction via Camelot |
| `cuda` | `torch`, `flash-attn` | NVIDIA GPU acceleration for Docling (requires CUDA) |
| `gmft` | `torch`, `transformers`, `Pillow` | GMFT-based table analysis |

```bash
# Camelot table extraction (CPU, all platforms)
make install-table-extraction
# or: uv sync --extra table-extraction

# FlashAttention-2 (NVIDIA GPU + CUDA required — do not run on macOS)
make install-cuda
# or: uv sync --extra cuda
# If uv cannot resolve flash-attn without CUDA headers:
# pip install flash-attn --no-build-isolation
```

After installing `cuda`, enable it in `.env`:

```bash
DOCLING_CUDA_USE_FLASH_ATTENTION2=true
DOCLING_ACCELERATOR_DEVICE=cuda
```

If `flash-attn` is not installed but `DOCLING_CUDA_USE_FLASH_ATTENTION2=true` is set, the server logs a warning and falls back to standard attention — ingestion still succeeds.

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

The server and Celery workers can send LLM traces to [Opik](https://github.com/comet-ml/opik). The compose integration pins Opik to `1.10.46` and pins its backing services instead of using floating `latest` tags.

### What is traced

When `OPIK_ENABLED=true`, Ollama LLM calls are captured as spans:

| Span | Tags | Description |
|------|------|-------------|
| `ollama_generate` | `llm`, `ollama` | Raw Ollama `/api/generate` call (prompt + response) |
| `ollama_embed` | `llm`, `ollama` | Raw Ollama `/api/embed` call (embedding input + response) |

These spans cover semantic extraction, change-record summarization, markdown diff summaries, follow-up generation, and any other code path that goes through `OllamaClient`.

### Accessing the Opik UI

1. Start the main dependencies and Opik:
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.opik.yml up -d
   ```
2. Open **[http://localhost:5173](http://localhost:5173)** in your browser.
3. Enable tracing in `.env`:
   ```bash
   OPIK_ENABLED=true
   OPIK_URL_OVERRIDE=http://localhost:5173/api
   OPIK_PROJECT_NAME=grc-policy-server
   OPIK_WORKSPACE=default
   ```
4. Restart the API and Celery workers so they pick up the Opik settings.

You will see traces with prompt payloads, model responses, latency, and errors for the LLM calls made during ingestion and comparison.

### Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `OPIK_ENABLED` | `false` | Set to `true` to enable SDK tracing |
| `OPIK_URL_OVERRIDE` | `http://localhost:5173/api` | Opik API URL through the local frontend proxy |
| `OPIK_PROJECT_NAME` | `grc-policy-server` | Project name in the Opik UI |
| `OPIK_WORKSPACE` | `default` | Workspace used by the Opik SDK |
| `OPIK_VERSION` | `1.10.46` | Opik backend/frontend/python-backend image version |

When running the API or worker on the host, use `OPIK_URL_OVERRIDE=http://localhost:5173/api`.
When running the API or worker inside the same Docker network, use `OPIK_URL_OVERRIDE=http://opik-frontend:5173/api` or `http://opik-backend:8080`.

### Evaluating LLM accuracy

From the Opik UI you can:
- Browse individual traces to inspect prompts and model outputs side-by-side.
- Filter by tag (`semantic-extraction`, `summarization`, `generation`) to focus on a specific operation type.
- Compare latency across runs to detect regressions.
- Add manual annotations or automated evaluations against a golden dataset.

## Docling JobKit

[Docling JobKit](https://docling-project.github.io/docling/usage/jobkit/) is useful for scaling ingestion beyond interactive uploads. It does not replace the current Docling extraction path or the canonical PostgreSQL comparison store; it provides a job runner layer around Docling conversion.

What it can enhance:

- Run Docling conversion as distributed jobs with Kubeflow Pipelines, Ray, or a local runner.
- Pull source documents from HTTP endpoints, S3, or Google Drive.
- Export converted Docling outputs to targets such as S3.
- Keep conversion options, OCR settings, source locations, and target locations in a job configuration file.

How it fits this service:

- Use JobKit for bulk import and conversion when documents come from object storage or shared drives.
- Feed JobKit outputs into the same canonical ingestion flow: raw Docling JSON -> normalized node tree -> PostgreSQL canonical store -> Weaviate retrieval chunks.
- Keep comparison unchanged: compare canonical nodes, not retrieval chunks.

## Infrastructure

`docker-compose.yml` includes:

- `postgres`
- `redis`
- `weaviate`

### Docker builds

**Standard image (CPU only):**

```bash
docker build -t grc-policy-server .
```

**With Camelot table extraction:**

```bash
docker build --build-arg EXTRAS=table-extraction -t grc-policy-server:camelot .
```

**GPU image with FlashAttention-2** (`Dockerfile.gpu`) — requires an NVIDIA GPU host with the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html):

```bash
make build-gpu
# or: docker build -f Dockerfile.gpu -t grc-policy-server:gpu .
```

The GPU image uses `nvidia/cuda:12.6.3-devel-ubuntu24.04` as its base so that `flash-attn` can compile against the CUDA headers at build time. `DOCLING_CUDA_USE_FLASH_ATTENTION2=true` is set automatically inside the image.

To run the GPU worker alongside the standard services, uncomment the `worker-gpu` service block in `docker-compose-prod.yml` and start it:

```bash
docker compose -f docker-compose-prod.yml up -d worker-gpu
```

`docker-compose.opik.yml` adds the optional Opik observability stack:

- `opik-backend`
- `opik-frontend`
- `opik-python-backend`
- `opik-mysql`
- `opik-clickhouse`
- `opik-zookeeper`
- `opik-redis`
- `opik-minio`

Start with:

```bash
docker compose up -d
```

Start with Opik:

```bash
docker compose -f docker-compose.yml -f docker-compose.opik.yml up -d
```

Stop the current stack:

```bash
docker compose -f docker-compose.yml -f docker-compose.opik.yml down
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
