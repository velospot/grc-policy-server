# GRC Policy Server — Accuracy Benchmark Evaluation

**Evaluation date:** 2026-06-03  
**Branch:** `v3-rearchitecture`  
**Engine:** `OfflineDiffEngine` (no LLM, no Weaviate) for reproducibility

---

## 1. Document Corpus

| Document ID (short) | Filename | Family | Pages | Nodes | Size |
|---|---|---|---|---|---|
| `f3751af3` | TL_81000_2018-03_p013-022.pdf | TL-81000 | p13–22 | 83 | 1.8 MB |
| `7ef2f211` | TL_81000_2021-09_GER_p013-022.pdf | TL-81000 | p13–22 | 76 | 1.9 MB |
| `f40aaab0` | TL_81000_2018-03_p003-012.pdf | TL-81000 | p3–12 | 107 | 180 KB |
| `ed2e8ccd` | TL_81000_2021-09_GER_p003-012.pdf | TL-81000 | p3–12 | 116 | 248 KB |
| `30feff8f` | DNVGL-CG-0339_Nov_2016_p013-022.pdf | DNVGL-CG-0339 | p13–22 | 151 | 384 KB |
| `7bd665a2` | DNVGL-CG-0339_Dez_2019_p013-022.pdf | DNVGL-CG-0339 | p13–22 | 131 | 608 KB |
| `114ae398` | DNVGL-CG-0339_Nov_2016_p023-032.pdf | DNVGL-CG-0339 | p23–32 | 158 | 384 KB |
| `df480787` | DNVGL-CG-0339_Dez_2019_p023-032.pdf | DNVGL-CG-0339 | p23–32 | 179 | 612 KB |
| `75c254cc` | DIN EN 60068-2-64_2020.pdf | DIN EN 60068-2 | full | 564 | 972 KB |
| `85f60c88` | DIN EN 60068-2-38_2022.pdf | DIN EN 60068-2 | full | 255 | 2.1 MB |

**Document families:**
- **TL-81000** — German automotive supplier EMC/Safety standard (Volkswagen AG)
- **DNVGL-CG-0339** — DNV GL marine safety classification guideline (English)
- **DIN EN 60068-2-xx** — German/European IEC environmental test methods (EMC context)

---

## 2. Comparison Pairs & Alignment Benchmark

Seven comparison traces were analysed. Five unique document pairs are reported below.

| # | Pair (old → new) | Family | Doc1 Nodes | Doc2 Nodes | Matched | Unmatched L | Unmatched R | **Match Rate** |
|---|---|---|---|---|---|---|---|---|
| 1 | TL-81000 2018-03→2021-09 p013-022 | Safety/DE | 83 | 76 | 34 | 34 | 27 | **35.8%** |
| 2 | TL-81000 2018-03→2021-09 p003-012 | Safety/DE | 107 | 116 | 32 | 45 | 54 | **24.4%** |
| 3 | DNVGL Nov2016→Dec2019 p013-022 | Safety/EN | 151 | 131 | 59 | 32 | 26 | **50.4%** |
| 4 | DNVGL Nov2016→Dec2019 p023-032 | Safety/EN | 158 | 179 | 72 | 21 | 31 | **58.1%** |
| 5 ❌ | DIN 60068-2-64 (2020) vs 60068-2-38 (2022) | EMC/DE | 564 | 255 | 16 | 362 | 178 | **2.9%** |

> **Note on pair #5:** DIN EN 60068-2-64 (Wideband Random Vibration) and DIN EN 60068-2-38 (Temperature Humidity Cycling) are **different test methods**, not different versions of the same standard. The 2.9% match rate correctly reflects near-zero content overlap. This is a user workflow error; the system has no guardrail to warn about incompatible document pairs.

**Average match rate (in-family, same-version pairs 1–4): 42.2%**

---

## 3. Change Record Statistics

| # | Pair | REMOVED | ADDED | MODIFIED | Total | High Sev | Med Sev | Low Sev | Numeric Δ | Table Δ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TL-81000 p013 | 25 | 18 | 11 | **54** | 40 | 14 | 0 | 74 | 6 |
| 2 | TL-81000 p003 | 21 | 29 | 15 | **65** | 47 | 17 | 1 | 74 | 1 |
| 3 | DNVGL p013 | 26 | 26 | 5 | **57** | 42 | 12 | 3 | 49 | 5 |
| 4 | DNVGL p023 | 16 | 25 | 18 | **59** | 36 | 16 | 7 | 68 | 12 |
| 5 ❌ | DIN 60068 ❌ | 279 | 134 | 8 | **421** | 393 | 26 | 2 | 885 | 22 |

