# Detailed Design

## 1. Functional Scope

The system provides:

- Document upload and storage
- Document parsing and semantic extraction
- Derivative production (summaries now, previews next)
- Asynchronous processing with polling
- User-facing job status feedback
- Retrieval/comparison for policy deltas

## 2. High-Level Modules

### 2.1 API Layer (FastAPI)

Responsibilities:

- Validate requests and auth
- Accept upload payloads
- Enqueue async jobs
- Return status/result payloads
- List/delete documents

Key endpoints:

- `POST /documents/upload`
- `POST /documents/upload/v2`
- `GET /documents/upload/v2/{job_id}`
- `GET /documents`
- `POST /documents/delete`
- `POST /v2/compare`
- `GET /v2/compare/response/{job_id}`
- `POST /v3/compare/stream`
- `POST /v4/compare/stream` — two-stage department-aware SSE (preferred for interactive frontends)
- `POST /compare/with-summary`

### 2.2 Processing Layer (Celery Workers)

Responsibilities:

- Execute ingestion and comparison workloads outside HTTP request path
- Decode upload payloads and call ingestion pipeline
- Persist job outputs and return structured results

### 2.3 Ingestion Layer

Responsibilities:

- Parse document bytes (Docling)
- OCR fallback for low-text pages
- Preprocess and chunk content
- Enrich clause semantics via LLM
- Build hierarchy and indexable vector records
- Persist metadata and artifacts

### 2.4 Retrieval and Comparison Layer

Responsibilities:

- Store chunks in Weaviate
- Fetch and match chunks between document versions
- Generate structured differences and summary
- Cache comparison result by document pair

### 2.5 Frontend Layer (Next.js)

Responsibilities:

- Upload interaction and file selection
- Job status polling and feedback
- Document list/search/delete actions
- Compare job initiation and result display

## 3. Data Design

### 3.1 Primary Entities

- Document metadata:
  - `id`, `name`, `version`, `uploadDate`, `size`, `category`
- Upload job:
  - `jobId`, `status`, `done`, `result`, `error`
- Compare job:
  - `jobId`, `status`, `done`, `result`, `error`, `cacheHit`
- Chunk record (vector index):
  - structural metadata + normalized text + semantic fields

### 3.2 Artifact Storage (Current)

- Original files under `UPLOAD_ROOT/{document_id}/`
- Metadata in `metadata.json`
- Hierarchy in `hierarchy.json`
- Docling export in `*.docling.json`
- Comparison cache in `_comparison_cache/*.json`

### 3.3 Target Storage (Recommended)

- Object storage for raw files and generated previews
- Central DB for document metadata and job state
- Shared cache for comparison outputs

## 4. Non-Functional Design

### 4.1 Scalability

Current strengths:

- Queue-backed asynchronous execution
- Configurable worker pool and concurrency

Current bottlenecks:

- Global write mutex on API write operations
- Filesystem metadata/cache dependency

Target:

- Replace global mutex with per-document idempotency lock
- Stateless API replicas
- Distributed metadata/cache services

### 4.2 Reliability

- Task retries and timeout controls via Celery settings
- Polling endpoints expose deterministic status transitions
- Need stronger persistent job/event audit trail in datastore

### 4.3 Security

- Bearer token gate exists in backend
- Frontend should forward user/session-derived credentials instead of placeholders
- Tenant-aware authorization and document ownership checks should be added

## 5. Gap-Closure Design Changes

### 5.1 Real URL Ingestion

Add endpoint:

- `POST /documents/import-url`
  - Request: `{ "url": "...", "filenameHint": "..." }`
  - Behavior: validate URL, bounded download, enqueue same ingestion path

### 5.2 Preview and Derivative APIs

Add endpoints:

- `GET /documents/{id}/preview`
- `GET /documents/{id}/download`
- `GET /documents/{id}/summary`

Generation:

- Produce text preview and first-page image/PDF snapshot in background

### 5.3 Auth and Multi-Tenancy

- Propagate authenticated user token/session from frontend API routes
- Add tenant/user ownership fields to metadata and query filters

## 6. Acceptance Criteria (Design-Level)

1. Upload + extraction + async processing + status feedback remains stable.
2. Preview and URL import become first-class flows.
3. No global lock bottleneck for independent document operations.
4. Horizontal API scale supported by shared metadata/cache/storage.
5. User-scoped authorization is consistently enforced.
