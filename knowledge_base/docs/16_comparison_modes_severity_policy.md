# 16. Comparison Modes and Severity Policy

This document defines two operating modes for the offline GenAI GRC Auditor comparison pipeline:

1. **Simple User Mode** - produces only high-level semantic differences with citations.
2. **Auditor Grade Mode** - produces a defensible change register where every reported change is classified by `changeSeverity`: `low`, `medium`, or `high`.

The same ingestion, normalization, alignment, and evidence generation pipeline should be reused for both modes. The difference is in filtering, classification, explanation depth, and reporting strictness.

---

## 1. Design principle

Do not build two separate comparison engines.

Build one shared comparison engine with two output policies:

```text
PDF v1 + PDF v2
  -> extraction
  -> Compliance Intermediate Representation (CIR)
  -> normalization
  -> alignment
  -> raw diff detection
  -> semantic significance analysis
  -> severity classification
  -> evidence pack generation
  -> mode-specific report
```

The comparison engine should always compute structured deltas. The mode decides what is shown to the user.

---

## 2. Mode summary

| Area | Simple User Mode | Auditor Grade Mode |
|---|---|---|
| Target user | General business user, engineer, manager | Auditor, compliance owner, quality lead |
| Output goal | Show meaningful semantic changes only | Produce defensible audit change register |
| Severity required | No user-visible severity required | Every reported change must have `changeSeverity` |
| Cosmetic changes | Hidden | Classified as `low` if retained; hidden by default if configured |
| Move-only changes | Usually hidden | `low`, unless scope/applicability changes |
| Citations | Required | Required, stricter citation validation |
| Explanation | Short, readable | Structured, evidence-driven, reason-coded |
| LLM role | Summarize grounded changes | Explain classified changes with citations |
| Report style | High-level change summary | Full change register and audit trail |
| Human review | Optional | Required for uncertain medium/high changes |

---

## 3. Shared comparison pipeline

### 3.1 Ingestion

The ingestion stage parses both PDFs into a canonical Compliance Intermediate Representation.

The CIR should contain:

```text
- document metadata
- language
- pages
- sections
- paragraphs
- lists
- tables
- table rows
- table cells
- footnotes
- cross-references
- normalized technical facts
- citations with page and bounding box
- extraction confidence
```

### 3.2 Normalization

Before comparison, normalize:

```text
- whitespace
- hyphenation
- bullets and list markers
- section numbers
- table captions
- table headers
- repeated headers and footers
- units
- numeric values
- ranges
- references
- normative terms
- OCR correction candidates
```

### 3.3 Alignment

Align v1 objects to v2 objects by object type:

```text
section -> section
table -> table
table row -> table row
table cell -> table cell
requirement -> requirement
footnote -> footnote
cross-reference -> cross-reference
```

Use deterministic anchors first, then hybrid retrieval, then reranking.

Recommended alignment signals:

```text
- section path similarity
- section title similarity
- table caption similarity
- table schema similarity
- row key similarity
- normalized numeric facts
- standard references
- BM25 or keyword similarity
- embedding similarity
- reranker score
- language match
```

### 3.4 Raw diff detection

The raw diff layer detects possible differences without deciding audit importance.

Examples:

```text
- text changed
- requirement added
- requirement removed
- table row added
- table row removed
- table cell changed
- numeric value changed
- unit changed
- frequency range changed
- normative term changed
- section moved
- punctuation changed
- whitespace changed
- formatting changed
```

### 3.5 Semantic significance analysis

Classify whether a raw diff has meaning.

Recommended `semanticImpact` values:

```text
none
editorial
structural
semantic
technical
normative
scope
```

Examples:

| Raw diff | semanticImpact |
|---|---|
| Extra space | none |
| Punctuation only | editorial |
| Section moved, content same | structural |
| Wording changed but obligation same | semantic |
| 30 V/m changed to 60 V/m | technical |
| should changed to shall | normative |
| passenger vehicles changed to all vehicles | scope |

### 3.6 Severity classification

