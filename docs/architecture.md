# Architecture: Document Management and Processing System

## 1. Context

This system is designed by integrating:

- Backend service: `/Users/navm/projects/grc-policy-server`
- Existing UI: `/Users/navm/projects/ectest-audit-v2`

The architecture supports document ingestion, extraction, asynchronous processing, retrieval, and comparison output.

## 2. Current Architecture (As Implemented)

Core building blocks:

- FastAPI for HTTP APIs
- Celery + Redis for asynchronous jobs
- **OpenDataLoader (`opendataloader-pdf`)** as primary PDF extractor — XY-Cut++ reading order, preserves heading→section→table ordering; optional hybrid sidecar routes complex pages to a docling backend for ~93% table accuracy
- **Docling + OCR fallback** as extraction fallback — handles non-PDF (DOCX etc.) and OPD failures; pytesseract OCR applied when low-text pages are detected
- Unified `ParsedChunk` boundary — both extractors emit the same dataclass; all downstream code is extractor-agnostic
- vLLM (primary) / Ollama (fallback) for language detection, semantic extraction, and summarization
- PostgreSQL canonical document store for raw extraction JSON and normalized nodes
- Weaviate for vector/hybrid retrieval and semantic candidate search
- Optional Neo4j for graph hierarchy
- Filesystem-based upload metadata and comparison cache
- Next.js frontend with job polling and notifications

Primary request classes:

- Upload requests (`/documents/upload`, `/documents/upload/v2`)
- Polling requests (`/documents/upload/v2/{job_id}`)
- Comparison requests (`/v2/compare`, `/v2/compare/response/{job_id}`)
- Listing and deletion (`/documents`, `/documents/delete`)

## 3. Requirement Mapping

- Upload support: implemented via multipart upload endpoints and UI modal.
- Information extraction: implemented in ingestion pipeline (Docling/OCR/semantic enrichment).
- Derivatives: partially implemented (comparison summaries exist; document preview derivatives are not fully exposed).
- Background processing: implemented with Celery v2 endpoints.
- User feedback: implemented with status polling and UI notifications.
- Scalability: partial; queue architecture is good, but global write mutex and filesystem metadata/cache are current bottlenecks.

## 4. Current Limitations

1. Write serialization:
- Non-GET requests are guarded by a global lock file mutex, reducing write throughput.

2. Storage coupling:
- Document listing/deletion depends on per-document `metadata.json` on local disk.
- Comparison cache is local filesystem based.

3. Frontend integration risks:
- API routes in the UI currently pass fixed token placeholders rather than user/tenant credentials.

4. Derivative gap:
- "View" / "Download" UX is present, but endpoint wiring for rich previews is incomplete.

5. URL import gap:
- Link import flow in UI currently creates mock empty files instead of true server-side URL ingestion.

## 5. Target Architecture

Target refinements:

- Keep API + queue + workers model.
- Keep OpenDataLoader as primary PDF extractor; keep Docling as fallback and non-PDF handler.
- Keep PostgreSQL as the canonical comparison substrate:
  - raw Docling JSON
  - normalized document node tree
  - stable node IDs, hierarchy, ordering, page anchors, and metadata
- Keep Weaviate scoped to chat/retrieval and semantic candidate matching.
- Compare canonical nodes, then emit structured change records before LLM summarization.
- Send structured change records, not lossy "key diffs", to the LLM summary prompt:
  - source/target node IDs
  - exact before/after text
  - alignment type and confidence
  - requirement-verb and numeric changes
  - table/cell changes
  - impact and `changeSeverity`
  - citations and heading context
- Persist a compare loss-map trace for each job:
  - raw extracted structure
  - normalized node tree
  - retrieval/index artifacts
  - alignment results
  - diff/change records
  - exact LLM input payload
  - final summary coverage
- Move binary documents to object storage.
- Move metadata and cache to central datastore.
- Replace global mutex with resource-granular idempotency locks.
- Introduce tenant-aware auth propagation from frontend to backend.
- Add explicit derivative generation pipeline:
  - thumbnails/page previews
  - extracted text preview
  - executive summary snippets

## 6. Diagram References

- System context: `docs/uml/system-context.mmd`
- Component architecture: `docs/uml/component.mmd`
- Upload sequence: `docs/uml/upload-sequence.mmd`
- Compare sequence: `docs/uml/compare-sequence.mmd`
- Deployment architecture: `docs/uml/deployment.mmd`
- Domain model: `docs/uml/domain-class.mmd`
- Job lifecycle: `docs/uml/job-state.mmd`

## 7. Recommended Rollout

Phase 1:

- Implement real URL ingestion.
- Implement preview/download endpoints and wire UI actions.
- Replace static token forwarding in UI API routes.

Phase 2:

- Migrate filesystem metadata/cache to centralized datastore.
- Introduce object storage and signed file access.
- Add observability, autoscaling rules, and SLOs.

Phase 3:

- Tenant isolation and RBAC hardening.
- Optional streaming progress events for richer UX feedback.
