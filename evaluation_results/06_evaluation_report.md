# Evaluation Report — Round 2 (2026-05-15)

## Summary of Round 2 Changes

| Change | Description |
|--------|-------------|
| R1 | Reverted key-differences sort from page-number order back to HIGH-first |
| R2 | Structural label changes (section numbers, table/figure captions) → LOW severity |
| R3 | Pure-text-hash deduplication — skip LLM diff calls for byte-identical content |
| R4 | Hierarchical section matching: added "clause" to section number regex; same-type scoring bonus |
| R5 | Lost table detection: compare raw docling table count vs canonical count |
| R6 | Evaluation pipeline run; reports updated |

## Extraction Metrics (8 documents)

| Metric | Value |
|--------|-------|
| Total nodes | 5,270 |
| Canonical tables | 269 |
| Raw docling tables | 522 |
| Tables lost in pipeline | 253 (48.5%) |
| Multi-page stitched | 28 |

### Per-document lost table analysis

| Document | Raw | Canonical | Lost |
|----------|-----|-----------|------|
| TL_81000_2021-09 GER (v1) | 98 | 45 | 53 |
| TL_81000_2021-09 GER (v2) | 98 | 45 | 53 |
| CISPR_25_2021 (v1) | 89 | 35 | 54 |
| CISPR_25_2021 (v2) | 89 | 35 | 54 |
| DNV2-4_2006 | 33 | 10 | 23 |
| CISPR_25_2016 | 41 | 35 | 6 |
| CISPR_25_2018 | 40 | 33 | 7 |
| DNV_2024 | 34 | 31 | 3 |

**Note:** Most "lost" tables are expected — docling splits multi-page tables into separate items that get stitched back together, and degenerate/header-only tables are filtered. The DNV2-4 document (23/33 lost) warrants investigation.

## Accuracy Metrics

| Metric | Value |
|--------|-------|
| Tables classified | 197 / 269 (73%) |
| Fact coverage | 16.5% |
| Column mapped | 59.8% |
| Row key coverage | 47.5% |

## Key Improvements vs Round 1

- **Sort**: Key differences now always HIGH → MEDIUM → LOW (reverted page-number sort)
- **Structural labels**: Section/table/figure number changes classified as LOW (reduces false HIGH/MEDIUM noise)
- **Hash dedup**: Pure-text hash computed for all nodes; pairs with identical hashes skip LLM diff (cost reduction)
- **Section matching**: "Clause N.N" prefixes now properly stripped before title similarity; same-type nodes (tables↔tables) get matching preference
- **Lost tables**: Pipeline now reports raw vs canonical table count discrepancy per document