Auditor Grade Mode requires every reported change to have:

```json
{
  "changeSeverity": "low | medium | high",
  "severityReasonCodes": [],
  "severityConfidence": 0.0
}
```

Simple User Mode may compute severity internally but should not display it unless the UI chooses to expose it.

---

## 4. Simple User Mode

## 4.1 Purpose

Simple User Mode is designed for users who want to know:

```text
What materially changed between release v1 and release v2?
Where is the evidence?
What should I pay attention to?
```

It should not overwhelm the user with punctuation, layout, move-only, or formatting changes.

---

## 4.2 Simple Mode output policy

Simple Mode should show only high-value semantic differences.

Recommended default inclusion rule:

```text
Include a change if:
  semanticImpact in [semantic, technical, normative, scope]
  OR the internal severity would be medium or high

Hide a change if:
  semanticImpact in [none, editorial]
  OR the change is move-only with no semantic impact
  OR the change is formatting-only
  OR the change is header/footer/decorative-only
```

Structural-only changes should normally be hidden.

Example hidden changes:

```text
- punctuation-only change
- spelling correction with no meaning change
- table border or decoration change
- section moved with identical content and same scope
- page number changed
- header/footer changed
- whitespace or line-break change
- bullet style changed from '-' to 'a)'
```

Example visible changes:

```text
- requirement added
- requirement removed
- obligation changed from should to shall
- test level changed from 30 V/m to 60 V/m
- frequency range changed
- applicability changed
- exception added or removed
- acceptance criterion changed
- referenced test method changed
- table row with technical requirement added or removed
```

---

## 4.3 Simple Mode categories

Use simple user-facing categories instead of audit terminology.

Recommended categories:

```text
Added
Removed
Changed
```

Optional sublabels:

```text
Requirement change
Table change
Numeric change
Scope change
Reference change
```

Avoid forcing the user to interpret `low`, `medium`, and `high` unless the product UI explicitly wants to show impact.

---

## 4.4 Simple Mode output schema

```json
{
  "comparisonMode": "simple",
  "sourceLanguage": "en|de|fr",
  "summary": {
    "totalVisibleChanges": 12,
    "added": 3,
    "removed": 2,
    "changed": 7
  },
  "changes": [
    {
      "changeId": "CHG-00017",
      "category": "Changed",
      "changeKind": "numeric_threshold_changed",
      "title": "Radiated immunity test level changed",
      "summary": "The required test level changed from 30 V/m to 60 V/m for the 200-400 MHz frequency range.",
      "whyItMatters": "Existing evidence at 30 V/m may not be sufficient for this frequency band.",
      "citations": {
        "v1": ["EV-v1-0042"],
        "v2": ["EV-v2-0047"]
      },
      "confidence": 0.94
    }
  ],
  "hiddenDiffs": {
    "count": 148,
    "reason": "Non-semantic, cosmetic, structural-only, or low-value changes hidden in Simple Mode."
  }
}
```

---

## 4.5 Simple Mode prompt behavior

The LLM should receive only curated, meaningful changes.

Prompt instruction:

```text
Explain the following detected change in the same language as the source documents.
Use only the provided evidence.
Do not mention cosmetic, formatting, or move-only changes.
Do not invent impact beyond the evidence.
Return concise JSON.
```

---

## 5. Auditor Grade Mode

## 5.1 Purpose

Auditor Grade Mode is designed to produce a defensible comparison register.

The auditor should be able to answer:

```text
What changed?
Why was it classified this way?
Where is the evidence?
What is the compliance impact?
Was anything ignored?
Which changes require human review?
```

---

## 5.2 Auditor Mode output policy

Every reported change must have:

```text
- changeId
- changeType
- semanticImpact
- changeSeverity: low, medium, or high
- severity reason codes
- v1 citation
- v2 citation
- confidence
- review disposition
```

Recommended rule:

```text
Raw extraction noise may be discarded before reporting.
Real document changes should either be:
  - classified and reported, or
  - classified as low and hidden from the default view, or
  - sent to an appendix depending on audit configuration.
```

