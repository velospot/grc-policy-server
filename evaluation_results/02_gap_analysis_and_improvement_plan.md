# Gap Analysis & Improvement Plan
## EMC Compliance Document Ingestion — Source of Truth & Comparison

**Date:** 2026-05-12  
**Branch:** `feature/multi-layer-table-extraction`  
**KB docs reviewed:** 02, 03, 05, 11, 14, 15, 16, 18, 21, 23, 24

---

## 1. Current Accuracy Baseline

From `01_evaluation_report.md` (2026-05-12):

| Metric | TL_81000_2021 | TL_81000_2018 | Target (KB spec) |
|---|---|---|---|
| Header quality | 100% | 100% | 100% ✅ |
| Section coverage | 100% | 100% | 100% ✅ |
| EMC classification | 44% | 92% | >80% ❌ |
| Fact coverage | 5.3% | 13.2% | >50% ❌ |
| Row key coverage | 19.4% | 60.0% | >80% ❌ |
| Column mapping | 30% | 54% | >70% ❌ |
| High-severity change recall | not measured | not measured | ≥0.95 ❌ |
| Change detection precision | not measured | not measured | ≥0.90 ❌ |

---

## 2. What Is Implemented (Phases A–F)

| Component | Status | Files |
|---|---|---|
| CIR extensions: NormalizedFact, Citation, TableCell fields | ✅ | `canonical_table_model.py` |
| CanonicalNode OCR provenance fields | ✅ | `canonical_models.py` |
| Severity engine: SemanticImpact, SeverityReasonCode, AuditDisposition | ✅ | `severity_classifier.py` |
| 4 new severity rules (DomainEntityRule etc.) | ✅ | `severity_classifier.py` |
| EMC ontology: test types, entity types, unit normalization | ✅ | `ontology/emc_ontology.py` |
| Column header → entity type mapper (DE + EN) | ✅ | `ontology/column_mapper.py` |
| Domain row key extraction (per EMCTestType) | ✅ | `row_key_extractor.py` |
| NormalizedFact enrichment on table cells | ✅ | `table_normalization.py` |
| 6-factor table stitching continuation score | ✅ | `table_identity_resolver.py` |
| Backend reconciliation scoring | ✅ | `table_extraction_ensemble.py` |
| ExtractionValidator (structural metrics) | ✅ | `extraction_validator.py` |
| AccuracyEvaluator (enrichment metrics) | ✅ | `accuracy_evaluator.py` |
| Revalidation runner | ✅ | `revalidation_runner.py` |

---

## 3. Gap Analysis: KB Spec vs Implementation

### 3.1 Ingestion Pipeline Gaps

#### GAP-I1: No document family profile system
**KB spec (doc 23):** Versioned per-family profiles control heading detection rules, table caption patterns, row-key construction, unit normalization preferences, normative term dictionaries, alignment weights, severity overrides, and review thresholds. Profiles are registered with version/hash and used at every pipeline stage.  
**Current state:** Hardcoded generic logic everywhere. No profile selection.  
**Impact:** TL_81000 continuation tables (14/25 unclassified) and German header mismatches are direct consequences.  
**Fix:** Implement `DocumentFamilyProfile` dataclass + `AutomotiveEMCProfile` for TL_81000.

#### GAP-I2: No header/footer suppression
**KB spec (doc 03, 18):** Text blocks that appear on >40% of pages at the same y-coordinate band are headers/footers and must be excluded from section content and comparison.  
**Current state:** Not implemented. TL_81000 confidentiality stamps ("VOLKSWAGEN AG VERTRAULICH") and page-number footers may contaminate section text.  
**Fix:** Post-process Docling blocks; suppress repeated page-boundary blocks before hierarchy building.

#### GAP-I3: Heading detection not using weighted hybrid classifier
**KB spec (doc 03, 15):** Heading score = `0.25×numbering + 0.20×font_size_delta + 0.15×bold + 0.15×spacing + 0.10×bookmarks + 0.10×TOC + 0.05×keywords`.  
**Current state:** Delegated to Docling. No weighted classifier in our code — if Docling misses a heading, we have no fallback.  
**Fix:** Add `HybridHeadingDetector` as a post-Docling pass; re-score and promote/demote headings by weighted signals.

#### GAP-I4: Multi-page stitching weight mismatch
**KB spec (doc 03):** `header_sim = 0.35`, `section_path = 0.15`, `page_bottom/top = 0.10 each`.  
**Current state (Phase D):** `header_sim = 0.25`, `section_path = 0.10`. Column header similarity is under-weighted.  
**Fix:** Update `_continuation_score()` weights to match spec.