**Observation:** In pairs 1–4, REMOVED and ADDED rates are high relative to MODIFIED, indicating the matcher is creating many false REMOVED+ADDED pairs instead of recognising them as MODIFIED. This inflates high-severity counts because unmatched clauses default to HIGH severity.

---

## 4. Confidence & Accuracy Metrics

| # | Pair | Conf Mean | Conf Min | Low-Conf Matches | Citation Coverage |
|---|---|---|---|---|---|
| 1 | TL-81000 p013 | 0.961 | 0.480 | 1 | 100% |
| 2 | TL-81000 p003 | 0.960 | 0.254 | 1 | 100% |
| 3 | DNVGL p013 | 0.992 | 0.566 | 1 | 100% |
| 4 | DNVGL p023 | 0.958 | 0.177 | 1 | 100% |
| 5 ❌ | DIN 60068 ❌ | 0.995 | 0.565 | 0 | 100% |

**High mean confidence (0.96–0.99) is misleading** — confidence of 1.0 is assigned by default to all REMOVED/ADDED records (unmatched nodes). High-confidence doesn't indicate accurate matching; it reflects a trivial assignment rule.

---

## 5. Section Structure Quality

All documents exhibit **hierarchy depth = 1** in both raw docling output and canonical hierarchy:

| Document | Unique Sections | Hierarchy Depth | Sample Paths |
|---|---|---|---|
| TL-81000 2018-03 p013 | 15 | **1** | `"5.1.2.2 Durchfuhrung"`, `"5.1.3 Prufungen..."` |
| TL-81000 2021-09 p013 | 15 | **1** | `"5.1 ..."`, `"Unsectioned"` |
| DNVGL Nov2016 p023 | 53 | **1** | `"14.4.3"`, `"8.3.4"`, `"9.2.5"` |
| DNVGL Dec2019 p023 | 67 | **1** | `"7.1.1"`, `"8.3.4"`, `"14.1"` |
| DIN 60068-2-64 | 177 | **1** | `"NationalesVorwort"`, `"Vervielfaltigung..."` |

**Problem:** Even though DNVGL has proper dotted section numbers ("14.4.3", "9.2.1"), the hierarchy builder treats each as a flat, independent bucket. There is no parent-child relationship encoded: "14.4.3" does not know it belongs to "14.4" which belongs to "14". The `ClauseMatcher` therefore cannot exploit the hierarchical structure — it treats all 53 sections as siblings at the same level.

**OCR artifact example:** `"5.1.3.2Durchfuhrung"` — space missing between the numeric prefix and the title word, causing the section title to be misidentified and preventing numeric hierarchy parsing.

---

## 6. Root Cause Analysis

### 6.1 Ingestion Root Causes

#### 6.1.1 Flat Section Hierarchy (Critical)

**Root cause:** Docling outputs all section headers as direct children of the document body (`parent={'$ref': '#/body'}`), regardless of their nesting level implied by the section number. This is expected behaviour for Docling v1.10.0 operating on partial PDF excerpts.

**Impact:** `section_titles` is always a single-element list, `section_path` is always a flat string. The hierarchy builder cannot infer parent-child relationships between "5.1.2", "5.1.2.1", and "5.1.2.2" because they all appear at the same level in the docling parse tree.

**Affected metric:** Hierarchy depth = 1 for all 10 documents.

**Fix:** Parse the numeric prefix of section titles to infer ancestor levels. "5.1.2.2 Durchfuhrung" → `["5", "5.1", "5.1.2", "5.1.2.2 Durchfuhrung"]`.

#### 6.1.2 OCR Fusion Artifacts (Medium)

**Root cause:** Some OCR or text extraction passes fuse the section number with the title word: `"5.1.3.2Durchfuhrung"` instead of `"5.1.3.2 Durchfuhrung"`. This prevents correct regex matching and hierarchy expansion.

**Fix:** Regex repair: detect `<digits.digits><letter>` boundary and insert space.

