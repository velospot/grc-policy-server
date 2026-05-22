# 02 - Compliance Intermediate Representation

## Purpose

The Compliance Intermediate Representation, or CIR, is the canonical structured form of a compliance PDF. It exists to make PDF comparison deterministic, auditable, and reproducible.

CIR is not just text. It contains sections, headings, paragraphs, lists, tables, rows, cells, figures, references, language metadata, extraction confidence, and citation coordinates.

## Why CIR is necessary

Compliance release comparison fails when documents are treated as plain chunks. Chunking loses:

- Section hierarchy.
- Table row identity.
- Repeated multi-page table headers.
- Footnote scope.
- Unit meaning.
- Merged cell meaning.
- Page coordinates for citations.
- Distinction between normative and informative text.

## CIR top-level schema

```json
{
  "document_id": "doc_001",
  "project_id": "project_001",
  "release_label": "v2",
  "source_file": {
    "filename": "OEM_EMC_v2.pdf",
    "sha256": "...",
    "page_count": 120,
    "size_bytes": 12345678
  },
  "language": {
    "dominant": "de",
    "confidence": 0.98
  },
  "metadata": {
    "title": "...",
    "standard_family": "automotive_emc",
    "issuer": "...",
    "publication_date": "..."
  },
  "pages": [],
  "sections": [],
  "blocks": [],
  "lists": [],
  "tables": [],
  "figures": [],
  "requirements": [],
  "cross_references": [],
  "extraction_audit": {}
}
```

## Page object

```json
{
  "page_id": "page_000044",
  "page_number": 44,
  "width": 595.2,
  "height": 841.8,
  "rotation": 0,
  "language": "en",
  "text_density": 0.82,
  "has_native_text": true,
  "ocr_used": false,
  "image_hash": "..."
}
```

## Section object

```json
{
  "section_id": "sec_5_3_2",
  "number": "5.3.2",
  "title": "Radiated immunity test levels",
  "normalized_title": "radiated immunity test levels",
  "path": ["5 Test methods", "5.3 Immunity", "5.3.2 Radiated immunity test levels"],
  "parent_section_id": "sec_5_3",
  "page_start": 42,
  "page_end": 45,
  "bbox_start": [72, 80, 520, 140],
  "bbox_end": [72, 600, 520, 760],
  "language": "en",
  "confidence": 0.93
}
```

## Text block object

```json
{
  "block_id": "blk_000812",
  "document_id": "doc_001",
  "section_id": "sec_5_3_2",
  "page": 43,
  "bbox": [72, 210, 520, 260],
  "block_type": "paragraph",
  "text": "The device under test shall withstand ...",
  "language": "en",
  "reading_order": 128,
  "source_extractor": "docling",
  "confidence": 0.95
}
```

## Requirement object

A requirement may come from a paragraph, list item, table row, cell, note, or footnote.

```json
{
  "requirement_id": "REQ-000245",
  "document_id": "doc_001",
  "source_object_id": "row_000112",
  "source_type": "table_row",
  "section_id": "sec_5_3_2",
  "section_path": ["5", "5.3", "5.3.2"],
  "language": "en",
  "normative_level": "mandatory",
  "normative_term": "shall",
  "subject": "device under test",
  "action": "withstand",
  "condition": "frequency range 200 MHz to 1000 MHz",
  "acceptance_criteria": "no functional degradation",
  "raw_text": "...",
  "normalized_text": "...",
  "numeric_facts": [],
  "citations": ["cit_000812"],
  "confidence": 0.91
}
```

## Table object

```json
{
  "table_id": "TBL-5-3-2-01",
  "document_id": "doc_001",
  "section_id": "sec_5_3_2",
  "caption": "Table 12 - Test levels for radiated immunity",
  "normalized_caption": "test levels radiated immunity",
  "page_start": 43,
  "page_end": 45,
  "continued_from_previous_page": false,
  "continued_to_next_page": true,
  "header_rows": [],
  "columns": [],
  "rows": [],
  "footnotes": [],
  "confidence": 0.88
}
```

## Table column object

```json
{
  "column_id": "col_field_strength",
  "index": 2,
  "raw_header": "Field strength (V/m)",
  "normalized_name": "field_strength",
  "unit": "V/m",
  "header_path": ["Test level", "Field strength"],
  "is_key_column": false
}
```

## Table row object

```json
{
  "row_id": "row_000112",
  "table_id": "TBL-5-3-2-01",
  "row_index": 12,
  "semantic_key": "5.3.2|radiated_immunity|200-400MHz|AM80",
  "page": 44,
  "bbox": [72, 380, 520, 410],
  "cells": {
    "frequency_range": "200-400 MHz",
    "field_strength": "30 V/m",
    "modulation": "AM 80%",
    "acceptance_criterion": "Class A"
  },
  "normalized_facts": [
    {"type": "frequency_range", "lower_hz": 200000000, "upper_hz": 400000000},
    {"type": "field_strength", "value": 30, "unit": "V/m"}
  ],
  "footnote_refs": ["a"],
  "citations": ["cit_000901"],
  "confidence": 0.89
}
```

## Citation object

```json
{
  "citation_id": "cit_000901",
  "document_id": "doc_001",
  "release_label": "v2",
  "source_file_sha256": "...",
  "page": 44,
  "bbox": [72, 380, 520, 410],
  "object_type": "table_row",
  "object_id": "row_000112",
  "section_number": "5.3.2",
  "table_id": "TBL-5-3-2-01",
  "display_label": "v2, section 5.3.2, table 12, page 44, row 12",
  "quote": "200-400 MHz | 30 V/m | AM 80% | Class A",
  "confidence": 0.89
}
```

## Normalized fact object

Normalized facts allow deterministic comparison.

```json
{
  "fact_id": "fact_001",
  "owner_object_id": "row_000112",
  "fact_type": "numeric_quantity",
  "name": "field_strength",
  "value": 30.0,
  "unit": "V/m",
  "raw_value": "30 V/m",
  "confidence": 0.95
}
```

Frequency range example:

```json
{
  "fact_type": "range",
  "name": "frequency",
  "lower": 200000000,
  "upper": 400000000,
  "unit": "Hz",
  "raw_value": "200-400 MHz"
}
```

## CIR storage strategy

Store CIR in two forms:

1. Relational tables for queryable objects.
2. Full immutable JSON artifact for reproducibility.

Suggested storage:

```text
PostgreSQL tables:
  documents
  document_pages
  sections
  blocks
  tables
  table_columns
  table_rows
  requirements
  normalized_facts
  citations
  extraction_runs

Object storage:
  original PDFs
  rendered page images
  full CIR JSON snapshots
  extraction logs
```

## Versioning

Every CIR must include:

```json
{
  "cir_version": "1.0.0",
  "extractor_versions": {
    "docling": "pinned_version",
    "opendataloader": "pinned_version",
    "pymupdf": "pinned_version"
  },
  "normalizer_version": "1.0.0",
  "requirement_extractor_version": "1.0.0"
}
```

## Validation rules

- Each section must have a page_start.
- Each table row must belong to one table.
- Each requirement must have at least one citation.
- Each citation must point to a source object and PDF hash.
- Each numeric fact must preserve raw text.
- Each derived table row requirement must reference the original row.
- Each object must have a language or inherit one from parent.