#### GAP-I5: OpenDataLoader not used as cross-check
**KB spec (doc 03):** Docling is primary; OpenDataLoader is secondary for structural cross-check (table detection validation, cell accuracy verification). Disagreements between extractors are flagged.  
**Current state:** OpenDataLoader adapter exists (`opendataloader_adapter.py`) but is not wired to cross-check Docling output.  
**Fix:** After Docling extraction, run OpenDataLoader on tables only and compare cell counts; flag low-confidence tables.

#### GAP-I6: OCR error normalization not implemented
**KB spec (doc 15):** After OCR, normalize common substitution errors: `3O→30`, `l50→150`, `µV` variants, `rnuss→muss`, `lnitial→Initial`.  
**Current state:** `ocr_used` flag is stored; no normalization.  
**Fix:** Add `OCRErrorNormalizer` with regex substitution table applied when `ocr_used=True`.

#### GAP-I7: Continued table EMC type not propagated
**Current state:** 14/25 tables in TL_81000_2021 are "fortgesetzt" (continued) fragments. The classifier returns UNKNOWN because the caption lacks EMC keywords.  
**Fix:** During stitching, when a continuation table is identified, copy `emc_test_type` from the parent fragment's metadata forward.

#### GAP-I8: Emission limit regex misses spaced µV format
**Current state:** `_EMISSION_LIMIT_RE` matches `dBµV` but not `db (μv)` (space + parentheses variant from PDF word-breaking).  
**Fix:** Extend pattern: `r"(\d+(?:[.,]\d+)?)\s*db\s*[\(]?\s*[μu]v\s*[\)]?"` (case-insensitive).

#### GAP-I9: Tolerance-format voltage not matched
**Current state:** `13, 5 ± 0, 5` (comma as decimal, spaced tolerance) does not match `_VOLTAGE_LEVEL_RE`.  
**Fix:** Add `_TOLERANCE_VOLTAGE_RE = re.compile(r"(\d+[,\s]\d+)\s*[±]\s*[\d,\s]+\s*(kV|V)")`.

#### GAP-I10: "Störaussendung" not triggering conducted_emissions
**Current state:** `_CONDUCTED_EMISSIONS_SIGNALS` does not include "störaussendung". Tables 35/36 containing conducted emission limits are classified UNKNOWN.  
**Fix:** Add `"störaussendung"`, `"stoeraussendung"`, `"leitungsgebundene emissionen"` to `_CONDUCTED_EMISSIONS_SIGNALS`.

---

### 3.2 CIR Schema Gaps

#### GAP-C1: Missing `key_column` flag on TableColumn
**KB spec (doc 02):** Each column has `key_column: bool` indicating it participates in the row semantic key.  
**Fix:** Add `key_column: bool = False` to `TableColumn` dataclass; set `True` when column matches a domain row key component.

#### GAP-C2: Missing `continued` flag on CanonicalTable
**KB spec (doc 02):** Tables have `continued: bool` for multi-page fragments.  
**Current state:** `multi_page_stitched` exists on the merged result but there is no flag on individual fragments marking them as continuations.  
**Fix:** Add `is_continuation_fragment: bool = False` to `CanonicalTable`.

#### GAP-C3: No Requirement object model
**KB spec (doc 02):** Requirements are first-class objects with `requirement_id`, `source_type` (paragraph/list/table_row/cell), `normative_level`, `normative_term`, `subject`, `action`, `condition`, `acceptance_criteria`, `citations`.  
**Current state:** Requirements are treated as clause text blobs in the comparison engine; no structured decomposition.  
**Impact:** Comparison engine cannot deterministically detect obligation weakening from "shall" → "should" at object level.  
**Fix:** Add `Requirement` dataclass to `canonical_models.py`; extract from clause text using normative term regex + sentence parsing.

#### GAP-C4: No reproducibility metadata stored per comparison run
**KB spec (doc 18):** Every comparison result must store: source SHA256s, CIR hashes, extractor/normalizer/alignment/severity/ontology/prompt/model versions.  
**Fix:** Add `ComparisonMetadata` dataclass written alongside every comparison output.

---

### 3.3 Comparison Engine Gaps

#### GAP-E1: Alignment engine missing 3 of 8 specified signals
**KB spec (doc 05):** Alignment score = `exact_identifier(0.20) + section_path(0.20) + title_caption(0.15) + embedding(0.15) + keyword(0.10) + table_schema(0.10) + numeric_pattern(0.05) + reranker(0.05)`.  
**Current state (`_clause_score`):** Uses `text(0.35) + lexical(0.20) + length(0.05) + meaning(0.25) + signature(0.15)`. Section path used for bucketing but not scored. No `exact_identifier`, `numeric_pattern`, or `reranker` signals.  
**Impact:** Documents where section numbering changes (TL_81000_2018 → TL_81000_2021 section renumbering) may misalign.  
**Fix:** Add `exact_identifier` (stable ID match = 1.0), `numeric_pattern` (shared numeric facts), and section_path scoring to `_clause_score`.