#### 6.1.3 Unsectioned Nodes (Low)

**Cause:** 2–3 nodes per document land in the `Unsectioned` bucket due to missing or unrecognised heading context.

**Impact:** These nodes carry no section context, making them impossible to match hierarchically. They either produce false REMOVED/ADDED or are matched randomly.

#### 6.1.4 Section Nodes Are Indexed (Low)

**Cause:** `section_node.indexable = bool(aggregated_text)`. Sections with any descendant text become indexable and are loaded into the comparison node set. This creates duplicate content: the same clause text appears once as a `clause` node and once as aggregated text in the parent `section` node.

**Impact:** False positive MODIFIED records where a clause matches its own section ancestor.

**Note:** `COMPARISON_NODE_TYPES` in `canonical_models.py` excludes `"section"`, so this affects the vector index but NOT the comparison pipeline directly. However, it does inflate Weaviate storage and retrieval noise.

#### 6.1.5 Missing Obligation Enrichment (Low)

**Observation:** Many clause nodes have `obligation: None` despite being in normative sections. The rule-based obligation extractor (`ObligationPatternRegistry`) only fires on `clause` nodes; nodes classified as `paragraph` by Docling are not enriched.

**Impact:** The semantic scorer (`_meaning_score`) falls back to full-text extraction, which is slower and less accurate than pre-enriched fields.

---

### 6.2 Comparison Root Causes

#### 6.2.1 Flat Section Matching Degrades Hierarchical Intent (Critical)

**Root cause:** `ClauseMatcher._build_sections()` groups nodes by the full `section_path` string. With all sections at depth 1, this is equivalent to treating every section as an independent, unrelated unit.

**Impact:** When DNVGL restructures chapter 14 to chapter 8 (2016 → 2019), the matcher has no way to detect that "14.2 Performance criteria" should correspond to content now in chapter 8. All of chapter 14's content (16+ sections) appears as REMOVED; all of chapter 8's content appears as ADDED. The 58.1% match rate for DNVGL p023 would be ~75%+ if the matcher could align renumbered chapters by content.

**Fix:** Add chapter-level pre-matching: extract the top-level chapter number from each section path, match chapters by aggregated content similarity, then apply a scoring bonus to section pairs that share a matched chapter.

#### 6.2.2 Section Renumbering Detected as REMOVED+ADDED (High)

**Root cause:** The matcher has no "section renamed/renumbered" alignment type. When entire chapters are renumbered, every clause within them is independently classified as REMOVED (from the old chapter) and ADDED (in the new chapter), even if the content is identical.

**Impact:** DNVGL p023 shows 16 REMOVED + 25 ADDED from the chapter 14→8 renaming alone. All are classified HIGH severity. This generates ~40 false high-severity alerts per comparison.

**Fix:** Post-matching, detect unmatched section groups where one old chapter's child content has high token overlap (≥ 0.60) with one new chapter's child content. Classify as `section_renamed` with MEDIUM severity; suppress individual REMOVED/ADDED.

#### 6.2.3 Wrong Document Pair — No Compatibility Check (High)

**Root cause:** No pre-flight validation of document pair compatibility. The system compares any two documents regardless of whether they represent versions of the same standard.

**Impact:** DIN 60068-2-64 vs DIN 60068-2-38 generates 421 change records (393 HIGH), all of which are meaningless. A reviewer would waste significant time.

**Fix:** Pre-flight Jaccard overlap check on section title sets. Warn if overlap < 15% and document families differ.

#### 6.2.4 `doc1Content`/`doc2Content` Empty in Many Change Records (Medium)

**Root cause:** `_display_content()` only reads `node.get("text")`. When nodes are loaded from Weaviate with text under `clean_text` or the node was a section aggregate, `text` may be empty while `clean_text` or `comparison_text` is populated.

**Impact:** The LLM receives change records with empty content snippets, degrading summary quality. Reviewers also see empty "before/after" previews in the UI.

**Fix:** Fallback chain in `_display_content`: `text` → `clean_text` → `canonical_text` → `comparison_text`.

#### 6.2.5 Moved Sections Classified HIGH Despite Structural Move (Low)

**Observation:** DNVGL match `14.4.3 Test results` → `8.3.7 Test result` is classified MODIFIED with distance=0.823 and severity=HIGH. The content change ("In accordance with performance criterion A." → detailed pass criteria) is genuine, but the "moved" aspect (cross-chapter reassignment) is driving severity escalation.

