# GRC Policy Server — Accuracy Benchmark Evaluation

**Last updated:** 2026-06-23  
**Branch:** `feat/hybrid-comparison`  
**Engines evaluated:** `OfflineDiffEngine` (iterations 1–2), `RealDiffEngine` + Qdrant + Neo4j (iteration 3)

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

**Iteration 3 additions (2026-06-22, branch `feat/hybrid-comparison`):**

| Document ID (short) | Filename | Family | Pages | Hierarchy Nodes | New Types |
|---|---|---|---|---|---|
| `5843c9b9` | TL_81000_2018-03_p063-072.pdf | TL-81000 | p63–72 | 88 | table_caption:3, list_item:9 |
| `bc366598` | TL_81000_2021-09_GER_p063-072.pdf | TL-81000 | p63–72 | 74 | table_caption:8, list_item:7 |
| `5e89ae01` | DNVGL-CG-0339_Dez_2019_p023-032.pdf | DNVGL-CG-0339 | p23–32 | 198 | table_caption:2, list_item:2 |
| `176bdcd9` | DNVGL-CG-0339_Nov_2016_p023-032.pdf | DNVGL-CG-0339 | p23–32 | 176 | table_caption:3, list_item:2 |

> New node types (`table_caption`, `list_item`) are now produced by the ingestion pipeline and flow through comparison. These were previously collapsed into `clause` nodes.

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
| 6 | TL-81000 2018-03→2021-09 p063-072 *(Iter 3)* | Safety/DE | 50 | 47 | 13 | 37 | 34 | **15.5%** |

> **Note on pair #5:** DIN EN 60068-2-64 (Wideband Random Vibration) and DIN EN 60068-2-38 (Temperature Humidity Cycling) are **different test methods**, not different versions of the same standard. The 2.9% match rate correctly reflects near-zero content overlap. This is a user workflow error; the system has no guardrail to warn about incompatible document pairs.

> **Note on pair #6 (15.5%, pre-fix):** The match rate was lower than p013-022 (35.8%) because `document_family_from_filename()` encoded the year in the family slug (`tl-81000-2018-03-p063` vs `tl-81000-2021-09-ger-p063`), disabling stable_id and Qdrant cross-version matching. All 13 matches were via `section_alignment` only. **Fixed in `feat/hybrid-comparison`** — content-first detection now produces canonical `tl_81000` for both editions. After re-ingestion, estimated match rate ~50–60%. See §13.2.

**Average match rate (in-family, same-version pairs 1–4): 42.2%**  
**Iteration 3 pair (6): 15.5%** — degraded by document family mismatch bug

---

## 3. Change Record Statistics

| # | Pair | REMOVED | ADDED | MODIFIED | Total | High Sev | Med Sev | Low Sev | Numeric Δ | Table Δ |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | TL-81000 p013 | 25 | 18 | 11 | **54** | 40 | 14 | 0 | 74 | 6 |
| 2 | TL-81000 p003 | 21 | 29 | 15 | **65** | 47 | 17 | 1 | 74 | 1 |
| 3 | DNVGL p013 | 26 | 26 | 5 | **57** | 42 | 12 | 3 | 49 | 5 |
| 4 | DNVGL p023 | 16 | 25 | 18 | **59** | 36 | 16 | 7 | 68 | 12 |
| 5 ❌ | DIN 60068 ❌ | 279 | 134 | 8 | **421** | 393 | 26 | 2 | 885 | 22 |
| 6 | TL-81000 p063 *(Iter 3)* | 27 | 17 | 14 | **58** | 45 | 9 | 4 | 174 | 5 |

**Observations:**
- In pairs 1–4, REMOVED and ADDED rates are high relative to MODIFIED, indicating the matcher is creating many false REMOVED+ADDED pairs instead of recognising them as MODIFIED. This inflates high-severity counts because unmatched clauses default to HIGH severity.
- Pair 6 shows 174 numeric changes (highest per-change-record density of any pair) — reflecting the test parameter-heavy content of TL-81000 p063-072 (EMC test setups). New `list_item` and `table_caption` nodes appear in the diff for the first time.
- **New node types in pair 6 diff:** `list_item` (6 records: 2 MODIFIED, 2 REMOVED, 2 ADDED), `table_caption` (2 records: 1 MODIFIED, 1 ADDED). These were previously collapsed into `paragraph`/`clause` nodes.

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