#### GAP-E2: No frequency range comparison logic
**KB spec (doc 05):** `frequency_range_expanded` if `new_lower <= old_lower AND new_upper >= old_upper`; `frequency_range_restricted` otherwise. Must convert units before comparison.  
**Current state:** Frequency ranges are extracted as NormalizedFacts but the diff engine does not compare them semantically using this logic.  
**Fix:** Add `FrequencyRangeDiff` to `table_diff_engine.py` using the NormalizedFact values (already in Hz).

#### GAP-E3: No normative strength ordering in diff engine
**KB spec (doc 05, 24):** Normative strength ordering: `prohibited > mandatory > recommended > permitted > informative`. Change in direction = high severity.  
**Current state:** `SeverityClassifier` has `NormativeObligationEscalationRule` but diff engine does not extract verb pairs (old\_term, new\_term) for deterministic comparison.  
**Fix:** In diff engine, detect old/new normative term from `NormalizedFact` and compare strengths using the ordering.

#### GAP-E4: No evidence pack generation
**KB spec (doc 05, 18):** Every change must have an evidence pack with `v1_evidence` and `v2_evidence` (doc/release/section/table/page/bbox/quote).  
**Current state:** Changes have section references but no structured bbox citations.  
**Fix:** At diff time, attach the `Citation` objects already stored on cells/nodes to the `ChangeRecord`.

#### GAP-E5: No four-pass alignment resolution
**KB spec (doc 15):** Four-pass: (1) exact ID match → (2) section/table schema match → (3) semantic retrieval → (4) moved/split/merged detection.  
**Current state:** Single-pass using text similarity + section bucketing. Moved content detection exists but is not a distinct fourth pass.  
**Fix:** Restructure `ClauseMatcher` as explicit four-pass pipeline.

---

### 3.4 Evaluation Harness Gaps

#### GAP-V1: No gold dataset
**KB spec (doc 21):** Gold pairs: 20 EN + 20 DE + 20 FR + 10 OCR + 20 table-heavy + 10 multi-page + 10 renumbered + 10 split/merged. Each pair: `manifest.json`, `expected_cir_assertions.json`, `expected_changes.json`.  
**Current state:** Zero annotated gold pairs.  
**Fix:** Create `eval/gold/` directory; annotate at minimum 5 TL_81000_2018 vs TL_81000_2021 pairs covering known change types.

#### GAP-V2: Evaluation harness only measures structural metrics
**KB spec (doc 21):** Six metric categories: extraction, alignment, diff, severity, LLM, export. Each with precision/recall/accuracy.  
**Current state:** `AccuracyEvaluator` measures structural + enrichment coverage only. Zero measurement of change detection accuracy.  
**Fix:** Implement `EvaluationHarness` class against gold dataset; compute change detection precision/recall.

#### GAP-V3: No regression suites
**KB spec (doc 21):** Smoke, parser, comparison, severity, LLM, export regression suites. Run on every code change.  
**Fix:** Create `tests/regression/` directory with parameterized tests against synthetic mutations.

---

### 3.5 Operational Gaps

#### GAP-O1: No document family profile registration
**KB spec (doc 23):** Profiles registered with `version`, `hash`, `domain`, `approvedAt`. Profile selection logged with confidence and signals.  
**Fix:** Implement `ProfileRegistry` with `AutomotiveEMCOEMProfile` as first registered profile.

#### GAP-O2: No offline model registry
**KB spec (doc 22):** All models pinned with version, hash, download source. No runtime downloads.  
**Fix:** Create `offline_registry.yaml` with Docling model, embeddings model, LLM model, version pins.

---

## 4. Quick Wins (≤1 day each, high accuracy impact)

| # | Fix | Impact | File |
|---|---|---|---|
| QW-1 | Add "störaussendung" to conducted_emissions signals | Classifies 3 more tables | `emc_ontology.py` |
| QW-2 | Extend emission limit regex for `db (μv)` format | Extracts facts from emission tables | `emc_ontology.py` |
| QW-3 | Fix tolerance voltage `13, 5 ± 0, 5` regex | Extracts facts from Prüfspannung tables | `emc_ontology.py` |
| QW-4 | Propagate EMC type to continuation table fragments | Classifies 10+ more tables | `table_identity_resolver.py` |
| QW-5 | Update stitching weights to KB spec (header_sim 0.35) | Better multi-page detection | `table_identity_resolver.py` |
| QW-6 | Add `key_column` flag on TableColumn | Proper CIR schema | `canonical_table_model.py` |
| QW-7 | Add `is_continuation_fragment` flag | CIR correctness | `canonical_table_model.py` |

