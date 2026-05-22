# LLM-Based GRC Comparison Platform

## Architecture and Build Guide

### Purpose

This document defines the target architecture, functional decomposition, service boundaries, data flow, and implementation guidance for an on-prem/offline-capable LLM-based GRC platform. It is written to be used directly by engineering teams and by code-generation tools such as Codex, Claude Code, or similar LLM-based developer assistants.

---

# 1. Product scope

The platform must support:

* Uploading multilingual compliance and regulatory documents
* Supporting versioned revisions released yearly, half-yearly, or irregularly
* Parsing rich document content including:

  * chapters and sections
  * subsections and numbered clauses
  * paragraphs and bullet points
  * tables
  * formulas and equations
  * images, charts, and figures
  * abbreviations and glossary terms
* Comparing two document versions or related standards
* Producing two output modes:

  * **Audit mode**: exhaustive additions, deletions, modifications, with citations and traceability
  * **General mode**: only high-impact, meaningful regulatory changes with explanations and citations
* Providing a chat interface for grounded Q&A over the uploaded corpus and comparison results
* Running initially in offline or on-prem environments

---

# 2. Architecture principles

## Core rules

1. **Structure-first**: comparison must be built on canonical document structure, not only chunked RAG text.
2. **Evidence-first**: the LLM explains changes; it does not determine the raw factual diff.
3. **Model-agnostic**: all models are behind a model gateway.
4. **Extractor-agnostic**: document extractors are pluggable.
5. **On-prem-first**: no runtime dependence on external APIs.
6. **Recoverable suppression**: clutter is suppressed from comparison, not permanently deleted.
7. **Stable canonical model**: raw extraction can vary; the internal document model should not.

---

# 3. Functional blocks

## 3.1 API Gateway

### Responsibilities

* Authenticate requests
* Route to internal services
* Expose public APIs
* Enforce tenant and user authorization
* Rate limiting and request tracing

### Suggested stack

* FastAPI gateway or NGINX + FastAPI

---

## 3.2 Identity and Access Block

### Responsibilities

* User authentication
* RBAC
* Tenant isolation
* Role-aware access to documents, reports, and compare jobs

### Main concepts

* User
* Role
* Tenant
* Workspace
* Policy

---

## 3.3 Ingestion Block

### Responsibilities

* Receive uploaded files
* Create ingestion jobs
* Detect file type and metadata
* Compute checksum and candidate version linkage
* Store original file in object storage

### Output

* Ingestion job created
* Original artifact stored
* Extraction plan requested

---

## 3.4 Extraction Planning Block

### Responsibilities

* Determine which extractors to run
* Detect whether OCR is required
* Detect likely presence of tables, formulas, diagrams, and glossary content
* Route to suitable parsing pipeline

### Inputs

* File metadata
* Sample pages
* MIME type
* Extraction heuristics

### Output

* Execution plan for extractors

---

## 3.5 Parser Abstraction Block

### Responsibilities

* Run primary and specialized extractors
* Preserve raw outputs and provenance
* Provide unified extractor interfaces

### Extractor modules

* Primary extractor: Docling
* OCR extractor: PaddleOCR or Tesseract
* Table extractor: specialized tabular extraction and reconstruction
* Formula extractor: formula detection and normalization
* Vision extractor: optional chart/figure processing
* Language extractor: language identification and glossary tagging

### Rule

Each extractor returns structured output but does not define the platform-wide internal schema.

---

## 3.6 Content Governance and Noise Suppression Block

### Responsibilities

* Classify extracted blocks
* Identify clutter and low-signal content
* Score relevance for comparison and retrieval
* Suppress non-meaningful content from the compare-ready view
* Preserve raw traceability

### Suppress examples

* TOC pages
* index pages
* page headers and footers
* repeated boilerplate
* page numbers
* orphan OCR fragments
* decorative/non-normative text

### Conditionally include examples

* footnotes linked to normative clauses
* annexes marked normative
* warnings and notes affecting obligations

---

## 3.7 Canonical Document Model Block

### Responsibilities

* Merge extractor outputs into one canonical structure
* Build document tree/graph
* Assign stable node ids
* Preserve section hierarchy, order, and citations
* Store structured table/formula/figure payloads

### Output

* Compare-ready canonical nodes
* Retrieval-friendly derivative content

---

## 3.8 Validation and Confidence Block

### Responsibilities

* Validate hierarchy integrity
* Detect orphan nodes, duplicate numbering, broken reading order
* Assign node-level and document-level extraction confidence
* Mark ambiguous content for review or low-confidence handling

---

## 3.9 Persistence Block

### Responsibilities

* Persist raw extraction artifacts
* Persist canonical nodes and comparison metadata
* Persist compare jobs, alignments, change records, summaries, and reports
* Store retrieval artifacts for vector search

### Storage split

* PostgreSQL: canonical and relational records
* Object storage: original files and raw extractor outputs
* Weaviate or vector store: embeddings and retrieval objects

---

## 3.10 Retrieval and Indexing Block

### Responsibilities

* Build semantic retrieval chunks
* Index canonical nodes and selected derived summaries
* Store multilingual embeddings
* Support chat and semantic discovery

### Important rule

Retrieval chunks are **not** the system of record for comparison.

---

## 3.11 Alignment Block

### Responsibilities

* Align source and target document nodes before diffing
* Detect matched, added, removed, moved, split, and merged content
* Use structural, lexical, and semantic signals

### Signals

* section numbering similarity
* heading similarity
* semantic similarity
* parent lineage similarity
* node-type compatibility
* position within hierarchy
* table schema similarity
* formula similarity

---

## 3.12 Diff and Change Detection Block

### Responsibilities

* Compute node-level diffs after alignment
* Apply content-type-specific diff strategies
* Identify text changes, numeric changes, obligation changes, and structure changes

### Diff types

* text diff
* table diff
* formula diff
* structural diff
* scope/applicability diff
* glossary/definition diff

---

## 3.13 Change Record Generation Block

### Responsibilities

* Convert raw diffs into stable, machine-readable change records
* Attach citations, significance candidates, confidence, and categories
* Provide the factual basis for reports and chat

### Change record examples

* paragraph modified
* threshold tightened from X to Y
* row added to compliance table
* definition changed
* clause moved and modified

---

## 3.14 Significance Scoring Block

### Responsibilities

* Rank changes by impact
* Distinguish editorial vs meaningful changes
* Support audit mode and general mode views

### Signals

* modal verb changes (shall/must/may)
* scope/applicability changes
* numeric threshold changes
* testing requirement changes
* reporting obligation changes
* safety/risk changes
* data governance changes

---

## 3.15 Reasoning and Summary Block

### Responsibilities

* Generate audit-mode narrative
* Generate general-mode summary
* Explain what a change means using grounded evidence only
* Optionally generate recommendations and follow-up questions