This distinction is important:

```text
Extraction artifact != document change
Cosmetic document change = real document change with low severity
```

---

## 5.3 Auditor Mode severity levels

### 5.3.1 Low severity

A low severity change is unlikely to change compliance obligations, test execution, acceptance criteria, or evidence sufficiency.

Low severity examples:

```text
- punctuation-only change
- spelling correction with no meaning change
- whitespace or line-break change
- decorative/layout change
- table border or styling change
- page number change
- header/footer change
- section moved with unchanged content and unchanged scope
- section renumbered with unchanged content and valid references
- table row order changed with unchanged values
- column order changed with unchanged values and units
- wording clarified but obligation unchanged
- note rephrased without changing applicability
- reference text renamed but target remains equivalent
```

Recommended low severity reason codes:

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
```

Important exception:

A moved section is low only if its new location does not change scope, applicability, referenced parent section, or legal meaning.

---

### 5.3.2 Medium severity

A medium severity change may affect interpretation, implementation, test planning, or compliance evidence, but the impact is not clearly severe or automatically invalidating.

Medium severity examples:

```text
- wording change that could alter interpretation
- added clarification that affects how a test is performed
- changed informative note that may affect implementation guidance
- added exception with limited applicability
- removed clarification where effect is uncertain
- cross-reference changed but target appears related
- test setup wording changed without obvious threshold change
- table footnote changed with limited or ambiguous impact
- requirement split or merged with possible meaning change
- acceptance wording changed but criteria not clearly stricter or weaker
- applicability narrowed or broadened in a limited way
```

Recommended medium severity reason codes:

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
HUMAN_REVIEW_RECOMMENDED
```

Medium severity should often require reviewer attention, especially when confidence is low.

---

### 5.3.3 High severity

A high severity change is likely to affect compliance obligations, test burden, acceptance criteria, product applicability, or validity of existing evidence.

High severity examples:

```text
- mandatory requirement added
- mandatory requirement removed
- obligation strengthened, such as should -> shall
- obligation weakened, such as shall -> should
- prohibition added or removed
- numeric limit changed
- test level changed
- frequency range changed
- unit changed in a way that changes meaning
- acceptance criterion changed
- test method changed
- referenced standard changed materially
- applicability broadened materially
- applicability narrowed materially
- exception removed, making requirement stricter
- exception added, changing applicability
- table row with technical requirement added or removed
- table column defining a required parameter added or removed
- normative footnote added, removed, or changed
- conformance statement changed
```

Recommended high severity reason codes:

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
EVIDENCE_MAY_BE_INVALIDATED
```

---

## 6. Severity classification logic

Severity should not be assigned only by the LLM.

Use a rule-first classifier, then use the LLM to explain and validate.

Recommended order:

```text
1. Detect raw diff.
2. Determine semanticImpact.
3. Extract structured facts.
4. Apply deterministic severity rules.
5. Apply escalation and de-escalation rules.
6. Send evidence pack to LLM for explanation.
7. Validate LLM output against schema and citations.
8. Store final changeSeverity and reason codes.
```

---

## 6.1 Severity scoring model

Use score bands internally, but expose only low, medium, high.

Example:

```text
0.00 - 0.29 -> low
0.30 - 0.69 -> medium
0.70 - 1.00 -> high
```

Example feature scoring:

```text
+0.80 mandatory requirement added or removed
+0.80 numeric limit changed
+0.75 test method changed
+0.75 acceptance criterion changed
+0.70 scope broadened or narrowed
+0.65 normative footnote changed
+0.50 ambiguous wording change
+0.40 cross-reference changed
+0.25 section moved with possible scope change
+0.10 editorial rephrase
+0.05 punctuation-only change
+0.00 whitespace-only change
```

Escalation rules should override simple scores where appropriate.

Example:

```text
If numeric limit changed and unit is safety/test/compliance relevant:
  severity = high

