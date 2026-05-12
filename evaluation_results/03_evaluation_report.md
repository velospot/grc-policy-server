# Evaluation Iteration 03 — Post Quick-Wins (QW-1 through QW-7)

**Date**: 2026-05-12  
**Branch**: feature/multi-layer-table-extraction  
**Trigger**: Applied all 7 quick wins identified in `02_gap_analysis_and_improvement_plan.md`

---

## Changes Applied Since Baseline (01)

| QW | Description | File(s) Changed |
|----|-------------|-----------------|
| QW-1 | EMC type propagation to `(fortgesetzt)` continuation tables | `accuracy_evaluator.py` — `_propagate_emc_types()` |
| QW-2 | Extended `_CONDUCTED_EMISSIONS_SIGNALS` with German aliases (störaussendung, leitungsgebundene, etc.) | `emc_ontology.py` |
| QW-3 | Fixed `map_header("")` returning FieldStrength instead of `None` | `column_mapper.py` — early exit for empty string |
| QW-4 | Added 20+ TL_81000-specific German headers (prüfschärfe, impulstyp, betriebsspannung, u s in v, etc.) | `column_mapper.py` |
| QW-5 | Updated stitching weights to KB doc 03 spec: col_header_sim 0.25→0.35, adjacency 0.20→0.10, section_compat 0.10→0.15 | `table_identity_resolver.py` |
| QW-6 | Extended emission limit regex to match `db (μv)` spaced parenthesis format | `emc_ontology.py` |
| QW-7 | Split voltage regex: `_KV_STRONG_RE` (unconditional ±kV) + `_VOLTAGE_LEVEL_RE` (column-gated V); added `_TOLERANCE_VOLTAGE_RE` | `emc_ontology.py` |

Additional bug fixes:
- German "muss"/"müssen" added to `_NORMATIVE_TERM_RE`
- `_caption_similarity` returns `0.0` for both-empty captions (was `0.5` neutral — caused false stitches at boundary threshold)

---

## Metric Comparison

### Document 1: `20d49955` (TL_81000_2021-09 ~25 tables)

| Metric | Baseline (01) | Post QW (03) | Delta |
|--------|--------------|--------------|-------|
| EMC classified | 11/25 (44%) | 23/25 (92%) | **+48pp** |
| Fact coverage | 5.3% | 5.3% | 0 |
| Row key coverage | 19.4% | 57.4% | **+38pp** |
| Column mapping | 30.0% | 30.0% | 0 |
| Total NormalizedFacts | 84 | 84 | 0 |
| EMC distribution | radiated:7, ce:4, unknown:14 | radiated:10, ce:12, ti:1, unknown:2 | propagation working |

### Document 2: `766ac1df` (TL_81000_2018-03 ~12 tables)

| Metric | Baseline (01) | Post QW (03) | Delta |
|--------|--------------|--------------|-------|
| EMC classified | 11/12 (92%) | 11/12 (92%) | 0 |
| Fact coverage | 13.2% | 13.2% | 0 |
| Row key coverage | 60.0% | 60.0% | 0 |
| Column mapping | 54.2% | 54.2% | 0 |
| Total NormalizedFacts | 40 | 40 | 0 |

### Aggregate

| Metric | Baseline (01) | Post QW (03) | Delta | Sprint Target |
|--------|--------------|--------------|-------|---------------|
| EMC classified | 22/37 (59%) | 34/37 (92%) | **+33pp** | ≥70% ✅ |
| Fact coverage | ~9.2% | 9.2% | 0 | ≥15% ❌ |
| Row key coverage | ~39.7% | 58.7% | **+19pp** | — |
| Column mapping | ~42.1% | 42.1% | 0 | — |
| Total NormalizedFacts | 124 | 124 | 0 | — |

---

## Analysis

### What Improved