### Rule

LLM input must be structured change records plus citations, not raw chunks alone.

---

## 3.16 Chat and Q&A Block

### Responsibilities

* Accept user questions
* Retrieve evidence from canonical nodes, change records, glossary, and retrieval chunks
* Generate grounded answers with citations
* Support compare-aware and corpus-aware questions

### Chat queries may include

* What changed in section X?
* Show only critical changes
* Which changes affect testing obligations?
* Summarize all threshold changes

---

## 3.17 Reporting and Export Block

### Responsibilities

* Render audit reports
* Render business/general summaries
* Export PDF, DOCX, JSON, CSV/XLSX if required later

---

## 3.18 Workflow and Orchestration Block

### Responsibilities

* Manage Celery workflows and long-running jobs
* Maintain job state machine
* Support retries and partial completion
* Capture diagnostics

---

## 3.19 Observability and Audit Block

### Responsibilities

* Log ingestion and compare stages
* Trace prompt/model versioning
* Track extraction confidence and suppression decisions
* Track user access and report generation
* Support reproducibility and internal QA

---

# 4. Logical architecture diagram

```text
[Next.js Frontend]
      |
[API Gateway / FastAPI]
      |
 -------------------------------------------------
 |            |            |            |         |
Auth      Ingestion     Compare       Chat     Admin
               |            |            |
        Extraction Plan   Alignment   Retrieval
               |            |            |
        Parser Layer     Diff Engine  Vector Search
               |            |            |
     Content Governance  Change Records  LLM Gateway
               |            |            |
      Canonical Model ---- Reports ---- Summaries
               |
  -----------------------------------------------
  |               |                |             |
PostgreSQL     Object Store     Vector DB     Observability
```

---

# 5. Recommended data stores

## PostgreSQL

Use as the system of record for:

* documents
* versions
* canonical nodes
* tables/formulas metadata
* compare jobs
* alignments
* change records
* reports
* citations
* audit events

Use JSONB for:

* raw node metadata
* table JSON
* diff payloads
* extractor diagnostics

## Object storage

Use for:

* original files
* raw Docling JSON
* OCR output
* table extraction artifacts
* figure snapshots
* report binaries

## Vector store

Use for:

* embeddings
* retrieval chunks
* semantic search for chat and candidate matching

---

# 6. Core domain model

## Main entities

* Document
* DocumentVersion
* RawBlock
* CanonicalNode
* CanonicalTable
* CanonicalFormula
* ComparisonJob
* AlignmentPair
* ChangeRecord
* ComparisonSummary
* ChatSession
* Citation
* SuppressionDecision

---

# 7. Processing workflows

## 7.1 Upload and ingestion workflow

```text
Upload
 -> Create ingestion job
 -> Store original file
 -> Plan extraction
 -> Run Docling
 -> Run OCR if needed
 -> Run table/formula/vision enrichment if needed
 -> Classify blocks and suppress clutter
 -> Build canonical nodes
 -> Validate and score confidence
 -> Persist canonical structure
 -> Build retrieval chunks and embeddings
 -> Mark version ready
```

## 7.2 Comparison workflow

```text
User selects source and target
 -> Create comparison job
 -> Load canonical nodes
 -> Align nodes
 -> Diff aligned nodes
 -> Diff tables/formulas separately
 -> Generate change records
 -> Score significance
 -> Generate audit/general summaries
 -> Persist results
 -> Return worker/API response
```

## 7.3 Chat workflow

```text
User asks question
 -> Classify intent
 -> Retrieve from change records + canonical nodes + vector store
 -> Rerank evidence
 -> Generate grounded answer
 -> Attach citations
 -> Persist chat turn
```

---

# 8. Comparison DTO versus internal model

## Internal comparison model

Used by backend only.
Contains:

* alignment details
* node ids
* change type
* structured diff data
* confidence
* significance
* debug metadata

## External comparison DTO

Used by worker API or frontend.
Contains:

* summary
* key differences
* source/target citations
* optional action plan
* follow-up questions
* stats and status

The DTO should be a projection of the internal model, not the only data model.

---

# 9. Microservice split

Start as a **modular monolith** if the team is small. Split later when needed.

## Recommended future service boundaries

### Service 1: gateway-service

* auth integration
* public API
* request tracing

### Service 2: ingestion-service

* uploads
* metadata
* extraction planning
* raw artifact registration

### Service 3: extraction-service

* Docling integration
* OCR integration
* parser adapters
* canonicalization pre-merge

### Service 4: normalization-service

* structure normalization
* classification
* suppression decisions
* confidence scoring

### Service 5: comparison-service

* alignments
* diffs
* change records
* statistics

### Service 6: reasoning-service

* summary generation
* recommendation generation
* follow-up question generation
* chat orchestration

### Service 7: retrieval-service

* embeddings
* vector indexing
* hybrid retrieval
* reranking

### Service 8: reporting-service

* report assembly
* exports

### Service 9: admin-config-service

* ontology/policy config
* significance rules
* parser/model settings

### Service 10: observability-service

* audit logs
* prompt versions
* run diagnostics

---

# 10. Celery task graph

## Ingestion tasks

* `create_ingestion_job(version_id)`
* `plan_extraction(version_id)`
* `run_docling(version_id)`
* `run_ocr_if_needed(version_id)`
* `run_table_extraction_if_needed(version_id)`
* `run_formula_extraction_if_needed(version_id)`
* `run_vision_extraction_if_needed(version_id)`
* `classify_blocks(version_id)`
* `score_relevance(version_id)`
* `build_canonical_nodes(version_id)`
* `validate_document(version_id)`
* `persist_document(version_id)`
* `index_retrieval_artifacts(version_id)`

## Comparison tasks

* `create_comparison_job(source_version_id, target_version_id)`
* `load_comparison_scope(comparison_id)`
* `align_nodes(comparison_id)`
* `diff_nodes(comparison_id)`
* `diff_tables(comparison_id)`
* `generate_change_records(comparison_id)`
* `score_significance(comparison_id)`
* `generate_summary(comparison_id, mode)`
* `generate_followup_questions(comparison_id)`
* `finalize_comparison(comparison_id)`

## Chat tasks

* `prepare_chat_context(session_id, query)`
* `retrieve_evidence(session_id, query)`
* `rerank_evidence(session_id)`
* `generate_grounded_answer(session_id)`
* `persist_chat_turn(session_id)`

---

# 11. On-prem deployment view

## Single-node PoC

* frontend
* FastAPI backend
* Celery worker
* PostgreSQL
* Redis
* object storage
* vector store
* local model runtime

## Small enterprise

* app node
* worker node
* GPU model node
* DB/object storage node

## Scaled enterprise

* Kubernetes
* separate extraction, comparison, reasoning, and retrieval services
* GPU pool for model gateway

---