If shall changed to should:
  severity = high

If section moved but parent scope changed:
  severity >= medium

If table row order changed and all normalized row facts are equal:
  severity = low
```

---

## 6.2 Escalation rules

Escalate to high if any of these are true:

```text
- mandatory obligation added
- mandatory obligation removed
- normative level changed
- numeric compliance threshold changed
- acceptance criterion changed
- test method changed
- frequency range changed
- product applicability changed materially
- exception changed materially
- required table row added or removed
- existing evidence may no longer satisfy v2
```

Escalate to medium if any of these are true:

```text
- wording may affect interpretation
- cross-reference changed but not clearly equivalent
- footnote changed but impact is uncertain
- section moved under a different parent scope
- requirement split or merged with possible meaning change
- table header changed but values appear unchanged
```

Keep low if all of these are true:

```text
- normalized requirement text is equivalent
- normalized technical facts are equivalent
- normative level is equivalent
- scope is equivalent
- citations can be mapped reliably
- only presentation, order, punctuation, or location changed
```

---

## 7. Handling non-semantic changes

Use three categories:

```text
1. Extraction artifacts
2. Non-semantic real document changes
3. Semantic document changes
```

### 7.1 Extraction artifacts

These are not real document changes and should not be reported.

Examples:

```text
- OCR misread corrected by normalization
- duplicated footer extracted as content
- table line artifact
- page rendering artifact
```

Disposition:

```text
ignored_as_artifact
```

### 7.2 Non-semantic real document changes

These are real changes in the PDF but do not affect compliance meaning.

Examples:

```text
- punctuation
- formatting
- decorative change
- move-only change
- renumbering with same meaning
```

Disposition in Simple Mode:

```text
hidden
```

Disposition in Auditor Mode:

```text
classified as low if retained
hidden by default or included in appendix depending on audit configuration
```

### 7.3 Semantic document changes

These affect meaning, interpretation, obligations, technical values, scope, or evidence.

Disposition:

```text
reported
classified in Auditor Mode
explained with citations
```

---

## 8. Special rules for moved content

Move-only changes are common in release revisions.

Default rule:

```text
If content moved but normalized content, technical facts, scope, and normative level are unchanged:
  changeSeverity = low
```

Escalate to medium if:

```text
- moved under a different parent scope
- moved from informative annex to normative body
- moved from optional section to mandatory section
- cross-references may no longer resolve cleanly
- applicability may have changed
```

Escalate to high if:

```text
- move changes normative status
- move changes product applicability
- move changes test applicability
- move invalidates existing references or compliance evidence
```

Example:

```text
v1: requirement appears in Annex A, marked informative
v2: same text appears in Section 5, marked normative

Classification:
  semanticImpact = normative
  changeSeverity = high
  reasonCodes = [NORMATIVE_STATUS_CHANGED, OBLIGATION_STRENGTHENED]
```

---

## 9. Special rules for table changes

Tables require cell-level and row-level reasoning.

### 9.1 Low severity table changes

```text
- table formatting changed
- column order changed but headers and values are equivalent
- row order changed but row identities and values are equivalent
- repeated header style changed
- caption punctuation changed
```

### 9.2 Medium severity table changes

```text
- table caption changed in a way that may affect interpretation
- table footnote changed ambiguously
- column header wording changed but units and values unchanged
- row label changed but key technical facts unchanged
- row added that appears informative only
```

### 9.3 High severity table changes

```text
- numeric cell value changed
- unit changed
- frequency band changed
- test level changed
- detector type changed
- acceptance class changed
- technical row added
- technical row removed
- required parameter column added or removed
- normative footnote added or removed
```

Recommended table diff object:

```json
{
  "changeType": "table_cell_changed",
  "tableIdV1": "TBL-v1-0042-01",
  "tableIdV2": "TBL-v2-0047-01",
  "rowKey": "200-400 MHz | AM 80%",
  "columnKey": "Field strength",
  "oldValue": "30 V/m",
  "newValue": "60 V/m",
  "semanticImpact": "technical",
  "changeSeverity": "high",
  "severityReasonCodes": ["TEST_LEVEL_CHANGED", "NUMERIC_LIMIT_CHANGED"],
  "citations": {
    "v1": ["EV-v1-0042-row-12-cell-03"],
    "v2": ["EV-v2-0047-row-12-cell-03"]
  }
}
```

---

## 10. Special rules for multilingual documents

The comparison should be performed in the source language.

Do not translate documents before comparison.

For severity classification, maintain language-specific normative dictionaries.

Examples:

```text
English:
  shall, must, should, may, shall not

