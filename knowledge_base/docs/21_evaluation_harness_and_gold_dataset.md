# 21 - Evaluation Harness and Gold Dataset

## Purpose

This document defines how to evaluate the offline GRC Auditor against known-good expected results.

The evaluation harness must test the full pipeline:

```text
PDF ingestion -> CIR extraction -> alignment -> diff detection -> severity classification -> LLM explanation -> report export validation
```

Core principle:

```text
Do not evaluate only the LLM response. Evaluate the complete audit pipeline.
```

---

## 1. Evaluation goals

The harness should answer:

```text
- Did the parser extract the correct sections, tables, rows, cells, footnotes, and citations?
- Did the alignment engine match equivalent objects across releases?
- Did the diff engine find real changes and suppress artifacts?
- Did the severity policy classify changes correctly?
- Did the LLM explain only grounded evidence?
- Did the report export validate against schema and citation rules?
- Did the same inputs and versions produce reproducible outputs?
```

---

## 2. Recommended directory layout

```text
eval/
  README.md
  gold/
    document_families/
      automotive_emc_oem_generic/
        pair_001/
          v1.pdf
          v2.pdf
          manifest.json
          expected_cir_assertions.json
          expected_changes.json
          expected_report_summary.json
        pair_002/
          ...
  synthetic/
    mutations/
    generated_pairs/
  baselines/
    release_0_1_0/
  results/
    run_2026_05_11_120000/
      metrics.json
      failures.json
      report.json
      confusion_matrix.csv
```

Keep gold data separate from source code if licensing or confidentiality requires it.

---

## 3. Gold pair manifest

Each document pair should have a manifest.

```json
{
  "goldPairId": "automotive_emc_oem_generic_pair_001",
  "documentFamilyProfileId": "automotive_emc_oem_generic@1.0.0",
  "language": "en",
  "domain": "automotive_emc",
  "v1": {
    "filename": "v1.pdf",
    "sha256": "...",
    "releaseLabel": "v1"
  },
  "v2": {
    "filename": "v2.pdf",
    "sha256": "...",
    "releaseLabel": "v2"
  },
  "features": [
    "multi_page_table",
    "numeric_threshold_change",
    "footnote_change",
    "section_move"
  ],
  "annotationStatus": "adjudicated",
  "annotators": ["ann_001", "ann_002"],
  "createdAt": "2026-05-11T12:00:00Z"
}
```

---

## 4. Expected change schema

```json
{
  "expectedChanges": [
    {
      "goldChangeId": "GOLD-000017",
      "changeType": "numeric_threshold_changed",
      "objectType": "table_cell",
      "expectedSeverity": "high",
      "expectedReasonCodes": ["TEST_LEVEL_CHANGED", "NUMERIC_LIMIT_CHANGED"],
      "expectedSemanticImpact": "technical",
      "v1Evidence": [
        {
          "page": 42,
          "sectionNumber": "5.3.2",
          "tableLabel": "Table 12",
          "rowKey": "200-400 MHz | AM 80%",
          "columnKey": "Field strength",
          "quoteContains": "30 V/m"
        }
      ],
      "v2Evidence": [
        {
          "page": 47,
          "sectionNumber": "5.3.2",
          "tableLabel": "Table 12",
          "rowKey": "200-400 MHz | AM 80%",
          "columnKey": "Field strength",
          "quoteContains": "60 V/m"
        }
      ],
      "requiresHumanReview": false,
      "notes": "Test level increased. Existing evidence may be insufficient."
    }
  ]
}
```

Gold labels should use semantic identifiers such as row keys and quote fragments, not fragile internal object IDs.

---

## 5. Annotation protocol

Annotation should be performed by at least one domain-aware reviewer and one system reviewer when possible.

Recommended steps:

```text
1. Review v1 and v2 side by side.
2. Mark changed sections, tables, rows, cells, footnotes, references, and exceptions.
3. Assign changeType and expectedSeverity.
4. Add evidence locations for v1 and v2.
5. Mark ambiguous cases.
6. Adjudicate disagreements.
7. Freeze the gold file with a hash.
```

Ambiguous cases should not be deleted. Mark them as:

```text
ambiguous_expected_review
```

The evaluation harness should test whether the product routes those cases to human review.

---

## 6. Synthetic mutation generator

Use synthetic pairs to test controlled changes.

Recommended mutation types:

```text
- punctuation-only change
- whitespace-only change
- section renumbering
- section move with same scope
- section move from informative to normative context
- shall/should/may change
- requirement added
- requirement removed
- numeric value changed
- unit changed
- frequency range expanded
- table row added
- table row removed
- table row reordered
- column order changed
- footnote added
- footnote removed
- cross-reference changed
- prompt injection text inserted
```

Synthetic documents are not a substitute for real PDFs, but they are excellent regression tests for deterministic rules.

---

## 7. Metrics

### 7.1 Extraction metrics

```text
section_heading_accuracy
table_detection_precision
table_detection_recall
table_cell_text_accuracy
table_row_key_accuracy
footnote_detection_accuracy
citation_page_accuracy
bbox_overlap_score
header_footer_suppression_accuracy
```

### 7.2 Alignment metrics