**Fix:** For `alignment_type="moved"` with cross-chapter section paths, cap severity at MEDIUM unless content distance alone (excluding section-path change) exceeds threshold.

---

## 7. Benchmark Summary

| Metric | Current | Target (post-fix) |
|---|---|---|
| Avg match rate (in-family pairs) | **42.2%** | ≥ 65% |
| False REMOVED+ADDED from renumbering | ~40 per DNVGL pair | < 5 |
| Wrong-pair detection | None | Warn when Jaccard < 15% |
| Section hierarchy depth | 1 (flat) | 2–4 (inferred from numbers) |
| `doc1Content` empty rate | ~30% of REMOVED records | < 5% |
| OCR fusion artifacts repaired | 0 | 100% of numeric-prefix titles |

---

## 8. Recommended Improvements (Priority Order)

| Priority | Area | Change | Expected Impact |
|---|---|---|---|
| P0 (Critical) | Ingestion | Parse numeric section numbers into ancestor hierarchy | Enables hierarchical matching; fixes all flat-hierarchy issues |
| P0 (Critical) | Comparison | Chapter-level pre-matching in `ClauseMatcher` | Match rate +15–25% for version-pair docs |
| P1 (High) | Comparison | Section renaming detection (`section_renamed` alignment type) | Eliminates ~40 false HIGH alerts per DNVGL comparison |
| P1 (High) | Comparison | Wrong-pair pre-flight check | Prevents meaningless DIN 60068-2-64 vs 2-38 comparisons |
| P2 (Medium) | Comparison | Fix `_display_content` fallback chain | Populates empty `doc1Content`/`doc2Content` in change records |
| P2 (Medium) | Ingestion | OCR fusion repair regex | Fixes `"5.1.3.2Durchfuhrung"` artifacts |
| P3 (Low) | Comparison | Cap MOVED severity when section path changes cross chapters | Reduces false HIGH for structural reorganisations |
| P3 (Low) | Ingestion | Enrich `paragraph`-typed normative nodes for obligations | Better semantic scoring |

---

## 9. Reproduction Instructions

```bash
# Run offline comparison for any document pair (no Celery/LLM required)
cd /Users/navm/projects/grc-policy-server

# Extract metrics from existing trace
python3 -c "
import json, glob
for f in sorted(glob.glob('data/uploads/_comparison_traces/*.json')):
    with open(f) as fp: data = json.load(fp)
    cp = data.get('checkpoints', {})
    al = cp.get('alignmentResults', {})
    m = al.get('matchedNodes', 0)
    ul = al.get('unmatchedLeft', 0)
    ur = al.get('unmatchedRight', 0)
    total = m + ul + ur
    rate = m/total if total else 0
    print(f'{f[-60:]}: match={rate:.1%} matched={m}/{total}')
"
```

---

## 10. Table Extraction & Comparison Quality

**Evaluation date:** 2026-06-03  
**Corpus:** 71 tables across 10 documents (TL-81000 × 14, DNVGL-CG-0339 × 35, DIN EN 60068-2 × 22)

### 10.1 Extraction Quality Baseline

| Family | Tables | Avg Fill | Avg Numeric Density | Multi-Row Headers | Camelot Upgrades |
|---|---|---|---|---|---|
| TL-81000 (2 versions × p003+p013) | 14 | 0.86 | 0.80 | 4 (depth=2) | 0 |
| DNVGL-CG-0339 (2 versions × p013+p023) | 35 | 0.89 | 0.48 | 5 (depth=2) | 0 |
| DIN EN 60068-2 (2 standards) | 22 | 0.80 | 0.57 | 2 (depth=2) | 0 |

**Fill rate distribution (71 tables):**
- High ≥ 0.80: 47 tables (66%)
- Mid 0.50–0.80: 20 tables (28%)
- Low < 0.50: 4 tables (6%) ← extraction failures

**Numeric density distribution:**
- High ≥ 0.60: 31 tables (44%) — EMC/safety test matrices
- Mid 0.30–0.60: 31 tables (44%) — mixed content
- Low < 0.30: 9 tables (13%) — definition/legend tables