German:
  muss, muessen, soll, sollte, darf, darf nicht, ist erforderlich

French:
  doit, doivent, devrait, peut, ne doit pas, est obligatoire
```

Output language policy:

```text
Simple Mode:
  explain in source document language

Auditor Mode:
  classify using internal English enum values
  produce user-facing explanation in source document language
  preserve original quotes without translation
```

---

## 11. Auditor Mode output schema

```json
{
  "comparisonMode": "auditor_grade",
  "sourceLanguage": "de",
  "summary": {
    "totalReportedChanges": 52,
    "high": 8,
    "medium": 17,
    "low": 27,
    "requiresReview": 11,
    "ignoredArtifacts": 43
  },
  "changes": [
    {
      "changeId": "CHG-00017",
      "changeType": "numeric_threshold_changed",
      "semanticImpact": "technical",
      "changeSeverity": "high",
      "severityConfidence": 0.96,
      "severityReasonCodes": [
        "TEST_LEVEL_CHANGED",
        "NUMERIC_LIMIT_CHANGED",
        "EVIDENCE_MAY_BE_INVALIDATED"
      ],
      "objectType": "table_cell",
      "sectionPathV1": ["5", "5.3", "5.3.2"],
      "sectionPathV2": ["5", "5.3", "5.3.2"],
      "oldValue": "30 V/m",
      "newValue": "60 V/m",
      "summary": "Der geforderte Pruefpegel wurde von 30 V/m auf 60 V/m erhoeht.",
      "auditImpact": "Vorhandene Pruefnachweise mit 30 V/m koennen fuer diesen Frequenzbereich unzureichend sein.",
      "citations": {
        "v1": [
          {
            "evidenceId": "EV-v1-0042-row-12-cell-03",
            "documentId": "doc-v1",
            "page": 42,
            "section": "5.3.2",
            "table": "Table 12",
            "bbox": [72, 210, 520, 260]
          }
        ],
        "v2": [
          {
            "evidenceId": "EV-v2-0047-row-12-cell-03",
            "documentId": "doc-v2",
            "page": 47,
            "section": "5.3.2",
            "table": "Table 12",
            "bbox": [70, 218, 522, 268]
          }
        ]
      },
      "requiresHumanReview": false
    }
  ]
}
```

---

## 12. Suggested database fields

Add these fields to the `changes` table:

```sql
comparison_mode text not null,
change_type text not null,
semantic_impact text not null,
change_severity text,
severity_confidence numeric,
severity_reason_codes jsonb not null default '[]',
audit_disposition text not null,
requires_human_review boolean not null default false,
visible_in_simple_mode boolean not null default false,
visible_in_auditor_default_view boolean not null default true,
```

Recommended enum-like values:

```text
semantic_impact:
  none
  editorial
  structural
  semantic
  technical
  normative
  scope

audit_disposition:
  reported
  hidden_low_severity
  ignored_as_artifact
  requires_review

change_severity:
  low
  medium
  high
