# grc-policy-server

FastAPI service for GRC policy ingestion and policy comparison.

## Features

- Upload one or many policy documents in one request (`POST /documents/upload`).
- Delete one or many documents and their Weaviate chunks (`POST /documents/delete`).
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
   - `documents.router` (`/documents`, `/documents/upload`, `/documents/delete`)
   - `compare.router` (`/compare`)
   - `with_summary.router` (`/compare/with-summary`)
4. `run()` starts Uvicorn with configured `host`, `port`, `log_level`, and `debug` reload mode.

The dependency graph for routes is wired in `src/grc_policy_server/api/deps.py`.

## API endpoints

Authentication: all endpoints except `/health`, `/docs`, `/redoc`, and `/openapi.json` require `Authorization: Bearer <API_BEARER_TOKEN>`.
In Swagger (`/docs`), use the `Authorize` button and paste the token value (without the `Bearer ` prefix).

- `GET /health`
  - Basic service health.

- `GET /documents`
  - Lists uploaded document metadata from `upload_root`.

- `POST /documents/upload`
  - Uploads one or more documents in a single request.
  - Use multipart form-data and repeat the `file` field for batch upload.
  - Returns per-file ingestion status.

- `POST /documents/delete`
  - Deletes one or more documents by `documentIds`.
  - Removes local document artifacts and matching Weaviate chunk records.
  - Returns per-document deletion status with deleted chunk counts.

- `POST /compare`
  - Compares two documents and returns structured differences.

- `POST /compare/with-summary`
  - Runs streamed comparison and returns summarized result payload.

## Upload API contract

`POST /documents/upload` request (multipart):

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

Swagger UI:

- [http://localhost:8000/docs](http://localhost:8000/docs)

## Tests and lint

```bash
make test
make lint
```

## Infrastructure

`docker-compose.yml` includes:

- `neo4j`
- `weaviate`

Start with:

```bash
docker-compose up --build
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
