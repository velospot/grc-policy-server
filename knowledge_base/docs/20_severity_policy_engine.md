# 20 - Severity Policy Engine

## Purpose

This document turns the severity guidance in `16_comparison_modes_severity_policy.md` into an implementable policy engine.

The severity policy engine is responsible for producing defensible values for:

```text
changeSeverity
severityReasonCodes
severityConfidence
requiresHumanReview
auditDisposition
```

Core principle:

```text
The LLM may explain severity. It must not be the only component assigning severity.
```

---

## 1. Engine inputs

The engine receives a normalized change candidate, not raw PDF text.

```json
{
  "changeId": "CHG-000017",
  "changeType": "table_cell_changed",
  "objectType": "table_cell",
  "mappingType": "one_to_one",
  "semanticImpactCandidate": "technical",
  "alignmentScore": 0.94,
  "extractionConfidence": 0.89,
  "language": "en",
  "sectionContext": {
    "v1": {"sectionPath": ["5", "5.3", "5.3.2"], "normativeStatus": "normative"},
    "v2": {"sectionPath": ["5", "5.3", "5.3.2"], "normativeStatus": "normative"}
  },
  "oldFacts": [],
  "newFacts": [],
  "diffFacts": [],
  "citations": {"v1": [], "v2": []}
}
```

Required input properties:

```text
- changeType
- objectType
- mappingType
- source language
- extraction confidence
- alignment score where applicable
- normalized facts when values, units, ranges, terms, or references are present
- citation availability
```

---

## 2. Engine outputs

```json
{
  "semanticImpact": "technical",
  "changeSeverity": "high",
  "severityScore": 0.92,
  "severityConfidence": 0.96,
  "severityReasonCodes": [
    "TEST_LEVEL_CHANGED",
    "NUMERIC_LIMIT_CHANGED",
    "EVIDENCE_MAY_BE_INVALIDATED"
  ],
  "requiresHumanReview": false,
  "auditDisposition": "reported",
  "policyTrace": []
}
```

The UI may hide `severityScore`, but it should be stored for debugging.

---

## 3. Rule order

Apply rules in this order:

```text
1. Artifact rejection rules
2. Citation sufficiency rules
3. Semantic impact rules
4. High-severity deterministic rules
5. Medium-severity ambiguity rules
6. Low-severity equivalence rules
7. Confidence and review rules
8. LLM explanation and validation rules
```

Do not allow a later low-severity rule to override an earlier deterministic high-severity rule unless an explicit exception rule is present and logged.

---

## 4. Artifact rejection rules

A candidate may be ignored as extraction artifact only if evidence supports that it is not a real document change.

Examples:

```text
- duplicated footer extracted in one version only
- OCR hallucination corrected by another extractor
- table border detected as text
- page number mistaken for section number
- repeated header inserted into body text
```

Output:

```json
{
  "auditDisposition": "ignored_as_artifact",
  "changeSeverity": null,
  "severityReasonCodes": ["EXTRACTION_ARTIFACT"],
  "requiresHumanReview": false
}
```

If artifact confidence is low, do not discard. Mark for human review.

---

## 5. Semantic impact rules

Recommended semanticImpact mapping:

| Condition | semanticImpact |
|---|---|
| whitespace, repeated header, decoration | none |
| punctuation or spelling with no meaning change | editorial |
| moved or renumbered with equivalent meaning | structural |
| wording changes possible interpretation | semantic |
| numeric value, unit, range, test setup, acceptance class | technical |
| shall/should/may/prohibited status changed | normative |
| applicability, exception, product family, annex/body scope changed | scope |

A change may have multiple candidate impacts. Store the highest-impact primary value and keep all triggered reason codes.

Impact priority:

```text
scope > normative > technical > semantic > structural > editorial > none
```

---

## 6. High-severity deterministic rules

Escalate to high when any of these conditions is met:

```text
- mandatory requirement added or removed
- obligation strengthened or weakened
- prohibition added, removed, or changed
- numeric compliance limit changed
- test level changed
- frequency range changed
- unit changed and normalized values are not equivalent
- acceptance criterion changed
- test method changed
- product applicability materially broadened or narrowed
- exception added or removed with compliance impact
- normative footnote added, removed, or changed
- required technical table row added or removed
- required parameter column added or removed
- existing evidence may be invalidated
```

Example rule object:

```yaml
rule_id: HIGH_NUMERIC_LIMIT_CHANGED
when:
  any_change_fact_type: [numeric_limit, test_level, frequency_range]
  section_normative_status: normative
then:
  severity: high
  semantic_impact: technical
  reason_codes:
    - NUMERIC_LIMIT_CHANGED
  min_score: 0.80
  requires_review_if_confidence_below: 0.85
```

---

## 7. Medium-severity ambiguity rules

Assign medium when the change may affect interpretation but deterministic evidence is not enough to call it high.

Examples:

