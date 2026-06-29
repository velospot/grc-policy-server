# Changelog

All notable changes to the GRC Policy Server are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### 2026-06-28: Extraction confidence scoring & human review triggers

**WHY:** The LLM-based compliance comparison needs to know which cells, rows, and requirements it can trust and which require human review. Previously, only table-level confidence existed. Without per-cell confidence, the LLM treats all extracted text equally — risking analysis built on a cell with an OCR artefact or a missing unit being treated the same as a perfectly extracted limit value.

#### Added

- **`CellConfidence` model** in `schemas.py` — per-cell extraction confidence (0.0–1.0) scoring factors: 30% cell fill, 35% type match (numeric for limit columns, text for conditions), 20% unit validity (recognized units in limit columns), 15% OCR confidence. Includes `flags` array for `missing_unit`, `unparseable_number`, `missing_limit_value`.
- **`RequirementConfidence` model** — per-row confidence as minimum of key-column confidences (limit, result, condition, row_key), modified by table confidence and footnote scope applicability.
- **`ExtractionFlag` model** — structured human review alert with `flag_type`, `severity`, `description`, `affected_object`, and optional `confidence_if_applicable`.
- **`ConfidenceMetrics` model** — document-level summary: high/medium/low cell confidence counts, averages, extraction flags, review trigger count.
- **`_score_cell_confidence()` function** in `document_ingestion_service.py` — scores individual cells based on content quality, type-role matching, and OCR confidence; detects compliance-critical column issues.
- **`_score_row_confidence()` function** — propagates row confidence from key cells and table-level quality; flags rows below 0.70 threshold for human review.
- **OCR page-level confidence** in `ocr_fallback.py` — uses `pytesseract.image_to_data()` to extract per-word confidence (0–100 scale), aggregates to page-level score (0.0–1.0), flags pages below 0.70 threshold with `ocr_confidence_flag`.
- **`INGESTION_ASSESSMENT.md`** — comprehensive pipeline evaluation against PDF structure specification (03_pdf_structure_and_extraction.md), documenting 60% specification compliance, detailing gaps, strengths, and prioritized recommendations.

**LLM Trust Signals:**
- **High confidence (≥ 0.85):** Use directly in analysis. Safe to make decisions based on this cell/row.
- **Medium confidence (0.50–0.85):** Note uncertainty in explanation. Include confidence score in LLM reasoning.
- **Low confidence (< 0.50):** Flag for human review before using. Do not use in automatic compliance decisions.

---

### 2026-06-28: Docling 2.100 → 2.107 upgrade

**WHY:** Section hierarchy was flat (depth = 1) for all documents — a P0 issue identified in benchmark evaluation §13.6. Every section ended up at leaf level (e.g., `"5.4.2.4 Prüfimpuls 6"`), preventing ClauseMatcher's chapter-level alignment bonus (+0.20 score lift) from ever firing. The fix required `docling-parse v7` which was only integrated into `docling v2.106.0`. Additionally, TableFormer V2 was introduced for better nested/merged table extraction.

#### Added

- **`HeadingHierarchyOptions(enabled=True, use_numbering=True, use_style=True)`** in `docling_adapter.py` — enables docling-parse v7 heading inference using PDF numbering patterns and visual hierarchy, producing proper nested section paths (e.g., `5 / 5.4 / 5.4.2 / 5.4.2.4`).
- **`TableStructureV2Options`** in `docling_adapter.py` — optional improved table backend for merged cells, multi-level headers, deeply nested grids; gated by `docling_table_structure_v2: bool = False` config flag for safe rollout (V1 remains default).
- **`docling_table_structure_v2` setting** in `AppSettings` — runtime toggle for V2 table extraction backend.

#### Changed

- **Dependency lower bounds** — `docling>=2.106.0`, `docling-core>=2.83.0`; transitive `docling-parse` auto-upgrades to 7.0.0.
- **Removed dead `try/except` guard** around `do_formula_enrichment = True` — the attribute has existed since docling 2.97.0+; the guard was a compatibility shim for a version that is no longer relevant.

**Impact:** Section paths now have depth > 1 for numbered PDFs, unlocking chapter-level alignment in ClauseMatcher. Section renumbering (e.g., 5.4.2.5 → 5.4.2.4 across editions) can now benefit from the chapter-map +0.20 bonus during cross-version comparison, improving match rates for standards that renumber subsections in updated editions.

---

### 2026-06-24: Qdrant v1.18 API migration + document deletion robustness

**WHY (Qdrant):** `qdrant-client` v1.7 removed the `.search()` method; the codebase still called it. This caused a silent `AttributeError` during every comparison using vector search, falling back to no-vector mode without warning. Clause matching was running without Qdrant despite it being configured and available.

**WHY (delete robustness):** Document deletion was blocked when Neo4j was unreachable — even though Qdrant had already successfully cleaned up. A `continue` in the Neo4j exception handler skipped local file deletion, leaving documents in a half-deleted state (Qdrant clean, files on disk).

#### Fixed

- **`semantic_search_in_document()` in `qdrant_store.py`:**
  - `.search()` → `.query_points()` (qdrant v1.7+ API)
  - `query_vector=` → `query=` (renamed parameter)
  - `hits` list → `result.points` (response wrapper change)
- **Document delete route robustness:**
  - Removed `continue` from Neo4j exception handler
  - Neo4j failure now logs a warning but proceeds to delete local files and Qdrant data
  - Deletion only reported as failed if local file cleanup fails

#### Added

- **`warnings: list[str]` field** on `DeleteDocumentResult` to surface Neo4j partial failures without reporting the delete as failed overall.