**Header depth distribution:**
- Single-row headers (depth=1): 60 tables (85%)
- Multi-row headers (depth=2): 11 tables (15%)

### 10.2 Known Extraction Issues (Pre-Fix)

| Table | Document | fill | Issue |
|---|---|---|---|
| Table 6 "General vibration strain, class A" | DNVGL Nov2016 p013 | **0.08** | All 12 cells empty — PDF uses image rendering for this table; Docling cannot extract grid content |
| Table 10 "Tolerances" | DNVGL Nov2016 p013 | **0.25** | Sparse 2×2 table; tolerance values in non-standard format |
| Table 12 "Lower test levels" | DNVGL Nov2016 p023 | N/A | header `column_1` placeholder for empty stub column in col 0 → `schema_signature` mismatch vs Dec2019 version |
| Table 5 "Wide band random" | DNVGL Nov2016 p013 | 1.00 | 2×2 definition list, row 0 treated as header despite being a data row; passes degenerate filter |
| Multiple (11 tables) | Various | N/A | "column_1" placeholder headers → `schema_signature` never matches across doc versions |

**Key finding:** `canonical_table` field (indicating Camelot won the extraction) is absent from ALL 71 tables. The old winner-selection logic (IoU ≥ 0.4 AND Camelot has more cells) always favoured Docling because: (a) empty Docling grids still have a higher cell count than no-content Camelot output for image-rendered tables, and (b) no quality signal was used.

### 10.3 Table Comparison Quality (Pre-Fix)

From DNVGL Nov2016 vs Dec2019 p023 comparison trace:

| Metric | Value |
|---|---|
| Table change records | 7 (out of 59 total) |
| REMOVED | 4 — all from chapter 14 (section renumbering, not real removals) |
| ADDED | 1 — from chapter 8 (same content, renumbered) |
| MODIFIED (genuine) | 2 |
| Empty `doc1Content` in table records | **100%** — `_reference_source_text()` returned only title |
| LaTeX cosmetic classified as MODIFIED | Yes — "2 · un + 500" vs "2 · u$_{n}$ + 500" |
| Column reordering detection | None |

### 10.4 Improvements Implemented

| Phase | Change | Files |
|---|---|---|
| 1 | Quality-score dual extraction: `_score_table_extraction_quality()` (fill + numeric density + header quality + dimensions); ensemble merge when scores tied | `document_ingestion_service.py` |
| 1 | `extraction_quality_score` stored in table metadata for benchmarking | `document_ingestion_service.py` |
| 2 | `column_header` / `is_header` Docling flags used as priority-1 header signal | `table_normalization.py` |
| 2 | Empty stub columns tagged `_row_label_N` instead of `column_1` | `table_normalization.py` |
| 2 | `schema_signature()` excludes stub columns from hash → stable cross-version matching | `table_normalization.py` |
| 2 | Content-based header depth detection for tables without flags/spans (up to 3 levels) | `table_normalization.py` |
| 3 | `_align_table_columns()` maps columns by header similarity before cell comparison | `clause_matcher.py` |
| 3 | Column-aligned cell scoring: ≥50% column match → remap positions before Jaccard | `clause_matcher.py` |
| 3 | `_is_reliable_schema_signature()` neutralises schema score when placeholder headers detected | `clause_matcher.py` |
| 3 | LaTeX strip in `_norm_cell()`: "u$_{n}$" ≡ "un" in cell-level comparison | `clause_matcher.py` |
| 4 | `_strip_latex_and_math()` added to `is_cosmetic_text_change()` | `change_records.py` |
| 5 | `_reference_source_text()` returns markdown table rows for audit citations | `real_diff_engine.py` |

### 10.5 Targets After Improvements

| Metric | Before | Target |
|---|---|---|
| Tables with fill < 0.50 | 4/71 (6%) | < 1% (Camelot/ensemble covers image-tables) |
| Tables with "column_1" placeholder headers | ~11/71 (15%) | < 2% (stub-column tagging) |
| Camelot upgrade / ensemble rate | 0% | ≥ 20% for low-fill tables |
| Table MODIFIED records with empty `doc1Content` | ~100% | < 5% (markdown fallback) |
| LaTeX cosmetic misclassified as MODIFIED | Yes | No (normalized before comparison) |
| Column reordering handled in cell scoring | No | Yes (≥50% header alignment coverage) |

