# 07 - Storage, API, and Workers

## Persistence responsibilities

| Store | Responsibility |
|---|---|
| PostgreSQL | canonical metadata, CIR objects, comparison results, audit logs |
| Object storage | PDFs, page renders, CIR JSON snapshots, extraction logs, exports |
| Vector DB | derived retrieval objects, embeddings, sparse vectors, metadata filters |
| Redis | short-lived job queue and progress events |

## PostgreSQL as source of truth

The vector DB must be rebuildable. Store all source objects in PostgreSQL and/or immutable CIR JSON snapshots.

## Core entities

```text
projects
users
roles
documents
document_versions
extraction_runs
pages
sections
blocks
tables
table_columns
table_rows
requirements
normalized_facts
citations
index_objects
comparisons
change_items
evidence_items
llm_runs
audit_events
```

## Job design

Each heavy operation should be a job:

```text
extract_document
build_cir
normalize_tables
extract_requirements
index_document
compare_documents
explain_change_items
export_report
```

Job state machine:

```text
queued -> running -> succeeded
queued -> running -> failed -> retrying -> succeeded
queued -> running -> failed -> needs_manual_attention
```

## Idempotency

Each job should use stable idempotency keys.

Examples:

```text
extract_document: document_sha256 + extractor_version
index_document: document_id + cir_version + embedding_model_version
compare_documents: v1_doc_id + v2_doc_id + comparison_algorithm_version
explain_change: change_id + evidence_pack_hash + model_version + prompt_version
```

## API endpoints

```text
POST   /api/projects
GET    /api/projects
POST   /api/projects/{project_id}/documents
GET    /api/projects/{project_id}/documents
GET    /api/documents/{document_id}
GET    /api/documents/{document_id}/status
GET    /api/documents/{document_id}/cir
GET    /api/documents/{document_id}/page/{page_number}/image
POST   /api/comparisons
GET    /api/comparisons/{comparison_id}
GET    /api/comparisons/{comparison_id}/changes
GET    /api/comparisons/{comparison_id}/export
POST   /api/comparisons/{comparison_id}/review/{change_id}
GET    /api/jobs/{job_id}
```

## Upload API

Request:

```http
POST /api/projects/{project_id}/documents
Content-Type: multipart/form-data
```

Fields:

```text
file: PDF
release_label: v1/v2/etc.
language_hint: optional en/de/fr
document_type: optional standard/oem/internal
```

Response:

```json
{
  "document_id": "doc_001",
  "job_id": "job_extract_001",
  "status": "queued"
}
```

## Comparison API

Request:

```json
{
  "project_id": "project_001",
  "left_document_id": "doc_v1",
  "right_document_id": "doc_v2",
  "comparison_profile": "strict_compliance",
  "language_policy": "same_language_only"
}
```

Response:

```json
{
  "comparison_id": "cmp_001",
  "job_id": "job_cmp_001",
  "status": "queued"
}
```

## Change item response

```json
{
  "change_id": "CHG-000123",
  "change_type": "numeric_threshold_increased",
  "risk_level": "high",
  "summary": "The required test level increased from 30 V/m to 60 V/m.",
  "impact": "Existing test evidence at 30 V/m may no longer be sufficient.",
  "confidence": 0.94,
  "requires_human_review": false,
  "citations": [
    {
      "evidence_id": "E1",
      "side": "left",
      "document_id": "doc_v1",
      "page": 44,
      "bbox": [72, 380, 520, 410],
      "label": "v1, section 5.3.2, table 12, page 44"
    },
    {
      "evidence_id": "E2",
      "side": "right",
      "document_id": "doc_v2",
      "page": 47,
      "bbox": [72, 390, 520, 420],
      "label": "v2, section 5.3.2, table 12, page 47"
    }
  ]
}
```

## Worker pools

Suggested pools:

```text
worker_extract: CPU/OCR heavy
worker_index: embedding and vector DB writes
worker_compare: alignment/diff logic
worker_llm: explanation generation
worker_export: report exports
```

## Progress events

For UI progress, publish structured events:

```json
{
  "job_id": "job_cmp_001",
  "stage": "aligning_table_rows",
  "progress": 0.42,
  "message": "Aligning 812 table rows"
}
```

## Error model

Errors should be user-readable and operator-readable.

```json
{
  "error_code": "TABLE_EXTRACTION_LOW_CONFIDENCE",
  "user_message": "Some tables could not be extracted reliably and require review.",
  "operator_message": "Table TBL-5-3-2-01 has row confidence below 0.65 on pages 44-45.",
  "recoverable": true
}
```

## Audit event examples

```json
{
  "event_type": "comparison_completed",
  "comparison_id": "cmp_001",
  "left_document_sha256": "...",
  "right_document_sha256": "...",
  "algorithm_version": "1.0.0",
  "model_id": "granite-3.3-8b-instruct",
  "prompt_version": "explain_change_v1",
  "timestamp": "..."
}
```