```text
- ambiguous wording change
- cross-reference target changed but appears related
- footnote changed with unclear normative status
- requirement split or merged with possible meaning change
- moved section under different parent scope but text unchanged
- table header changed while values remain unchanged
- informative note changed in a way that may affect implementation
```

Medium severity should often require review when confidence is not high.

---

## 8. Low-severity equivalence rules

Assign low when all meaningful normalized facts remain equivalent.

Low conditions:

```text
- normalized technical facts equivalent
- normative level equivalent
- applicability equivalent
- section parent scope equivalent
- references resolve to equivalent targets
- citations map reliably
- only location, formatting, punctuation, order, or wording style changed
```

Examples:

```text
- section moved with same parent meaning and same normative status
- table row order changed, but row keys and values are equivalent
- column order changed, but headers, units, and values are equivalent
- spelling correction with no semantic effect
```

---

## 9. Reason code catalog

Use stable reason codes. Add new codes rather than changing old meanings.

### Low reason codes

```text
COSMETIC_ONLY
PUNCTUATION_ONLY
WHITESPACE_ONLY
LAYOUT_ONLY
MOVED_ONLY
RENUMBERED_ONLY
HEADER_FOOTER_ONLY
TABLE_ORDER_ONLY
EDITORIAL_REPHRASE
NO_OBLIGATION_CHANGE
NO_TECHNICAL_VALUE_CHANGE
REFERENCE_EQUIVALENT
```

### Medium reason codes

```text
POTENTIAL_INTERPRETATION_CHANGE
AMBIGUOUS_REQUIREMENT_CHANGE
LIMITED_SCOPE_CHANGE
FOOTNOTE_CHANGED
CROSS_REFERENCE_CHANGED
TEST_SETUP_CHANGED
REQUIREMENT_SPLIT
REQUIREMENT_MERGED
CLARIFICATION_WITH_POSSIBLE_IMPACT
MOVED_WITH_SCOPE_UNCERTAINTY
HUMAN_REVIEW_RECOMMENDED
```

### High reason codes

```text
MANDATORY_REQUIREMENT_ADDED
MANDATORY_REQUIREMENT_REMOVED
OBLIGATION_STRENGTHENED
OBLIGATION_WEAKENED
PROHIBITION_CHANGED
NUMERIC_LIMIT_CHANGED
TEST_LEVEL_CHANGED
FREQUENCY_RANGE_CHANGED
UNIT_CHANGED
ACCEPTANCE_CRITERION_CHANGED
TEST_METHOD_CHANGED
REFERENCE_STANDARD_CHANGED
SCOPE_BROADENED
SCOPE_NARROWED
EXCEPTION_ADDED
EXCEPTION_REMOVED
NORMATIVE_FOOTNOTE_CHANGED
TECHNICAL_TABLE_ROW_ADDED
TECHNICAL_TABLE_ROW_REMOVED
REQUIRED_PARAMETER_COLUMN_ADDED
REQUIRED_PARAMETER_COLUMN_REMOVED
EVIDENCE_MAY_BE_INVALIDATED
```

### System reason codes

```text
EXTRACTION_ARTIFACT
LOW_EXTRACTION_CONFIDENCE
LOW_ALIGNMENT_CONFIDENCE
MISSING_V1_CITATION
MISSING_V2_CITATION
MISSING_BBOX
POLICY_OVERRIDE_BY_REVIEWER
LLM_SEVERITY_DISAGREEMENT
```

---

## 10. Severity score calculation

Use score bands internally:

```text
0.00 - 0.29 -> low
0.30 - 0.69 -> medium
0.70 - 1.00 -> high
```

Recommended scoring approach:

```text
base score from strongest triggered rule
+ context modifiers
- equivalence modifiers
clamped to 0.00 - 1.00
```

Example base scores:

```text
mandatory requirement added/removed: 0.90
numeric limit changed: 0.85
test method changed: 0.80
scope broadened/narrowed: 0.78
normative footnote changed: 0.75
ambiguous wording change: 0.55
cross-reference changed: 0.45
move with possible scope change: 0.40
editorial rephrase: 0.15
punctuation-only: 0.05
whitespace-only: 0.00
```

Modifiers:

```text
+0.10 if section is explicitly normative
+0.10 if acceptance criterion is affected
+0.05 if product applicability is affected
-0.20 if normalized facts are equivalent
-0.10 if change is in informative note only and no requirement references it
```

Escalation rules override score bands.

---

## 11. Confidence calculation

Severity confidence should reflect evidence quality, not how dramatic the change seems.

Suggested formula:

```text
severityConfidence = min(
  extractionConfidence,
  alignmentConfidence,
  factExtractionConfidence,
  citationConfidence,
  policyRuleConfidence
)
```

Alternative implementations may use weighted averages, but must preserve the ability to explain low confidence.

Low-confidence triggers:

```text
- OCR used on cited object
- table reconstruction confidence below threshold
- alignment score below threshold
- missing bbox
- ambiguous split/merge
- conflicting parser outputs
- LLM disagrees with deterministic classification
```

---

## 12. Human review rules

