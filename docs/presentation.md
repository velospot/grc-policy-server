---
marp: true
theme: default
paginate: true
title: Document Management and Processing System
description: Architecture and design presentation
---

# Document Management and Processing System

## Project-Based Proposal

- Backend: `grc-policy-server` (FastAPI + Celery + Weaviate)
- Frontend: `ectest-audit-v2` (Next.js)
- Goal: align existing implementation to required system capabilities

---

# 1. Problem and Scope

Design a system that can:

- Upload documents
- Extract information from uploaded documents
- Produce derivatives (preview/summaries)
- Process in background
- Provide user feedback
- Scale for many concurrent users

---

# 2. What Already Exists

Current backend already provides:

- Upload APIs (`/documents/upload`, `/documents/upload/v2`)
- Async jobs with polling (`/documents/upload/v2/{job_id}`)
- Extraction pipeline (Docling + OCR fallback + semantic enrichment)
- Comparison and summarized outputs (`/v2/compare`, `/compare/with-summary`)

Current frontend already provides:

- Document management screen and upload modal
- Job status polling and notifications for upload/compare jobs

---

# 3. Requirement Coverage (Current State)

| Requirement | Status | Notes |
|---|---|---|
| Upload documents | Met | Sync + async upload endpoints and UI integration |
| Extract information | Met | Docling parsing, OCR fallback, semantic extraction |
| Produce derivatives | Partial | Comparison summaries exist; preview/download UX not complete |
| Background processing | Met | Celery queue and job status polling |
| User feedback | Met | Queued/running/finished/failed surfaced in UI |
| Concurrent users and scale | Partial | Global write mutex + local FS metadata limit scale |

---

# 4. Current Architecture

- API gateway role: FastAPI service
- Async workers: Celery + Redis
- Processing: Docling, OCR, LLM semantic enrichment
- Retrieval/indexing: Weaviate
- Optional graph: Neo4j
- Frontend: Next.js consumes API and polls job states

See diagrams in:

- `docs/uml/system-context.mmd`
- `docs/uml/component.mmd`

---

# 5. End-to-End Upload Flow

1. User uploads one or many files in UI
2. UI calls `/documents/upload/v2`
3. API enqueues Celery task and returns `jobId`
4. Worker processes files and stores extracted artifacts
5. UI polls `/documents/upload/v2/{jobId}`
6. UI shows final accepted/rejected result

See `docs/uml/upload-sequence.mmd`.

---

# 6. End-to-End Compare Flow

1. User selects two documents in UI
2. UI calls `/v2/compare`
3. API returns `jobId` (or cached finished response)
4. Worker computes structured differences + summary
5. UI polls `/v2/compare/response/{job_id}`
6. Result is stored and shown in chat/comparison output

See `docs/uml/compare-sequence.mmd`.

---

# 7. Gaps and Risks

Top gaps against assignment:

- Derivatives for document preview/download are not fully wired end-to-end
- Link upload in UI is mock behavior, not real URL ingestion
- Global write mutex (`423 Locked`) can reduce throughput
- Filesystem metadata/cache can hinder horizontal scaling
- Frontend API routes currently use fixed token placeholders

---

# 8. Target Design Enhancements

Short-term:

- Add preview endpoint and UI wiring for "View" and "Download"
- Implement real URL ingestion endpoint (validated fetch + same pipeline)
- Replace fixed token handling with session-derived bearer token

Mid-term:

- Move metadata/cache from local FS to centralized datastore
- Replace global mutex with finer-grained document/job locks
- Add queue autoscaling and observability dashboards

---

# 9. Scalability and Reliability Plan

- Horizontal API replicas behind load balancer
- Dedicated worker pool autoscaling by queue depth
- Object storage for binary files
- Database-backed metadata and comparison cache
- Idempotent job keys, retries, dead-letter strategy
- Metrics: queue depth, task latency, error rate, extraction throughput

See `docs/uml/deployment.mmd`.

---

# 10. Security and Tenant Model

- Replace static token with user/session token propagation
- Enforce RBAC on document CRUD and compare operations
- Add tenant-scoped document ownership and query filters
- Harden CORS, size limits, and upload type validation

---

# 11. Delivery Artifacts

This submission includes:

- Presentation deck: `docs/presentation.md`
- Architecture doc: `docs/architecture.md`
- Design doc: `docs/design.md`
- Workflow doc: `docs/workflow.md`
- UML diagrams: `docs/uml/*.mmd`

---

# 12. Conclusion

- Existing project provides a strong base for the assignment.
- Most mandatory/should requirements are already covered.
- Remaining work is focused and practical: derivatives UX, true link ingestion, and scalability hardening.
