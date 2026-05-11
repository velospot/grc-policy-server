# Challenges, limitations, debugging strategy, and accuracy improvement plan

This document captures the likely challenges, limitations, debugging workflows, and accuracy-improvement plan for an offline, multilingual, GenAI-assisted GRC Auditor used to compare compliance PDF releases in table-heavy automotive EMC testing contexts.

The core principle is:

```text
Do not use the LLM as the primary diff engine.
Use deterministic extraction, normalization, alignment, and comparison first.
Use the LLM only to explain already-grounded, cited evidence.
```

---

## 1. Biggest development challenge: PDFs are not semantic documents

A PDF is often not a clean tree of chapters, sections, paragraphs, and tables. Many PDFs are closer to positioned text, glyphs, lines, rectangles, images, and page coordinates. A low-level PDF parser may expose pages as blocks, lines, spans, and characters, but the extracted plain text may not match the human-visible reading order.

For this project, the failure mode is severe:

```text
Wrong reading order
-> wrong section reconstruction
-> wrong requirement extraction
-> wrong comparison
-> wrong auditor explanation
```

The system must treat PDF extraction as an evidence-building process, not a simple `read_pdf_text()` step.

---

## 2. Potential challenges during document ingestion

### 2.1 Reading order problems

#### Symptom

The extracted text appears as:

```text
Table heading
Footer
Left column
Right column
Page number
Actual paragraph continuation
```

instead of the human-visible order.

#### Why it happens

PDFs may store text in creation order, coordinate order, or fragmented drawing order. Multi-column layouts, headers, footers, side notes, rotated labels, and table cells can break naive extraction.

#### Impact

Reading order errors cause:

```text
section text mixed with table text
table notes attached to the wrong row
requirement wording split incorrectly
footers indexed as requirements
false document differences
```

#### Debugging approach

Store and visualize every extracted block:

```json
{
  "page": 42,
  "block_id": "blk_0042_017",
  "text": "...",
  "bbox": [72, 180, 512, 224],
  "reading_order_index": 17,
  "extractor": "docling",
  "confidence": 0.88
}
```

Create a debug viewer with toggles:

```text
Show text blocks
Show reading order numbers
Show section boundaries
Show table boxes
Show extracted text next to PDF image
```

Docling is useful because it is designed for page layout, reading order, table structure, OCR, and structured document representation, but no extractor will be perfect on every compliance PDF. Always keep a visual debug layer.

---

### 2.2 Header and footer contamination

#### Symptom

The system detects fake requirements such as:

```text
Confidential
Page 42 of 181
Release 2024-07
Automotive EMC Requirements
```

#### Why it happens

Headers, footers, watermarks, copyright notices, and document-control banners repeat on many pages.

#### Impact

This causes:

```text
duplicate chunks
bad embeddings
false repeated requirements
incorrect similarity matches
noisy comparison reports
```

#### Accuracy improvement

Detect repeated text by page frequency:

```text
If same or near-same text appears on > 40% of pages
and appears in top/bottom page bands
then classify as header/footer
```

Keep the text in the extraction audit, but exclude it from semantic comparison.

Do not delete it permanently. Citations may still need page-level context.

---

### 2.3 Section hierarchy reconstruction

#### Symptom

The system maps:

```text
5.3.2 Radiated immunity
```

under the wrong parent section, or treats appendix sections as normal body sections.

#### Why it happens

Compliance documents use many heading styles:

```text
1
1.1
1.1.1
A.1
Annex B
Appendix C
Table 5-2
REQ-5.3.2-01
```

Some headings are visually obvious but not tagged as headings in the PDF.

#### Impact

Bad hierarchy causes bad comparison because section path is one of the strongest alignment anchors.

#### Accuracy improvement

Use a hybrid heading detector:

```text
heading_score =
  numbering_pattern_score
+ font_size_score
+ bold_score
+ spacing_before_score
+ spacing_after_score
+ table_of_contents_match_score
+ repeated_style_score
```

Store uncertain headings:

