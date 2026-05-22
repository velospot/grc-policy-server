# API Contract Blueprint

## Authentication

Production should require authentication. Development may use a local mock user.

Headers:

```http
Authorization: Bearer <local-token>
```

## Health

```http
GET /health
```

Response:

```json
{
  "status": "ok",
  "version": "0.1.0"
}
```

## Create project

```http
POST /api/projects
```

Request:

```json
{
  "name": "OEM EMC comparison project",
  "description": "Release comparison for 2025 standards"
}
```

Response:

```json
{
  "project_id": "project_001",
  "name": "OEM EMC comparison project"
}
```

## Upload document

```http
POST /api/projects/{project_id}/documents
Content-Type: multipart/form-data
```

Fields:

```text
file: PDF
release_label: v1
document_language_hint: en|de|fr optional
document_family: automotive_emc optional
```

Response:

```json
{
  "document_id": "doc_001",
  "sha256": "...",
  "extraction_job_id": "job_001",
  "status": "queued"
}
```

## Get document

```http
GET /api/documents/{document_id}
```

Response:

```json
{
  "document_id": "doc_001",
  "project_id": "project_001",
  "filename": "OEM_EMC_v1.pdf",
  "release_label": "v1",
  "language": "en",
  "status": "indexed",
  "page_count": 120
}
```

## Get document page image

```http
GET /api/documents/{document_id}/pages/{page_number}/image
```

Returns PNG or JPEG page render.

## Get citation highlight

```http
GET /api/citations/{citation_id}
```

Response:

```json
{
  "citation_id": "cit_001",
  "document_id": "doc_001",
  "page": 44,
  "bbox": [72, 380, 520, 410],
  "label": "v1, section 5.3.2, table 12, page 44",
  "quote": "..."
}
```

## Start comparison

```http
POST /api/comparisons
```

Request:

```json
{
  "project_id": "project_001",
  "left_document_id": "doc_v1",
  "right_document_id": "doc_v2",
  "profile": "strict_compliance"
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

## Get comparison

```http
GET /api/comparisons/{comparison_id}
```

Response:

```json
{
  "comparison_id": "cmp_001",
  "project_id": "project_001",
  "left_document_id": "doc_v1",
  "right_document_id": "doc_v2",
  "language": "en",
  "status": "completed",
  "summary": {
    "total_changes": 42,
    "high_risk": 8,
    "requires_review": 3
  }
}
```

## List changes

```http
GET /api/comparisons/{comparison_id}/changes?risk=high&requires_review=false
```

Response:

```json
{
  "items": [
    {
      "change_id": "CHG-0001",
      "change_type": "numeric_threshold_increased",
      "risk_level": "high",
      "section": "5.3.2",
      "title": "Field strength changed",
      "summary": "...",
      "impact": "...",
      "confidence": 0.94,
      "citations": ["cit_left_001", "cit_right_001"]
    }
  ]
}
```

## Review change

```http
POST /api/comparisons/{comparison_id}/changes/{change_id}/review
```

Request:

```json
{
  "review_state": "accepted",
  "comment": "Confirmed by EMC lead."
}
```

## Export report

```http
POST /api/comparisons/{comparison_id}/exports
```

Request:

```json
{
  "format": "markdown"
}
```

Response:

```json
{
  "export_id": "exp_001",
  "job_id": "job_export_001"
}
```