---

## 11. Iteration History

| Timestamp |
| 2026-06-03T20:19 | dd5e115 | 42.2% | 35.8% | 58.1% | 0 | 12 | 1 | Git | Avg Match | TL p013 | DNVGL p023 | Camelot | Stub Headers | Max Depth |
|---|---|---|---|---|---|---|---|
| 2026-06-04T07:25 | dd5e115 | ? | ? | ? | 0 | 8 | 1 |
| 2026-06-04T07:42 | dd5e115 | ? | ? | ? | 0 | 8 | 1 |
| 2026-06-04T07:46 | dd5e115 | 100.0% | 100.0% | 100.0% | 0 | 8 | 1 |
| 2026-06-04T14:52 | 8fd0725 | 100.0% | 100.0% | 100.0% | 0 | 8 | 1 |

---

## 12. Iteration 2 Analysis — 2026-06-04

### 12.1 Non-Compliance Filter Effectiveness (Phase 1)

Comparing DIN 60068-2-64 vs 60068-2-38 (incompatible pair):

| Metric | Pre-Fix (old trace) | Post-Fix (new trace) |
|---|---|---|
| Total change records | 421 | 354 |
| Non-compliance sections in changes | **11 types** | **0** |
| Change records from non-compliance | ~67 | 0 |

Non-compliance sections successfully suppressed: `NationalesVorwort`, `Europaisches Vorwort`, `Vervielfaltigung-auchfurinnerbetrieblicheZwecke-nichtgestattet.`, `Anwendungsbeginn`, `Anderungen`, `Fruhere Ausgaben`, `Zusammenhang mit...`, `EUROPAISCHE NORM EUROPEANSTANDARD NORME EUROPEENE`, `Europaisches Vorwort zur Anderung A1`, plus 2 more.

Both the ingestion-time filter (`_NON_COMPLIANCE_RE` in `hierarchy_builder.py`) and the comparison-time backstop (`_is_skip_heading()` in `real_diff_engine.py`) are now active.

### 12.2 Section Heading Normalization Improvements (Phase 2)

| Input | Old normalization | New normalization |
|---|---|---|
| `Systemprüfung` | `systemprüfung` (no synonym) | `system test` ✓ |
| `Systemtest` | `systemtest` (no synonym) | `system test` ✓ |
| `Durchführung` | `durchführung` (no synonym) | `procedure` ✓ |
| `Prüfverfahren` | `prüfverfahren` → `test method` | `test method` ✓ |
| `ElektrostatischeEntladung` | `elektrostatischeentladung` (fused) | `elektrostatische entladung` ✓ |
| `6.2.5Testresult` | `testresult` (fused digit) | `testresult` → `result` |

Accent folding + fused-word splitting + extended HEADING_SYNONYMS now handle common GRC document heading variants.

### 12.3 Semantic Output Fields Added (Phases 3 & 4)

Every `KeyDifference` now includes:
- `complianceExplanation: str` — deterministic rules-based compliance narrative
- Structural LOW diffs suppressed into `suppressedDiffsCount` 
- `skippedSections: list[str]` — sections examined with no semantic change detected

Examples of generated `complianceExplanation`:
- "Obligation weakened: 'shall' → 'should'. This relaxes the compliance requirement — verify if intentional and update test records."
- "Test parameter changed: 3 V/m → 5 V/m. Review impact on pass/fail criteria and update test documentation."
- "Section restructured: content relocated (section numbering changed). Verify compliance requirement traceability against the new section reference."
- "New compliance requirement added. Review for additional obligations, test procedures, or documentation burden."

### 12.4 Compatibility Warning (Improved)

DIN 60068-2-64 vs DIN 60068-2-38 now correctly triggers:

> "Low section-title overlap (12%) and low section-number overlap (7%) between 'DIN EN 60068-2-64_2020.pdf' and 'DIN EN 60068-2-38_2022.pdf'. These documents may be different standards rather than versions of the same standard."

The warning uses two independent signals (title word Jaccard + section number Jaccard) to avoid false positives from shared German boilerplate.

### 12.5 In-Family Comparison Results (Post-Fix)

