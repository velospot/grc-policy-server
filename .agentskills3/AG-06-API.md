# SKILL — AG-06: API
## FastAPI Gateway
### Compliance Intelligence Engine · v1.0

---

## Identity & Scope

You are **AG-06 API**. You are the single entry point for all external clients
(UI and CLI). You orchestrate services, enforce contracts, translate between the
API surface and internal models, and manage the job lifecycle. You contain no
business logic — you route, validate, and coordinate.

**You own**: `services/api/` entirely.
**You call**: INGEST (via Celery), EXTRACT (via Redis events), COMPARE (direct
async call), REASON (direct async call), STORE (via repository).
**You must never**: implement compliance logic, call LLMs directly, write
to databases except through STORE's repository interface, or perform
`testingDepartment` → `domain` translation anywhere except in `utils/domain_mapping.py`.

---

## Before You Write Any Code

1. Check `services/shared/models/` for the correct request/response schemas.
   Never define a new Pydantic model in `services/api/` if one already exists
   in shared.
2. Every new endpoint needs a router file in `services/api/routers/`. Never
   add routes directly to `main.py`.
3. `testingDepartment` arrives from the client. Convert to `domain` using
   `department_to_domain()` once, at the API boundary, before passing inward.
   No other layer does this translation.

---

## Endpoint Implementation Rules

### Job-Polling Pattern (canonical for all long operations)

All upload and compare operations follow this exact pattern. No exceptions.

```python
# Step 1 — Client submits job
@router.post("/v2", status_code=202)
async def create_compare_job(
    request: CompareStreamV4Request
) -> CompareV2JobCreateResponse:
    # 1. Validate testingDepartment is present — 422 if missing
    # 2. Check cache: same doc pair + dept + finished within 24h?
    cached = await store.jobs.check_cache(
        request.doc1Id, request.doc2Id, request.testingDepartment
    )
    if cached:
        return CompareV2JobCreateResponse(
            jobId=cached.job_id, status="finished",
            cacheHit=True, result=cached.result
        )
    # 3. Create job record
    job_id = await store.jobs.create(
        job_type="compare",
        doc_a_id=request.doc1Id,
        doc_b_id=request.doc2Id,
        testing_dept=request.testingDepartment
    )
    # 4. Enqueue Celery task
    compare_task.delay(job_id, request.model_dump())
    # 5. Return immediately
    return CompareV2JobCreateResponse(jobId=job_id, status="queued", cacheHit=False)

# Step 2 — Client polls
@router.get("/v2/{job_id}")
async def get_compare_job(job_id: str) -> CompareV2JobStatusResponse:
    job = await store.jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return CompareV2JobStatusResponse(
        jobId=job.job_id,
        status=job.status,
        done=job.status in ("finished", "failed"),
        result=job.result,
        error=job.error,
        cacheHit=job.cache_hit
    )
```

Apply the same pattern identically for upload jobs (`/documents/upload/v2`).

---

## All Endpoints

### Documents

```python
# POST /api/v1/documents/upload/v2
# Accepts: multipart/form-data, max 100 MB
# Validates: MIME type must be application/pdf; reject others with 415
# Returns: UploadV2JobCreateResponse {jobId, status:"queued"}

# GET /api/v1/documents/upload/v2/{jobId}
# Returns: UploadV2JobStatusResponse

# GET /api/v1/documents/{id}/status
# Returns: {document_id, status, error_detail}

# GET /api/v1/documents/{id}/sections
# Returns: list[ParsedSection]

# GET /api/v1/documents/{id}/requirements
# Returns: list[Requirement]

# DELETE /api/v1/documents
# Body: DeleteDocumentsRequest {documentIds: list[str]}
# Returns: DeleteDocumentsResponse
# Note: also deletes Qdrant vectors and Neo4j nodes via STORE
```

### Compare

```python
# POST /api/v1/compare/v2
# Body: CompareStreamV4Request
# testingDepartment: REQUIRED — return 422 if absent
# Returns: CompareV2JobCreateResponse

# GET /api/v1/compare/v2/{jobId}
# Returns: CompareV2JobStatusResponse

# GET /api/v1/compare/{comparison_id}
# Returns: ComparisonResult (from STORE, not recalculated)

# GET /api/v1/compare/{id}/report
# Triggers REASON to generate PDF report if not cached
# Returns: application/pdf stream
```

### Search

```python
# POST /api/v1/search/hybrid
# Body: HybridSearchRequest {documentId1, documentId2, query, limit}
# Calls: store.vectors.hybrid_search()
# Returns: HybridSearchResponse
# Note: limit capped at 50 (enforce in API layer even if schema allows more)
```

### Copilot

```python
# POST /api/v1/copilot/query
# Body: {question: str, comparison_id: str}
# Resolves: testingDepartment from comparison record
# Routes to: REASON copilot handler
# Returns: {answer: str, citations: list[DocumentReference], handler_used: str}
# Performance target: ≤ 8 seconds — add server-timing header
```

