# Accuracy Evaluation Report — Phase 2 (2026-05-22)

Changes applied: document family detection fix, is_header=False fallback, Safety/Env extractor routing,
family-aware routing priority, bare-numeric Env column inference, OCR normalization in column mapper,
~50 new HEADER_TO_ENTITY entries (TL 81000 automotive, DIN EN 60068 env, DNV maritime).

## Improvement vs Baseline

| Metric | Baseline | Phase 2 | Delta |
|--------|----------|---------|-------|
| Overall fact coverage | 13.0% | 16.8% | +3.8% |
| Row-key coverage | 41.4% | 41.1% | -0.3% |
| Column mapping | 56.1% | 63.1% | +7.0% |
| Document family detected | 0% | 100% | +100% |
| Total normalized facts | 2,709 | 2,892 | +183 |

## Per-Document Results

| Doc ID | Filename | Family | Tables | Classified | Fact Cov | Row Key | Col Map | Facts |
|--------|----------|--------|--------|------------|----------|---------|---------|-------|
| `07483639` | DIN EN 60068-2-38_2022.pdf | din_en_60068 | 9 | 7 (78%) | 47.5% | 34.5% | 32.5% | 197 |
| `0ad45810` | TL_81000_2018-03.pdf | tl_81000 | 38 | 24 (63%) | 5.4% | 28.2% | 52.2% | 186 |
| `178499a7` | TL_81000_2021-09 GER.pdf | tl_81000 | 43 | 28 (65%) | 17.6% | 62.2% | 60.6% | 399 |
| `1ce6b6e8` | TL_81000_2021-09 GER.pdf | tl_81000 | 45 | 35 (78%) | 18.3% | 57.7% | 54.6% | 427 |
| `4076f181` | DNVGL-CG-0339_Nov.2016.pdf | dnv_cg_0339 | 31 | 23 (74%) | 18.2% | 37.0% | 76.1% | 93 |
| `57918453` | DNVGL-CG-0339_Dez_2019.pdf | dnv_cg_0339 | 33 | 24 (73%) | 14.2% | 28.3% | 72.0% | 88 |
| `5a115978` | DNVGL-CG-0339_Dez_2019.pdf | dnv_cg_0339 | 33 | 24 (73%) | 14.2% | 28.3% | 72.0% | 88 |
| `5a33bef0` | DNV-CG-0339_2021-08.pdf | dnv_cg_0339 | 35 | 24 (69%) | 14.5% | 29.3% | 71.3% | 89 |
| `61cb4489` | DIN EN 60068-2-64_2020.pdf | din_en_60068 | 14 | 11 (79%) | 21.5% | 58.6% | 73.2% | 129 |
| `6e230f1f` | TL_81000_2018-03.pdf | tl_81000 | 38 | 24 (63%) | 5.4% | 28.2% | 52.2% | 186 |
| `7663cce0` | DNVGL-CG-0339_Nov.2016.pdf | dnv_cg_0339 | 27 | 18 (67%) | 15.8% | 43.5% | 70.4% | 73 |
| `79f81b22` | DNVGL-CG-0339_Dez_2019.pdf | dnv_cg_0339 | 32 | 23 (72%) | 12.6% | 30.9% | 63.5% | 75 |
| `8cad88c5` | TL_81000_2018-03.pdf | tl_81000 | 35 | 26 (74%) | 18.8% | 67.6% | 54.0% | 306 |
| `9b631823` | TL_81000_2021-09 GER.pdf | tl_81000 | 43 | 28 (65%) | 17.6% | 62.2% | 60.6% | 399 |
| `a1c73fbb` | DNVGL-CG-0339_Nov.2016.pdf | dnv_cg_0339 | 31 | 22 (71%) | 15.1% | 34.0% | 73.9% | 82 |
| `a69a77e7` | DNVGL-CG-0339_Dez_2019.pdf | dnv_cg_0339 | 34 | 25 (74%) | 12.3% | 27.8% | 70.5% | 75 |

## Remaining Gaps

- **TL 81000 2018 (5.4%)**: Heavy OCR corruption (`0` for `ö`, `ln` for `in`). Many headers like `"pr0f- schrfe"` fail partial matching. Re-uploading with current OCR pipeline would help; or add more targeted OCR repair rules.
- **Row-key coverage 41%**: Generic patterns only work for formal IDs. Environment test rows use step numbers (1,2,3) or condition labels without prefix patterns. Adding a step-number extractor would help.
- **Column mapping 63% → 75%+**: Remaining unmapped headers are OCR-corrupted abbreviated forms that require per-document-family header pre-processing.
- **Safety ontology coverage**: Safety tables are not present in current uploads. Coverage will show when IEC 62368 / ISO 26262 documents are uploaded.

## Snapshot
Written to `data/uploads/_accuracy_snapshots/20260522T194327Z.json`
Use `GET /accuracy/drift?document_id=X` to compare against next run.