| Pair | Changes | MODIFIED | REMOVED | ADDED | Non-Compliance |
|---|---|---|---|---|---|
| TL-81000 p003 (2018→2021) | 66 | 16 | 21 | 29 | 0 |
| TL-81000 p013 (2018→2021) | 55 | 12 | 25 | 18 | 0 |
| DNVGL p013 (Nov2016→Dec2019) | 64 | 12 | 32 | 20 | 0 |
| DNVGL p023 (Nov2016→Dec2019) | 94 | 36 | 23 | 35 | 0 |
| DIN 60068-2 (incompatible) ❌ | 354 | 4 | 248 | 102 | 0 ✓ |


---

## 13. Iteration 3 Analysis — 2026-06-04 (Ingestion Fidelity & Table Accuracy)

### 13.1 Subscript/Superscript Preservation (Phase 1)

**Root cause fixed:** `unicodedata.normalize("NFKC")` in `utils/hashing.py` was silently converting `m²` → `m2`, `CO₂` → `CO2`, making `m²` == `m³` in all comparisons. Additionally, `_UNICODE_PUNCT_TRANSLATION` in `policy_semantics.py` was explicitly stripping superscript characters to empty string, so `10 V/m²` became `10 V/m`.

**Verification:**
```python
normalize_for_comparison("m²")   # → "m^2"   (was "m2")
normalize_for_comparison("m³")   # → "m^3"   (was "m2" — identical!)
normalize_for_comparison("CO₂")  # → "co_2"  (was "co2")
normalize_for_comparison("10 V/m²") # → "10v/m^2" (was "10v/m")
```

`m² ≠ m³` is now correctly detected — critical for EMC/safety thresholds where exponent differences change the physical unit entirely.

**LaTeX notation in cell comparison:** `$_{n}$` → `_n` (subscript) and `$^{2}$` → `^2` (superscript) are now preserved as distinct markers in `_norm_cell()`. Previously both were collapsed to just the inner character with the sub/super distinction lost.

### 13.2 Double-NFKC Eliminated (Phase 1)

`table_normalization.normalize_cell()` was applying NFKC directly, then calling `normalize_for_comparison()` which applied NFKC again. The double-application bypassed the subscript/superscript preservation added to `hashing.py`. Fixed by removing the explicit NFKC call from `normalize_cell()`.

### 13.3 Table Caption Detection Improved (Phase 2)

`_CAPTION_ROW_RE` extended from English-only `table|figure` to cover German/French: `tabelle|tab|bild|abbildung|abb|tableau`.

Added title-heuristic fallback: a single spanning row-0 cell with short (3–120 char), low numeric density text is treated as a caption regardless of language prefix. This handles:
- "Test Setup Overview" (unnumbered)  
- Custom headings without "Table N" prefix

| Stub headers in corpus | Before | After |
|---|---|---|
| Tables with `column_1` placeholder | 12 | 8 |

### 13.4 Content-Set Jaccard for Table Comparison (Phase 2)

Added a position-independent cell content comparison alongside the position-based Jaccard. When all cell values from one table exist in the other (set_jaccard ≥ 0.85) but position-score is low (e.g., due to a caption row shift), the scores are blended. This prevents tables that differ only in caption placement from being classified HIGH severity.

### 13.5 Fragment/Orphan Node Merging (Phase 3)

Added `_merge_orphan_fragments()` post-processing pass in `hierarchy_builder.py`. Clause/paragraph nodes that start with a lowercase letter (mid-sentence continuation) or are very short (< 15 chars) and share the same `section_path` as the preceding node are merged into it.

**Example fixed:**
- Before: `"nannten Erganzungen bzw. Anderungen."` (36 chars, starts lowercase) — standalone node, creates false REMOVED record
- After: merged with preceding clause → single coherent comparison unit

### 13.6 Reference Source Text Preservation (Phase 4)

`_reference_source_text()` now prefers `raw_text` (original unmodified document wording, preserving subscripts/superscripts) over `clean_text`/`normalized_text` for citation purposes. Auditors now see the original clause text in `doc1Reference.sourceText`, not the normalized comparison form.

**Measurement:** `empty_doc1Content` rate for table MODIFIED records: 100% → 0% in latest traces.

### 13.7 Compliance Explanation Populated

`complianceExplanation` field present in 100% of `KeyDifference` records across all 5 comparison pairs (354 DIN, 94 DNVGL p023, 64 DNVGL p013, 66 TL p003, 55 TL p013).

