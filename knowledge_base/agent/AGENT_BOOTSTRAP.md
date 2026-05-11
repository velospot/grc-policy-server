# Agent Bootstrap

Use this file as the first context for any coding agent.

## Mission

Build an offline, local, multilingual GenAI-based GRC Auditor for comparing two same-language compliance PDF release documents with cited explanations.

## Architecture in one sentence

Convert PDFs into a structured Compliance Intermediate Representation, align equivalent compliance objects across versions, run deterministic diffs, then ask a local LLM to explain the machine-detected changes using source citations.

## Non-negotiables

- Offline runtime only.
- No cloud LLM/API calls.
- Same-language comparison only.
- No translation before diffing.
- Every change item must include citations.
- Citations must include document, version, page, object, and bbox when available.
- LLM output must be JSON schema validated.
- Vector DB is not the source of truth.
- Deterministic diff comes before LLM explanation.

## Preferred stack

```text
FastAPI, PostgreSQL, Redis, React/Next.js, Docling, OpenDataLoader, PyMuPDF/pdfplumber, Tesseract, Weaviate or Qdrant, Qwen3 Embedding/Reranker, Granite 3.3 8B, Ollama for MVP, llama.cpp or vLLM for production.
```

## First vertical slice

Implement:

1. Upload a PDF.
2. Store original PDF and SHA-256.
3. Extract one page of text and one table.
4. Store minimal CIR.
5. Create a citation with page and bbox.
6. Show the page with highlight.

Do not start with the chatbot.

## When adding code

- Add tests.
- Update docs.
- Preserve lineage.
- Do not break offline mode.
- Avoid model-specific assumptions in domain logic.

## Key docs to read

1. `docs/00_system_overview.md`
2. `docs/01_reference_architecture.md`
3. `docs/02_compliance_intermediate_representation.md`
4. `docs/05_comparison_engine.md`
5. `docs/12_agent_neutral_development_playbook.md`