```json
{
  "text": "5.3.2 Radiated immunity",
  "detected_as": "heading",
  "confidence": 0.74,
  "reason_codes": [
    "numbering_pattern",
    "larger_font",
    "appears_in_toc"
  ]
}
```

Add a human-review UI for low-confidence hierarchy. A small correction to hierarchy can improve hundreds of downstream comparisons.

---

### 2.4 Table extraction failures

This will likely be the hardest ingestion problem.

#### Common symptoms

```text
merged cells are lost
row headers become normal cells
multi-line cells are split into several rows
tables spanning pages become separate tables
footnotes are not connected to the table
repeated table headers become data rows
empty cells are interpreted as missing requirements
units in column headers are lost
```

#### Why it happens

PDF tables are often not real tables. They may be:

```text
text positioned in rows and columns
drawn rectangles and lines
borderless text aligned by coordinates
images of tables
partially tagged table structures
```

#### Impact

A single table extraction error can produce many false compliance changes.

Example:

```text
Actual v1:
Frequency: 200-400 MHz | Limit: 30 V/m

Bad extraction:
Frequency: 200-400 MHz
Limit: empty

Comparison result:
"Limit removed"
```

That would be a false high-risk finding.

#### Accuracy improvement

Use layered table extraction:

```text
1. Try Docling table output.
2. Try OpenDataLoader or another table-aware extraction path.
3. Use pdfplumber/PyMuPDF for coordinate validation.
4. Compare table shape, headers, row count, and cell density.
5. Select best extraction or mark table for review.
```

For each table, compute quality metrics:

```json
{
  "table_id": "TBL-0042-01",
  "header_confidence": 0.92,
  "cell_grid_confidence": 0.81,
  "row_count": 31,
  "column_count": 7,
  "empty_cell_ratio": 0.08,
  "multi_page_stitch_confidence": 0.73,
  "requires_review": true
}
```

---

### 2.5 Multi-page table stitching

#### Symptom

The system treats this as three different tables:

```text
Table 12 - Test levels
Table 12 continued
Table 12 continued
```

or incorrectly merges two unrelated tables.

#### Why it happens

A table can continue across pages with repeated headers, partial captions, missing captions, or footnotes between page breaks.

#### Impact

The comparison engine may report:

```text
50 rows removed
50 rows added
```

instead of:

```text
same table, 2 numeric cells changed
```

#### Accuracy improvement

Use a table stitching score:

```text
stitch_score =
  0.25 * caption_similarity
+ 0.25 * column_header_similarity
+ 0.20 * horizontal_alignment_similarity
+ 0.10 * repeated_header_detection
+ 0.10 * page_adjacency
+ 0.10 * section_path_similarity
```

Merge tables only when the score exceeds a calibrated threshold.

Use three states:

```text
stitched
not_stitched
needs_review
```

Never silently merge low-confidence tables.

---

### 2.6 Scanned documents and OCR variability

#### Symptom

Text contains errors:

```text
30 V/m -> 3O V/m
150 kHz -> l50 kHz
uV -> µV
shall -> shal1
muss -> rnuss
```

#### Why it happens

OCR errors depend on scan quality, skew, resolution, font, noise, table lines, language pack, and image compression.

#### Impact

OCR errors can break:

```text
normative term detection
numeric comparison
unit parsing
embedding quality
citation quality
```

#### Accuracy improvement

Implement OCR confidence gates:

```text
If OCR confidence < threshold:
  mark page or cell for review
  avoid high-confidence compliance conclusions
```

Run OCR only where needed:

```text
Digital PDF text available:
  use native text
  OCR only image regions

Scanned page:
  OCR full page using detected or selected language

Mixed page:
  combine native extraction and OCR image-region extraction
```

Add OCR-specific normalization:

```text
3O V/m -> 30 V/m when column expects numeric
l50 kHz -> 150 kHz when pattern expects frequency
uV -> µV when unit context exists
rnuss -> muss only if German text context is strong
```

Keep corrections auditable:

```json
{
  "raw_ocr": "3O V/m",
  "normalized": "30 V/m",
  "correction_reason": "numeric_column_ocr_confusion_O_zero",
  "confidence": 0.82
}
```

---

### 2.7 Multilingual requirement detection

#### Symptom

The system detects English requirements well but misses German and French obligations.

Example missed terms:

```text
German:
muss, müssen, darf nicht, ist erforderlich, soll

French:
doit, doivent, ne doit pas, est requis, il convient de
```

#### Impact

The system under-reports obligation changes in German/French documents.

#### Accuracy improvement

Use language-specific normative dictionaries:

```json
{
  "en": {
    "mandatory": ["shall", "must", "is required to"],
    "recommended": ["should", "is recommended"],
    "permitted": ["may", "is permitted"],
    "prohibited": ["shall not", "must not", "is prohibited"]
  },
  "de": {
    "mandatory": ["muss", "müssen", "ist erforderlich"],
    "recommended": ["soll", "sollen", "sollte", "wird empfohlen"],
    "permitted": ["darf", "dürfen", "ist zulässig"],
    "prohibited": ["darf nicht", "ist unzulässig", "ist verboten"]
  },
  "fr": {
    "mandatory": ["doit", "doivent", "est requis", "est obligatoire"],
    "recommended": ["devrait", "il convient de", "est recommandé"],
    "permitted": ["peut", "peuvent", "est autorisé"],
    "prohibited": ["ne doit pas", "est interdit"]
  }
}
```

Store both the normalized level and the original phrase:

```json
{
  "normative_level": "mandatory",
  "normative_term": "muss",
  "language": "de"
}
```

Use language metadata throughout retrieval and comparison.

---

## 3. Potential challenges during comparison

### 3.1 Section renumbering

#### Symptom

The system reports many removed and added sections, although the content was only moved.

Example:

```text
v1: 5.3.2 Radiated immunity
v2: 6.1.4 Radiated immunity
```

#### Root cause

The system overweights section number and underweights title, content, and table identity.

#### Accuracy improvement

Use multiple alignment signals:

```text
section number
section title
parent title
table captions
requirement wording
technical terms
numeric patterns
cross-references
embedding similarity
BM25 similarity
```

Do not make section number the only key.

---

### 3.2 Table row alignment errors

#### Symptom

A row in v1 is matched to the wrong row in v2.

Example:

```text
v1 row: 200-400 MHz | 30 V/m
v2 row: 400-800 MHz | 30 V/m
```

#### Root cause

The text is semantically similar, but the frequency band differs.

#### Accuracy improvement

For table rows, prioritize deterministic keys over embeddings:

```text
row_identity_score =
  frequency_range_similarity
+ test_method_similarity
+ condition_similarity
+ row_header_similarity
+ unit_similarity
+ column_schema_similarity
```

For technical tables, embeddings are secondary. Exact structured facts matter more.

A good row identity should include:

```text
section path
table caption
row header
frequency range
condition
test method
unit-bearing columns
footnote references
```

---

### 3.3 Numeric changes hidden in text

#### Symptom

The LLM says wording is similar, but misses:

```text
30 V/m -> 60 V/m
150 kHz-30 MHz -> 150 kHz-108 MHz
Class B -> Class A
```

#### Root cause

The system treats technical values as plain text.

#### Accuracy improvement

Extract normalized facts before comparison:

```json
{
  "quantity_type": "field_strength",
  "old_value": 30,
  "new_value": 60,
  "unit": "V/m",
  "change_direction": "increased",
  "ratio": 2.0
}
```

Normalize:

```text
kHz, MHz, GHz
V/m
dBµV
dBµA
dBm
ms, µs, ns
%, °C
```

Then let the LLM explain the already-detected numeric delta.

---

### 3.4 False semantic matches

#### Symptom

The system aligns two clauses that sound similar but are legally or technically different.

Example:

```text
v1: shall be tested according to ISO 11452-2
v2: shall be tested according to ISO 11452-4
```

Embedding similarity may be high, but the reference changed.

#### Accuracy improvement