# 12. Recommended tech stack

## Frontend

* Next.js
* React
* TypeScript
* Tailwind
* PDF.js viewer

## Backend

* Python
* FastAPI
* Celery + Redis

## Core stores

* PostgreSQL
* S3-compatible object storage / MinIO
* Weaviate or equivalent vector store

## Parsing and extraction

* Docling
* PaddleOCR or Tesseract
* custom table parser/post-processing
* SymPy or formula parsing helpers
* optional local multimodal model for figures/charts

## Models

* local LLM served via vLLM / TGI / Ollama depending environment
* multilingual embeddings
* optional reranker

## Observability

* OpenTelemetry
* Prometheus
* Grafana
* structured logs

---

# 13. How to use this document with Codex, Claude Code, or similar tools

## Guidance for code-generation tools

Use the following instructions when generating implementation code:

### Mandatory constraints

* Language: Python 3.11+
* API framework: FastAPI
* Background processing: Celery
* Storage: PostgreSQL + SQLAlchemy or SQLModel
* Object storage adapter must be interface-based
* Vector store must be interface-based
* Services must be written so they can run inside a modular monolith first, but be split into microservices later
* All business logic must be separated from transport/controller layers
* Use type hints throughout
* Use Pydantic models for external DTOs
* Use repository/service layers for persistence
* Add structured logging and clear error boundaries
* Keep offline/on-prem compatibility; do not use external SaaS APIs in generated runtime code

### Architecture generation rules

When generating code:

1. Create internal domain models separately from API DTOs.
2. Do not use retrieval chunks as the source of truth for comparison.
3. Build extractor adapters behind interfaces.
4. Create canonical document nodes as first-class persisted objects.
5. Add compare-job state handling and resumable Celery workflows.
6. Add suppression/classification metadata for noisy content.
7. Generate code that is testable with dependency injection.
8. Keep models and services deterministic where possible.

---

# 14. Suggested prompts for code-generation tools

## Prompt: generate backend skeleton

```text
Generate a Python FastAPI backend skeleton for an on-prem LLM-based GRC platform.

Requirements:
- Modular monolith layout designed for future microservice extraction
- FastAPI for APIs
- Celery for background jobs
- PostgreSQL persistence using SQLAlchemy or SQLModel
- Pydantic schemas for DTOs
- Repository and service layers
- Modules: ingestion, extraction, normalization, comparison, reasoning, retrieval, reporting
- Include a compare-job state machine
- Include canonical document node entities, change record entities, and comparison result DTOs
- Include interfaces for object storage, vector store, and model gateway
- Use dependency injection-friendly patterns
- Add structured logging and config management
- Output a production-style project layout and starter code files
```

## Prompt: generate ingestion module

```text
Generate a Python ingestion module for a GRC platform.

Requirements:
- Accept uploaded files and create ingestion jobs
- Plan extractor execution based on file metadata
- Support Docling as primary extractor
- Support optional OCR/table extraction hooks
- Persist raw extraction metadata and build canonical document nodes
- Add block classification and suppression decisions for TOC, headers, footers, index pages, orphan OCR fragments, and non-meaningful text
- Include Celery tasks and Pydantic request/response models
- Use repository pattern and type hints
```

## Prompt: generate comparison engine

```text
Generate a Python comparison engine module for regulatory documents.

Requirements:
- Input: canonical nodes from two document versions
- Stages: candidate alignment, alignment resolution, text diff, table diff, change record generation, significance scoring
- Detect: added, removed, modified, moved, split, merged changes
- Return internal change records and a projection function that creates a ComparisonResult DTO
- Include confidence and citations
- Keep logic deterministic and testable
```

## Prompt: generate internal and external models

```text
Generate Python domain models and Pydantic DTOs for a GRC comparison platform.

Requirements:
- Internal models: Document, DocumentVersion, CanonicalNode, CanonicalTable, ComparisonJob, AlignmentPair, ChangeRecord
- External DTOs: ComparisonResult, KeyDifference, CitationReference, ComparisonStats
- Keep internal models separate from DTOs
- Use enums where appropriate
- Add type hints and docstrings
```

## Prompt: generate service interfaces

```text
Generate Python interface definitions and basic implementations for:
- ObjectStorageProvider
- VectorStoreProvider
- ModelGateway
- ExtractorAdapter
- ComparisonRepository
- DocumentRepository

Use abstract base classes or protocols. Add dependency injection-friendly patterns.
```

---

# 15. Repository structure recommendation

```text
backend/
  app/
    api/
      routes/
      dto/
    core/
      config.py
      logging.py
      enums.py
    domain/
      models/
      services/
      policies/
    repositories/
      document_repository.py
      comparison_repository.py
    ingestion/
      planner.py
      service.py
      tasks.py
    extraction/
      adapters/
      docling_adapter.py
      ocr_adapter.py
      table_adapter.py
      merge.py
    normalization/
      classifier.py
      suppressor.py
      canonicalizer.py
      validator.py
    comparison/
      aligner.py
      differ.py
      change_records.py
      scorer.py
      projector.py
    reasoning/
      summarizer.py
      questions.py
      chat.py
    retrieval/
      chunker.py
      embeddings.py
      search.py
    reporting/
      renderer.py
    infra/
      db/
      object_storage/
      vector_store/
      model_gateway/
    workers/
      celery_app.py
```

---

# 16. Service API contracts

This section defines recommended internal and external API contracts for the platform. The goal is to keep transport DTOs stable even as internal implementation evolves.

## 16.1 Common response envelope

Use a standard envelope for synchronous HTTP APIs.

```json
{
  "success": true,
  "data": {},
  "error": null,
  "request_id": "req_123",
  "timestamp": "2026-04-10T10:15:00Z"
}
```

For async job creation APIs, return a job reference immediately.

```json
{
  "success": true,
  "data": {
    "job_id": "cmp_001",
    "status": "queued"
  },
  "error": null,
  "request_id": "req_124",
  "timestamp": "2026-04-10T10:15:03Z"
}
```

---

## 16.2 Ingestion service API contracts

### POST /api/v1/documents/upload

Uploads one or more files and creates ingestion jobs.

**Request**

* multipart form data
* fields:

  * `file`
  * `document_name` optional
  * `workspace_id` optional
  * `language_hint` optional
  * `revision_label` optional
  * `effective_date` optional
  * `metadata_json` optional

**Response DTO**

```json
{
  "document_id": "doc_123",
  "version_id": "ver_001",
  "ingestion_job_id": "ing_001",
  "status": "uploaded"
}
```

### GET /api/v1/documents

List documents with filters.

**Query params**

* `workspace_id`
* `status`
* `language`
* `q`
* `limit`
* `offset`

### GET /api/v1/documents/{document_id}

Return document metadata and available versions.

### GET /api/v1/versions/{version_id}