#### 6.2.5 Document Family Encodes Version Year — Stable ID Matching Fails Across Versions (Critical) ✅ Fixed

**Root cause:** `document_family_from_filename()` in `hierarchy_builder.py` derived the family slug from the full filename stem. For `TL_81000_2018-03_p063-072.pdf` this produced `tl-81000-2018-03-p063`; for `TL_81000_2021-09_GER_p063-072.pdf` it produced `tl-81000-2021-09-ger-p063`. Filename convention variability (month abbreviations `_Nov_2016`, locale codes `_GER`, page ranges `_p063`) made stripping fragile.

**Impact:** Stable IDs for clauses are hashed as `{chunk_type}::{doc_family}::…`. Since the two editions had different family slugs, no stable_id match was possible across editions. All cross-version matching fell back to `section_alignment` only. Confirmed by pair #6: 0 stable_id matches, 0 Qdrant vector matches, 13 section_alignment matches only.

**Fix implemented (`feat/hybrid-comparison`):** Three-stage content-first family detection in `hierarchy_builder.py`:
1. **Document body scan** — regex patterns for known standard identifiers ("TL 81000", "DNVGL-CG-0339", "IEC 60068", etc.) are searched in chunk titles and early body text. Returns the canonical `family_id` from the profile registry (`tl_81000`, `dnv_cg_0339`, `din_en_60068`).
2. **Filename pattern matching** — same standard ID patterns applied to the filename stem. Handles page-range extracts (p063-072) that don't include the cover page.
3. **Legacy strip-based fallback** — only for documents not matching any known standard.

**Outcome after re-ingestion:**
- Both TL 81000 editions → `tl_81000` (identical family → stable_id matching enabled)
- Both DNVGL editions → `dnv_cg_0339` (identical family → Qdrant vector search enabled)
- Expected match rate for pair #6: ~50–60% (up from 15.5%)

#### 6.2.6 `clause` Node Type Mapped to `paragraph` in Comparison Records (Medium)

**Root cause:** `_canonical_node_type()` in `canonical_models.py` remaps `clause` → `paragraph` when building comparison records from hierarchy nodes. This causes all clause-type content to appear as `paragraph` in change records, losing the node type signal.

**Impact:** The severity classifier, evidence pack, and UI display all use `node_type`. Clauses classified as `paragraph` cannot be distinguished from generic paragraphs, preventing clause-specific routing (e.g., normative clause ADDED → HIGH should be reliable, but requires knowing it is a `clause`, not generic `paragraph`).

**Fix:** Preserve `clause` as a comparison node type. Update `TEXT_COMPARISON_NODE_TYPES` and `COMPARISON_NODE_TYPES` to include `"clause"`, or reverse the mapping in `_canonical_node_type()`.

#### 6.2.7 Moved Sections Classified HIGH Despite Structural Move (Low)

**Observation:** DNVGL match `14.4.3 Test results` → `8.3.7 Test result` is classified MODIFIED with distance=0.823 and severity=HIGH. The content change ("In accordance with performance criterion A." → detailed pass criteria) is genuine, but the "moved" aspect (cross-chapter reassignment) is driving severity escalation.

**Fix:** For `alignment_type="moved"` with cross-chapter section paths, cap severity at MEDIUM unless content distance alone (excluding section-path change) exceeds threshold.

---

## 7. Benchmark Summary

| Metric | Iter 1–2 | Iter 3 | Target |
|---|---|---|---|
| Avg match rate (in-family pairs 1–4) | **42.2%** | N/A (new pair only) | ≥ 65% |
| New pair match rate (TL-81000 p063) | — | **15.5%** | ≥ 50% (after family fix) |
| Stable_id cross-version matches | Unknown | **0** (family mismatch) | > 40% of MODIFIED |
| Qdrant vector matches | Unknown | **0** (family mismatch) | > 20% of section_alignment |
| False REMOVED+ADDED from renumbering | ~40 per DNVGL pair | TBD | < 5 |
| Wrong-pair detection | None → ✓ | ✓ active | Warn when Jaccard < 15% |
| Section hierarchy depth | 1 (flat) | 1 (unchanged) | 2–4 (inferred from numbers) |
| `list_item` nodes in diff | 0 (collapsed to clause) | **6 per pair** | Per-item granularity |
| `table_caption` nodes in diff | 0 (invisible) | **2 per pair** | All caption changes surfaced |
| Numeric changes detected per comparison | 49–885 | **174** (p063 pair) | Accurate for EMC content |
| `clause` → `paragraph` type loss | — | **100% of clauses** | 0% (preserve clause type) |

