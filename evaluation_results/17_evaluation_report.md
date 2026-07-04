# Evaluation Report 17 — Extraction accuracy + confidence-aware v5 compare

**Date:** 2026-07-04
**Branch:** `feat/hybrid-comparison` (working tree on top of 9f5a149, not committed)
**Reports:** `17_accuracy_report.json`, `17_validation_report.json`

## Important: what these numbers are

The accompanying JSON reports were produced by `revalidation_runner` against the
**stored** `canonical_nodes.json` artifacts in `data/uploads` — i.e. they reflect
the *pre-change* ingestion (baseline re-run at this commit):

```
AGGREGATE  tables=30  classified=16/30 (53%)  fact_cov=20.8%
           row_key_cov=34.2%  col_map=51.0%  facts=121
```

The code changes below only take effect on **re-ingestion**. To measure the
post-change numbers: re-upload the benchmark PDFs (or run the upload pipeline
with `forceReExtract`), then re-run:

```
UPLOAD_ROOT=data/uploads uv run python -m grc_policy_server.services.ingestion.revalidation_runner
```

and save the outputs as `18_*` for the before/after comparison.

## What changed (working tree)

### Ingestion accuracy (root-cause fixes)
1. **OCR policy** (`docling_adapter.py`): `OcrAutoOptions(force_full_page_ocr=True)`
   was unconditional — every born-digital PDF had its native text layer replaced
   by full-page OCR (mangling µ, ±, superscripts; slowing ingestion). Auto OCR now
   uses `force_full_page_ocr=False`; full-page OCR only on the explicit force path.
2. **TableFormer v2 on by default** (`config.py: docling_table_structure_v2=True`);
   v1 fallback pinned to `mode=ACCURATE`.
3. **OCR error normalizer** (new `ocr_error_normalizer.py`): 3O→30, l50→150,
   rnuss→muss, dB(µV/m) unification — applied only to OCR-sourced chunks.
   Also fixed: OCR fallback chunks now set `ocr_used`/`ocr_confidence` metadata
   (previously the flag name mismatch dropped OCR provenance entirely).
4. **Math canonicalization** (new `utils/math_text.py`, wired into
   `normalize_for_comparison` *before* NFKC — NFKC alone flattens 10⁶→106):
   superscripts→`^n`, µ/μ unification, decimal commas, ±/×/dash spacing,
   value–unit spacing. Equal quantities now compare equal across notation variants.
5. **Formulas** (`docling_chunker.py`): all FORMULA items per chunk kept (was:
   first only); `formula_facts` (value/unit pairs) extracted at ingest;
   formula `extraction_confidence` scored, `unparsed_formula` flagged.
6. **Hierarchy** (`hierarchy_builder.py`): orphan chunks adopt the most recent
   section (reading order, ≤1 page gap) instead of landing in "Unsectioned".

### Deep tables
7. Per-table `extraction_confidence` + `confidence_flags` +
   `low_confidence_cell_count` persisted on table nodes (`_stage_normalize_tables`).
8. **Merged-cell propagation** in `rows_from_cells` (spanned values now apply to
   all covered rows/cols); table footnote markers extracted and same-page
   footnote texts linked (`table_footnote_markers` / `table_footnotes`).
9. **Continuation table type inheritance** (QW-4): "fortgesetzt"/schema-identical
   fragments inherit the parent's `table_type` instead of UNKNOWN.
10. Targeted table re-OCR trigger now fires on low quality score as well as low
    cell density.

### Confidence → human review in comparison (v5)
11. `to_comparison_record()` exposes `extraction_confidence`, `ocr_used`,
    `ocr_confidence`, `confidence_flags`, `requires_extraction_review`.
12. Every ChangeRecord/KeyDifference now carries `extractionConfidence` (min over
    evidence nodes) + `reviewReasons`; confidence < 0.70
    (`extraction_review_threshold`) forces `requiresHumanReview`.
13. **Real `compare_stream_v5`**: `extraction_quality` event per document,
    per-diff confidence + review reasons, low-confidence diffs enqueued to
    `HumanReviewQueue`, `done` event splits extraction vs semantic review counts.
14. **Runtime**: v5 only calls the per-diff LLM for High/Medium-impact diffs with
    trusted evidence; Low-impact and low-confidence diffs get deterministic rows
    (`analysis_source: "deterministic"`). `llm_calls` reported in `done`.
15. `auditor_v5` warnings marker now includes `lowConfidenceDiffs` and
    `minExtractionConfidence`; queued v5 path keeps the Celery soft-time-limit
    budget from c948d67.

### Docling native confidence scores (added same day)

16. **`ConversionResult.confidence` (docling ConfidenceReport) captured** in all
    three `DoclingAdapter.convert_bytes*` paths and serialized NaN-safe via
    `confidence_report_to_dict` (docling grades: POOR <0.5, FAIR <0.8,
    GOOD <0.9, EXCELLENT ≥0.9).
17. Per-page scores attached to chunks (`docling_page_confidence`); text chunks
    on pages with `ocr_score`/`parse_score` < 0.8 get `extraction_confidence` +
    `docling_poor_ocr`/`docling_poor_parse` flags (healthy pages stay trusted).
18. Table `extraction_confidence` blends the local structural score with the
    page's docling score (0.7/0.3); POOR pages add `docling_low_confidence_page`.
19. `ConfidenceMetrics.docling_confidence` (document scores + grades) persisted;
    document `low_grade == poor` adds a `docling_low_grade` ExtractionFlag and
    counts toward `requires_human_review_count`. Persisted also as
    `normalized_tree.metadata.docling_confidence`.
20. Targeted table re-OCR additionally triggers on pages docling grades POOR
    for table/OCR scores; v5 `extraction_quality` event now exposes
    `docling_mean_grade`, `docling_low_grade`, `docling_scores`.

Sanity-verified on a real conversion (TL_81000_2021 p1–2): document report
`{parse 1.0, layout 0.74, ocr 0.91, mean_grade good, low_grade fair}` with
per-page entries; `table_score` NaN → null.

## Tests

`tests/test_extraction_confidence_v5.py` (34 tests): OCR normalizer, math
equalities, formula confidence, merged cells, footnote markers, confidence
propagation, review gating, full v5 stream contract, docling ConfidenceReport
serialization/annotation/blend/metrics/summary.

Full suite: **493 passed**, 12 skipped, 1 xfailed, 3 xpassed. The 4
`test_delete_documents*` failures are pre-existing on clean HEAD (verified via
`git stash`), unrelated to these changes.
