# 00 - System Overview

## Product objective

Build an offline web application that compares compliance release PDFs and explains the changes with citations. The primary industry context is automotive EMC compliance testing, where standards and OEM documents are often table-heavy and contain multi-page tables, numeric limits, frequency ranges, test conditions, acceptance criteria, notes, and exceptions.

The system must support English, German, and French documents. A comparison pair is assumed to be in the same language.

## Users

- Compliance auditor
- EMC test engineer
- Quality or homologation engineer
- GRC reviewer
- Document control manager
- Administrator

## Core user journey

1. User logs into local web app.
2. User uploads one or more PDF release documents.
3. System extracts structure, tables, text, citations, language, and requirements.
4. User selects two documents of the same language.
5. User clicks Compare.
6. System aligns equivalent sections, requirements, and table rows.
7. System detects changes deterministically.
8. System builds a cited evidence pack.
9. Local LLM explains each change in the source language.
10. User reviews side-by-side citations and exports the report.

## What the system is not

- Not a general question-answering chatbot over PDFs.
- Not a translation system.
- Not a cloud RAG system.
- Not an LLM-only diff tool.
- Not a replacement for human compliance sign-off.

## Top-level pipeline

```mermaid
flowchart LR
    A[PDF Upload] --> B[File Storage + SHA256]
    B --> C[Document Extraction]
    C --> D[Compliance Intermediate Representation]
    D --> E[Requirement + Table Normalization]
    E --> F[Embedding + Lexical Indexing]
    F --> G[Version Alignment]
    G --> H[Deterministic Diff Engine]
    H --> I[Cited Evidence Packs]
    I --> J[Local LLM Explanation]
    J --> K[Review UI + Export]
```

## High-level components

| Component | Responsibility |
|---|---|
| Web UI | Upload, manage releases, select comparison pair, view results, view PDF highlights, export |
| API backend | Auth, projects, documents, jobs, comparison API, report API |
| Extraction workers | Parse PDF into text, structure, tables, OCR data, coordinates |
| CIR builder | Convert extractor output into canonical Compliance Intermediate Representation |
| Requirement extractor | Identify normative statements and table-derived requirements |
| Table normalizer | Stitch multi-page tables, normalize headers, rows, units, footnotes |
| Indexer | Generate embeddings, sparse/keyword fields, vector DB objects |
| Alignment engine | Map v1 objects to likely v2 equivalents |
| Diff engine | Detect additions, removals, modifications, numeric changes, table changes |
| Evidence pack builder | Create compact cited context for the LLM |
| LLM explainer | Generate auditor-friendly explanation in source language |
| Audit logger | Record parser versions, model versions, prompts, hashes, outputs |

## Key architecture decision

The canonical source of truth is not the vector database. The source of truth is:

1. Original PDF file.
2. PDF file hash.
3. Compliance Intermediate Representation stored in PostgreSQL or structured JSON.
4. Source citation records with page and bounding boxes.
5. Comparison result records.

Vector search is an acceleration and candidate retrieval mechanism only.

## Offline runtime boundaries

All models, OCR data, containers, wheels, and system packages must be available inside the offline environment.

Runtime must not call:

- External LLM APIs.
- External embedding APIs.
- External telemetry.
- External package registries.
- External search or translation services.

Development may use internet only to prepare an offline bundle. Production must be network-isolated except for local LAN access if required.
