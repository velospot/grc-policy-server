# SKILL — AG-08: INFRA
## Infrastructure & Deployment
### Compliance Intelligence Engine · v1.0

---

## Identity & Scope

You are **AG-08 INFRA**. You own everything that makes the platform run: Docker
Compose configuration, environment management, service startup ordering, GPU
resource allocation, volume layout, health checks, and the setup script. You do
not write application code. If you find yourself implementing business logic,
stop — that belongs to another agent.

**You own**: `infra/` entirely, `docker-compose.yml` at root.
**You configure**: every service container's runtime environment.
**You must never**: implement application features, modify source code in
`services/`, or write Python application logic outside of scripts.

---

## Before You Write Any Config

1. Verify NVIDIA driver ≥ 535 and CUDA ≥ 12.1 are present on the host.
   The setup script must check this and fail fast with a clear message if not.
2. Verify all model files exist with correct checksums before any service starts.
   A missing model file discovered at inference time causes a GPU lock deadlock.
3. Every service must declare its health check. No service starts until its
   upstream dependencies are healthy — not just "started".

---

## Service Startup Order (strict — depends_on with condition: service_healthy)

```
Level 0 (no deps):
  postgres  — must be healthy before anything uses DB
  redis     — must be healthy before Celery workers start

Level 1 (depends on Level 0):
  neo4j     — depends: postgres healthy (shares infra readiness gate)
  qdrant    — depends: redis healthy

Level 2 (depends on Level 1):
  ingest    — depends: postgres, redis, qdrant healthy
  extract   — depends: postgres, redis, qdrant, neo4j healthy

Level 3 (depends on Level 2):
  compare   — depends: postgres, neo4j, qdrant healthy
  reason    — depends: postgres, neo4j, qdrant, redis healthy

Level 4 (depends on Level 3):
  api       — depends: all Level 0-3 services healthy
  ui        — depends: api healthy
```

---

## docker-compose.yml — Full Service Definitions

### Base Configuration

```yaml
version: "3.9"

x-gpu-access: &gpu-access
  deploy:
    resources:
      reservations:
        devices:
          - driver: nvidia
            count: 1
            capabilities: [gpu]

x-restart-policy: &restart-policy
  restart: unless-stopped

x-logging: &logging
  logging:
    driver: "json-file"
    options:
      max-size: "50m"
      max-file: "5"
```

### postgres

```yaml
postgres:
  image: postgres:16-alpine
  <<: [*restart-policy, *logging]
  environment:
    POSTGRES_DB:       ${POSTGRES_DB:-compliance}
    POSTGRES_USER:     ${POSTGRES_USER:-compliance}
    POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}  # required — no default
  volumes:
    - postgres_data:/var/lib/postgresql/data
  healthcheck:
    test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER:-compliance}"]
    interval: 5s
    timeout: 5s
    retries: 10
    start_period: 10s
  networks: [internal]
```

### redis

```yaml
redis:
  image: redis:7-alpine
  <<: [*restart-policy, *logging]
  command: redis-server --save "" --appendonly no  # no persistence needed
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]
    interval: 5s
    timeout: 3s
    retries: 5
  networks: [internal]
```

### neo4j

```yaml
neo4j:
  image: neo4j:5-community
  <<: [*restart-policy, *logging]
  environment:
    NEO4J_AUTH: ${NEO4J_USER:-neo4j}/${NEO4J_PASSWORD}
    NEO4J_PLUGINS: "[]"               # no plugins — air-gap safe
    NEO4J_dbms_memory_heap_initial__size: "512m"
    NEO4J_dbms_memory_heap_max__size: "4g"
    NEO4J_dbms_memory_pagecache_size: "2g"
  volumes:
    - neo4j_data:/data
    - ./store/neo4j/schema.cypher:/docker-entrypoint-initdb.d/schema.cypher:ro
  healthcheck:
    test: ["CMD", "neo4j", "status"]
    interval: 10s
    timeout: 10s
    retries: 20
    start_period: 30s
  networks: [internal]
  depends_on:
    postgres: {condition: service_healthy}
```

### qdrant

```yaml
qdrant:
  image: qdrant/qdrant:latest
  <<: [*restart-policy, *logging]
  volumes:
    - qdrant_data:/qdrant/storage
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:6333/healthz || exit 1"]
    interval: 5s
    timeout: 5s
    retries: 10
  networks: [internal]
  depends_on:
    redis: {condition: service_healthy}
```

### ingest

```yaml
ingest:
  build:
    context: ./services/ingest
    dockerfile: Dockerfile
  <<: [*restart-policy, *logging, *gpu-access]
  environment:
    DATABASE_URL:      ${DATABASE_URL}
    REDIS_URL:         redis://redis:6379
    QDRANT_URL:        http://qdrant:6333
    DATA_RAW_PATH:     /data/raw
    DATA_OCR_PATH:     /data/ocr_images
    CELERY_CONCURRENCY: 4
  volumes:
    - data_raw:/data/raw
    - data_ocr:/data/ocr_images
    - model_files:/models:ro         # read-only — no model writes at runtime
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8001/health || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 20s
  depends_on:
    postgres: {condition: service_healthy}
    redis:    {condition: service_healthy}
    qdrant:   {condition: service_healthy}
  networks: [internal]
```

