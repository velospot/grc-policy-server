## TO DO
- [ ] compare api
- [ ] revalidate schema and logic.


# grc-policy-server

Production‑grade Python service template using **uv**, designed to run consistently across local development, CI, and containerized environments.

This repository provides:

* Modern dependency management with `uv`
* Typed, environment‑driven configuration with sensible defaults
* Docker and Docker Compose support
* A clean FastAPI service skeleton
* Optional, open‑source LLM observability and monitoring

The goal is not cleverness. The goal is *predictability under pressure*.

---

## Requirements

Before you begin, ensure you have:

* Python **3.11+**
* Docker (optional, required for containerized runs)
* Git (recommended)

Verify Python:

```bash
python --version
```

---

## Install `uv`

`uv` replaces `pip`, `virtualenv`, and most waiting.

```bash
pip install --upgrade uv
```

Verify:

```bash
uv --version
```

---

## Project Structure

```
my_uv_project/
├── pyproject.toml        # Dependencies and project metadata
├── uv.lock               # Locked, reproducible dependency graph
├── Dockerfile
├── docker-compose.yml
├── .env.example          # Documented environment variables
├── src/
│   └── my_uv_project/
│       ├── main.py       # Application entrypoint
│       ├── config.py     # Environment-based configuration
│       └── health.py     # Health check endpoint
│       └── logging.py    # logging
├── tests/
│   └── test_health.py
└── README.md
```

---

## Setup (Local Development)

### 1. Clone the repository

```bash
git clone <repo-url>
cd my_uv_project
```

### 2. Install dependencies

```bash
uv sync --dev
```

This will:

* Resolve dependencies
* Create an isolated environment
* Generate `uv.lock`

Commit `uv.lock`. It is the source of truth for production.

---

## Environment Configuration

All configuration is driven by environment variables with defaults.

### 1. Create a local `.env` file

```bash
cp .env.example .env
```

Edit as needed:

```env
APP_NAME=my-uv-project
ENVIRONMENT=development
LOG_LEVEL=info
PORT=8000
DEBUG=true
```

If a variable is not provided, the application falls back to its default value.

---

## Run the Service Locally

```bash
uv run uvicorn my_uv_project.main:app --reload
```

Verify:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "ok"}
```

---

## Run Tests

```bash
uv run pytest
```

---

## Containerized Execution

### Build the image

```bash
docker build -t my-uv-project .
```

### Run with environment variables

```bash
docker run \
  -e PORT=9000 \
  -e LOG_LEVEL=debug \
  -p 9000:9000 \
  my-uv-project
```

Defaults apply for any variables not provided.

---

## Docker Compose (Recommended for Teams)

```bash
docker-compose up --build
```

Docker Compose reads from `.env` and ensures local and container runs behave identically.

---

## Optional: LLM Observability & Monitoring

This project supports **optional, open‑source observability** for applications that call LLMs.

You can enable this later without restructuring the app.

### Supported tooling

* **OpenTelemetry** – request tracing and latency
* **Opik (by Comet ML)** – LLM‑specific observability (prompts, outputs, evaluations)

### When to enable observability

Enable observability if you need:

* Prompt and response inspection
* Latency and cost tracking
* Regression detection
* Debugging non‑deterministic LLM behavior

### How to enable (high level)

1. Add the Opik and OpenTelemetry dependencies
2. Run the Opik backend (Docker Compose or Kubernetes)
3. Configure environment variables pointing to the observability backend
4. Wrap LLM calls with Opik tracing decorators

This setup is entirely optional. If you do nothing, the application runs normally.

---

## Configuration Precedence

From strongest to weakest:

1. Runtime environment variables
2. `.env` file
3. Defaults defined in `config.py`

This ensures predictable behavior across environments.

---

## Production Notes

* Stateless by design
* Configuration via environment variables only
* Health endpoint suitable for load balancers and orchestration
* Deterministic dependency resolution via `uv.lock`

This template is intentionally conservative. It is meant to survive growth, audits, and on‑call rotations.

---

## Update 19th Feb 2026
# grc-policy-server

FastAPI service for GRC policy ingestion and comparison.

## What it does

- Uploads policy documents and ingests them through:
  - Docling conversion
  - Hierarchical chunking
  - Embedding generation
  - Storage in Weaviate (vector) and Neo4j (graph)
- Lists uploaded documents from local metadata.
- Compares two documents using stored chunks and returns:
  - Key differences
  - Summary
  - Action plan
  - Follow-up questions

## API docs (Swagger)

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- OpenAPI JSON: `http://localhost:8000/openapi.json`

## Requirements

- Python `>=3.13` (from `pyproject.toml`)
- `uv`
- Running dependencies:
  - Weaviate
  - Neo4j
  - Ollama (for embeddings/summaries used by ingestion and compare)

## Local setup

1. Install dependencies:

```bash
uv sync --dev
```

2. Copy env file:

```bash
cp .env.example .env
```

3. Start Weaviate + Neo4j:

```bash
docker compose up -d
```

4. Run API:

```bash
make dev
```

## Core environment variables

`Settings` (`src/grc_policy_server/core/config.py`):

- `APP_NAME`
- `ENVIRONMENT`
- `LOG_LEVEL`
- `HOST`
- `PORT`
- `DEBUG`
- `WEAVIATE_URL`
- `WEAVIATE_COLLECTION`
- `WEAVIATE_EMBEDDED`
- `UPLOAD_ROOT` (defaults to `/data/uploads`)

Dependency wiring (`src/grc_policy_server/api/deps.py`):

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`
- `OLLAMA_URL`
- `OLLAMA_CHAT_MODEL`
- `OLLAMA_EMBED_MODEL`
- `OLLAMA_TIMEOUT_SEC`

## Endpoints

### Health

- `GET /health`

Response:

```json
{"status":"ok"}
```

### Documents

- `GET /documents`
  - Lists uploaded documents from metadata under `UPLOAD_ROOT`.
- `POST /documents/upload`
  - Multipart form upload (`file` field).
  - Runs full ingestion pipeline.

Upload response shape:

```json
{
  "filename": "policy.pdf",
  "contentType": "application/pdf",
  "accepted": true,
  "documentId": "uuid",
  "chunksStored": 42
}
```

### Compare

- `POST /compare`
- `POST /compare/with-summary`

Both endpoints accept:

```json
{
  "doc1": {
    "id": "policy-v1",
    "name": "Security Policy",
    "version": "1.0",
    "uploadDate": "2026-02-01",
    "size": "2 MB",
    "category": "security"
  },
  "doc2": {
    "id": "policy-v2",
    "name": "Security Policy",
    "version": "2.0",
    "uploadDate": "2026-02-15",
    "size": "2.2 MB",
    "category": "security"
  }
}
```

They return `ComparisonResult`:
- `summary`
- `keyDifferences`
- `actionPlan`
- `followUpQuestions`

## Upload ingestion flow

`POST /documents/upload` calls `DocumentIngestionService`:

1. Convert upload bytes with `DoclingAdapter`.
2. Chunk docling document via hierarchical chunker.
3. Embed each chunk using Ollama client.
4. Upsert chunk vectors into Weaviate.
5. Upsert document/chunk/section graph into Neo4j.
6. Persist file + `metadata.json` to `UPLOAD_ROOT/<document_id>/`.

## Development commands

- Run app: `make dev`
- Run tests: `make test`
- Lint: `make lint`

## Tests

Current API tests validate:

- Swagger/OpenAPI availability
- Health endpoint
- Documents list endpoint
- Document upload endpoint contract
- Compare endpoints contract

Run all tests:

```bash
make test
```