Return version metadata and ingestion status.

### GET /api/v1/versions/{version_id}/ingestion-status

Return the ingestion pipeline state.

**Response DTO**

```json
{
  "version_id": "ver_001",
  "status": "processing",
  "current_stage": "build_canonical_nodes",
  "stages": [
    {"name": "run_docling", "status": "completed"},
    {"name": "classify_blocks", "status": "completed"},
    {"name": "build_canonical_nodes", "status": "running"}
  ]
}
```

### GET /api/v1/versions/{version_id}/structure

Return compare-ready canonical structure.

### GET /api/v1/versions/{version_id}/suppression-report

Return suppressed blocks and reasons.

**Response DTO**

```json
{
  "version_id": "ver_001",
  "suppressed_count": 42,
  "by_reason": {
    "toc_page": 8,
    "page_footer": 12,
    "ocr_orphan": 5,
    "boilerplate": 17
  },
  "ambiguous_count": 3
}
```

---

## 16.3 Comparison service API contracts

### POST /api/v1/comparisons

Create a comparison job.

**Request DTO**

```json
{
  "source_version_id": "ver_001",
  "target_version_id": "ver_002",
  "mode": "general",
  "workspace_id": "ws_001",
  "options": {
    "include_low_confidence": false,
    "include_editorial": false,
    "max_key_differences": 25
  }
}
```

**Response DTO**

```json
{
  "comparison_id": "cmp_001",
  "status": "queued"
}
```

### GET /api/v1/comparisons/{comparison_id}

Return comparison job status and high-level result metadata.

### GET /api/v1/comparisons/{comparison_id}/status

**Response DTO**

```json
{
  "comparison_id": "cmp_001",
  "status": "processing",
  "current_stage": "generate_change_records",
  "progress_percent": 72
}
```

### GET /api/v1/comparisons/{comparison_id}/result

Return the external comparison DTO.

**Response DTO shape**

* `comparisonId`
* `status`
* `summary`
* `keyDifferences`
* `actionPlan` optional
* `followUpQuestions` optional
* `stats`
* `diagnostics` optional

### GET /api/v1/comparisons/{comparison_id}/change-records

Return full internal change records for advanced views.

### GET /api/v1/comparisons/{comparison_id}/findings

Return grouped findings for the UI.

### GET /api/v1/comparisons/{comparison_id}/diff-tree

Return hierarchical compare tree.

### GET /api/v1/comparisons/{comparison_id}/diagnostics

Return alignment, suppression, and confidence diagnostics.

### POST /api/v1/comparisons/{comparison_id}/summaries/regenerate

Regenerate audit or general summaries from existing change records.

**Request DTO**

```json
{
  "mode": "audit"
}
```

---

## 16.4 Chat service API contracts

### POST /api/v1/chat/sessions

Create a chat session.

**Request DTO**

```json
{
  "workspace_id": "ws_001",
  "scope": {
    "document_ids": ["doc_123"],
    "comparison_ids": ["cmp_001"]
  }
}
```

### POST /api/v1/chat/sessions/{session_id}/messages

Submit a question.

**Request DTO**

```json
{
  "message": "What are the critical changes in testing thresholds?"
}
```

**Response DTO**

```json
{
  "answer": "Two critical threshold changes were identified...",
  "citations": [],
  "related_change_ids": ["chg_001", "chg_004"]
}
```

### GET /api/v1/chat/sessions/{session_id}

Return session metadata.

### GET /api/v1/chat/sessions/{session_id}/history

Return prior turns.

---

## 16.5 Reporting service API contracts

### POST /api/v1/reports

Create a report artifact.

**Request DTO**

```json
{
  "comparison_id": "cmp_001",
  "mode": "audit",
  "format": "pdf"
}
```

### GET /api/v1/reports/{report_id}

Return report metadata.

### GET /api/v1/reports/{report_id}/download

Download rendered report.

---

## 16.6 Admin/config service API contracts

### GET /api/v1/admin/policies

### POST /api/v1/admin/policies

### GET /api/v1/admin/models

### POST /api/v1/admin/model-routing

### GET /api/v1/admin/extractors

### POST /api/v1/admin/extractors/test

These APIs support parser and model configuration, policy tuning, and diagnostics.

---

# 17. Backend skeleton

This section defines a production-oriented modular monolith skeleton that can be split into microservices later.

## 17.1 Folder structure

```text
backend/
  app/
    main.py
    api/
      dependencies.py
      routes/
        documents.py
        comparisons.py
        chat.py
        reports.py
        admin.py
      dto/
        common.py
        document_dto.py
        comparison_dto.py
        chat_dto.py
        report_dto.py
    core/
      config.py
      logging.py
      enums.py
      exceptions.py
      security.py
    domain/
      models/
        document.py
        version.py
        raw_block.py
        canonical_node.py
        canonical_table.py
        comparison_job.py
        alignment_pair.py
        change_record.py
        chat_session.py
        citation.py
        suppression_decision.py
      value_objects/
        references.py
        confidence.py
        diff_types.py
      services/
        ingestion_service.py
        extraction_service.py
        normalization_service.py
        comparison_service.py
        retrieval_service.py
        reasoning_service.py
        report_service.py
        chat_service.py
      policies/
        suppression_policy.py
        significance_policy.py
        extraction_policy.py
    repositories/
      document_repository.py
      version_repository.py
      comparison_repository.py
      chat_repository.py
      report_repository.py
    ingestion/
      planner.py
      orchestrator.py
      tasks.py
    extraction/
      adapters/
        base.py
        docling_adapter.py
        ocr_adapter.py
        table_adapter.py
        formula_adapter.py
        vision_adapter.py
      merge.py
      provenance.py
    normalization/
      classifier.py
      suppressor.py
      canonicalizer.py
      validator.py
      confidence.py
    comparison/
      aligner.py
      resolver.py
      differ.py
      table_differ.py
      formula_differ.py
      change_record_builder.py
      scorer.py
      projector.py
    reasoning/
      summarizer.py
      question_generator.py
      prompt_builder.py
    retrieval/
      chunker.py
      embeddings.py
      indexer.py
      search.py
      reranker.py
    reporting/
      renderer.py
      exporters.py
    infra/
      db/
        base.py
        models.py
        session.py
      object_storage/
        base.py
        minio_provider.py
      vector_store/
        base.py
        weaviate_provider.py
      model_gateway/
        base.py
        local_llm_gateway.py
      messaging/
        celery_app.py
    workers/
      ingestion_worker.py
      comparison_worker.py
      chat_worker.py
  tests/
    unit/
    integration/
  alembic/
  requirements.txt
  pyproject.toml
  Dockerfile
  docker-compose.yml
```

---

## 17.2 Starter code skeleton

### `app/main.py`