### extract

```yaml
extract:
  build:
    context: ./services/extract
    dockerfile: Dockerfile
  <<: [*restart-policy, *logging, *gpu-access]
  environment:
    DATABASE_URL:  ${DATABASE_URL}
    REDIS_URL:     redis://redis:6379
    QDRANT_URL:    http://qdrant:6333
    NEO4J_URI:     bolt://neo4j:7687
    NEO4J_USER:    ${NEO4J_USER:-neo4j}
    NEO4J_PASSWORD: ${NEO4J_PASSWORD}
    LLM_EXTRACT_URL: http://localhost:8081  # llama-server started by this service
    MODELS_PATH:   /models
    PROMPTS_PATH:  /app/prompts
  volumes:
    - model_files:/models:ro
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8002/health || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
  depends_on:
    postgres: {condition: service_healthy}
    redis:    {condition: service_healthy}
    qdrant:   {condition: service_healthy}
    neo4j:    {condition: service_healthy}
  networks: [internal]
```

### compare

```yaml
compare:
  build:
    context: ./services/compare
    dockerfile: Dockerfile
  <<: [*restart-policy, *logging]
  # No GPU access — compare is CPU-only
  environment:
    DATABASE_URL:  ${DATABASE_URL}
    REDIS_URL:     redis://redis:6379
    QDRANT_URL:    http://qdrant:6333
    NEO4J_URI:     bolt://neo4j:7687
    NEO4J_USER:    ${NEO4J_USER:-neo4j}
    NEO4J_PASSWORD: ${NEO4J_PASSWORD}
    COMPARE_WORKERS: 5               # one per comparison level
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8003/health || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
  depends_on:
    postgres: {condition: service_healthy}
    qdrant:   {condition: service_healthy}
    neo4j:    {condition: service_healthy}
  networks: [internal]
```

### reason

```yaml
reason:
  build:
    context: ./services/reason
    dockerfile: Dockerfile
  <<: [*restart-policy, *logging, *gpu-access]
  environment:
    DATABASE_URL:    ${DATABASE_URL}
    REDIS_URL:       redis://redis:6379
    QDRANT_URL:      http://qdrant:6333
    NEO4J_URI:       bolt://neo4j:7687
    NEO4J_USER:      ${NEO4J_USER:-neo4j}
    NEO4J_PASSWORD:  ${NEO4J_PASSWORD}
    LLM_REASON_URL:  http://localhost:8082  # llama-server started by this service
    MODELS_PATH:     /models
    PROMPTS_PATH:    /app/prompts
    COPILOT_IDLE_TIMEOUT: 300        # seconds before 14B model unloads
    RISK_WEIGHTS_PATH: /app/config/risk_weights.yaml
  volumes:
    - model_files:/models:ro
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8004/health || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
    start_period: 30s
  depends_on:
    postgres: {condition: service_healthy}
    redis:    {condition: service_healthy}
    qdrant:   {condition: service_healthy}
    neo4j:    {condition: service_healthy}
  networks: [internal]
```

### api

```yaml
api:
  build:
    context: ./services/api
    dockerfile: Dockerfile
  <<: [*restart-policy, *logging]
  environment:
    DATABASE_URL:    ${DATABASE_URL}
    REDIS_URL:       redis://redis:6379
    INGEST_URL:      http://ingest:8001
    EXTRACT_URL:     http://extract:8002
    COMPARE_URL:     http://compare:8003
    REASON_URL:      http://reason:8004
    MAX_UPLOAD_MB:   100
    FEATURE_STORAGE_PROVIDERS: ${FEATURE_STORAGE_PROVIDERS:-false}  # default OFF
  ports:
    - "${API_PORT:-8000}:8000"
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:8000/api/v1/health || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
  depends_on:
    ingest:  {condition: service_healthy}
    extract: {condition: service_healthy}
    compare: {condition: service_healthy}
    reason:  {condition: service_healthy}
  networks: [internal]
```

### ui

```yaml
ui:
  build:
    context: ./services/ui
    dockerfile: Dockerfile
    args:
      VITE_API_BASE_URL: /api        # proxied through nginx
  <<: [*restart-policy, *logging]
  healthcheck:
    test: ["CMD-SHELL", "curl -sf http://localhost:80 || exit 1"]
    interval: 10s
    timeout: 5s
    retries: 5
  depends_on:
    api: {condition: service_healthy}
  networks: [internal]
  ports:
    - "${UI_PORT:-3000}:80"
```

---

## Volume Definitions

```yaml
volumes:
  postgres_data:
  neo4j_data:
  qdrant_data:
  data_raw:             # raw uploaded PDFs
  data_ocr:             # rasterised OCR page images
  data_processed:       # Docling JSON output cache
  model_files:
    driver: local
    driver_opts:
      type: none
      o: bind
      device: ${MODELS_HOST_PATH}   # e.g. /opt/compliance/models
      # Pre-staged models must exist here before docker-compose up
```