Use hybrid search plus reranking and structured validators.

Add validators after retrieval:

```text
Reject match if:
  standard reference differs significantly
  frequency range incompatible
  table schema incompatible
  normative subject incompatible
  section family incompatible
```

Use the LLM only after deterministic and retrieval-based alignment have produced a candidate pair.

---

### 3.5 Added/removed vs moved/modified confusion

#### Symptom

The report says:

```text
Requirement removed from v1
Requirement added in v2
```

but the actual change is:

```text
Requirement moved and slightly modified
```

#### Accuracy improvement

Use a four-pass comparison:

```text
Pass 1: exact ID / exact section / exact table match
Pass 2: section-title and table-caption match
Pass 3: semantic + lexical candidate retrieval
Pass 4: moved/split/merged detection
```

Change status should be one of:

```text
unchanged
modified
added
removed
moved
moved_and_modified
split
merged
uncertain
```

Do not collapse everything into added/removed/changed.

---

### 3.6 Split and merged requirements

#### Symptom

v1 has one long paragraph; v2 splits it into bullets.

```text
v1:
The DUT shall meet A, B, and C.

v2:
The DUT shall:
a) meet A;
b) meet B;
c) meet C.
```

A naive system may report one removal and three additions.

#### Accuracy improvement

Use many-to-one and one-to-many alignment:

```text
one v1 requirement -> multiple v2 requirements
multiple v1 requirements -> one v2 requirement
```

Compare normalized atomic obligations:

```json
[
  {"subject": "DUT", "obligation": "meet", "object": "A"},
  {"subject": "DUT", "obligation": "meet", "object": "B"},
  {"subject": "DUT", "obligation": "meet", "object": "C"}
]
```

This is especially important in compliance documents because formatting often changes without substantive requirement changes.

---

## 4. LLM-related challenges

### 4.1 Hallucinated explanations

#### Symptom

The model says:

```text
This change increases test severity and may require retesting.
```

even when the evidence only shows editorial wording.

#### Root cause

The model is asked to infer too much from too little context.

#### Accuracy improvement

Use evidence-pack prompting:

```text
The model receives only:
  old extracted object
  new extracted object
  machine-detected delta
  citations
  allowed change types
```

Require structured JSON:

```json
{
  "summary": "...",
  "impact": "...",
  "change_type": "...",
  "citations_used": ["v1:e1", "v2:e2"],
  "unsupported_claims": []
}
```

Reject output if:

```text
citation IDs are missing
language does not match document language
change_type is not in enum
summary mentions facts not in evidence pack
JSON schema validation fails
```

---

### 4.2 Output language drift

#### Symptom

German input produces English explanations, or French input gets mixed English/French output.

#### Accuracy improvement

Pass language explicitly:

```text
The source documents are German.
Write all user-facing fields in German.
Do not translate quoted evidence.
Return JSON only.
```

Validate output language after generation. If output language does not match the source document, regenerate once with a stricter prompt.

---

### 4.3 Context window misuse

#### Symptom

The system sends hundreds of pages into the LLM and gets slow, expensive, inconsistent answers.

#### Root cause

The LLM is being used as a document diff engine.

#### Accuracy improvement

Only send curated evidence packs.

Bad:

```text
Send entire v1 PDF + entire v2 PDF to LLM.
Ask: What changed?
```

Good:

```text
Use extraction + alignment + deterministic diff.
Send one change candidate at a time.
Ask LLM to explain the detected change with citations.
```

For local deployments, parallelism and context length need careful tuning because required memory can scale with the number of parallel requests and context length.

---

## 5. Debugging plan

### 5.1 Build a debug artifact for every comparison

Every ingestion and comparison job should produce a debug bundle:

```text
debug/
  input/
    v1.pdf
    v2.pdf
    file_hashes.json

  extraction/
    v1_docling.json
    v1_opendataloader.json
    v1_blocks.json
    v1_tables.json
    v1_requirements.json
    v2_docling.json
    v2_opendataloader.json
    v2_blocks.json
    v2_tables.json
    v2_requirements.json

  normalization/
    v1_normalized_facts.json
    v2_normalized_facts.json
    unit_parse_errors.json
    language_detection.json

  alignment/
    section_candidates.json
    requirement_candidates.json
    table_candidates.json
    row_candidates.json
    rejected_matches.json

  diff/
    raw_deltas.json
    final_changes.json
    uncertain_changes.json

  llm/
    prompts.jsonl
    responses.jsonl
    validation_failures.json

  visual/
    page_0042_blocks.png
    page_0042_tables.png
    page_0042_citations.png
```

This makes every wrong answer traceable.

---

### 5.2 Add reason codes everywhere

Never store only:

```json
{
  "matched_to": "REQ-v2-0081",
  "confidence": 0.87
}
```

Store:

```json
{
  "matched_to": "REQ-v2-0081",
  "confidence": 0.87,
  "reason_codes": [
    "same_table_caption",
    "same_frequency_range",
    "same_normative_subject",
    "high_bm25_score",
    "high_embedding_score"
  ],
  "negative_signals": [
    "section_number_changed"
  ]
}
```

This is essential for debugging alignment.

---

### 5.3 Add visual overlays

For every extracted object, generate a page overlay:

```text
blue boxes: text blocks
green boxes: tables
orange boxes: requirements
red boxes: low-confidence objects
purple boxes: citations used in final answer
```

This quickly answers:

```text
Did extraction fail?
Did hierarchy fail?
Did comparison fail?
Did the LLM explain badly?
```

Without visual overlays, debugging table-heavy PDFs becomes guesswork.

---

### 5.4 Keep extractor disagreement reports

Run multiple extractors on the same page and compare:

```text
Docling found 4 tables.
pdfplumber found 5 tables.
OpenDataLoader found 4 tables.

Docling row count for Table 12: 31
pdfplumber row count for Table 12: 34
```

Flag disagreement:

```json
{
  "page": 42,
  "issue": "extractor_disagreement",
  "severity": "medium",
  "details": {
    "docling_tables": 4,
    "pdfplumber_tables": 5
  }
}
```

Extractor disagreement is not a failure by itself. It is a signal that the page may need review or alternative extraction logic.

---

## 6. Accuracy improvement plan

### Phase 1: Establish measurable baselines

Before tuning models, create a gold dataset.

Minimum baseline set:

```text
20 English PDF pairs
20 German PDF pairs
20 French PDF pairs
10 scanned or OCR-heavy PDF pairs
20 table-heavy PDF pairs
10 multi-page table PDF pairs
10 documents with section renumbering
10 documents with split/merged clauses
```

For each pair, manually label:

```text
sections
tables
table rows
requirements
changed requirements
added requirements
removed requirements
numeric deltas
normative-strength deltas
citations
```

Track metrics separately. Do not use one generic accuracy score.

Recommended metrics:

```text
section detection F1
table detection F1
table cell accuracy
multi-page table stitching accuracy
requirement extraction precision/recall
requirement alignment precision/recall
numeric delta precision/recall
normative change precision/recall
citation accuracy
LLM explanation factuality
language correctness
human-review rate
```

---

### Phase 2: Improve ingestion accuracy

#### 2.1 Use extractor ensembles

Do not trust one extractor blindly.

Recommended flow:

```text
Primary extraction:
  Docling

Secondary validation:
  OpenDataLoader or pdfplumber/PyMuPDF

For tables:
  compare table count, row count, column count, header names, cell density

For text:
  compare block count, reading order, repeated headers/footers

For coordinates:
  validate citations against page rendering
```

Use each tool for its strength rather than treating all extractors as interchangeable.

#### 2.2 Build document-family profiles

Compliance standards often follow repeatable templates.

Create per-family profiles:

```json
{
  "family": "OEM_EMC_STANDARD_X",
  "heading_patterns": [
    "^\\d+(\\.\\d+)*\\s+.+$",
    "^Annex\\s+[A-Z]"
  ],
  "table_caption_patterns": [
    "^Table\\s+\\d+",
    "^Tabelle\\s+\\d+",
    "^Tableau\\s+\\d+"
  ],
  "footer_patterns": [
    "Confidential",
    "Page \\d+ of \\d+"
  ],
  "known_units": ["MHz", "GHz", "V/m", "dBµV", "dBm"],
  "normative_language": "de"
}
```

This improves accuracy much more than blindly changing LLM prompts.

#### 2.3 Use quality gates

After ingestion, classify document readiness:

```text
green: safe for automated comparison
yellow: compare but mark uncertain changes
red: require human review before comparison
```

Example gates:

```text
OCR confidence below threshold -> yellow/red
table extraction disagreement high -> yellow
missing table captions -> yellow
section hierarchy confidence low -> yellow
too many empty table cells -> red
language mismatch -> block comparison
```

---

### Phase 3: Improve comparison accuracy

#### 3.1 Use deterministic comparison for technical facts

The following should not be left to the LLM:

```text
numeric values
units
frequency ranges
test levels
class names
requirement IDs
section numbers
standard references
table row identity
added/removed rows
changed cells
```

The LLM should explain these deltas, not discover them.

#### 3.2 Use hybrid retrieval, not embeddings only

Embeddings are good for semantic similarity, but compliance comparison also needs exact matches on terms like:

```text
ISO 11452-4
CISPR 25
BCI
ALSE
150 kHz
30 MHz
dBµV
Class A
```

Hybrid retrieval combines semantic and lexical matching and should be used before reranking and validation.

#### 3.3 Add reranking

Use retrieval in two steps:

```text
Step 1: retrieve top 50 candidates using hybrid search
Step 2: rerank top 50 using multilingual reranker
Step 3: apply deterministic validators
Step 4: accept, reject, or mark uncertain
```

This reduces false matches.

#### 3.4 Add contradiction checks

For every accepted match, run validators:

```text
same language?
compatible section family?
compatible object type?
same or similar table caption?
compatible units?
compatible frequency range?
same normative subject?
not a known forbidden match?
```

Example forbidden match:

```text
Do not align ISO 11452-2 with ISO 11452-4 solely because wording is similar.
```

---

## 7. Production limitations to acknowledge

### 7.1 Fully automatic comparison will not be perfect

Some documents will require human review, especially:

```text
bad scans
rotated tables
multi-page merged tables
ambiguous row labels
complex annexes
bilingual pages
missing captions
poor OCR
unusual formatting
legal-style exception clauses
```

The product should be designed as:

```text
high-confidence automated comparison
+ human review workflow
+ audit trail
```

not as an unsupervised authority.

---

### 7.2 Source PDF quality controls the ceiling

A clean, digitally generated PDF may support high-accuracy extraction.

A scanned PDF with skewed tables and low resolution may not.

Production should display ingestion quality:

```text
Document extraction quality: 87%
Table extraction quality: 76%
OCR confidence: 92%
Comparison reliability: medium
```

This builds trust and prevents overclaiming.

---

### 7.3 Local hardware limits concurrency

With 5 local users, the web app can support concurrent sessions, but LLM generation should be queued and controlled.

Recommended production controls:

```text
max concurrent PDF extraction jobs: 2-4
max concurrent OCR jobs: 1-2
max concurrent embedding jobs: 1
max concurrent LLM jobs: 1-2
max queued comparison jobs: configurable
max context per LLM request: 8k-16k initially
```

---

## 8. Development-time debugging checklist

Use this when a comparison result is wrong.

### 8.1 First question: extraction or comparison?

Check:

```text
Is the source text/table extracted correctly?
Are page and bbox citations correct?
Is the section hierarchy correct?
Is the table stitched correctly?
Are rows/cells correct?
Are units normalized correctly?
```

If no, fix ingestion.

If yes, continue.

---

### 8.2 Second question: alignment or diff?

Check:

```text
Was the v1 object matched to the correct v2 object?
What were the top 10 candidates?
Why was the selected candidate chosen?
Were better candidates rejected?
Did validators reject/accept correctly?
```

