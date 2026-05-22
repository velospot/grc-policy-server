# 14 - Implementation Backlog

## Epic 1 - Platform skeleton

### Story 1.1 - Create backend skeleton

Acceptance criteria:

- FastAPI app starts.
- `/health` endpoint returns OK.
- Structured logging configured.
- Config loaded from environment.
- Unit test passes.

### Story 1.2 - Create frontend skeleton

Acceptance criteria:

- React/Next.js app starts.
- Login placeholder exists.
- Documents page placeholder exists.
- Comparisons page placeholder exists.

### Story 1.3 - Database migrations

Acceptance criteria:

- PostgreSQL connects.
- Alembic migration creates core tables.
- Test DB can be reset.

## Epic 2 - Document upload

### Story 2.1 - Upload PDF

Acceptance criteria:

- User uploads PDF.
- SHA-256 calculated.
- Original file stored.
- Document record created.
- Extraction job queued.

### Story 2.2 - Document status

Acceptance criteria:

- UI shows queued/running/succeeded/failed.
- API returns job progress.

## Epic 3 - Extraction

### Story 3.1 - Docling adapter

Acceptance criteria:

- Adapter converts PDF to raw structured output.
- Raw output stored.
- Pages and text blocks persisted.

### Story 3.2 - OpenDataLoader adapter

Acceptance criteria:

- Adapter can be run as fallback or comparison extractor.
- Bounding boxes are persisted where available.

### Story 3.3 - OCR fallback

Acceptance criteria:

- Scanned PDF triggers OCR.
- OCR language selected from language hint or detection.
- OCR confidence stored.

## Epic 4 - CIR

### Story 4.1 - CIR schema

Acceptance criteria:

- Pydantic models exist.
- JSON snapshot validates.
- Version metadata included.

### Story 4.2 - Section hierarchy

Acceptance criteria:

- Nested headings detected in sample PDF.
- Blocks assigned to sections.

### Story 4.3 - Tables

Acceptance criteria:

- Tables, columns, rows, cells persisted.
- Multi-page table stitching works for sample.

## Epic 5 - Language support

### Story 5.1 - Language detection

Acceptance criteria:

- Document/page/block language detected.
- en/de/fr supported.
- Low confidence flags review.

### Story 5.2 - Same-language validation

Acceptance criteria:

- Same-language comparison allowed.
- Mismatch-language comparison blocked.

### Story 5.3 - Normative dictionaries

Acceptance criteria:

- en/de/fr terms classified.
- Tests cover strengthening/weakening examples.

## Epic 6 - Normalization

### Story 6.1 - Unit parser

Acceptance criteria:

- Hz/kHz/MHz/GHz parse and convert.
- V/m, dBuV, dBuA parse.
- Tests pass.

### Story 6.2 - Range parser

Acceptance criteria:

- "150 kHz - 30 MHz" parses.
- "150 kHz to 30 MHz" parses.
- German/French connectors parse.

### Story 6.3 - Table row semantic keys

Acceptance criteria:

- Stable row key generated.
- Reordered rows still align.

## Epic 7 - Indexing and retrieval

### Story 7.1 - Embedding provider

Acceptance criteria:

- Provider interface implemented.
- Local model returns embeddings.
- Embeddings cached by content hash.

### Story 7.2 - Vector DB adapter

Acceptance criteria:

- Index objects written.
- Metadata filters enforced.
- Query returns candidates.

### Story 7.3 - Reranker provider

Acceptance criteria:

- Reranker scores candidate pairs.
- Alignment engine can use scores.

## Epic 8 - Comparison

### Story 8.1 - Section alignment

Acceptance criteria:

- Moved section detected.
- Added/removed sections detected.

### Story 8.2 - Requirement alignment

Acceptance criteria:

- Equivalent requirements mapped.
- Split/merge ambiguity flagged.

### Story 8.3 - Table row alignment

Acceptance criteria:

- Table rows align across reordered versions.
- Added/removed rows detected.

### Story 8.4 - Diff classification

Acceptance criteria:

- Numeric threshold changes detected.
- Obligation changes detected.
- Footnote changes detected.

## Epic 9 - Explanation

### Story 9.1 - Evidence pack builder

Acceptance criteria:

- Each change has evidence IDs.
- Evidence includes page and bbox when available.

### Story 9.2 - LLM explanation

Acceptance criteria:

- Local LLM called through provider interface.
- JSON schema validation enforced.
- Output language matches document language.

### Story 9.3 - Explanation validator

Acceptance criteria:

- Invalid citations rejected.
- Unsupported numbers flagged.
- Human-review state assigned.

## Epic 10 - UI and export

### Story 10.1 - Comparison dashboard

Acceptance criteria:

- Filter by change type/risk/review state.
- Sort by section/table/page.

### Story 10.2 - Side-by-side PDF viewer

Acceptance criteria:

- Citation click opens correct page.
- Highlight bbox visible.

### Story 10.3 - Export

Acceptance criteria:

- Export JSON.
- Export Markdown.
- Export PDF or Excel if needed.
- Exports preserve citations.

## Epic 11 - Production readiness

### Story 11.1 - Offline bundle

Acceptance criteria:

- Images and models import offline.
- Checksums verified.

### Story 11.2 - Backups

Acceptance criteria:

- Database backup works.
- Object store backup works.
- Restore test passes.

### Story 11.3 - Load test

Acceptance criteria:

- Five concurrent users supported.
- Queue behavior stable.
- No external network calls.
