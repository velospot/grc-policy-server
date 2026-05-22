# Production Deployment Guide

Deploying `grc-policy-server` in production using Docker and Docker Compose.

---

## System Requirements

### Minimum (CPU-only)

| Resource | Minimum      |
|----------|-------------|
| CPU      | 4 cores      |
| RAM      | 16 GB        |
| Disk     | 50 GB SSD    |
| OS       | Ubuntu 22.04+ / Debian 12+ |

### Recommended (GPU-accelerated)

| Resource | Recommended  |
|----------|-------------|
| CPU      | 8+ cores     |
| RAM      | 32 GB+       |
| Disk     | 100 GB+ SSD  |
| GPU      | NVIDIA 8 GB+ VRAM (optional — accelerates Docling OCR and LLM inference) |

GPU is optional. When present, Docling auto-detects CUDA (`DOCLING_ACCELERATOR_DEVICE=auto`). Without a GPU the service runs on CPU — ingestion is slower but fully functional.

---

## Prerequisites

### Required software

- **Docker Engine 24.0+** — [install](https://docs.docker.com/engine/install/ubuntu/)
- **Docker Compose v2** — ships with Docker Desktop or `docker-compose-plugin`
- **Git**

```bash
docker --version        # Docker version 24.x or higher
docker compose version  # Docker Compose version v2.x
```

### LLM backend — choose one

#### Option A: Ollama (recommended for single-node)

```bash
curl -fsSL https://ollama.com/install.sh | sh

# Pull required models
ollama pull granite4.1:8b           # chat / reasoning
ollama pull qwen3-embedding:0.6b    # embeddings

# Optional: VLM for table extraction
ollama pull ibm/granite-docling:258m
```

Ollama listens on `localhost:11434`. When the API runs inside Docker, set `OLLAMA_EMBEDDING_URL=http://host.docker.internal:11434` (the default in `.env.example`).

#### Option B: vLLM

Set `LLM_PRIMARY_PROVIDER=vllm` and configure the `VLLM_*` variables in `.env`. Ensure the vLLM server is running and models are loaded before starting the API.

---

## Docker Network Setup

The production Compose file uses an external network. Create it once on the host:

```bash
docker network create grc_shared_net
```

---

## Quickstart

### 1. Clone the repository

```bash
git clone <your-repo-url> grc-policy-server
cd grc-policy-server
```

### 2. Configure environment

```bash
cp .env.example .env
```

Open `.env` and update at minimum:

```env
API_BEARER_TOKEN=<strong-random-token>
POSTGRES_PASSWORD=<strong-password>
# Update DATABASE_URL to match the password above:
DATABASE_URL=postgresql://grc_admin:<strong-password>@grc_postgres:5432/grc_db
```

See [Environment Variable Reference](#environment-variable-reference) for the full list.

### 3. Create required local directories

```bash
mkdir -p data/uploads weaviate_data
```

### 4. Start infrastructure services

```bash
docker compose -f docker-compose-prod.yml up -d
```

This starts PostgreSQL 17, Redis 7, and Weaviate. Wait ~30 seconds for health checks to pass:

```bash
docker compose -f docker-compose-prod.yml ps
# All services should show "healthy" or "running"
```

### 5. Build the application image

```bash
docker build -t grc-policy-server:latest .
```

Or pull from GitHub Container Registry:

```bash
docker pull ghcr.io/<your-org>/grc-policy-server:latest
docker tag ghcr.io/<your-org>/grc-policy-server:latest grc-policy-server:latest
```

### 6. Start the API server

```bash
docker run -d \
  --name grc_api \
  --network grc_shared_net \
  --env-file .env \
  -e HOST=0.0.0.0 \
  -e PORT=8500 \
  -e POSTGRES_HOST=grc_postgres \
  -e CELERY_BROKER_URL=redis://grc_redis:6379/0 \
  -e CELERY_RESULT_BACKEND=redis://grc_redis:6379/1 \
  -e WEAVIATE_URL=http://weaviate_chat_db:8080 \
  -v "$(pwd)/data:/app/data" \
  -p 8500:8500 \
  --restart unless-stopped \
  grc-policy-server:latest
```

### 7. Start the Celery worker

```bash
docker run -d \
  --name grc_worker \
  --network grc_shared_net \
  --env-file .env \
  -e POSTGRES_HOST=grc_postgres \
  -e CELERY_BROKER_URL=redis://grc_redis:6379/0 \
  -e CELERY_RESULT_BACKEND=redis://grc_redis:6379/1 \
  -e WEAVIATE_URL=http://weaviate_chat_db:8080 \
  -e CELERY_WORKER_POOL=prefork \
  -e CELERY_WORKER_CONCURRENCY=4 \
  -v "$(pwd)/data:/app/data" \
  --restart unless-stopped \
  grc-policy-server:latest \
  python -m celery -A grc_policy_server.worker:celery_app worker \
    --loglevel=INFO \
    --pool=prefork \
    --concurrency=4
```

Adjust `--concurrency` to available CPU cores. For memory-heavy workloads (large PDFs, GPU), use 2–4.

### 8. Validate

```bash
curl http://localhost:8500/health
# {"status":"ok"}

curl -H "Authorization: Bearer <your-token>" http://localhost:8500/documents
# {"documents":[...]}
```

Swagger UI: `http://localhost:8500/docs`

---

## Environment Variable Reference

### Application

| Variable | Default | Required | Description |
|----------|---------|:--------:|-------------|
| `API_BEARER_TOKEN` | `dummy-token` | **YES** | Auth token for all non-health endpoints — **change this** |
| `HOST` | `0.0.0.0` | No | Bind address |
| `PORT` | `8500` | No | Listen port |
| `ENVIRONMENT` | `production` | No | Runtime label |
| `LOG_LEVEL` | `INFO` | No | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `UPLOAD_ROOT` | `./data/uploads` | No | Local path for uploaded PDFs |
| `CORS_ORIGINS` | `*` | No | Comma-separated allowed origins — restrict in production |

### PostgreSQL

| Variable | Default | Required | Description |
|----------|---------|:--------:|-------------|
| `POSTGRES_HOST` | `postgres` | **YES** | Container hostname (e.g. `grc_postgres`) |
| `POSTGRES_PORT` | `5432` | No | Port |
| `POSTGRES_USER` | `grc_admin` | **YES** | Database user |
| `POSTGRES_PASSWORD` | `grc_admin` | **YES** | Database password — **change this** |
| `POSTGRES_DB` | `grc_db` | No | Database name |
| `DATABASE_URL` | auto-built | No | Full DSN; constructed automatically from the above if not set |

### Redis / Celery

| Variable | Default | Required | Description |
|----------|---------|:--------:|-------------|
| `CELERY_BROKER_URL` | `redis://localhost:6379/0` | **YES** | Redis broker URL |
| `CELERY_RESULT_BACKEND` | `redis://localhost:6379/1` | **YES** | Redis result store URL |
| `CELERY_WORKER_CONCURRENCY` | auto | No | Worker processes (default: `min(4, cpu_count)`) |
| `CELERY_WORKER_POOL` | `prefork` | No | `prefork` (Linux) or `solo` (macOS/debug) |
| `CELERY_TASK_HARD_TIME_LIMIT_SEC` | `3600` | No | Hard kill limit per task |
| `CELERY_WORKER_MAX_TASKS_PER_CHILD` | `200` | No | Restart worker after N tasks (memory leak guard) |

### Weaviate

| Variable | Default | Required | Description |
|----------|---------|:--------:|-------------|
| `WEAVIATE_URL` | `http://weaviate:8080` | **YES** | Weaviate service URL |
| `WEAVIATE_COLLECTION` | `PolicyChunk` | No | Collection name |
| `WEAVIATE_VECTORIZER` | `huggingface` | No | `ollama` or `huggingface` |
| `WEAVIATE_HUGGINGFACE_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | No | Model for HuggingFace vectorizer |

### LLM Backend

| Variable | Default | Required | Description |
|----------|---------|:--------:|-------------|
| `LLM_PRIMARY_PROVIDER` | `vllm` | **YES** | `ollama` or `vllm` |
| `OLLAMA_URL` | `http://localhost:11434` | Conditional | Ollama base URL |
| `OLLAMA_EMBEDDING_URL` | `http://host.docker.internal:11434` | Conditional | Ollama URL visible from Docker |
| `OLLAMA_CHAT_MODEL` | `granite4.1:8b` | Conditional | Chat model name |
| `OLLAMA_EMBED_MODEL` | `qwen3-embedding:0.6b` | Conditional | Embedding model name |
| `VLLM_CHAT_URL` | `http://localhost:8001` | Conditional | vLLM chat endpoint |
| `VLLM_EMBED_URL` | `http://localhost:8001` | Conditional | vLLM embedding endpoint |
| `VLLM_CHAT_MODEL` | `ibm-granite/granite-3.3-8b-instruct` | Conditional | vLLM chat model |
| `VLLM_EMBED_MODEL` | `Qwen/Qwen3-Embedding-0.6B` | Conditional | vLLM embedding model |

### Docling / OCR

| Variable | Default | Description |
|----------|---------|-------------|
| `DOCLING_ACCELERATOR_DEVICE` | `auto` | `auto`, `cuda`, `mps`, or `cpu` |
| `DOCLING_ACCELERATOR_THREADS` | auto | CPU thread count for document conversion |
| `PDF_EXTRACTOR` | `opendataloader` | Primary extractor: `opendataloader` or `docling` |
| `DOCLING_TABLE_OCR_ENABLED` | `true` | Enable OCR for table cells |

---

## Starting and Stopping

### Start the full stack

```bash
docker network create grc_shared_net 2>/dev/null || true
docker compose -f docker-compose-prod.yml up -d
# Then start grc_api and grc_worker as shown in the Quickstart
```

### Stop everything

```bash
docker stop grc_api grc_worker
docker compose -f docker-compose-prod.yml down
```

### Restart a single service

```bash
docker restart grc_api
docker restart grc_worker
```

### View logs

```bash
docker logs -f grc_api
docker logs -f grc_worker
docker compose -f docker-compose-prod.yml logs -f postgres
docker compose -f docker-compose-prod.yml logs -f redis
```

---

## Running Celery Workers

### Worker command

```bash
python -m celery -A grc_policy_server.worker:celery_app worker \
  --loglevel=INFO \
  --pool=prefork \
  --concurrency=4 \
  --queues=grc_policy_server.upload
```

### Pool guidance

| Environment | Pool | Notes |
|-------------|------|-------|
| Linux production | `prefork` | Process isolation; correct handling of ML library state |
| macOS / debug | `solo` | Avoids CoreFoundation/Objective-C fork issues |
| High-memory / GPU | `prefork --concurrency=2` | Limit processes when each worker loads large models |

### Memory management

Docling and sentence-transformers load large models. To prevent memory bloat:

```env
CELERY_WORKER_MAX_TASKS_PER_CHILD=200      # recycle workers periodically
CELERY_WORKER_MAX_MEMORY_PER_CHILD_KB=4000000  # hard-limit 4 GB per worker
```

---

## Health Check Validation

### API

```bash
curl http://localhost:8500/health
# {"status":"ok"}
```

### Weaviate

```bash
curl http://localhost:8080/v1/meta | python -m json.tool | grep version
```

### PostgreSQL

```bash
docker exec grc_postgres pg_isready -U grc_admin -d grc_db
```

### Redis

```bash
docker exec grc_redis redis-cli ping
# PONG
```

### Full smoke test

```bash
TOKEN="your-api-bearer-token"
curl -s http://localhost:8500/health
curl -s -H "Authorization: Bearer $TOKEN" http://localhost:8500/documents
```

---

## Troubleshooting

### API fails to connect to PostgreSQL

Ensure `POSTGRES_HOST` matches the Postgres container name and both are on `grc_shared_net`:

```bash
docker inspect grc_postgres | grep -A5 Networks
```

### Celery workers not picking up tasks

Check the worker connected to the right broker:

```bash
docker logs grc_worker | grep "Connected to redis"
```

Verify `CELERY_BROKER_URL` is identical in both the API and worker containers.

### OOM kills during document ingestion

Reduce worker concurrency and thread count:

```bash
-e CELERY_WORKER_CONCURRENCY=1
-e DOCLING_ACCELERATOR_THREADS=2
```

### GPU not detected inside container

Add `--gpus all` to the `docker run` command and set `DOCLING_ACCELERATOR_DEVICE=cuda` explicitly:

```bash
docker run --gpus all -e DOCLING_ACCELERATOR_DEVICE=cuda ...
```

### `401 Unauthorized` on API calls

All endpoints except `/health`, `/docs`, and `/redoc` require the header:

```
Authorization: Bearer <API_BEARER_TOKEN>
```

Verify the token in your request matches the value set in the running container.

### `network grc_shared_net not found`

```bash
docker network create grc_shared_net
```

### Worker queue name mismatch

The default queue is `grc_policy_server.upload`. If you customized `CELERY_DEFAULT_QUEUE`, pass the same value to `--queues` on the worker command.

---

## Optional: opendataloader-hybrid Sidecar

Improves table extraction accuracy (from ~78% to ~93% on complex PDFs). Build and run alongside the main stack:

```bash
docker build -t grc-opd-hybrid -f Dockerfile.hybrid .

docker run -d \
  --name opendataloader_hybrid \
  --network grc_shared_net \
  -p 5002:5002 \
  --restart unless-stopped \
  grc-opd-hybrid
```

Then in `.env`:

```env
OPENDATALOADER_HYBRID_URL=http://opendataloader_hybrid:5002
```

If the sidecar is unreachable at startup, the service automatically falls back to standard OpenDataLoader extraction.

---

## Optional: Neo4j Graph Store

Uncomment the `neo4j` service in `docker-compose-prod.yml` and set:

```env
NEO4J_ENABLED=true
NEO4J_URI=bolt://rag_neo4j:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=<your-password>
NEO4J_DATABASE=neo4j
```

Neo4j is disabled by default and not required for document ingestion or comparison.

---

## CI/CD

### GitHub Actions

- **CI** (`.github/workflows/ci.yml`): Runs on push to `main` / `release/*` and PRs to `main`. Runs lint (ruff), type check (mypy), tests (pytest), and Docker build validation in parallel.
- **Docker Publish** (`.github/workflows/docker-publish.yml`): Builds and pushes to `ghcr.io/<your-org>/grc-policy-server` on push to `main` or `v*` tags. Uses `GITHUB_TOKEN` — no additional secrets required.

### Pulling the published image

```bash
echo $GITHUB_TOKEN | docker login ghcr.io -u <username> --password-stdin
docker pull ghcr.io/<your-org>/grc-policy-server:latest
```

Tag a release to publish a versioned image:

```bash
git tag v1.0.0
git push origin v1.0.0
# Publishes ghcr.io/<your-org>/grc-policy-server:1.0.0 and :1.0
```