---

## 5. Medium-Term Improvements (≤1 week each)

| # | Feature | Expected gain |
|---|---|---|
| MT-1 | `DocumentFamilyProfile` + `AutomotiveEMCOEMProfile` | Drives all downstream accuracy |
| MT-2 | Header/footer suppression in hierarchy builder | Cleaner section text |
| MT-3 | Frequency range diff logic in table_diff_engine | High-severity change detection |
| MT-4 | Normative strength comparison in diff engine | Obligation weakening/strengthening detection |
| MT-5 | Evidence pack citation attachment on ChangeRecord | Auditor-grade requirement |
| MT-6 | `Requirement` object model extraction | CIR completeness |
| MT-7 | OpenDataLoader cross-check on table confidence | Catch missed tables |

---

## 6. Long-Term (Gold Dataset + Evaluation Harness)

| # | Feature | Purpose |
|---|---|---|
| LT-1 | Gold dataset: 5–10 TL_81000 pairs with annotated changes | Measure real change detection accuracy |
| LT-2 | `EvaluationHarness` running against gold dataset | Replace coverage proxies with true precision/recall |
| LT-3 | Synthetic mutation test suite | Regression coverage for all 20+ change types |
| LT-4 | Reproducibility metadata per comparison | Auditor-grade requirement |
| LT-5 | Human review workflow (UI) | Audit trail for escalated changes |
| LT-6 | Hybrid heading detector (6-factor weighted) | Improve section hierarchy in complex PDFs |

---

## 7. Missing Pieces for Clean EMC Compliance Ingestion as Source of Truth

The following are **blocking gaps** for treating the extracted CIR as an auditor-grade source of truth:

### A. Table completeness
1. **Continued fragment stitching** must be lossless — every cell from every fragment must appear in the merged table with correct row/column indices.
2. **Footnotes** attached to table cells must be extracted and linked (currently not extracted).
3. **Merged cells** must propagate values to all covered (row, col) positions.

### B. Section hierarchy fidelity
1. **Header/footer suppression** prevents confidentiality stamps from appearing as section content.
2. **Appendix/normative annex detection** — normative annexes ("Normative Anhang") must be treated differently from informative ones.
3. **Cross-references** within documents (e.g., "see Table 3") must be resolved to node IDs.

### C. Requirement extraction
1. **Normative term extraction** from paragraph text (currently only table cells are enriched).
2. **Structured Requirement objects** (`subject`, `action`, `condition`, `acceptance_criteria`) needed for semantic comparison beyond text similarity.

### D. Comparison groundedness
1. **Every change needs a bounding box citation** back to the source PDF — not just section text.
2. **Frequency range comparison** must use normalized Hz values (implemented but not wired to diff).
3. **Table row keys** must be stable across versions for row-level change tracking (currently only extracted, not used in comparison).

### E. Evaluation
1. **Zero annotated gold pairs** means all accuracy numbers are coverage proxies, not true precision/recall.
2. The **change detection recall** for high-severity changes is entirely unmeasured.

---

## 8. Accuracy Improvement Roadmap

```
Sprint 1 — Quick wins (this week)
  QW-1 through QW-7 above
  → Re-run evaluation: target EMC classified ≥70%, fact coverage ≥15%

Sprint 2 — Document family profiles + header/footer suppression
  MT-1, MT-2
  → Re-run evaluation: target column mapping ≥70%, row key coverage ≥60%

Sprint 3 — Comparison engine: freq range diff + normative strength
  MT-3, MT-4, MT-5
  → First measurable change detection metrics (even without gold dataset)

Sprint 4 — Gold dataset annotation (5 pairs minimum)
  LT-1, LT-2
  → Replace coverage proxies with true high-severity change recall

Sprint 5 — CIR completeness (footnotes, merged cells, requirements)
  GAP-C1..C3, footnote extraction
  → Auditor-grade source of truth for ingested documents
```

---

## 9. Evaluation Iteration Protocol

Every significant code change should:
1. Run `uv run python -m grc_policy_server.services.ingestion.revalidation_runner`
2. Save output to `evaluation_results/NN_evaluation_report.md` (incrementing NN)
3. Copy `_accuracy_report.json` → `evaluation_results/NN_accuracy_report.json`
4. Note in the markdown: what changed, what improved, what regressed

Reports are numbered sequentially:
```
evaluation_results/
  01_evaluation_report.md         ← baseline (2026-05-12)
  01_accuracy_report.json
  01_validation_report.json
  02_gap_analysis_and_improvement_plan.md   ← this file
  03_evaluation_report.md         ← after Sprint 1 quick wins
  ...
```
