# 23 - Document Family Profiles

## Purpose

Document family profiles let the auditor adapt extraction, normalization, alignment, and severity behavior to different document types without hard-coding assumptions in the engine.

Automotive EMC documents are not uniform. OEM specifications, international standards, internal test plans, supplier evidence packages, and homologation regulations use different section patterns, table styles, terminology, and evidence expectations.

Core principle:

```text
The engine should be generic. The document family profile should contain document-specific parsing and comparison knowledge.
```

---

## 1. What a profile controls

A profile may define:

```text
- document family name and scope
- supported languages
- expected section numbering patterns
- heading detection rules
- appendix and annex handling
- normative vs informative section rules
- repeated header/footer patterns
- table caption patterns
- table continuation rules
- key table schemas
- row-key construction rules
- unit normalization preferences
- normative term dictionaries
- ontology mapping
- alignment weights
- severity overrides
- review thresholds
- export labels
```

Profiles should be versioned and registered in the offline dependency registry.

---

## 2. Profile schema

```yaml
profile_id: automotive_emc_oem_generic
version: 1.0.0
domain: automotive_emc
description: Generic OEM automotive EMC requirements document profile.
supported_languages: [en, de, fr]
ontology:
  ontology_id: automotive_emc
  version: 1.0.0
section_patterns:
  numbered_heading_regex:
    - '^\\d+(\\.\\d+)*\\s+.+$'
    - '^Annex\\s+[A-Z]\\s+.+$'
  appendix_markers: [Annex, Appendix, Anhang, Annexe]
normative_status:
  default_body: normative
  annex_default: informative
  override_keywords:
    normative: [normative, verbindlich, normatif]
    informative: [informative, informativ, informatif]
headers_footers:
  repeated_text_page_frequency_threshold: 0.40
  top_band_ratio: 0.12
  bottom_band_ratio: 0.12
tables:
  detect_multi_page_tables: true
  repeated_header_similarity_threshold: 0.90
  row_key_rules: []
alignment:
  weights:
    section_path: 0.20
    title_caption: 0.15
    keyword: 0.10
    embedding: 0.15
    table_schema: 0.15
    numeric_pattern: 0.15
    reranker: 0.10
severity:
  policy_id: automotive_emc_default
  review_thresholds:
    high_confidence_min: 0.85
    medium_confidence_min: 0.70
```

---

## 3. Profile selection

Selection signals:

```text
- user-selected document family
- filename pattern
- metadata title
- issuer or organization
- detected keywords
- section and table structure
- known template identifiers
- previous project setting
```

Recommended behavior:

```text
1. User may explicitly select profile.
2. System may suggest profile with confidence.
3. If confidence is low, use generic profile and warn user.
4. Store selected profile ID and version with document ingestion.
```

Never silently switch profile versions for an already-ingested document.

---

## 4. Generic fallback profile

The fallback profile should be conservative.

```text
- Extract structure with minimal assumptions.
- Use broad heading detection.
- Avoid aggressive severity overrides.
- Mark low-confidence tables and alignments for review.
- Prefer review over silent high-confidence classification.
```

Fallback is acceptable for exploration, but production workflows should define family-specific profiles for recurring document types.

---

## 5. Automotive EMC OEM requirements profile

Typical properties:

```text
- Table-heavy technical requirements
- Numeric limits and test levels
- Frequency ranges
- Test methods and acceptance criteria
- Applicability by component, vehicle platform, voltage class, port, or harness type
- Footnotes and exceptions that materially change scope
- Repeated confidentiality headers and document-control footers
```

Recommended row-key signals:

```text
- test phenomenon
- frequency range
- port or coupling method
- modulation or detector
- component category
- acceptance class
- supply voltage or operating mode
```

Recommended high-severity triggers:

```text
- field strength changed
- conducted disturbance limit changed
- frequency band changed
- acceptance class changed
- test method reference changed
- applicability expanded to additional product family
- exception removed
```

---

## 6. International or industry standard profile

Typical properties:

```text
- More formal section hierarchy
- Normative and informative annexes
- Defined terms and references
- Tables with stable numbering
- Requirements expressed through shall/should/may language
```

Profile settings:

```text
- Strong annex normative-status detection
- Strict cross-reference resolution
- Preserve clause numbers as alignment anchors
- Treat reference standard changes as potentially high severity
- Route annex/body moves to review
```

---

## 7. Homologation regulation profile

Typical properties:

```text
- Legal or regulatory clauses
- Approval, conformity, and evidence obligations
- Amendments and supplements
- Applicability by vehicle category or component type
```