```

---

## 13. UI behavior

### 13.1 Simple User Mode UI

Default view:

```text
- summary of meaningful changes
- grouped by Added, Removed, Changed
- short explanation
- citations
- side-by-side evidence viewer
```

Do not show:

```text
- raw diff count
- punctuation changes
- move-only changes
- severity matrix
- reason-code noise
```

Optional footer:

```text
148 low-value formatting or structural differences were hidden.
```

### 13.2 Auditor Grade Mode UI

Default view:

```text
- severity dashboard
- high changes first
- medium changes second
- low changes collapsed by default
- filters by section, table, change type, severity, confidence
- requires-review queue
- side-by-side PDF evidence
- exportable change register
```

Filters:

```text
changeSeverity = high | medium | low
semanticImpact = technical | normative | scope | semantic | structural | editorial
objectType = requirement | table | table_row | table_cell | section | footnote
requiresHumanReview = true | false
confidence range
```

---

## 14. LLM usage by mode

### 14.1 Simple Mode LLM prompt

```text
You are explaining a compliance document comparison to a non-auditor user.
The source documents are in {language}.
Write only in {language}.
Explain only the meaningful semantic change provided in the evidence pack.
Do not discuss punctuation, formatting, section movement, or cosmetic differences.
Do not add unsupported claims.
Use citations exactly as provided.
Return valid JSON.
```

### 14.2 Auditor Mode LLM prompt

```text
You are assisting an auditor reviewing a compliance document comparison.
The source documents are in {language}.
Write user-facing fields only in {language}.
Use the provided machine-detected delta, severity candidate, and evidence.
Do not invent facts.
Do not change the severity unless the evidence clearly contradicts it.
Every explanation must reference the provided evidence IDs.
Return valid JSON matching the schema.
```

---

## 15. Validation rules

Reject or regenerate LLM output if:

```text
- JSON is invalid
- output language does not match source language
- citation IDs are missing
- unsupported citations are used
- severity is not low, medium, or high in Auditor Mode
- explanation mentions facts not present in evidence
- high severity change lacks reason codes
- numeric change explanation omits old or new value
```

---

## 16. Human review rules

Require human review when:

```text
- severity is high and confidence < 0.85
- severity is medium and confidence < 0.70
- table extraction confidence is low
- OCR confidence is low
- alignment confidence is low
- section moved under different parent scope
- requirement split or merge is detected
- footnote or exception changed ambiguously
- v1/v2 citations cannot be validated visually
```

---

## 17. Test cases for mode behavior

| Test case | Simple Mode | Auditor Mode |
|---|---|---|
| Punctuation changed | hidden | low, hidden/collapsed |
| Section moved, same scope | hidden | low |
| Section moved from informative annex to normative body | visible | high |
| should changed to shall | visible | high |
| 30 V/m changed to 60 V/m | visible | high |
| Frequency range expanded | visible | high |
| Cross-reference changed to related clause | visible if meaningful | medium |
| Header/footer changed | hidden | low or ignored |
| Table row order changed only | hidden | low |
| Technical table row added | visible | high |
| Footnote changed ambiguously | visible | medium, review |
| Requirement split into bullets, same meaning | hidden or summarized | low |
| Requirement split with added obligation | visible | high |

---

## 18. Recommended defaults

### 18.1 Simple Mode defaults

```json
{
  "showSeverity": false,
  "includeLowSeverity": false,
  "includeCosmetic": false,
  "includeMoveOnly": false,
  "includeMediumAndHigh": true,
  "requireCitations": true,
  "maxExplanationLength": "short"
}
```

### 18.2 Auditor Mode defaults

```json
{
  "showSeverity": true,
  "includeLowSeverity": true,
  "collapseLowSeverityByDefault": true,
  "includeCosmeticInAppendix": true,
  "includeMoveOnly": true,
  "requireCitations": true,
  "requireReasonCodes": true,
  "requireHumanReviewQueue": true,
  "maxExplanationLength": "detailed"
}
```

---

## 19. Key implementation recommendation

The product should not ask the LLM:

```text
Compare these PDFs and classify severity.
```

Instead, the product should compute:

```text
- aligned object pair
- raw delta
- semantic impact
- structured technical facts
- deterministic severity candidate
- evidence citations
```

Then ask the LLM:

```text
Explain this already-detected and cited change in the source language.
```

This keeps Simple Mode readable and Auditor Grade Mode defensible.