```python
from fastapi import FastAPI
from app.api.routes import documents, comparisons, chat, reports, admin


def create_app() -> FastAPI:
    app = FastAPI(title="GRC Platform API", version="0.1.0")
    app.include_router(documents.router, prefix="/api/v1/documents", tags=["documents"])
    app.include_router(comparisons.router, prefix="/api/v1/comparisons", tags=["comparisons"])
    app.include_router(chat.router, prefix="/api/v1/chat", tags=["chat"])
    app.include_router(reports.router, prefix="/api/v1/reports", tags=["reports"])
    app.include_router(admin.router, prefix="/api/v1/admin", tags=["admin"])
    return app


app = create_app()
```

### `app/api/dto/common.py`

```python
from datetime import datetime
from typing import Generic, Optional, TypeVar
from pydantic import BaseModel

T = TypeVar("T")


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: Optional[dict] = None


class ApiResponse(BaseModel, Generic[T]):
    success: bool = True
    data: Optional[T] = None
    error: Optional[ErrorResponse] = None
    request_id: Optional[str] = None
    timestamp: datetime
```

### `app/api/dto/comparison_dto.py`

```python
from typing import List, Literal, Optional
from pydantic import BaseModel


class CitationReferenceDTO(BaseModel):
    document_id: Optional[str] = None
    version_id: Optional[str] = None
    node_id: Optional[str] = None
    section: str
    page: int
    line_start: Optional[int] = None
    line_end: Optional[int] = None
    source_text: str


class KeyDifferenceDTO(BaseModel):
    change_id: str
    section: str
    category: Optional[str] = None
    change_type: Optional[Literal["added", "removed", "modified", "moved", "split", "merged"]] = None
    doc1_content: str
    doc2_content: str
    impact: Literal["Low", "Medium", "High", "Critical"]
    confidence: Optional[float] = None
    rationale: Optional[str] = None
    tags: Optional[List[str]] = None
    doc1_reference: CitationReferenceDTO
    doc2_reference: CitationReferenceDTO


class ComparisonStatsDTO(BaseModel):
    total_differences: int
    high_impact: int
    critical_impact: int


class ComparisonResultDTO(BaseModel):
    comparison_id: str
    status: Literal["completed", "failed", "partial", "processing", "queued"]
    summary: Optional[str] = None
    key_differences: List[KeyDifferenceDTO] = []
    action_plan: Optional[list] = None
    follow_up_questions: Optional[List[str]] = None
    stats: Optional[ComparisonStatsDTO] = None
    diagnostics: Optional[dict] = None
```

### `app/domain/models/change_record.py`

```python
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ChangeRecord:
    change_id: str
    comparison_id: str
    source_node_id: Optional[str]
    target_node_id: Optional[str]
    category: str
    change_type: str
    significance: str
    confidence: float
    title: str
    source_citation_json: dict
    target_citation_json: dict
    diff_json: dict = field(default_factory=dict)
    impact_json: dict = field(default_factory=dict)
```

### `app/domain/services/comparison_service.py`

```python
from app.domain.models.change_record import ChangeRecord


class ComparisonService:
    def __init__(self, comparison_repository, document_repository, reasoning_service):
        self.comparison_repository = comparison_repository
        self.document_repository = document_repository
        self.reasoning_service = reasoning_service

    def create_job(self, source_version_id: str, target_version_id: str, mode: str) -> str:
        return self.comparison_repository.create_job(
            source_version_id=source_version_id,
            target_version_id=target_version_id,
            mode=mode,
        )

    def finalize_result_projection(self, comparison_id: str):
        change_records = self.comparison_repository.get_change_records(comparison_id)
        return self.reasoning_service.project_comparison_result(comparison_id, change_records)
```

### `app/infra/messaging/celery_app.py`

```python
from celery import Celery

celery_app = Celery(
    "grc_platform",
    broker="redis://redis:6379/0",
    backend="redis://redis:6379/1",
)

celery_app.conf.task_routes = {
    "app.ingestion.tasks.*": {"queue": "ingestion"},
    "app.workers.comparison_worker.*": {"queue": "comparison"},
    "app.workers.chat_worker.*": {"queue": "chat"},
}
```

### `app/workers/comparison_worker.py`

```python
from app.infra.messaging.celery_app import celery_app


@celery_app.task(name="app.workers.comparison_worker.run_comparison")
def run_comparison(comparison_id: str) -> None:
    # Load scope
    # Align nodes
    # Diff nodes and tables
    # Generate change records
    # Score significance
    # Generate summary
    # Persist results
    return None
```

---

## 17.3 Backend implementation rules

### Rule 1

Internal domain models must not depend on FastAPI or Pydantic.

### Rule 2

API DTOs must not be used as persistence entities.

### Rule 3

Comparison logic must consume canonical nodes from the canonical store, not retrieval chunks from the vector store.

### Rule 4

Every long-running workflow must have a job state table and stage-level progress updates.

### Rule 5

Suppression decisions must be persisted with reasons.

### Rule 6

LLM prompts must be built from structured change records and citations.

---

# 18. Database schema, ORM guidance, job state machine, vector store, and graph layer

This section locks the persistence shape so code-generation tools can produce a stable backend foundation.

## 18.1 Persistence strategy

### System of record

Use PostgreSQL as the authoritative store for:

* documents
* versions
* raw block registry
* canonical nodes
* canonical tables/formulas/figures metadata
* suppression decisions
* ingestion jobs
* comparison jobs
* alignment pairs
* change records
* summaries
* chat sessions and turns
* citations
* reports

### Retrieval layer

Use a vector store for:

* retrieval chunks
* node embeddings
* semantic candidate search
* compare-assist candidate generation
* chat retrieval

### Optional graph layer

Use Neo4j later if relationship-heavy queries become central, especially:

* cross-document traceability
* control mapping
* ontology relationships
* clause-to-clause lineage across multiple revisions
* impacted-control and impacted-process traversal queries

---

## 18.2 Relational schema overview

### Core tables

* `documents`
* `document_versions`
* `raw_artifacts`
* `raw_blocks`
* `canonical_nodes`
* `canonical_tables`
* `canonical_formulas`
* `canonical_figures`
* `suppression_decisions`
* `ingestion_jobs`
* `ingestion_job_events`
* `comparison_jobs`
* `comparison_job_events`
* `alignment_pairs`
* `change_records`
* `comparison_summaries`
* `chat_sessions`
* `chat_turns`
* `citations`
* `reports`

---

## 18.3 Suggested relational schema

## `documents`