Require human review when:

```text
- severity is high and severityConfidence < 0.85
- severity is medium and severityConfidence < 0.70
- alignment mapping is ambiguous
- requirement split or merge affects obligations
- footnote or exception changed and normative status is unclear
- citations do not have bbox for table cells
- parser confidence is below document-family threshold
- LLM explanation validation fails twice
- deterministic policy and LLM critique disagree materially
```

Human review may increase or decrease severity, but must create an override record.

---

## 13. Policy configuration

Store policy as versioned configuration.

Example:

```yaml
policy_id: automotive_emc_default
version: 1.0.0
severity_bands:
  low: [0.00, 0.29]
  medium: [0.30, 0.69]
  high: [0.70, 1.00]
review_thresholds:
  high_confidence_min: 0.85
  medium_confidence_min: 0.70
rules:
  - rule_id: HIGH_TEST_LEVEL_CHANGED
    enabled: true
    priority: 100
    when:
      diff_fact_type: test_level
      comparison: changed
    then:
      severity: high
      semantic_impact: technical
      reason_codes: [TEST_LEVEL_CHANGED, EVIDENCE_MAY_BE_INVALIDATED]
```

The policy file hash must be stored with every comparison.

---

## 14. Pseudocode

```python
def classify_change(candidate, policy):
    trace = []

    artifact = detect_artifact(candidate, policy)
    if artifact.confident:
        return decision(
            auditDisposition="ignored_as_artifact",
            reasonCodes=["EXTRACTION_ARTIFACT"],
            trace=trace + artifact.trace,
        )

    citation_issues = validate_citations(candidate)
    impact = classify_semantic_impact(candidate, policy)
    triggered = evaluate_rules(candidate, impact, policy)

    highest = choose_highest_priority(triggered)
    score = apply_modifiers(highest.score, candidate, policy)
    severity = score_to_band(score, policy)

    severity = apply_escalation_overrides(severity, triggered, policy)
    severity = apply_low_equivalence_guard(severity, candidate, policy)

    confidence = compute_confidence(candidate, triggered, citation_issues)
    requires_review = review_required(severity, confidence, candidate, citation_issues, policy)

    return decision(
        semanticImpact=impact.primary,
        changeSeverity=severity,
        severityScore=score,
        severityConfidence=confidence,
        severityReasonCodes=collect_reason_codes(triggered, citation_issues),
        requiresHumanReview=requires_review,
        auditDisposition="requires_review" if requires_review else "reported",
        policyTrace=trace + triggered.trace,
    )
```

---

## 15. LLM critique step

After deterministic classification, the LLM may receive:

```text
- normalized change facts
- severity candidate
- reason codes
- citations
- source-language evidence quotes
```

The LLM returns:

```json
{
  "explanation": "...",
  "auditImpact": "...",
  "severityCandidateSupported": true,
  "severityConcern": null,
  "requiresHumanReviewSuggestion": false
}
```

If the LLM claims the severity candidate is unsupported, do not automatically change severity. Instead:

```text
- validate the critique against evidence
- add LLM_SEVERITY_DISAGREEMENT reason code if valid
- require human review if material
```

---

## 16. Test cases

Minimum severity policy tests:

| Case | Expected severity | Reason code |
|---|---|---|
| should -> shall | high | OBLIGATION_STRENGTHENED |
| shall -> should | high | OBLIGATION_WEAKENED |
| 30 V/m -> 60 V/m | high | TEST_LEVEL_CHANGED |
| 150 kHz-30 MHz -> 150 kHz-108 MHz | high | FREQUENCY_RANGE_CHANGED |
| Class A -> Class B acceptance | high | ACCEPTANCE_CRITERION_CHANGED |
| Table row order changed only | low | TABLE_ORDER_ONLY |
| Section moved, same scope | low | MOVED_ONLY |
| Section moved from informative annex to normative body | high | OBLIGATION_STRENGTHENED |
| Cross-reference changed to related clause | medium | CROSS_REFERENCE_CHANGED |
| Footnote text changed ambiguously | medium | FOOTNOTE_CHANGED |
| OCR-only disagreement in numeric cell | medium or review | LOW_EXTRACTION_CONFIDENCE |
| Header/footer changed | low or artifact | HEADER_FOOTER_ONLY |

---

## 17. Debugging output

Every severity decision should be explainable to an engineer.

Recommended debug object:

```json
{
  "changeId": "CHG-000017",
  "policyId": "automotive_emc_default",
  "policyVersion": "1.0.0",
  "policyHash": "...",
  "triggeredRules": [
    {
      "ruleId": "HIGH_TEST_LEVEL_CHANGED",
      "priority": 100,
      "matchedFacts": ["fact-003"],
      "scoreContribution": 0.85
    }
  ],
  "modifiers": ["NORMATIVE_SECTION_PLUS_0_10"],
  "finalScore": 0.95,
  "finalSeverity": "high"
}
```

This debug object may be hidden from normal users but should be available in engineering logs and evaluation reports.