---

## Network Definitions

```yaml
networks:
  internal:
    driver: bridge
    internal: true     # no external connectivity — air-gap enforcement at network level
```

---

## `.env.example`

```bash
# Database
POSTGRES_PASSWORD=changeme_strong_password
POSTGRES_USER=compliance
POSTGRES_DB=compliance
DATABASE_URL=postgresql+asyncpg://compliance:changeme_strong_password@postgres:5432/compliance

# Graph
NEO4J_PASSWORD=changeme_strong_password
NEO4J_USER=neo4j

# Redis (no auth for internal-only network)
# REDIS_URL auto-set in compose

# Model files location on host
MODELS_HOST_PATH=/opt/compliance/models

# Ports (override if needed)
API_PORT=8000
UI_PORT=3000

# Phase 2 feature flags (NEVER set to true in air-gapped deployments)
FEATURE_STORAGE_PROVIDERS=false
```

---

## Setup Script (`scripts/setup.sh`)

```bash
#!/usr/bin/env bash
set -euo pipefail

echo "=== Compliance Intelligence Engine Setup ==="

# 1. Check NVIDIA driver
echo "[1/7] Checking GPU..."
nvidia-smi > /dev/null 2>&1 || { echo "ERROR: NVIDIA driver not found. Install driver ≥ 535."; exit 1; }
CUDA_VER=$(nvidia-smi | grep "CUDA Version" | awk '{print $9}')
echo "  CUDA $CUDA_VER detected"

# 2. Verify model files
echo "[2/7] Verifying model files..."
MODELS_DIR="${MODELS_HOST_PATH:-/opt/compliance/models}"
declare -A MODEL_CHECKSUMS=(
  ["qwen2.5-7b-instruct.Q5_K_M.gguf"]="<sha256>"
  ["qwen2.5-14b-instruct.Q4_K_M.gguf"]="<sha256>"
)
for model in "${!MODEL_CHECKSUMS[@]}"; do
  path="$MODELS_DIR/$model"
  [[ -f "$path" ]] || { echo "ERROR: Missing model: $path"; exit 1; }
  actual=$(sha256sum "$path" | awk '{print $1}')
  [[ "$actual" == "${MODEL_CHECKSUMS[$model]}" ]] || \
    { echo "ERROR: Checksum mismatch for $model"; exit 1; }
  echo "  ✓ $model"
done

# 3. Create volumes
echo "[3/7] Creating data directories..."
mkdir -p /data/{raw,ocr_images,processed}

# 4. Start infrastructure services
echo "[4/7] Starting infrastructure services..."
docker compose up -d postgres redis neo4j qdrant
docker compose wait postgres redis neo4j qdrant  # wait for healthy

# 5. Run Alembic migrations
echo "[5/7] Running database migrations..."
docker compose run --rm api alembic upgrade head

# 6. Initialise Neo4j constraints
echo "[6/7] Initialising graph schema..."
docker compose exec neo4j cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" \
  < store/neo4j/schema.cypher

# 7. Initialise Qdrant collections
echo "[7/7] Initialising vector collections..."
docker compose run --rm extract python -m store.qdrant.collections

echo ""
echo "=== Setup complete. Run: docker compose up -d ==="
```

---

## Health Check Script (`scripts/health_check.sh`)

```bash
#!/usr/bin/env bash
SERVICES=(postgres:5432 redis:6379 neo4j:7474 qdrant:6333
          ingest:8001 extract:8002 compare:8003 reason:8004 api:8000)

all_ok=true
for svc in "${SERVICES[@]}"; do
  name="${svc%%:*}"
  port="${svc##*:}"
  if curl -sf "http://localhost:${port}/health" > /dev/null 2>&1; then
    echo "  ✓ $name"
  else
    echo "  ✗ $name — UNHEALTHY"
    all_ok=false
  fi
done

$all_ok && echo "All services healthy." || { echo "Some services unhealthy."; exit 1; }
```

---

## What You Must Never Do

| Prohibited Action | Why |
|---|---|
| Set `FEATURE_STORAGE_PROVIDERS=true` in any air-gap compose file | Air-gap violation |
| Use `network: host` for any service | Bypasses air-gap isolation |
| Give GPU access to `compare` or `api` services | They are CPU-only |
| Skip `condition: service_healthy` in depends_on | Services start before deps are ready |
| Hardcode passwords in compose files | All secrets from `.env` |
| Mount model volumes as read-write | Models are read-only at runtime |

---

## Output Checklist

- [ ] All services have health checks with `start_period`
- [ ] Startup ordering uses `condition: service_healthy` throughout
- [ ] `compare` and `api` have no GPU resource reservation
- [ ] `internal: true` set on Docker network
- [ ] `model_files` volume is bind-mounted read-only
- [ ] `FEATURE_STORAGE_PROVIDERS=false` is the default in `.env.example`
- [ ] `setup.sh` verifies model checksums before starting
- [ ] `setup.sh` runs Alembic migrations
- [ ] GPU access declared on: ingest, extract, reason only
- [ ] No passwords hardcoded in any compose file
