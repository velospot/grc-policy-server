# Evaluation Iteration 04 — Post Phase A (Column-Unit Inheritance) + Phase B (Header Fusion)

**Date**: 2026-05-13  
**Branch**: feat/knowledge-base  
**Trigger**: Implemented Sprint 2 Phase A and Phase B improvements

---

## Changes Applied Since Eval 03

| Phase | Description | File(s) Changed |
|-------|-------------|-----------------|
| A | Column-unit inheritance: bare-numeric cells in typed columns (EmissionLimit→dBuV, FieldStrength→V/m, FrequencyRange→Hz) synthesize NormalizedFact | `emc_ontology.py` — `extract_bare_numeric_with_unit()`; `table_normalization.py` — `enrich_table_with_facts()` column-unit fallback; `accuracy_evaluator.py` — same fallback in evaluation loop |
| A | Fixed `m.group(2) or "dBuV"` null-safety bug in emission limit regex (spaced `db (μv)` form has no group 2) | `emc_ontology.py` |
| B | Hyphen-fusion of split headers: `"grenz-" + "wert u in db"` → `"grenzwert u in db"` before column mapping | `table_normalization.py` — `_fuse_hyphenated_headers()` applied in `extract_headers_from_cells()` |
| B | Extended `HEADER_TO_ENTITY` with 15+ TL_81000 fragment patterns: `"wert u in db"`, `"f in mhz"`, `"f in khz"`, `"bw f in khz"`, `"e in v/m"`, `"messempfänger"`, `"detektor"`, `"messpunkt"`, `"messbereich"`, `"bandbreite"`, `"feld in v/m"`, etc. | `column_mapper.py` |
| B | Added `ENTITY_TYPE_DEFAULT_UNIT` dict to `column_mapper.py` for column-unit inheritance | `column_mapper.py` |
| B | Added `_MIN_PARTIAL_MATCH_LEN = 5` guard + de-hyphenation in `map_header()` to prevent false positives from short fragments ("khz", "in") | `column_mapper.py` |
| B | Accuracy evaluator now uses `extract_headers_from_cells()` for proper multi-row header normalization — headers[i] directly corresponds to column position i | `accuracy_evaluator.py` |
| fix | Aggregate reporter excludes zero-table documents from coverage averages | `accuracy_evaluator.py` — `print_report()` |

---

## Metric Comparison

### Document 1: `82cf92e7` (TL_81000_2021-09, 25 tables)

| Metric | Baseline (01) | Post QW (03) | Post Phase A+B (04) | Delta 03→04 |
|--------|--------------|--------------|---------------------|-------------|
| EMC classified | 11/25 (44%) | 23/25 (92%) | 24/25 (96%) | **+4pp** |
| Fact coverage | 5.3% | 5.3% | **19.5%** | **+14.2pp** |
| Row key coverage | 19.4% | 57.4% | **67.1%** | **+9.7pp** |
| Column mapping | 30.0% | 30.0% | **48.4%** | **+18.4pp** |
| Total NormalizedFacts | 84 | 84 | **299** | **+215** |

### Document 2: `64d92a00` (TL_81000_2018-03, 12 tables)

| Metric | Baseline (01) | Post QW (03) | Post Phase A+B (04) | Delta 03→04 |
|--------|--------------|--------------|---------------------|-------------|
| EMC classified | 11/12 (92%) | 11/12 (92%) | **12/12 (100%)** | **+8pp** |
| Fact coverage | 13.2% | 13.2% | **16.1%** | **+2.9pp** |
| Row key coverage | 60.0% | 60.0% | **66.0%** | **+6pp** |
| Column mapping | 54.2% | 54.2% | **61.0%** | **+6.8pp** |
| Total NormalizedFacts | 40 | 40 | **48** | **+8** |

### Aggregate (37 tables across 2 TL_81000 docs)

| Metric | Baseline (01) | Post QW (03) | Post Phase A+B (04) | Sprint Target |
|--------|--------------|--------------|---------------------|---------------|
| EMC classified | 59% (22/37) | 92% (34/37) | **97% (36/37)** | ≥80% ✅ |
| Fact coverage | 9.2% | 9.2% | **17.8%** | ≥50% (progress) |
| Row key coverage | 39.7% | 58.7% | **66.6%** | ≥80% (progress) |
| Column mapping | 42.1% | 42.1% | **54.7%** | ≥70% (progress) |
| Total NormalizedFacts | 124 | 124 | **347** | — |

*Note: Aggregate averages now computed only over documents with tables > 0 (2 docs) to avoid dilution from empty/non-table docs in the upload root.*

---

## Analysis

### Column-Unit Inheritance (Phase A)

