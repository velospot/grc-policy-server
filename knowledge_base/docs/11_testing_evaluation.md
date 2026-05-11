# 11 - Testing and Evaluation

## Evaluation philosophy

The system must be tested like a compliance engine, not just a chatbot.

Primary quality metric:

```text
Did the system detect the correct source-grounded changes with correct citations?
```

## Test corpus layers

### Synthetic corpus

Create controlled PDFs with known changes:

- Simple paragraph change.
- Shall/should change.
- Added requirement.
- Removed requirement.
- Table row added.
- Table row removed.
- Numeric threshold changed.
- Frequency range expanded.
- Footnote added.
- Section moved.
- Multi-page table row continuation.

### Semi-real corpus

Use de-identified internal documents or generated look-alike standards.

### Real corpus

Use actual compliance documents under local access controls.

## Golden answer format

```json
{
  "comparison_id": "golden_sample_001",
  "expected_changes": [
    {
      "change_type": "numeric_threshold_increased",
      "left_locator": {"section": "5.3.2", "table": "Table 12", "row_key": "200-400 MHz"},
      "right_locator": {"section": "5.3.2", "table": "Table 12", "row_key": "200-400 MHz"},
      "old_value": "30 V/m",
      "new_value": "60 V/m"
    }
  ]
}
```

## Metrics

### Extraction metrics

```text
section_detection_precision
section_detection_recall
table_detection_precision
table_detection_recall
cell_extraction_accuracy
reading_order_accuracy
citation_bbox_iou
ocr_character_error_rate
```

### Alignment metrics

```text
section_alignment_accuracy
requirement_alignment_accuracy
table_row_alignment_accuracy
split_merge_detection_accuracy
ambiguous_alignment_rate
```

### Diff metrics

```text
change_detection_precision
change_detection_recall
numeric_change_accuracy
normative_change_accuracy
footnote_change_accuracy
false_added_removed_rate
```

### Explanation metrics

```text
valid_json_rate
citation_validity_rate
unsupported_claim_rate
language_match_rate
human_acceptance_rate
```

## Acceptance thresholds for MVP

For synthetic corpus:

```text
change_detection_recall >= 0.95
change_detection_precision >= 0.90
citation_validity_rate = 1.00
valid_json_rate >= 0.98
language_match_rate >= 0.98
```

For real corpus, thresholds depend on extraction quality and should be measured by document family.

## Unit tests

Test deterministic functions heavily:

- Unit conversion.
- Range parsing.
- Header normalization.
- Section number parsing.
- Normative term classification.
- Footnote scope assignment.
- Table stitching decision.
- Alignment scoring.
- Change classification.

## Integration tests

End-to-end tests:

```text
upload -> extract -> CIR -> index -> compare -> explain -> export
```

Each integration test should assert:

- No external network calls.
- Expected jobs complete.
- Expected changes exist.
- Every change has citations.
- PDF page highlight endpoint returns a valid image or annotation.

## Regression tests

Every manually found error becomes a regression test.

Bug example:

```text
Bug: German table header "Pruefpegel" not normalized to test_level.
Fix: Add German header alias.
Regression: tests/test_header_aliases_de.py
```

## LLM evaluation

The LLM prompt should be evaluated separately from extraction.

Create evidence-pack fixtures:

```text
tests/fixtures/evidence_packs/en_numeric_threshold.json
tests/fixtures/evidence_packs/de_obligation_strengthened.json
tests/fixtures/evidence_packs/fr_footnote_added.json
```

Expected checks:

- JSON parses.
- Required fields present.
- Language correct.
- Citations valid.
- Summary mentions only evidence-backed facts.

## Load testing

Simulate:

- 5 active users.
- 5 uploads.
- 3 comparisons queued.
- 1 export running.

Measure:

- UI responsiveness.
- Queue latency.
- Extraction duration.
- LLM duration.
- GPU memory.
- Database load.

## Offline testing

Run a network-deny test:

```text
Block external network at container or host firewall.
Run full test suite.
Fail if any component tries to reach public internet.
```

## Manual review UX tests

Validate:

- Clicking citation opens correct PDF page.
- Highlight box overlays the cited text/table row.
- User can mark change as accepted, rejected, or needs clarification.
- Review decision is audited.
- Export includes review state.
