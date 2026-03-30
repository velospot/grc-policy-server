# Workflow Specification

## 1. Upload Workflow (Async v2)

### Trigger

User uploads one or more documents from the frontend.

### Steps

1. User selects files in `DocumentUploadModal`.
2. Frontend submits multipart payload to `/documents/upload/v2`.
3. Backend API validates payload and enqueues Celery task.
4. API returns `{ jobId, status: "queued" }`.
5. Frontend starts polling `/documents/upload/v2/{jobId}`.
6. Worker processes each file:
   - parse document
   - apply OCR fallback where needed
   - extract semantic metadata
   - upsert chunks to Weaviate
   - persist file + metadata artifacts
7. Polling endpoint transitions through `queued -> running -> finished|failed`.
8. Frontend shows terminal toast and refreshes document list.

### Success Output

- Accepted/rejected counts
- Per-file result with `documentId`, `chunksStored`, and errors if any

### Failure Modes

- Empty or invalid uploads
- Parsing/indexing failure
- Queue/worker unavailability

## 2. Compare Workflow (Async v2)

### Trigger

User selects two documents for comparison.

### Steps

1. Frontend posts compare request to `/v2/compare`.
2. API checks for cached result unless `forceReExtract=true`.
3. If cache hit:
   - immediate finished response with `cacheHit=true`.
4. If cache miss:
   - enqueue worker task and return queued `jobId`.
5. Frontend polls `/v2/compare/response/{job_id}`.
6. Worker:
   - fetches chunks
   - aligns and matches clauses/tables
   - classifies deltas
   - generates summary/action/follow-up
   - stores result in comparison cache
7. Frontend stores and renders comparison output.

### Success Output

- `summary`
- `keyDifferences`
- `actionPlan`
- `followUpQuestions`

### Failure Modes

- Missing document chunks
- LLM/Weaviate dependency failure
- Invalid task payload/result shape

## 3. Document Listing and Deletion Workflow

### Listing

1. Frontend requests `/documents`.
2. API reads metadata records from upload root.
3. Frontend renders searchable/filterable table.

### Deletion

1. Frontend posts document IDs to `/documents/delete`.
2. API deletes:
   - Weaviate chunk records
   - optional Neo4j records
   - local document directory
3. API returns per-document deletion results.

## 4. Job Feedback Workflow (UI)

1. Job IDs are persisted in frontend state store.
2. Polling hooks query upload/compare status endpoints.
3. Top bar `JobStatus` component displays active and recent jobs.
4. Terminal job states trigger success/error toast notifications.

## 5. Proposed New Workflow: URL Import

Current UI "Import from Link" is placeholder behavior and should be replaced.

Proposed production flow:

1. User submits URL in upload modal.
2. Frontend posts URL payload to `/documents/import-url`.
3. Backend validates domain/type/size and downloads with timeout/size guard.
4. Backend enqueues same ingestion pipeline as file upload.
5. Frontend polls standard job status endpoint.

## 6. Proposed New Workflow: Document Preview

1. On successful ingestion, enqueue derivative generation task.
2. Generate:
   - text preview snippet
   - first-page preview image/pdf derivative
3. Store derivative references with document metadata.
4. UI "View" action calls `/documents/{id}/preview` and renders preview.