**Fact coverage 9.2% → 17.8% (+8.6pp)**: The `extract_bare_numeric_with_unit()` method synthesizes NormalizedFacts for bare-numeric cells whose column is typed. For TL_81000_2021 emission limit tables, cells containing `"66"`, `"46"`, etc. now produce `emission_limit` facts with unit `dBuV` because their column (`grenzwert u in db`) maps to `OntologyEntityType.EMISSION_LIMIT`. This accounts for the bulk of the 215 new facts in doc 1.

**NormalizedFacts 124 → 347 (+223)**: Nearly tripled. The 2021 document gained 215 new facts from column-unit inheritance; the 2018 document gained 8 from both inheritance and the corrected emission limit regex.

### Header Fusion + Fragment Mapping (Phase B)

**Column mapping 42.1% → 54.7% (+12.6pp)**: Two mechanisms:
1. `_fuse_hyphenated_headers()` merges hyphen-broken header cells (`"pk grenz-"` + `"wert u in db"` → `"pk grenzwert u in db"`) before column mapping, allowing the full compound key to match.
2. New `HEADER_TO_ENTITY` entries for unit-fragment patterns (`"f in mhz"`, `"bw f in khz"`, `"e in v/m"`, etc.) catch columns where the sub-header text alone is a unit expression.

**Row key coverage 58.7% → 66.6% (+7.9pp)**: Better column mapping means more columns are assigned entity types, enabling domain-specific row key extraction on previously-unmapped columns.

### Multi-Row Header Normalization Fix

The accuracy evaluator previously collected ALL `is_header` cells and enumerated them, causing enumeration index ≠ actual column position for multi-row header tables (group headers in row 0 + sub-headers in row 1). This was replaced with `extract_headers_from_cells(cells_raw, num_cols)` which:
- Returns exactly `num_cols` entries, one per column position
- Combines group header + sub-header text (e.g., `"pk" + "grenzwert e in db"` → `"pk grenzwert e in db"`)
- Ensures `headers[i]` corresponds to actual column position `i` in data cells

---

## Remaining Gaps

### Fact Coverage (17.8% vs ≥50% target)

Remaining opportunities:
1. **Short group-header columns**: Columns whose only header text is a short group label like `"pk"` or `"av"` (2 chars) don't match any entity type. Need combined header patterns like `"pk grenz-"` + sub-header fusion to produce full compound keys.
2. **Non-numeric cells**: ESD category tables with ordinal values ("A", "B", "C") in immunity level columns require a separate extraction path for ordinal fact types — not addressable with numeric regex alone.
3. **Formula/expression cells**: Row key formula expressions like `"1_164:72_-_9_409_×_lg(f/1_164)"` are being used as row keys, not as fact values. These are actually frequency formula cells that need a dedicated parser.

### Column Mapping (48.4% for TL_81000_2021)

Still-unmatched patterns in the 2021 doc:
- `"prüf- nr."` (hyphen-fused to "prüf- nr." but still not in HEADER_TO_ENTITY)
- `"pk→?"`, `"av→?"`, `"qp→?"` — 2-char group labels below partial-match threshold
- `"pk bw f in→?"` — hyphen-fused partial ("bw f in khz" has "khz" cut off)
- `"anzahl→?"` — count column; intentionally unmapped

### One Unknown Table Remains

`82cf92e7` table 14 (`"Tabelle 38 - Einstellwerte"`) stays `unknown` — its caption lacks EMC signals. It propagated a `conducted_emissions` type from the predecessor in eval 03, but now correctly classifies as `unknown` with this table's actual caption. Acceptable: it's a general parameter table without EMC-specific test type.

---

## Test Suite Status

- **337 passed** — same baseline, no regressions introduced
- **7 pre-existing failures** (unchanged from eval 03):
  - 6 × `TestPageSplitTableDetection`/`TestPageSplitTableMerge` — old Weaviate schema mismatch
  - 1 × `test_real_diff_engine_enriches_missing_semantics_with_llm` — LLM stub API mismatch

---

## Sprint 2 Next Steps

| Phase | Item | Expected Impact |
|-------|------|----------------|
| C | `DocumentFamilyProfile` dataclass + `AutomotiveEMCOEMProfile` | Foundation for profiles; enables per-family unit maps and stitching weights |
| D | Header/footer suppression in `hierarchy_builder.py` | Cleaner nodes; removes confidentiality stamps from comparison |
| E1 | Frequency range diff in `table_diff_engine.py` | First semantic frequency change detection |
| E2 | Normative strength comparison in `real_diff_engine.py` | Obligation-level semantic impact on ChangeRecord |
| F | Evidence pack citation attachment on `ChangeRecord` | Auditor-grade citations per change |