**EMC classification (44% → 92% for doc 1)**: QW-1 `_propagate_emc_types()` correctly forward-propagates the EMC type from named parent tables to their `(fortgesetzt)` continuation fragments. The 2021 document had 14 continuation fragments with captions like `"Tabelle 19 (fortgesetzt)"` — none of which contain EMC keyword signals. These now inherit the type from their preceding classified table.

**Row key coverage (19.4% → 57.4% for doc 1)**: The EMC type propagation (QW-1) enables domain-specific row key extraction on the previously-unknown continuation tables. Once a table is classified as `conducted_emissions`, the domain row key formula selects `[phenomenon, frequency, limit_class]` columns, yielding non-empty keys for most rows.

### What Did Not Change

**Fact coverage (unchanged at 9.2% aggregate)**: The NormalizedFact extraction regexes already worked — the data cells that match patterns haven't changed. Most TL_81000 tables have numeric values in voltage and frequency columns, but:
- ESD category tables (prüfschärfe, kategorie 1/2/3) store ordinal text like "A", "B", "C" — no numeric patterns to extract
- Emission limit tables have `db (μv)` format in headers but the data cells contain numeric-only values like `66` without units adjacent — requires context-aware extraction to link column unit to cell value

**Column mapping (unchanged)**: Column headers were already being parsed correctly for doc 2. Doc 1 col mapping stays at 30% because many split-header cells produce fragments like `"grenz-"` and `"wert u in db"` as separate columns — the header text is broken across cells in the raw extraction.

---

## Remaining Gaps (From Sprint 1 Targets)

### Fact Coverage Gap (9.2% vs ≥15% target)

Root cause: Most TL_81000 data cells are:
1. **Unit-implicit numeric values** — e.g., emission limit tables where the cell contains `"66"` and the unit `dBμV` is in the column header, not repeated in every cell
2. **Ordinal/textual immunity levels** — "A", "B", "C" in FPSC category tables; regex cannot extract these as NormalizedFacts

Proposed fixes (Sprint 2 medium-term items):
- **MT-3**: Column-unit inheritance — when `column_mapper` detects `EmissionLimit` or `FieldStrength` for a column, tag bare-numeric cells as facts with that unit
- This is a context-aware extraction step not possible with pure per-cell regex

### Column Mapping Gap (30% for doc 1)

Root cause: PDF extraction produces split headers across multiple rows — the column "Grenzwert U in dB (μV)" is fragmented into `"grenz-"`, `"wert u in db"`, `"(μv)"` as separate cells. The column mapper sees only these fragments, not the full header string.

Proposed fix:
- **MT-6**: Header cell merging — join fragmented header rows before column mapping; detect continuation hyphens and fuse across rows

### Still Unknown Tables (2 remaining)

Both are `"Tabelle 13 - Funktionszustandsklassifizierung (Streifen...)"` with empty header rows in one variant — these are classification-criteria tables describing functional states (A/B/C) and correctly remain unclassified (not EMC test tables).

---

## Sprint 2 Priorities (Medium-Term Improvements)

| Priority | Item | Expected Impact |
|----------|------|----------------|
| MT-3 | Column-unit inheritance for bare-numeric cells | Fact coverage: 9.2% → ~25-35% |
| MT-6 | Header cell merging / hyphen fusion | Column mapping for split-header tables |
| MT-1 | `DocumentFamilyProfile` (TL_81000 profile) | Structured extraction config per family |
| MT-2 | Header/footer suppression in hierarchy builder | Remove repeated page headers from nodes |
| MT-4 | Normative obligation comparison in diff engine | Semantically weighted ChangeRecord severity |
| MT-5 | Evidence pack citation attachment | `Citation` objects on ChangeRecord |

---

## Test Suite Status

- **337 passed** — same baseline as before quick wins
- **7 pre-existing failures** (not introduced by these changes):
  - 6 × `TestPageSplitTableDetection`/`TestPageSplitTableMerge` — old Weaviate schema mismatch
  - 1 × `test_real_diff_engine_enriches_missing_semantics_with_llm` — LLM stub API mismatch