---

### 2026-06-14: Weaviate → Qdrant migration

**WHY:** Weaviate introduced breaking API changes and performance regressions for the single-collection, filter-by-document-id query pattern used by this system. Qdrant's native payload filtering and `query_points()` API are faster and better suited to per-document semantic search. The migration also reduced infrastructure complexity (single Qdrant service vs. Weaviate's multi-service topology).

#### Changed

- **Vector backend:** `QdrantVectorClient` replaces `WeaviateClient`
- **Collection model:** Single `"PolicyChunk"` collection, filtered by `document_id` payload field
- **Infrastructure:** Ports `6333` (HTTP), `6334` (gRPC); `docker-compose.yml` updated

#### Removed

- `WeaviateClient` and all Weaviate-specific ingestion/search code
- Weaviate environment variables from config

---

### 2026-06-11: Knowledge graph and graph DB integration improvements

**WHY:** Neo4j graph integration was fragile — ingestion failures propagated when Neo4j was unreachable, blocking document uploads even when canonical storage succeeded. The knowledge graph comparison needed cleaner entity-graph diffing for table entities with semantic descriptions.

#### Changed

- **Neo4j write resilience:** Failures during ingestion now log a warning and allow upload to succeed (canonical nodes already saved locally)
- **Entity-graph diff:** Improved semantic descriptions for EMC/Safety/Environment table entities
- **Qdrant initialization:** `check_compatibility=False` to prevent startup failures on version mismatch

---

### 2026-06-04: Benchmark evaluation, comparison schema, and accuracy improvements

**WHY:** Comparison output was showing incorrect match rates (100% due to wrong unmatched-count derivation), and the benchmark scripts couldn't handle both `RealDiffEngine` and `OfflineDiffEngine` trace formats. The comparison result schema needed `SKIPPED`/`SUPPRESSED`/`WARNING` states for non-normative content and cosmetic changes.

#### Added

- **Comparison change types:** `SKIPPED`, `SUPPRESSED`, `WARNING` for filtering non-normative and cosmetic differences from key-difference output
- **`--engine {offline,real}` flag** to `scripts/run_comparisons.py` — enables running comparisons with live Qdrant for hybrid match rate measurement
- **Strategy breakdown columns** (`stable_id`, `section_alignment`, `qdrant`) in `benchmark_metrics.py` output to track match strategy contribution
- **First evaluable benchmark pair:** TL-81000 p063-072 (2018→2021) added to `comparison_pairs_resolved.json` with confirmed doc IDs in uploads

#### Fixed

- **`benchmark_metrics.py` dual-format support:**
  - `RealDiffEngine` traces: `matchedNodes`, `unmatchedLeft`, `matchTypes` fields
  - `OfflineDiffEngine` traces: `total_matches`, `confidence_breakdown`, derived `unmatchedLeft`/`unmatchedRight` from `changeCounts.REMOVED/ADDED`
- **Match rate formula:** Was treating absent `unmatchedLeft` as 0 (giving false 100% rates); now correctly derived from `changeCounts.REMOVED` + `changeCounts.ADDED`
- **Graceful error handling:** Stale benchmark pairs with missing doc IDs now skip silently ("SKIP - docs not uploaded") instead of producing tracebacks

---

### 2026-06-03: Architecture refactor

**WHY:** Ingestion, comparison, and vector storage code had grown entangled across several large files. Separation into `OfflineDiffEngine` (no external services, for CI/benchmark), `RealDiffEngine` (full pipeline with Qdrant + Neo4j), and `RealDiffEngineStream` (SSE streaming) clarified which code paths need what infrastructure and enabled deterministic testing without external dependencies.

#### Added

- **`OfflineDiffEngine`** — runs full comparison without Qdrant, Celery, or LLM; produces `AccuracyMetrics`; enables CI/benchmark runs without external services
- **`LocalEmbeddingService`** — local embedding fallback for offline use
- **`NoOpLLM`** — LLM stub for offline/benchmark use
- **`CompareV5Service`** + **`/v5/compare/stream` endpoint** — hybrid SSE comparison with `hybridSignals` metadata in `done` event, streaming progress updates
- **Document family content-first detection:** Three-stage chain (body/title content scan → filename pattern → legacy string stripping); canonical family IDs (`tl_81000`, `dnv_cg_0339`, `din_en_60068`) stable across editions

#### Removed

- **LLM enrichment at ingestion time** — was causing I/O timeouts and was not meaningful without a production-grade model; enrichment is now done lazily at comparison time using rule-based extraction
- **OpenDataLoader as secondary extractor** — removed due to API instability and false-positive table detection

---

### 2026-06-01: OpenDataLoader removal + delete API fix

**WHY:** OpenDataLoader was producing false-positive table detections (lists as tables) and its API was unstable between versions. The integration cost exceeded the benefit given Docling's improving table extraction. The delete API was blocking on vector-store failures.

#### Removed

- OpenDataLoader PDF secondary extractor and all related code

#### Fixed

- Delete document API now treats vector-store failures (Weaviate/Qdrant unavailable) as non-blocking for local file cleanup

---

## References

- [PDF Structure and Extraction Specification](knowledge_base/docs/03_pdf_structure_and_extraction.md)
- [Ingestion Assessment (June 28, 2026)](INGESTION_ASSESSMENT.md)
- [Benchmark Evaluation](docs/benchmark_evaluation.md) — §12–14 for detailed iteration analysis
- [Compliance Intermediate Representation](knowledge_base/docs/02_compliance_intermediate_representation.md)