---

## 8. Recommended Improvements (Priority Order)

| Priority | Area | Change | Expected Impact |
|---|---|---|---|
| ~~**P0 (Critical)**~~ ✅ | Ingestion | Content-first family detection in `hierarchy_builder.py` (`_detect_family_from_chunks` + `_detect_family_from_filename_patterns`) | Implemented — canonical family IDs (`tl_81000`, `dnv_cg_0339`) produced after re-ingestion; estimated +30–40% match rate |
| P0 (Critical) | Ingestion | Parse numeric section numbers into ancestor hierarchy | Enables hierarchical matching; fixes all flat-hierarchy issues |
| P0 (Critical) | Comparison | Chapter-level pre-matching in `ClauseMatcher` | Match rate +15–25% for version-pair docs |
| **P1 (High)** | Ingestion | Preserve `clause` node type through to comparison records | Eliminates clause→paragraph type loss; restores node-type-specific severity rules |
| P1 (High) | Comparison | Section renaming detection (`section_renamed` alignment type) | Eliminates ~40 false HIGH alerts per DNVGL comparison |
| P1 (High) | Comparison | Wrong-pair pre-flight check | ✅ Implemented — Jaccard < 15% triggers warning |
| P2 (Medium) | Comparison | Fix `_display_content` fallback chain | Populates empty `doc1Content`/`doc2Content` in change records |
| P2 (Medium) | Ingestion | OCR fusion repair regex | ✅ Implemented — `_SEC_FUSION_RE` in `hierarchy_builder.py` |
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
| 2026-06-23T20:53 | a70ef9c | 100.0% | ? | ? | 0 | 0 | 0 | 0 | 1 | 5 |
| 2026-06-23T20:54 | a70ef9c | 22.8% | ? | ? | 0 | 13 | 0 | 0 | 1 | 5 |

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

## 13. Iteration 3 Analysis — 2026-06-23 (feat/hybrid-comparison)

**Engine:** `RealDiffEngine` with Qdrant vector store + Neo4j knowledge graph  
**Branch:** `feat/hybrid-comparison`  
**Comparison pair:** TL-81000 p063-072 (2018-03 → 2021-09 GER)  
**Trace:** `5843c9b9__bc366598__20260622T212227997878Z.json`

### 13.1 Hybrid Architecture Assessment (Qdrant + Neo4j)

| Component | Role | Contribution in Iter 3 |
|---|---|---|
| **Qdrant** | Semantic vector search for cross-document clause matching | **0 matches** — disabled by document family mismatch; vectors indexed per-family |
| **Neo4j** | Citation lookup for `doc1Reference` / `v1Evidence` page+section | **Active** — page numbers and section paths correctly populated in all 58 change records |
| **RealDiffEngine** | Canonical node comparison, severity classification | Active — all new severity rules (FormulaNumericsRule, ListItemAddedRemovedRule, TableCaptionRule) exercised |
| **ClauseMatcher** | Section alignment + stable_id + vector fallback | **Section alignment only** — 13/13 matches via section titles; stable_id: 0; Qdrant: 0 |

**Root cause of Qdrant/stable_id failure:** `document_family_from_filename()` produces `tl-81000-2018-03-p063` for the 2018 edition and `tl-81000-2021-09-ger-p063` for the 2021 edition. Qdrant indexes vectors per document family, and stable_id hashes include the family. Cross-edition matching requires the same base family. This is a single-line fix in `hierarchy_builder.py`.

**Neo4j graph integration:** Citations (`doc1Reference.page`, `v1Evidence[*].page`) are correctly populated from canonical node provenance. Page numbers match Docling's `prov.page_no` (1-indexed within the uploaded PDF). Neo4j KG entity-graph diff (`_diff_entity_graphs`) is active for table nodes; entity changes feed back into severity classification via `EntityGraphChangeRule`.

### 13.2 Document Family Detection — Fix Implemented ✅

