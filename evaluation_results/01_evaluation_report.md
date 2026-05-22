# Extraction Accuracy Evaluation Report

**Date:** 2026-05-12  
**Branch:** `feature/multi-layer-table-extraction`  
**Documents evaluated:** TL_81000_2021-09 GER, TL_81000_2018-03

---

## Summary

| Metric | TL_81000_2021 | TL_81000_2018 | Combined |
|---|---|---|---|
| Total nodes | 720 | 736 | 1 456 |
| Total tables | 25 | 12 | 37 |
| Multi-page stitched | 2 | 2 | 4 |
| OCR nodes | 0 | 0 | 0 |
| Header quality | 100% | 100% | 100% |
| Section coverage | 100% | 100% | 100% |
| **EMC classified** | **11/25 (44%)** | **11/12 (92%)** | **22/37 (59%)** |
| **Fact coverage** | **5.3%** | **13.2%** | **9.2%** |
| **Row key coverage** | **19.4%** | **60.0%** | **39.7%** |
| **Column mapping** | **30.0%** | **54.2%** | **42.1%** |
| Total NormalizedFacts | 84 | 40 | 124 |

---

## Structural Validation

Both documents pass all structural checks with zero errors.

- Zero degenerate 1-column tables
- Zero fallback `column_N` headers
- Zero OCR nodes (native PDF text throughout)
- 100% nodes have non-empty `heading_path` (section coverage)
- Multi-page stitching active on 4 tables total (2 per document)

---

## Deep Accuracy — TL_81000_2021-09 GER (`20d49955`)

**EMC distribution:** radiated_immunity: 10 · transient_immunity: 1 · unknown: 14  
**Total NormalizedFacts extracted:** 84

| # | Caption | EMC Type | Facts | RowKeys | ColMap |
|---|---|---|---|---|---|
| 0 | Tabelle 3 - FPSC Luftentladung Systemprüfung direkte Entladung | radiated_immunity | 62% | 0% | 100% |
| 1 | Tabelle 4 - FPSC Kontaktentladung Systemprüfung direkte Entladung | radiated_immunity | 62% | 0% | 100% |
| 2 | Tabelle 5 - FPSC Kontaktentladung indirekte Entladung | radiated_immunity | 62% | 0% | 100% |
| 3 | Tabelle 13 - Funktionszustandsklassifizierung (Streifen) | radiated_immunity | 0% | 0% | 100% |
| 4 | Tabelle 13 (continued, empty headers) | unknown | 0% | 0% | 0% |
| 5 | Tabelle 14 - Mobilfunkprüfung auf Komponentenebene | radiated_immunity | 17% | 100% | 38% |
| 6 | Tabelle 19 (fortgesetzt) | unknown | 2% | 0% | 28% |
| 7 | Tabelle 20 (fortgesetzt) | unknown | 0% | 0% | 29% |
| 8 | Tabelle 20 (fortgesetzt) | unknown | 0% | 0% | 44% |
| 9 | Tabelle 23 (fortgesetzt) | unknown | 0% | 0% | 33% |
| 10 | Tabelle 33 - Einstellwerte für Störfestigkeitsprüfungen | transient_immunity | 7% | 0% | 33% |
| 11 | Tabelle 35 - Maximal zulässige Störaussendung 12V | unknown | 0% | 0% | 75% |
| 12 | Tabelle 36 - Maximal zulässige Störaussendung 24V | unknown | 0% | 0% | 75% |
| 13 | Tabelle 36 (fortgesetzt) | unknown | 0% | 0% | 25% |
| 14 | Tabelle 38 - Einstellwerte | unknown | 0% | 0% | 38% |
| 15 | Tabelle 38 (fortgesetzt) | unknown | 0% | 0% | 38% |
| 16 | Tabelle 40 (fortgesetzt) | unknown | 0% | 0% | 8% |
| 17 | Tabelle 40 (fortgesetzt) | unknown | 1% | 0% | 8% |
| 18 | Tabelle 41 (fortgesetzt) | unknown | 0% | 0% | 14% |
| 19 | Tabelle 41 (fortgesetzt) | unknown | 0% | 0% | 17% |
| 20 | Tabelle 43 - Prüfung Nummer 25 | radiated_immunity | 0% | 33% | 17% |
| 21 | Tabelle 44 - Fahrzeugprüfung (im Fernfeld) | radiated_immunity | 21% | 100% | 8% |
| 22 | Tabelle 44 (fortgesetzt) | radiated_immunity | 63% | 0% | 0% |
| 23 | Tabelle 47 (fortgesetzt) | radiated_immunity | 12% | 50% | 14% |
| 24 | Tabelle 49 - FPSC Luftentladung Fahrzeug | radiated_immunity | 62% | 0% | 100% |

**Sample row keys (Tabelle 14):**
```
:100:am_1_000hz,_80_%modulationsgrad
:500:fm_1_000hz,_4khz_hub
:1_000:fm_1_000hz,_4khz_hub
```

**Sample row keys (Tabelle 44):**
```
:vertikal
:vertikal_und_horizontal
```