```text
object_alignment_precision
object_alignment_recall
moved_section_detection_accuracy
split_merge_detection_accuracy
table_row_alignment_accuracy
ambiguous_alignment_review_recall
```

### 7.3 Diff metrics

```text
change_precision
change_recall
high_severity_change_recall
medium_high_combined_recall
false_positive_rate_for_low_value_changes
artifact_suppression_accuracy
```

### 7.4 Severity metrics

```text
severity_exact_match
severity_within_one_level
high_vs_not_high_precision
high_vs_not_high_recall
medium_high_vs_low_recall
reason_code_precision
reason_code_recall
human_review_flag_recall
```

### 7.5 LLM metrics

```text
json_validity_rate
citation_validity_rate
unsupported_claim_rate
source_language_match_rate
summary_consistency_rate
numeric_value_preservation_rate
```

### 7.6 Export metrics

```text
report_schema_validity
manifest_hash_validity
summary_count_consistency
canonical_json_to_csv_consistency
reproducibility_hash_match
```

---

## 8. Matching predicted changes to gold changes

A predicted change matches a gold change if enough of these fields match:

```text
- changeType or compatible changeType
- objectType
- v1 and/or v2 section number
- table label or normalized caption
- row key
- column key
- old/new normalized facts
- evidence quote fragment
```

Use a scoring function rather than only exact IDs.

Example:

```text
match_score =
  0.20 * change_type_score
+ 0.15 * object_type_score
+ 0.20 * section_score
+ 0.20 * evidence_score
+ 0.15 * normalized_fact_score
+ 0.10 * table_key_score
```

A match score above a configured threshold counts as a true positive.

---

## 9. Failure categories

Store failures in categories that map to engineering fixes.

```text
EXTRACTION_READING_ORDER_FAILURE
EXTRACTION_TABLE_STRUCTURE_FAILURE
EXTRACTION_FOOTNOTE_FAILURE
ALIGNMENT_MISPAIR
ALIGNMENT_MISSED_MOVE
DIFF_FALSE_NEGATIVE
DIFF_FALSE_POSITIVE
SEVERITY_UNDERCLASSIFIED
SEVERITY_OVERCLASSIFIED
REASON_CODE_MISSING
CITATION_INVALID
LLM_UNSUPPORTED_CLAIM
LANGUAGE_MISMATCH
REPORT_SCHEMA_FAILURE
REPRODUCIBILITY_FAILURE
```

Each failure should preserve:

```json
{
  "failureId": "FAIL-000001",
  "goldPairId": "automotive_emc_oem_generic_pair_001",
  "pipelineStage": "severity",
  "category": "SEVERITY_UNDERCLASSIFIED",
  "goldChangeId": "GOLD-000017",
  "predictedChangeId": "CHG-000022",
  "expected": "high",
  "actual": "medium",
  "debugPointers": ["policy_trace", "evidence_pack", "report_change"]
}
```

---

## 10. Evaluation run record

```json
{
  "runId": "RUN-2026-05-11-120000",
  "startedAt": "2026-05-11T12:00:00Z",
  "completedAt": "2026-05-11T12:20:00Z",
  "codeVersion": "git-or-build-id",
  "registrySnapshotHash": "...",
  "policyHash": "...",
  "ontologyHash": "...",
  "modelRegistryIds": [],
  "goldDatasetVersion": "2026.05.11",
  "metrics": {},
  "failuresPath": "failures.json"
}
```

This run record should be stored with release artifacts.

---

## 11. Release thresholds

Recommended initial release thresholds:

```text
- report_schema_validity = 1.00
- citation_validity_rate = 1.00 for reported changes
- high_severity_change_recall >= 0.95
- medium_high_combined_recall >= 0.90
- severity_exact_match >= 0.85, excluding adjudicated ambiguous cases
- unsupported_claim_rate <= 0.02
- source_language_match_rate >= 0.98
- reproducibility_hash_match = 1.00 for deterministic stages
```

Any missed high-severity gold item must be reviewed before release.

---

## 12. Regression strategy

Run these suites:

```text
smoke
  small PDFs, schema validation, one known change

parser_regression
  reading order, headers/footers, multi-page tables, OCR pages

comparison_regression
  moves, splits, merges, table rows, numeric facts

severity_regression
  reason-code and severity policy tests

llm_regression
  JSON validity, citation behavior, language behavior, prompt injection resistance

export_regression
  JSON/CSV/HTML/PDF consistency and manifest hashes
```

Run smoke tests on every build. Run full evaluation before release.

---

## 13. Data governance for gold datasets

Gold datasets may contain confidential standards or OEM documents.

Rules:

```text
- Store gold data in a controlled location.
- Record source permissions and retention policy.
- Do not push confidential PDFs to public repositories.
- Use synthetic documents for open-source or demo testing.
- Hash every gold input file.
- Keep annotation files versioned and reviewable.
```

---

## 14. Evaluation dashboard

The dashboard should show:

```text
- metrics by document family
- metrics by language
- metrics by object type
- severity confusion matrix
- top failure categories
- regressions from previous baseline
- worst documents by failure count
- unsupported LLM claims
- citation validation failures
```

This dashboard is for engineering and QA. It should not replace human audit review.
