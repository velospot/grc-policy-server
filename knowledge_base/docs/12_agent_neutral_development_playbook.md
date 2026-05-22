# 12 - Agent-Neutral Development Playbook

## Purpose

This document tells any coding agent how to work on the project without depending on a specific agentic platform. It applies to Codex-like, Claude-like, Cursor-like, Continue-like, or local autonomous development tools.

## Core instruction for agents

Build a deterministic, cited compliance comparison engine. Do not build a generic PDF chatbot.

## Agent operating rules

1. Preserve offline operation.
2. Do not introduce external API calls.
3. Do not add cloud LLM dependencies.
4. Do not remove citation lineage.
5. Do not compare raw chunks as the final comparison method.
6. Keep model providers behind interfaces.
7. Write tests for every deterministic parser or diff rule.
8. Store raw extractor output for debugging.
9. Every user-facing comparison item must cite source evidence.
10. Same-language comparison only unless explicitly implementing future cross-language mode.

## Recommended implementation order

### Phase 0: Project skeleton

Deliver:

- FastAPI app.
- PostgreSQL migrations.
- Redis queue.
- Local file storage.
- Basic React UI.
- Health checks.

Definition of done:

- `docker compose up` starts local services.
- `/health` passes.
- Tests run.

### Phase 1: Upload and document registry

Deliver:

- PDF upload endpoint.
- SHA-256 calculation.
- Document metadata.
- Storage path.
- Job creation.

Definition of done:

- Upload stores PDF.
- Duplicate hash detection works.
- Document is visible in UI.

### Phase 2: Extraction MVP

Deliver:

- Docling extraction adapter.
- Page and text block storage.
- Minimal CIR snapshot.
- Page rendering endpoint.

Definition of done:

- Sample PDF text blocks appear in DB.
- UI can show page image.

### Phase 3: Section hierarchy

Deliver:

- Heading detection.
- Section tree.
- Blocks assigned to sections.

Definition of done:

- Synthetic nested sections match expected tree.

### Phase 4: Tables

Deliver:

- Table extraction adapter.
- Column/row/cell model.
- Multi-page table stitching.
- Citation bounding boxes.

Definition of done:

- Synthetic multi-page table produces one logical table.

### Phase 5: Requirements and facts

Deliver:

- Normative dictionary en/de/fr.
- Requirement extraction from paragraphs and table rows.
- Unit/range parser.

Definition of done:

- Known normative statements classified correctly.
- Known numeric facts normalized correctly.

### Phase 6: Indexing

Deliver:

- Embedding provider interface.
- Vector DB adapter.
- Index sections, requirements, table rows.
- Metadata filters by project/document/language.

Definition of done:

- Candidate lookup returns expected matches.

### Phase 7: Alignment and diff

Deliver:

- Candidate retrieval.
- Reranking interface.
- Alignment scoring.
- Deterministic diff taxonomy.

Definition of done:

- Synthetic v1/v2 expected changes found.

### Phase 8: Explanation

Deliver:

- Evidence pack builder.
- Prompt template.
- Local LLM adapter.
- JSON schema validation.

Definition of done:

- Explanation uses only valid citations.
- Output language matches document language.

### Phase 9: UI and reports

Deliver:

- Comparison dashboard.
- Change filters.
- Side-by-side PDF viewer.
- Export to Markdown/PDF/Excel/JSON.

Definition of done:

- Auditor can click each citation and see highlighted source.

### Phase 10: Production hardening

Deliver:

- Offline bundle.
- Monitoring.
- Backups.
- RBAC.
- Audit logs.
- Load testing.

Definition of done:

- Five concurrent users validated.
- Restore drill passes.

## Standard coding conventions

- Use typed Python.
- Use Pydantic schemas for API and CIR models.
- Use SQLAlchemy or SQLModel for database access.
- Use Alembic migrations.
- Use structured logging.
- Keep business logic out of API route functions.
- Keep deterministic code separated from LLM code.

## Agent task template

```markdown
# Task
Implement [specific feature].

## Context
This system is an offline compliance PDF comparison tool. Maintain citation lineage.

## Requirements
- ...

## Files to inspect
- ...

## Expected output
- Code changes
- Tests
- Migration if needed
- Documentation update if needed

## Definition of done
- ...
```

## Pull request checklist

```text
[ ] No external network dependency added
[ ] Tests added or updated
[ ] Citations preserved
[ ] Offline mode respected
[ ] Language metadata preserved
[ ] DB migration included if schema changed
[ ] Prompt/version updated if LLM behavior changed
[ ] No raw PDF text logged unnecessarily
[ ] Documentation updated
```

## Anti-patterns agents must avoid

- Building a single huge prompt with both PDFs.
- Using vector similarity as proof of equivalence.
- Translating documents to English before diffing.
- Ignoring tables and footnotes.
- Dropping bounding boxes.
- Making model-specific calls throughout domain code.
- Returning prose without structured JSON.
- Failing silently on low extraction confidence.