Profile settings:

```text
- Treat applicability and scope changes as high impact by default.
- Preserve legal clause identifiers.
- Use conservative review thresholds.
- Avoid unsupported legal conclusions in LLM explanations.
```

---

## 8. Internal test plan profile

Typical properties:

```text
- Lab procedure details
- Test setup, equipment, calibration, environmental conditions
- Step-by-step procedures
- Acceptance criteria may reference external specs
```

Profile settings:

```text
- Detect procedure steps and setup parameters.
- Treat equipment, calibration, and acceptance changes as medium or high depending on context.
- Distinguish local procedure changes from compliance requirement changes.
```

---

## 9. Supplier evidence package profile

Typical properties:

```text
- Test reports, certificates, data sheets, or evidence summaries
- Measured values rather than requirements
- Pass/fail statements
- Lab metadata and sample identifiers
```

Profile settings:

```text
- Distinguish requirement documents from evidence documents.
- Treat missing sample identifiers or test dates as review items.
- Do not compare measured values as requirement limits unless profile marks them as limits.
```

---

## 10. Header and footer rules

Profile-specific repeated content rules should include:

```yaml
headers_footers:
  repeated_text_page_frequency_threshold: 0.40
  top_band_ratio: 0.12
  bottom_band_ratio: 0.12
  preserve_in_audit: true
  exclude_from_semantic_comparison: true
  known_patterns:
    - '^Confidential$'
    - '^Page \\d+ of \\d+$'
    - '^Document No\\. .+$'
```

Do not delete headers and footers. Preserve them in extraction audit and exclude from semantic comparison.

---

## 11. Table profile rules

Example table schema rule:

```yaml
tables:
  known_schemas:
    - schema_id: radiated_immunity_test_levels
      caption_patterns:
        - 'radiated immunity'
        - 'field strength'
      required_columns:
        - frequency_range
        - field_strength
        - modulation
        - acceptance_criterion
      optional_columns:
        - test_method
        - dwell_time
        - notes
      row_key:
        fields:
          - frequency_range
          - modulation
          - port_or_component
      high_severity_columns:
        - field_strength
        - frequency_range
        - acceptance_criterion
        - test_method
```

For table-heavy EMC documents, row identity should rarely depend on row index alone.

---

## 12. Alignment weights by profile

Different profiles need different alignment weights.

Example:

```yaml
alignment:
  object_type_weights:
    table_row:
      row_key: 0.25
      numeric_pattern: 0.20
      table_schema: 0.20
      section_path: 0.10
      caption: 0.10
      embedding: 0.10
      reranker: 0.05
    paragraph_requirement:
      section_path: 0.20
      normative_terms: 0.15
      keyword: 0.15
      embedding: 0.25
      reranker: 0.15
      numeric_pattern: 0.10
```

Profile evaluation should tune these values using the gold dataset.

---

## 13. Severity overrides by profile

Profile-specific overrides should be explicit.

```yaml
severity_overrides:
  - override_id: EMC_ACCEPTANCE_CLASS_CHANGED_HIGH
    when:
      ontology_entity: acceptance_class
      change: changed
    then:
      severity: high
      reason_codes: [ACCEPTANCE_CRITERION_CHANGED]

  - override_id: INFORMATIVE_NOTE_REPHRASE_LOW
    when:
      section_normative_status: informative
      semantic_impact: editorial
    then:
      severity: low
      reason_codes: [EDITORIAL_REPHRASE]
```

Do not bury severity behavior in parser code.

---

## 14. Profile lifecycle

```text
1. Draft profile from sample documents.
2. Run extraction and alignment debug views.
3. Add gold annotations for representative pairs.
4. Tune row keys, heading rules, and thresholds.
5. Run evaluation harness.
6. Register profile with version and hash.
7. Approve for dev or production.
8. Monitor failures and create new profile version when needed.
```

Profile changes can alter audit results, so they must be versioned and included in reports.

---

## 15. Profile debugging output

Store profile decisions:

```json
{
  "documentId": "doc-v2",
  "selectedProfileId": "automotive_emc_oem_generic",
  "selectedProfileVersion": "1.0.0",
  "selectionConfidence": 0.88,
  "signals": [
    "caption contains radiated immunity",
    "frequency range tables detected",
    "shall/should normative terms detected"
  ],
  "warnings": [
    "Annex normative status uncertain on pages 80-92"
  ]
}
```

This helps auditors understand why the engine behaved differently across document families.