**Problem:** `document_family_from_filename()` produced edition-specific slugs:
- `TL_81000_2018-03_p063-072.pdf` → `tl-81000-2018-03-p063`
- `TL_81000_2021-09_GER_p063-072.pdf` → `tl-81000-2021-09-ger-p063`  
- `DNVGL-CG-0339_Nov_2016_p023-032.pdf` → `dnvgl-0339-nov-2016-p023` (CG stripped!)
- `DNVGL-CG-0339_Dez_2019_p023-032.pdf` → `dnvgl-0339-dez-2019-p023` (month-abbrev not stripped)

The regex-stripping approach was fragile: month abbreviations (`_Nov_`, `_Dez_`), locale codes (`_GER_`), and page ranges (`_p063`) each required separate rules, and the CG identifier in `DNVGL-CG-0339` was incorrectly matched by the locale pattern.

**Fix applied:** Three-stage content-first detection in `build_document_hierarchy()`:

```python
doc_family = (
    _detect_family_from_chunks(filtered_chunks)       # body text / headings
    or _detect_family_from_filename_patterns(filename) # filename standard ID patterns
    or document_family_from_filename(filename)         # legacy fallback
)
```

`_CONTENT_FAMILY_PATTERNS` contains regex patterns for `TL 81000`, `DNVGL-CG-0339`, `IEC 60068`, `IEC 61000`, `CISPR`, `ISO 11452`, `ISO 7637`. The patterns are checked against chunk titles first (most reliable), then early body text.

**Verified outcomes for corpus documents:**

| Filename | Content match | Filename pattern | Final family |
|---|---|---|---|
| `TL_81000_2018-03_p063-072.pdf` | `tl_81000` (body: "tl 81000" in OCR) | `tl_81000` | **`tl_81000`** |
| `TL_81000_2021-09_GER_p063-072.pdf` | `tl_81000` | `tl_81000` | **`tl_81000`** |
| `DNVGL-CG-0339_Nov_2016_p023-032.pdf` | None | `dnv_cg_0339` | **`dnv_cg_0339`** |
| `DNVGL-CG-0339_Dez_2019_p023-032.pdf` | None | `dnv_cg_0339` | **`dnv_cg_0339`** |

Both editions of each standard now produce the same canonical `family_id`. After re-ingestion, stable_id cross-version matching and Qdrant vector search will be enabled for all four documents.

### 13.3 Ingestion Accuracy — New Node Types (2026-06-23)

Node type distribution across 4 newly ingested documents:

| Document | Total Nodes | section | clause | table | table_caption | list_item |
|---|---|---|---|---|---|---|
| TL_81000_2018-03_p063-072 | 88 | 33 | 39 | 4 | 3 | 9 |
| TL_81000_2021-09_GER_p063-072 | 74 | 24 | 31 | 3 | 8 | 7 |
| DNVGL-CG-0339_Nov_2016_p023-032 | 176 | 74 | 86 | 11 | 3 | 2 |
| DNVGL-CG-0339_Dez_2019_p023-032 | 198 | 87 | 100 | 7 | 2 | 2 |

**Ingestion improvements active (feat/hybrid-comparison):**
- `table_caption` nodes produced from standalone Docling `CAPTION` items — previously invisible
- `list_item` nodes produced from `DocItemLabel.LIST_ITEM` — previously merged into single clause
- `merge_list_items=False` preserves individual bullet items at ingestion time
- `do_cell_matching=True` in Docling adapter — merged/spanning cells properly extracted
- `classify_columns()` stores column roles (`limit`, `measured`, `margin`, `result`, `frequency`) in table metadata
- Clause number encoded in `stable_id` — renumbered-but-stable clauses match across editions

**Known issue — `clause` → `paragraph` type loss:**  
`_canonical_node_type()` in `canonical_models.py` remaps `clause` to `paragraph` when loading from hierarchy records. All 39 clause nodes in TL-81000 p063-072 (2018) appear as `paragraph` in the comparison. This means `ListItemAddedRemovedRule` and `TableCaptionRule` work correctly (those types survive), but clause-specific severity routing is degraded. The diff for pair 6 shows `paragraph` (45 records) instead of `clause`.

### 13.4 Comparison Quality — Pair 6 (TL-81000 p063-072)