```sql
CREATE TABLE documents (
  id UUID PRIMARY KEY,
  workspace_id UUID NULL,
  tenant_id UUID NULL,
  name TEXT NOT NULL,
  domain TEXT NULL,
  standard_family TEXT NULL,
  source_type TEXT NULL,
  language TEXT NULL,
  jurisdiction TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `document_versions`

```sql
CREATE TABLE document_versions (
  id UUID PRIMARY KEY,
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  revision_label TEXT NULL,
  version_number TEXT NULL,
  effective_date DATE NULL,
  publication_date DATE NULL,
  checksum TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  original_filename TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  parser_version TEXT NULL,
  extraction_status TEXT NOT NULL DEFAULT 'uploaded',
  extraction_confidence NUMERIC(5,4) NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(document_id, checksum)
);
```

## `raw_artifacts`

```sql
CREATE TABLE raw_artifacts (
  id UUID PRIMARY KEY,
  version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  artifact_type TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `raw_blocks`

```sql
CREATE TABLE raw_blocks (
  id UUID PRIMARY KEY,
  version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  extractor_name TEXT NOT NULL,
  block_type TEXT NOT NULL,
  page_from INT NULL,
  page_to INT NULL,
  order_index INT NOT NULL,
  raw_text TEXT NULL,
  bbox_json JSONB NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence NUMERIC(5,4) NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `canonical_nodes`

```sql
CREATE TABLE canonical_nodes (
  id UUID PRIMARY KEY,
  version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  parent_id UUID NULL REFERENCES canonical_nodes(id) ON DELETE CASCADE,
  node_type TEXT NOT NULL,
  section_label TEXT NULL,
  title TEXT NULL,
  heading_path_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  order_index INT NOT NULL,
  raw_text TEXT NULL,
  normalized_text TEXT NULL,
  language TEXT NULL,
  page_from INT NULL,
  page_to INT NULL,
  bbox_json JSONB NULL,
  provenance_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  compare_score NUMERIC(5,4) NULL,
  retrieval_score NUMERIC(5,4) NULL,
  noise_score NUMERIC(5,4) NULL,
  normative_score NUMERIC(5,4) NULL,
  confidence NUMERIC(5,4) NULL,
  comparison_decision TEXT NOT NULL DEFAULT 'include',
  retrieval_decision TEXT NOT NULL DEFAULT 'include',
  suppression_reason TEXT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Indexes recommended:

* `(version_id, order_index)`
* `(version_id, node_type)`
* `(version_id, section_label)`
* GIN on `heading_path_json`
* GIN on `metadata_json`

## `canonical_tables`

```sql
CREATE TABLE canonical_tables (
  id UUID PRIMARY KEY,
  node_id UUID NOT NULL UNIQUE REFERENCES canonical_nodes(id) ON DELETE CASCADE,
  caption TEXT NULL,
  headers_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  rows_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  footnotes_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  markdown_render TEXT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `canonical_formulas`

```sql
CREATE TABLE canonical_formulas (
  id UUID PRIMARY KEY,
  node_id UUID NOT NULL UNIQUE REFERENCES canonical_nodes(id) ON DELETE CASCADE,
  raw_expression TEXT NULL,
  normalized_expression TEXT NULL,
  symbols_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `canonical_figures`

```sql
CREATE TABLE canonical_figures (
  id UUID PRIMARY KEY,
  node_id UUID NOT NULL UNIQUE REFERENCES canonical_nodes(id) ON DELETE CASCADE,
  caption TEXT NULL,
  summary_text TEXT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `suppression_decisions`

```sql
CREATE TABLE suppression_decisions (
  id UUID PRIMARY KEY,
  version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  raw_block_id UUID NULL REFERENCES raw_blocks(id) ON DELETE SET NULL,
  canonical_node_id UUID NULL REFERENCES canonical_nodes(id) ON DELETE SET NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  confidence NUMERIC(5,4) NULL,
  details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `ingestion_jobs`

```sql
CREATE TABLE ingestion_jobs (
  id UUID PRIMARY KEY,
  version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  status TEXT NOT NULL,
  current_stage TEXT NULL,
  progress_percent INT NOT NULL DEFAULT 0,
  error_code TEXT NULL,
  error_message TEXT NULL,
  started_at TIMESTAMPTZ NULL,
  completed_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `ingestion_job_events`

```sql
CREATE TABLE ingestion_job_events (
  id UUID PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES ingestion_jobs(id) ON DELETE CASCADE,
  stage_name TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `comparison_jobs`

```sql
CREATE TABLE comparison_jobs (
  id UUID PRIMARY KEY,
  source_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  target_version_id UUID NOT NULL REFERENCES document_versions(id) ON DELETE CASCADE,
  mode TEXT NOT NULL,
  status TEXT NOT NULL,
  current_stage TEXT NULL,
  progress_percent INT NOT NULL DEFAULT 0,
  options_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  statistics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  error_code TEXT NULL,
  error_message TEXT NULL,
  started_at TIMESTAMPTZ NULL,
  completed_at TIMESTAMPTZ NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `comparison_job_events`

```sql
CREATE TABLE comparison_job_events (
  id UUID PRIMARY KEY,
  job_id UUID NOT NULL REFERENCES comparison_jobs(id) ON DELETE CASCADE,
  stage_name TEXT NOT NULL,
  event_type TEXT NOT NULL,
  payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `alignment_pairs`

```sql
CREATE TABLE alignment_pairs (
  id UUID PRIMARY KEY,
  comparison_job_id UUID NOT NULL REFERENCES comparison_jobs(id) ON DELETE CASCADE,
  source_node_id UUID NULL REFERENCES canonical_nodes(id) ON DELETE CASCADE,
  target_node_id UUID NULL REFERENCES canonical_nodes(id) ON DELETE CASCADE,
  alignment_type TEXT NOT NULL,
  match_score NUMERIC(5,4) NOT NULL,
  confidence NUMERIC(5,4) NULL,
  rationale_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Indexes recommended:

* `(comparison_job_id, alignment_type)`
* `(comparison_job_id, source_node_id)`
* `(comparison_job_id, target_node_id)`

## `change_records`

```sql
CREATE TABLE change_records (
  id UUID PRIMARY KEY,
  comparison_job_id UUID NOT NULL REFERENCES comparison_jobs(id) ON DELETE CASCADE,
  source_node_id UUID NULL REFERENCES canonical_nodes(id) ON DELETE SET NULL,
  target_node_id UUID NULL REFERENCES canonical_nodes(id) ON DELETE SET NULL,
  category TEXT NOT NULL,
  change_type TEXT NOT NULL,
  significance TEXT NOT NULL,
  confidence NUMERIC(5,4) NOT NULL,
  title TEXT NOT NULL,
  diff_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  impact_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  source_citation_json JSONB NULL,
  target_citation_json JSONB NULL,
  review_flags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

Indexes recommended:

* `(comparison_job_id, significance)`
* `(comparison_job_id, change_type)`
* `(comparison_job_id, category)`

## `comparison_summaries`

```sql
CREATE TABLE comparison_summaries (
  id UUID PRIMARY KEY,
  comparison_job_id UUID NOT NULL REFERENCES comparison_jobs(id) ON DELETE CASCADE,
  mode TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  findings_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  follow_up_questions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  action_plan_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(comparison_job_id, mode)
);
```

## `chat_sessions`

```sql
CREATE TABLE chat_sessions (
  id UUID PRIMARY KEY,
  workspace_id UUID NULL,
  scope_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `chat_turns`

```sql
CREATE TABLE chat_turns (
  id UUID PRIMARY KEY,
  session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
  role TEXT NOT NULL,
  message_text TEXT NOT NULL,
  retrieved_evidence_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  citations_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

## `reports`

```sql
CREATE TABLE reports (
  id UUID PRIMARY KEY,
  comparison_job_id UUID NOT NULL REFERENCES comparison_jobs(id) ON DELETE CASCADE,
  mode TEXT NOT NULL,
  format TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

---

## 18.4 SQLAlchemy / SQLModel generation guidance

### Rules for ORM generation

* Use UUID primary keys everywhere
* Separate ORM models from API DTOs
* Use JSON/JSONB columns for flexible payloads
* Keep canonical node model explicit in columns and store variable fields in `metadata_json`
* Add repository methods for all job-state transitions
* Add explicit indexes for version, comparison job, node type, and significance lookups

### Example ORM skeleton

```python
from uuid import uuid4
from sqlalchemy import Column, String, Text, Integer, ForeignKey, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.sql import func

Base = declarative_base()


class DocumentORM(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    name = Column(Text, nullable=False)
    domain = Column(Text, nullable=True)
    standard_family = Column(Text, nullable=True)
    language = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    versions = relationship("DocumentVersionORM", back_populates="document", cascade="all, delete-orphan")
```

```python
class DocumentVersionORM(Base):
    __tablename__ = "document_versions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    document_id = Column(UUID(as_uuid=True), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False)
    revision_label = Column(Text, nullable=True)
    checksum = Column(Text, nullable=False)
    mime_type = Column(Text, nullable=False)
    original_filename = Column(Text, nullable=False)
    storage_uri = Column(Text, nullable=False)
    extraction_status = Column(Text, nullable=False, default="uploaded")
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())

    document = relationship("DocumentORM", back_populates="versions")
```

```python
class CanonicalNodeORM(Base):
    __tablename__ = "canonical_nodes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid4)
    version_id = Column(UUID(as_uuid=True), ForeignKey("document_versions.id", ondelete="CASCADE"), nullable=False)
    parent_id = Column(UUID(as_uuid=True), ForeignKey("canonical_nodes.id", ondelete="CASCADE"), nullable=True)
    node_type = Column(Text, nullable=False)
    section_label = Column(Text, nullable=True)
    title = Column(Text, nullable=True)
    heading_path_json = Column(JSON, nullable=False, default=list)
    order_index = Column(Integer, nullable=False)
    raw_text = Column(Text, nullable=True)
    normalized_text = Column(Text, nullable=True)
    metadata_json = Column(JSON, nullable=False, default=dict)
    confidence = Column(String, nullable=True)
```

Generate the rest of the ORM models following the same pattern.

---

## 18.5 Job state machine

A strict job state machine is required for both ingestion and comparison workflows.

## Ingestion job states

### Allowed states

* `uploaded`
* `queued`
* `planning`
* `extracting`
* `classifying`
* `canonicalizing`
* `validating`
* `indexing`
* `completed`
* `failed`
* `partial`
* `cancelled`

### Stage transitions

```text
uploaded -> queued
queued -> planning
planning -> extracting
extracting -> classifying
classifying -> canonicalizing
canonicalizing -> validating
validating -> indexing
indexing -> completed

Any active stage -> failed
Any active stage -> partial
queued/planning/extracting/... -> cancelled
```

### Failure policy

* If Docling fails completely: mark `failed`
* If primary extraction succeeds but table/formula enrichment fails: mark `partial`
* If indexing fails after canonical model is persisted: mark `partial`, because compare may still proceed

## Comparison job states

### Allowed states

* `queued`
* `loading_scope`
* `aligning`
* `diffing`
* `building_change_records`
* `scoring`
* `summarizing`
* `completed`
* `failed`
* `partial`
* `cancelled`

### Stage transitions

```text
queued -> loading_scope
loading_scope -> aligning
aligning -> diffing
diffing -> building_change_records
building_change_records -> scoring
scoring -> summarizing
summarizing -> completed

Any active stage -> failed
Any active stage -> partial
```

### Example persistence rules

* update `current_stage`
* update `progress_percent`
* append row to `comparison_job_events`
* write machine-readable payload in `payload_json`

### Example event payload

```json
{
  "matched_nodes": 381,
  "low_confidence_pairs": 7,
  "suppressed_nodes_considered": 42
}
```

---

## 18.6 Repository contracts for job state

### `IngestionJobRepository`

* `create(version_id) -> job_id`
* `mark_queued(job_id)`
* `mark_stage(job_id, stage_name, progress_percent)`
* `mark_partial(job_id, error_code, error_message)`
* `mark_failed(job_id, error_code, error_message)`
* `mark_completed(job_id)`
* `append_event(job_id, stage_name, event_type, payload)`

### `ComparisonJobRepository`

* `create(source_version_id, target_version_id, mode, options) -> job_id`
* `mark_stage(job_id, stage_name, progress_percent)`
* `store_statistics(job_id, statistics)`
* `mark_partial(job_id, error_code, error_message)`
* `mark_failed(job_id, error_code, error_message)`
* `mark_completed(job_id)`
* `append_event(job_id, stage_name, event_type, payload)`

---

## 18.7 Vector store architecture

Use the vector store as a retrieval and candidate-assist layer, not the authoritative comparison store.

### What to store in vector index

* retrieval chunks for chat
* canonical node embeddings
* optionally change record embeddings
* optionally section-title embeddings

### Separate collections/classes recommended

* `document_chunks`
* `canonical_nodes`
* `change_records` optional

### `document_chunks` object shape

```json
{
  "id": "chunk_001",
  "document_id": "doc_123",
  "version_id": "ver_001",
  "node_ids": ["node_1", "node_2"],
  "section_path": ["5", "5.1"],
  "text": "...",
  "language": "en",
  "chunk_type": "retrieval",
  "metadata": {
    "page_from": 12,
    "page_to": 13,
    "contains_table": false
  },
  "embedding": [0.1, 0.2]
}
```

### `canonical_nodes` vector object shape

```json
{
  "id": "node_1",
  "document_id": "doc_123",
  "version_id": "ver_001",
  "node_type": "paragraph",
  "section_label": "5.1",
  "heading_path": ["Data Privacy", "Retention"],
  "text": "...",
  "language": "en",
  "compare_score": 0.92,
  "embedding": [0.1, 0.2]
}
```

### Vector store interfaces

Define interfaces so Weaviate can be replaced later.

```python
from typing import Protocol, Iterable


class VectorStoreProvider(Protocol):
    def upsert_chunks(self, items: list[dict]) -> None: ...
    def upsert_nodes(self, items: list[dict]) -> None: ...
    def similarity_search(self, collection: str, query_text: str, top_k: int, filters: dict | None = None) -> list[dict]: ...
    def delete_by_version(self, collection: str, version_id: str) -> None: ...
```

### Recommended uses

* chat retrieval
* semantic candidate lookup for alignment assistance
* “find related clause” navigation
* semantic summary context gathering

### Not recommended as source of truth for

* exact diff generation
* citation lineage
* deterministic move/split/merge resolution

---

## 18.8 Weaviate-specific guidance

If using Weaviate initially:

* create separate classes or collections for retrieval chunks and canonical nodes
* store stable IDs that map back to Postgres records
* store `version_id`, `document_id`, `node_type`, `section_label`, and `page` metadata for filtering
* never allow vector-store-only records to become uncoupled from canonical DB entities

### Suggested collections

* `DocumentChunk`
* `CanonicalNode`
* `ChangeRecord` optional later

### Filtering examples

* by `version_id`
* by `document_id`
* by `node_type`
* by `section_label`
* by `language`

---

## 18.9 When Neo4j is needed

Neo4j is not required for the first solid version.

Use Neo4j later if you need scalable traversal across many linked entities such as:

* document version lineage over multiple years
* clause-to-clause linkage across revisions and related standards
* mapping regulations to controls, risks, systems, owners, and evidence
* graph exploration across domains or standard families
* impact propagation queries like “which controls are impacted by all changed obligations in this revision?”

### Good fit queries for Neo4j

* Find all descendants of changed normative clauses across 4 revisions
* Show all controls linked to clauses changed in 2025 revision
* Traverse from changed test threshold -> impacted lab procedure -> impacted control owner
* Show similarities between clauses across automotive and manufacturing standards

### Keep in PostgreSQL first

* direct compare job results
* canonical document storage
* chat sessions and reports
* fixed relational workflows

### Add Neo4j as a projection layer

Do not replace Postgres. Instead project selected entities and relationships into Neo4j.

---

## 18.10 Neo4j graph model

### Node labels

* `Document`
* `Version`
* `Clause`
* `Table`
* `ChangeRecord`
* `Control`
* `Risk`
* `Process`
* `GlossaryTerm`

### Relationship examples

* `(Document)-[:HAS_VERSION]->(Version)`
* `(Version)-[:HAS_CLAUSE]->(Clause)`
* `(Clause)-[:NEXT_REVISION_OF]->(Clause)`
* `(Clause)-[:CHANGED_BY]->(ChangeRecord)`
* `(Clause)-[:REFERENCES_TERM]->(GlossaryTerm)`
* `(Clause)-[:IMPACTS_CONTROL]->(Control)`
* `(Clause)-[:IMPACTS_PROCESS]->(Process)`
* `(Table)-[:BELONGS_TO]->(Clause)`

### Example clause node shape

```json
{
  "id": "node_123",
  "version_id": "ver_001",
  "section_label": "5.2",
  "node_type": "paragraph",
  "title": "Retention requirements",
  "compare_score": 0.91,
  "language": "en"
}
```

### Sync strategy

Use an async projector:

* when canonical nodes are persisted, project selected nodes to Neo4j
* when comparison completes, project `ChangeRecord` nodes and linkage edges
* when control mappings are added, connect clauses to controls/risks/processes

### Graph service interface

```python
class GraphStoreProvider(Protocol):
    def upsert_document_projection(self, payload: dict) -> None: ...
    def upsert_version_projection(self, payload: dict) -> None: ...
    def upsert_clause_projection(self, payload: dict) -> None: ...
    def upsert_change_record_projection(self, payload: dict) -> None: ...
    def run_query(self, cypher: str, params: dict | None = None) -> list[dict]: ...
```

---

## 18.11 Code-generation prompts for persistence layer

## Prompt: generate SQLAlchemy models and Alembic migrations

```text
Generate SQLAlchemy ORM models and Alembic migrations for a GRC comparison platform.

Requirements:
- PostgreSQL target
- Use UUID primary keys
- Tables: documents, document_versions, raw_artifacts, raw_blocks, canonical_nodes, canonical_tables, canonical_formulas, suppression_decisions, ingestion_jobs, ingestion_job_events, comparison_jobs, comparison_job_events, alignment_pairs, change_records, comparison_summaries, chat_sessions, chat_turns, reports
- Use JSONB/JSON columns for flexible payloads
- Add indexes for version_id, comparison_job_id, node_type, section_label, significance, and status lookups
- Keep ORM entities separate from Pydantic DTOs
- Include sensible relationships and cascade behavior
```

## Prompt: generate job state machine layer

```text
Generate a Python job state machine layer for ingestion and comparison workflows.

Requirements:
- Ingestion states: uploaded, queued, planning, extracting, classifying, canonicalizing, validating, indexing, completed, failed, partial, cancelled
- Comparison states: queued, loading_scope, aligning, diffing, building_change_records, scoring, summarizing, completed, failed, partial, cancelled
- Enforce valid transitions
- Persist stage events using repository methods
- Raise clear exceptions on invalid transitions
- Include unit tests
```

## Prompt: generate vector store provider

```text
Generate Python provider interfaces and a Weaviate implementation for a GRC platform vector layer.

Requirements:
- Support collections for document chunks and canonical nodes
- Upsert, delete-by-version, and similarity search methods
- Store stable IDs that map to PostgreSQL entities
- Support metadata filters: document_id, version_id, node_type, section_label, language
- Keep this layer independent from the comparison engine truth model
```

## Prompt: generate Neo4j projection layer

```text
Generate a Python Neo4j projection layer for a GRC platform.

Requirements:
- Project documents, versions, clauses, and change records into Neo4j
- Create relationships for version lineage and changed clauses
- Keep PostgreSQL as system of record
- Use an async projector service and provider interface
- Include example Cypher queries for impact traversal
```

---

# 19. Delivery phases

## Phase 1

* canonical model
* ingestion workflow
* compare-ready node tree
* node alignment
* change records
* summary DTO

## Phase 2

* table-aware diff
* better suppression/governance
* significance scoring
* citations hardening

## Phase 3

* chat over change records + corpus
* domain policy packs
* reporting/export

## Phase 4

* microservice split
* enterprise observability
* multi-tenant hardening
* optional Neo4j projection layer

---

# 20. Final implementation rule

Do not optimize first for LLM summarization quality.
Optimize first for:

* canonical structure fidelity
* meaningful-content filtering
* alignment quality
* change record quality
* persistence model stability

If those layers are strong, the LLM layer becomes much more accurate and easier to control.