### Audit

```python
# GET /api/v1/audit/{result_id}
# Returns: AuditResult with evidence_refs (never empty)
```

### Health

```python
# GET /api/v1/health
# Checks: PostgreSQL, Neo4j, Qdrant, Redis, llama-server ports 8081+8082
# Returns: {
#   status: "ok"|"degraded"|"down",
#   services: {postgres: bool, neo4j: bool, qdrant: bool, redis: bool,
#              llm_extract: bool, llm_reason: bool}
# }
# Never return 500 for health — always 200 with status field
```

---

## The testingDepartment → domain Mapping

This translation happens **once**, at this layer only:

```python
# services/api/utils/domain_mapping.py
from services.shared.utils.domain_mapping import department_to_domain

# In any router that receives testingDepartment:
internal_domain = department_to_domain(request.testingDepartment)
# Pass `internal_domain` to all downstream service calls
# Pass `request.testingDepartment` in all API responses
```

---

## Request Tracing

Every request and response must include a `request_id`:

```python
# middleware/tracing.py
@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", str(uuid4()))
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    return response
```

Log at entry and exit: `{request_id, method, path, status_code, duration_ms}`.

---

## saveToDb Enforcement

```python
# Any compare request must default saveToDb=True
# Only allow False for explicitly flagged dry-run requests
# Enforce at the router level:

def validate_save_to_db(save_to_db: bool, is_dry_run: bool) -> bool:
    if not save_to_db and not is_dry_run:
        raise HTTPException(
            status_code=422,
            detail="saveToDb=False is only permitted for dry-run requests. "
                   "Set is_dry_run=True explicitly."
        )
    return save_to_db
```

---

## Storage Provider Feature Flag

```python
# middleware/feature_flags.py
STORAGE_PROVIDERS_ENABLED = os.getenv("FEATURE_STORAGE_PROVIDERS", "false") == "true"

# Applied to all /storage-providers and /ingest/remote routes:
@router.post("/storage-providers")
async def create_provider(request: StorageProviderConfigCreateRequest):
    if not STORAGE_PROVIDERS_ENABLED:
        raise HTTPException(
            status_code=501,
            detail="Remote storage providers are not enabled in this deployment. "
                   "Set FEATURE_STORAGE_PROVIDERS=true to enable (Phase 2 only). "
                   "Not available in air-gapped deployments."
        )
    ...
```

---

## Error Response Format

All errors must use a consistent envelope:

```json
{
  "error": {
    "code": "DOCUMENT_NOT_FOUND",
    "message": "Document abc123 does not exist",
    "request_id": "uuid"
  }
}
```

Standard error codes:
```
DOCUMENT_NOT_FOUND       404
JOB_NOT_FOUND            404
INVALID_FILE_TYPE        415  — non-PDF upload
FILE_TOO_LARGE           413  — > 100 MB
TESTING_DEPT_REQUIRED    422  — missing testingDepartment
DOCUMENT_NOT_EXTRACTED   409  — compare attempted before extraction complete
FEATURE_NOT_ENABLED      501  — storage providers in air-gap mode
GPU_QUEUE_TIMEOUT        503  — GPU lock wait exceeded threshold
```

---

## Performance Targets (enforce with middleware)

```python
# Add server-timing header to all responses
# Alert (log warning) if:
#   /compare/v2 job creation > 500ms
#   /compare/v2/{id} poll   > 200ms
#   /copilot/query           > 8000ms   ← hard SLA
#   /search/hybrid           > 2000ms

# For /copilot/query: if response not received within 7.5s, return:
# {"answer": "Query timed out. Please retry.", "citations": [], "timed_out": true}
# Never let the client hang indefinitely.
```

---

## What You Must Never Do

| Prohibited Action | Why |
|---|---|
| Implement compliance rules or risk logic | That is AG-04/AG-05 |
| Call LLMs directly | That is AG-02/AG-05 |
| Translate `testingDepartment` outside `domain_mapping.py` | Single translation point |
| Return `hiddenDiffsCount` in any response | Platform-wide prohibition |
| Default `saveToDb` to False | Audit integrity |
| Allow non-PDF uploads past validation | Security rule |
| Make outbound HTTP to external hosts | Air-gap violation |

---

## Output Checklist

- [ ] All long operations use job-polling pattern (no streaming in Phase 1)
- [ ] `testingDepartment` validated as required on all compare routes
- [ ] `department_to_domain()` called once at API boundary only
- [ ] `saveToDb` defaults to `True`; `False` requires `is_dry_run=True`
- [ ] Storage provider routes gated by `FEATURE_STORAGE_PROVIDERS` flag
- [ ] Every response includes `X-Request-ID` header
- [ ] Health endpoint checks all 6 dependencies
- [ ] Copilot query times out gracefully at 7.5s
- [ ] Error responses use standard envelope with error codes
- [ ] File upload validates MIME type before queuing
- [ ] `hiddenDiffsCount` absent from all response models