| Metric | Value | Comment |
|---|---|---|
| Total comparison nodes (L/R) | 50 / 47 | After section exclusion; section type not in COMPARISON_NODE_TYPES |
| Matched | 13 (15.5%) | All via section_alignment; 0 stable_id, 0 Qdrant |
| Unmatched left | 37 | False REMOVED — caused by family mismatch |
| Unmatched right | 34 | False ADDED — caused by family mismatch |
| MODIFIED | 14 (24.1%) | Genuine content changes in matched pairs |
| REMOVED | 27 (46.6%) | Mostly false — should be MODIFIED after family fix |
| ADDED | 17 (29.3%) | Mostly false — should be MODIFIED after family fix |
| High severity | 45 (77.6%) | Inflated by false REMOVED/ADDED defaulting to HIGH |
| Medium severity | 9 (15.5%) | |
| Low severity | 4 (6.9%) | |
| Numeric changes total | 174 | Highest per-record density — EMC test parameters in p063-072 |
| Table changes | 5 | Column-role aware: limit/measured/margin columns tagged |
| `list_item` in diff | 6 records | New — previously hidden in clause/paragraph aggregates |
| `table_caption` in diff | 2 records | New — captions were previously invisible |
| Citation coverage | 100% | `doc1Reference.page` and `v1Evidence[*].page` populated via Neo4j |

**Estimated post-fix match rate** (after family slug fix): ~50–60%, based on section alignment contributing 13 matches and stable_id/Qdrant adding ~25–35 additional matches for the content-stable renumbered clauses.

### 13.5 Severity Rule Improvements (Implemented in Iteration 3)

New rules active in `_DEFAULT_ENGINE` as of `feat/hybrid-comparison`:

| Rule | Condition | Severity | Status |
|---|---|---|---|
| `ListItemAddedRemovedRule` | `list_item` ADDED/REMOVED, normative + shall/must | HIGH | ✅ Active — confirmed list_item records classified correctly |
| `ListItemAddedRemovedRule` | `list_item` ADDED/REMOVED, no modal verb | MEDIUM + human_review | ✅ Active |
| `TableCaptionRule` | `table_caption` any change type | MEDIUM + human_review | ✅ Active — 2 caption records in pair 6 |
| `FormulaNumericsRule` | `formula` MODIFIED + numeric_changes non-empty | HIGH + human_review | ✅ Active — fires when LaTeX numeric extraction detects value changes |

Unit normalization in table comparison:
- `cell_values_equivalent()` active in both `RealDiffEngine` and `RealDiffEngineStream`
- PASS→FAIL transitions tagged `[result_column:CRITICAL]`
- Margin ≤ 0 tagged `[margin_column:CRITICAL]`; drop > 3 dB tagged `[margin_column:HIGH]`

### 13.6 Open Issues After Iteration 3

| Issue | Priority | File | Impact |
|---|---|---|---|
| ~~`document_family_from_filename()` encodes year~~ | ~~**P0**~~ **✅ Fixed** | `hierarchy_builder.py` | Content-first detection → canonical family IDs; re-ingest to activate |
| `clause` → `paragraph` type loss in `_canonical_node_type()` | **P1** | `canonical_models.py` | Clause-specific severity routing degraded; node type info lost in diff output |
| Section hierarchy depth = 1 for all documents | P0 | `hierarchy_builder.py` | ClauseMatcher cannot exploit nested chapter structure |
| No `heading` nodes in new corpus (despite NodeType expansion) | P2 | `docling_chunker.py` | Heading renames still invisible in diff |
| `formula` nodes not yet produced (no FORMULA label in test docs) | P2 | `docling_chunker.py` | `FormulaNumericsRule` correct but untested on real formula nodes |

**To activate the document family fix:** Re-ingest the four documents (`TL_81000_2018-03_p063-072.pdf`, `TL_81000_2021-09_GER_p063-072.pdf`, `DNVGL-CG-0339_Nov_2016_p023-032.pdf`, `DNVGL-CG-0339_Dez_2019_p023-032.pdf`) and re-run the comparison. Existing stored hierarchy.json files retain the old family slugs until re-ingested.

---

## 14. Iteration 4 Analysis — 2026-06-23 (feat/hybrid-comparison)

### 14.1 Coverage and Data Availability

Only the TL-81000 p063-072 pair (2018→2021) is available in `data/uploads/` with confirmed correct family IDs (`tl_81000` on both sides). All five standard benchmark pairs reference document IDs that are no longer present in uploads and produce "SKIP - docs not uploaded" without traceback.