If alignment is wrong, fix retrieval, reranking, and validators.

If alignment is correct, continue.

---

### 8.3 Third question: deterministic delta or LLM explanation?

Check:

```text
Was the machine delta correct?
Did the LLM add unsupported claims?
Did the LLM ignore a numeric change?
Did the LLM write in the wrong language?
Did the output schema validation catch the issue?
```

If machine delta is wrong, fix comparison rules.

If machine delta is right but explanation is wrong, fix prompt, schema, and validation.

---

## 9. Recommended observability fields

Every job should emit structured events:

```json
{
  "job_id": "cmp_2026_000123",
  "stage": "table_alignment",
  "document_id": "doc_v2",
  "page": 42,
  "object_id": "TBL-v2-0042-01",
  "status": "warning",
  "message": "Low table stitching confidence",
  "metrics": {
    "caption_similarity": 0.91,
    "column_header_similarity": 0.77,
    "row_count_delta": 4,
    "stitch_score": 0.68
  }
}
```

Use these event categories:

```text
extraction_started
extraction_completed
language_detected
ocr_completed
section_hierarchy_built
table_extracted
table_stitched
requirement_extracted
embedding_created
alignment_candidate_generated
alignment_candidate_rejected
change_detected
evidence_pack_built
llm_generation_started
llm_generation_validated
report_completed
human_review_required
```

---

## 10. Accuracy improvement backlog

### Highest priority

```text
1. Visual extraction debugger
2. Gold dataset and evaluation harness
3. Table quality scoring
4. Multi-page table stitching
5. Unit and numeric normalization
6. Language-specific normative dictionaries
7. Hybrid retrieval with reranking
8. Citation bbox validation
9. LLM JSON schema validation
10. Human review workflow
```

### Medium priority

```text
1. Document-family templates
2. OCR correction rules
3. Active learning from reviewer corrections
4. Split/merge requirement detection
5. Section move detection
6. Table footnote linking
7. Confidence calibration
8. Regression test dashboard
```

### Later priority

```text
1. Fine-tuned extraction model
2. Fine-tuned reranker on compliance pairs
3. Cross-language comparison mode
4. Domain-specific ontology for EMC concepts
5. Automatic test-case impact analysis
```

---

## 11. Human review loop

A production GRC auditor should learn from corrections.

When a reviewer fixes:

```text
wrong table boundary
wrong section heading
wrong requirement alignment
wrong change classification
wrong citation
```

store it as training/evaluation data:

```json
{
  "correction_type": "wrong_alignment",
  "old_match": "REQ-v1-0021 -> REQ-v2-0099",
  "correct_match": "REQ-v1-0021 -> REQ-v2-0023",
  "reason": "same frequency range and same table caption",
  "reviewer": "user_17",
  "timestamp": "2026-05-11T10:15:00Z"
}
```

Use these corrections to improve:

```text
rules
document-family profiles
evaluation datasets
alignment thresholds
reranker training data
prompt examples
```

---

## 12. Practical target quality gates

Use thresholds like these at first, then tune with real data.

```text
Document language confidence:
  >= 0.90 pass
  0.75-0.90 warn
  < 0.75 review

Section hierarchy confidence:
  >= 0.85 pass
  0.70-0.85 warn
  < 0.70 review

Table extraction confidence:
  >= 0.85 pass
  0.65-0.85 warn
  < 0.65 review

Requirement alignment confidence:
  >= 0.88 auto-accept
  0.70-0.88 uncertain
  < 0.70 no match

Numeric delta confidence:
  >= 0.95 auto-report
  0.80-0.95 report with warning
  < 0.80 review

Citation bbox confidence:
  >= 0.90 pass
  < 0.90 review
```

The exact values should be calibrated from the gold dataset.

---

## 13. Known hard cases catalog

Keep this catalog in the repo and add examples as they appear.