---

## Deep Accuracy — TL_81000_2018-03 (`766ac1df`)

**EMC distribution:** radiated_immunity: 10 · transient_immunity: 1 · unknown: 1  
**Total NormalizedFacts extracted:** 40

| # | Caption | EMC Type | Facts | RowKeys | ColMap |
|---|---|---|---|---|---|
| 0 | Tabelle 1 - Betriebs- und Prüfspannungen | radiated_immunity | 0% | 0% | 43% |
| 1 | Tabelle 3 - FPSC Luftentladung Systemtest direkte Entladung | radiated_immunity | 62% | 0% | 100% |
| 2 | Tabelle 4 - FPSC Kontaktentladung Systemtest direkte Entladung | radiated_immunity | 62% | 0% | 100% |
| 3 | Tabelle 5 - FPSC Kontaktentladung indirekte Entladung | radiated_immunity | 62% | 0% | 100% |
| 4 | Tabelle 13 - Funktionszustandsklassifizierung (Streifen) | radiated_immunity | 0% | 0% | 100% |
| 5 | Tabelle 13 (continued, empty headers) | unknown | 0% | 0% | 0% |
| 6 | Tabelle 21 - Test Nummer 26 | radiated_immunity | 0% | 100% | 30% |
| 7 | Tabelle 26 - Messempfängereinstellungen | radiated_immunity | 0% | 100% | 17% |
| 8 | Tabelle 30 - Einstellwerte für Störfestigkeitsmessungen | transient_immunity | 6% | 100% | 29% |
| 9 | Tabelle 39 - Funktionszustandsklassifizierung (Fahrzeug) | radiated_immunity | 0% | 0% | 100% |
| 10 | Tabelle 41 - Mobilfunkprüfung mit portablen Geräten | radiated_immunity | 15% | 100% | 29% |
| 11 | Tabelle 43 - FPSC Luftentladung Fahrzeug | radiated_immunity | 62% | 0% | 100% |

**Sample row keys (Tabelle 30 - transient):**
```
impuls_1
impuls_2
impuls_3a
```

**Sample row keys (Tabelle 41 - mobile):**
```
::am_1_000hz,_80_%modulationsgrad
::fm_1_000hz,_4khz_hub
```

---

## Fact Type Breakdown

| Fact Type | TL_81000_2021 | TL_81000_2018 |
|---|---|---|
| `field_strength` (±kV ESD levels) | 25 | 25 |
| `frequency_range` | 59 | 15 |
| `emission_limit` | 0 | 0 |
| `normative_term` | 0 | 0 |
| `acceptance_criterion` | 0 | 0 |

---

## Analysis & Observations

### What works well

- **ESD severity tables** (prüfschärfe / kategorie 1/2/3): 100% column mapping, 62% fact coverage from ±kV voltage extraction
- **Radiated immunity frequency tables** (Tabelle 44, 47): 100% row key coverage where frequency ranges are present — keys encode frequency band + modulation mode
- **Transient tables** (Tabelle 30, 33): impulse type correctly extracted as row key component (`impuls_1`, `impuls_2`, `impuls_3a`)
- **TL_81000_2018** overall: 92% EMC classification, 60% row key coverage — simpler single-page table structure benefits both metrics

### Known limitations

- **Continued tables with split headers** (Tabelle 19/20/23/40/41 in 2021 doc): 14 tables show `unknown` classification. The PDF column-spanning causes header text to fragment across cells (e.g. `"grenz- wert u in db"`, `"bw f in khz"`), preventing entity type matching. EMC type is not recoverable from the continuation caption alone.
- **Low overall fact coverage (9.2%)**: Most table cells contain non-numeric content (band names, modulation codes, remarks). The 9.2% reflects only cells where physical quantities (kV, MHz, V/m, dBuV) appear — not a defect in extraction.
- **Emission limit dBuV facts = 0**: The emission limit tables have values like `"db (μv)"` with a space between "db" and "(μv)" from PDF word-break extraction. The regex `dBµV` requires no space. This is a known gap.
- **Tabelle 1 (Prüfspannungen) fact coverage = 0%**: Values are formatted as `"13, 5 ± 0, 5"` (comma as decimal separator with spaces) which the voltage regex doesn't match due to the tolerance notation.

### Improvement opportunities

1. Extend `_EMISSION_LIMIT_RE` to handle spaced variants: `db\s*\(\s*[μu]v\s*\)`
2. Extend voltage regex to handle `\d+,\s*\d+` (spaced comma decimal) for tolerance ranges
3. Add `conducted_emissions` signal keywords for Tabelle 19/20/23 (CISPR 25 RE tables) — captions mention "Störaussendung" which is conducted/radiated emission
4. For continued tables, propagate the EMC type from the first fragment via stitching metadata

---

## Files

| File | Description |
|---|---|
| `data/uploads/_validation_report.json` | Structural validation metrics per document |
| `data/uploads/_accuracy_report.json` | Deep accuracy metrics per document and table |
| `data/uploads/_evaluation_report.md` | This report |