**Comparison pair in scope:**

| Pair | doc1 | doc2 | Family | Engine |
|---|---|---|---|---|
| TL-81000 p063-072 (2018→2021) | `05738b4a` | `b9dea506` | `tl_81000` | OfflineDiffEngine |

### 14.2 Match Rate Results (Iteration 4)

| Metric | Value |
|---|---|
| Total nodes (left + right) | 57 |
| Matched | 13 |
| Unmatched (REMOVED + ADDED) | 44 |
| **Match rate** | **22.8%** |
| stable_id matches | 0 |
| section_alignment matches | 13 |
| vector_search (Qdrant) matches | 0 |
| neo4j_graph matches | 0 |

All 13 matches came from section_alignment. Hybrid components (Qdrant vector search, Neo4j graph) contributed zero.

### 14.3 Why Hybrid Contributes Zero

Two independent reasons prevent hybrid signal contribution:

**1. Engine choice — OfflineDiffEngine has no external service calls.**
The benchmark runs with `scripts/run_comparisons.py` which defaults to `--engine offline`. `OfflineDiffEngine` bypasses all I/O: no Qdrant semantic search, no Neo4j graph queries. To measure hybrid uplift, the benchmark must be run with `--engine real` while Qdrant is serving.

**2. Section renumbering limits stable_id coverage.**
The stable_id is derived from `{chunk_type}::{doc_family}::{numeric_prefix}::{title_slug}`. Between the 2018 and 2021 editions of TL-81000, sections were renumbered (e.g., `5.4.2.5 Prüfimpuls 6` → `5.4.2.4 Prüfimpuls 6`). A renumbered section has a different `numeric_prefix`, so stable_ids differ even when `doc_family` matches. Section renumbering is exactly the problem Qdrant semantic search is designed to solve: it finds semantically equivalent clauses across renumbered boundaries.

### 14.4 Path to Measuring Hybrid Uplift

To get a real measure of Qdrant's contribution:

```bash
# 1. Start Qdrant (port 6333)
docker-compose up qdrant -d

# 2. Re-ingest the TL-81000 p063-072 pair so vectors are indexed in Qdrant
#    (use the /api/v1/documents/upload endpoint or scripts/ingest_docs.py)

# 3. Run comparison with RealDiffEngine
QDRANT_URL=http://localhost:6333 \
  uv run python scripts/run_comparisons.py --engine real

# 4. Compute metrics from the new trace
uv run python scripts/benchmark_metrics.py
```

The resulting trace will contain `matchTypes.vector_search` counts showing Qdrant's contribution. The expected outcome is that sections with renumbered numeric prefixes but matching semantic content are bridged by Qdrant, increasing the match rate above 22.8%.

### 14.5 ClauseMatcher Strategy Test Coverage (Added in Iteration 4)

Three unit tests were added to `tests/test_ingestion_pipeline.py` to pin the strategy routing logic:

| Test | What it verifies |
|---|---|
| `test_clause_matcher_calls_search_fn_for_unmatched_nodes` | `search_fn` is invoked for each unmatched left node in a mapped section (2 calls for 2 unmatched nodes) |
| `test_clause_matcher_stable_id_priority_in_matched_section` | When L1 has `stable_id="X"` and both R1 (stable_id="X") and R2 (identical text) exist, L1 matches R1 via `stable_id`, not R2 via lexical score |
| `test_compare_v5_service_decorates_done_event_with_hybrid_signals` | `done` SSE event contains `hybridSignals.matchingStrategy = "canonical+qdrant+neo4j_graph_fallback"`; non-done events do not |

All three tests pass. The strategy priority order `stable_id → section_alignment → vector_search (Qdrant) → local_fallback` is verified end-to-end.

### 14.6 Summary

| Item | Status |
|---|---|
| Document family detection (content-first) | ✅ Fixed and verified |
| benchmark_metrics.py dual-format support | ✅ Fixed (OfflineDiffEngine + RealDiffEngine traces) |
| `run_comparisons.py --engine real` flag | ✅ Implemented |
| ClauseMatcher strategy unit tests | ✅ 3 tests added and passing |
| Hybrid uplift measured end-to-end | ⏳ Requires Qdrant running + re-ingestion |
| All 5 standard benchmark pairs available | ⏳ Requires re-ingestion of source PDFs |