```text
CASE-001: Borderless table misread as paragraphs
CASE-002: Multi-page table with repeated header row
CASE-003: Table row split across page break
CASE-004: Footnote changes applicability of limit
CASE-005: Section renumbered but content unchanged
CASE-006: Requirement split into bullets
CASE-007: Requirement merged into paragraph
CASE-008: German normative term changed from sollte to muss
CASE-009: French negative obligation ne doit pas missed
CASE-010: OCR reads 30 as 3O
CASE-011: Unit µV extracted as uV
CASE-012: Section footer indexed as requirement
CASE-013: Annex table compared against main-body table
CASE-014: Same table caption but different scope
CASE-015: Same wording but changed referenced standard
```

Each case should include:

```text
PDF page image
expected extraction
actual failed extraction
expected comparison result
actual failed comparison result
fix
regression test
```

---

## 14. Core limitation and design response

The core limitation is that an offline GenAI GRC Auditor cannot rely on an LLM to “understand two PDFs and tell what changed” with audit-grade reliability.

The design response is:

```text
Use deterministic extraction, normalization, alignment, and diff wherever possible.

Use embeddings and rerankers to find candidate matches.

Use the LLM only to explain already-grounded, cited evidence.

Use confidence scores and human review for uncertainty.

Use visual debugging and regression tests to improve over time.
```

That is the difference between a demo and an auditor-grade local compliance comparison system.

---

## 15. Implementation checklist

Use this checklist when turning the plan into implementation tasks.

### Ingestion checklist

- [ ] Store original PDF and SHA-256 hash.
- [ ] Extract page images for visual debugging.
- [ ] Extract text blocks with coordinates.
- [ ] Extract tables with cell coordinates.
- [ ] Detect language at document, page, section, and block level.
- [ ] Remove or suppress repeated headers and footers from semantic comparison.
- [ ] Reconstruct heading hierarchy.
- [ ] Stitch multi-page tables.
- [ ] Normalize units, ranges, symbols, and OCR confusions.
- [ ] Extract requirement candidates.
- [ ] Assign confidence and reason codes.
- [ ] Persist CIR objects.

### Comparison checklist

- [ ] Enforce same-language comparison.
- [ ] Align sections using multiple signals.
- [ ] Align tables using caption, schema, section, and page context.
- [ ] Align rows using deterministic technical keys first.
- [ ] Extract normalized numeric and unit-bearing facts.
- [ ] Detect added, removed, modified, moved, split, and merged requirements.
- [ ] Build evidence packs with citations and bounding boxes.
- [ ] Use LLM only for cited explanation.
- [ ] Validate output JSON schema.
- [ ] Validate output language.
- [ ] Flag low-confidence changes for review.

### Debugging checklist

- [ ] Generate per-page visual overlays.
- [ ] Persist top candidate matches and rejected matches.
- [ ] Persist reason codes and negative signals.
- [ ] Persist prompts and responses.
- [ ] Persist validator failures.
- [ ] Produce a job-level debug bundle.

### Production checklist

- [ ] Queue heavy jobs.
- [ ] Limit concurrent LLM requests.
- [ ] Limit OCR concurrency.
- [ ] Add audit logs.
- [ ] Add role-based access.
- [ ] Add model and prompt version tracking.
- [ ] Add reproducibility metadata.
- [ ] Add human review workflow.
- [ ] Add evaluation dashboard.
- [ ] Add backup and restore procedure.

---

## References

These are useful references for implementation research and tool validation:

- Docling documentation: <https://docling-project.github.io/docling/>
- PyMuPDF text extraction appendix: <https://pymupdf.readthedocs.io/en/latest/app1.html>
- pdfplumber repository: <https://github.com/jsvine/pdfplumber>
- Tesseract language data documentation: <https://tesseract-ocr.github.io/tessdoc/Data-Files.html>
- Qwen3 Embedding repository: <https://github.com/QwenLM/Qwen3-Embedding>
- Weaviate hybrid search documentation: <https://docs.weaviate.io/weaviate/search/hybrid>
- Qdrant hybrid queries documentation: <https://qdrant.tech/documentation/search/hybrid-queries/>
- Ollama FAQ: <https://docs.ollama.com/faq>
