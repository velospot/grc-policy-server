# Phase-Wise Implementation Plan  
## Compliance Standards Comparison AI System

**Goal:** Build a local, traceable, tester-oriented compliance intelligence engine for comparing revisions of TL, DIN, DNV/DNVGL, EMC, environmental, safety, and supplier validation documents.

The system must compare **structured compliance atoms**, not raw PDFs.

---

# 0. Core Architecture Principles

## 0.1 System Philosophy

The system should behave like a:

> **Compliance intelligence engine**

Not like a:

> Chatbot over PDFs

The output must help testers understand:

- what changed
- why it matters
- whether retesting is likely
- what action is needed
- how reliable the conclusion is

---

## 0.2 KISS Principles

Keep the architecture simple and auditable.

### Do

- Compare normalized records.
- Persist every intermediate result.
- Use deterministic comparison before AI reasoning.
- Use LLMs only where interpretation is needed.
- Treat tables as first-class data objects.
- Provide evidence for every conclusion.
- Flag uncertainty instead of hiding it.

### Do Not

- Compare PDFs directly.
- Trust OCR or extraction blindly.
- Use embeddings as the source of truth.
- Let LLMs perform numeric diffing.
- Treat TOC entries as requirements.
- Output uncertain changes as facts.

---

# 1. Target Capabilities

The system must ingest and compare:

- TL automotive standards
- DIN standards
- DNV / DNVGL standards
- EMC test procedures
- environmental compliance specifications
- safety compliance specifications
- supplier validation documents

The system must produce:

- tester-friendly change intelligence
- structured change records
- evidence-backed reports
- confidence and review flags
- no-change coverage summaries
- long-running local processing jobs

---

# 2. High-Level Pipeline

```text
Document Upload
↓
Document Classification
↓
Docling Extraction
↓
Structure Repair
↓
Front Matter Filtering
↓
Hierarchical Chunking
↓
Table Normalization
↓
Compliance Atom Extraction
↓
Persistence
↓
Candidate Matching
↓
Layered Comparison
↓
Change Classification
↓
Impact Reasoning
↓
Tester-Facing Report Generation
↓
No-Change Coverage Report
```

---

# 3. Phase 1 — Foundation & Local Runtime

## Objective

Set up the local runtime, storage, job model, and basic document ingestion.

## Deliverables

- local processing environment
- document upload service
- job queue
- persistent database
- raw file storage
- logging and audit trail

## Hardware Assumption

Target machine:

```text
CPU: 24 cores
RAM: 64 GB
GPU: NVIDIA RTX 5070 Ti 16 GB
```

Shared GPU usage:

```text
Docling: ~3–4 GB VRAM
Available for LLM: ~12–13 GB VRAM
```

## Recommended Local LLM

```text
Model: Qwen3.5 / Qwen3.6 9B Instruct
Quantization: Q4_K_M
Context: 16k
```

## Recommended Inference Settings

```bash
--ctx-size 16384
--parallel 1
--n-gpu-layers 99
--flash-attn on
--cache-type-k q8_0
--cache-type-v q8_0
--batch-size 512
--ubatch-size 256
```

## GPU Scheduling Rule

Avoid simultaneous heavy GPU tasks.

Recommended order:

```text
1. Docling extraction
2. Structure repair
3. Atom extraction
4. Embeddings
5. Comparison
6. Reporting
```

Do not run at the same time:

```text
Docling GPU OCR + LLM inference + large embedding batches
```

---

# 4. Phase 2 — Document Classification

## Objective

Classify every document before comparison.

## Required Metadata

```json
{
  "standard_family": "TL",
  "standard_id": "TL12345",
  "revision": "2024",
  "issuer": "VW",
  "language": ["de"],
  "title": "...",
  "document_type": "test specification"
}
```

## Relationship Detection

Group documents into comparison candidates.

Examples:

```text
same standard + different revision
→ direct comparison

referenced standard
→ dependency relationship

unrelated standard
→ no comparison
```

## Multilingual Rule

The system allows comparison only when documents are in the same language.

```text
German old revision + German new revision
→ allowed

English old revision + English new revision
→ allowed

German old revision + English new revision
→ unsupported or review-only mode
```

## Agent Responsibility

The Classification Agent must:

- identify standard family
- identify standard ID
- identify revision
- identify issuer
- identify language
- identify document type
- detect possible comparison pairs
- reject or warn on mixed-language comparison

---

# 5. Phase 3 — Docling Extraction

## Objective

Extract document content while preserving evidence.

Docling should extract:

- text
- headings
- hierarchy
- tables
- captions
- bounding boxes
- page numbers

## Critical Warning

Docling hierarchy is not fully reliable.

Known issues:

- merged cells
- broken headings
- page continuation errors
- annex misclassification
- header/footer contamination
- incorrect table boundaries
- missing footnotes

## Rule

Never trust Docling hierarchy directly.

Docling output is raw evidence, not final structure.

---

# 6. Phase 4 — Structure Repair

## Objective

Repair extraction issues before chunking and comparison.

## Required Repair Logic

### Remove repeated headers and footers

```text
same text appearing on many pages
→ classify as header/footer
→ remove from comparison body
```

### Detect headings

Example pattern:

```regex
^\d+(\.\d+)*\s+
```

### Merge page continuations

```text
paragraph starts lowercase after page break
→ possible continuation of previous clause
```

### Repair annexes

Detect whether annexes are:

```text
normative
informative
unknown
```

Only normative annexes are treated as requirement-bearing by default.

---

# 7. Phase 5 — Front Matter Filtering

## Objective

Prevent TOC, preface, and foreword content from becoming fake requirements.

## Exclude from Requirement Comparison

- table of contents
- foreword
- preface
- introduction, unless explicitly normative
- legal notice
- copyright page
- revision history
- index
- bibliography
- list of figures
- list of tables

## Still Extract Metadata From Front Matter

Front matter may contain important metadata:

- revision date
- superseded standard
- applicability date
- scope note
- normative references
- change history

## Rule

```text
front matter = metadata only
body clauses = comparison candidates
```

---

# 8. Phase 6 — Semantic Hierarchical Chunking

## Objective

Chunk documents by meaning and structure, not by raw token count.

## Hierarchy

```text
Standard
→ Chapter
→ Section
→ Clause
→ Requirement
→ Table
→ Table Row
```

## Chunking Rules

- Preserve section path.
- Preserve page number.
- Preserve heading context.
- Preserve table caption.
- Preserve footnotes.
- Preserve annex status.
- Do not split requirement text from its conditions.

---

# 9. Phase 7 — Table Normalization

## Objective

Treat tables as structured compliance data.

## Do Not

Store tables only as markdown.

## Persist

- table
- table caption
- table page range
- header path
- row
- cell
- merged-cell information
- raw row
- normalized row
- footnotes
- source coordinates
- confidence

## Example Normalized Table Row

```json
{
  "test": "Radiated immunity",
  "frequency": "80 MHz - 1 GHz",
  "level_value": 150,
  "unit": "V/m",
  "acceptance": "No malfunction",
  "footnotes": [],
  "table_confidence": 0.86
}
```

## Table Risk Rule

If table extraction confidence is low, report:

```text
Human review required before accepting this change.
```

---

# 10. Phase 8 — Compliance Atom Extraction

## Objective

Extract normalized compliance atoms from repaired structure.

## Atom Types

- requirement
- test condition
- test limit
- acceptance criterion
- reference standard
- duration
- environmental condition
- sample definition
- scope rule
- applicability rule
- exemption
- definition
- table footnote constraint

## Example Atom

```json
{
  "atom_id": "ATOM-000421",
  "type": "test_limit",
  "section": "6.4.2",
  "subject": "Radiated immunity",
  "value": 150,
  "unit": "V/m",
  "condition": "80 MHz - 1 GHz",
  "normativity": "normative",
  "source_page": 42,
  "source_table": "Table 6",
  "confidence": 0.91
}
```

## Normativity Classification

Every atom must include:

```text
normative | informative | example | note | unknown
```

## English Signals

```text
shall
must
is required
should
may
example
note
```

## German Signals

```text
muss
ist zu
darf nicht
soll
kann
Anmerkung
Beispiel
```

## Rule

Tester-facing reports should prioritize normative changes.

Notes, examples, and informative text must not be promoted to requirements without review.

---

# 11. Phase 9 — Persistence Architecture

## Objective

Persist every artifact needed for auditability and reprocessing.

## Primary Storage Options

- PostgreSQL
- SQLite
- DuckDB

## Source of Truth

```text
normalized structured data
```

Not:

```text
embeddings
LLM output alone
raw PDF text
```

## Required Tables

```text
documents
sections
clauses
tables
table_rows
table_cells
footnotes
atoms
references
definitions
comparison_jobs
candidate_matches
change_records
no_change_records
confidence_scores
evidence_links
```

## Persist Everything

Store:

- raw extraction
- repaired structure
- normalized atoms
- table objects
- comparison outputs
- rejected candidates
- confidence values
- evidence references

---

# 12. Phase 10 — Embeddings and Vector Search

## Objective

Use embeddings for semantic support, not primary comparison.

## Recommended Embedding Model

```text
bge-large-en-v1.5
```

## Useful For

- moved clauses
- renumbered sections
- rewritten requirements
- semantic alignment
- retrieval/chat
- Docling repair assistance

## Not Used For

- exact table comparison
- numeric limit comparison
- unit comparison
- deterministic matching

## Rule

Vector search proposes candidates. It does not decide compliance changes.

---

# 13. Phase 11 — Comparison Engine

## Objective

Compare old and new compliance atoms using layered matching.

## Input

```text
old atoms
+
new atoms
```

## Pipeline

```text
candidate matching
↓
alignment
↓
change classification
↓
impact explanation
```

## Layer 1 — Deterministic Matching

Match by:

- same section
- same row key
- same parameter
- same unit
- same reference ID
- same table caption

## Layer 2 — Fuzzy Symbolic Matching

Match by:

- similar heading
- similar section path
- similar table caption
- renumbered clause
- similar requirement key

## Layer 3 — Semantic Matching

Use for:

- moved clauses
- rewritten requirements
- terminology drift
- equivalent phrasing

## Rule

Compliance comparison must be repeatable and auditable.

LLM-only comparison is insufficient.

---

# 14. Phase 12 — Change Classification

## Objective

Convert aligned differences into structured change records.

## Change Categories

- limit_changed
- test_added
- test_removed
- acceptance_changed
- sample_requirement_changed
- referenced_standard_changed
- scope_changed
- applicability_changed
- exemption_changed
- product_class_changed
- definition_changed
- editorial_only
- uncertain

## Example Change Record

```json
{
  "change_id": "CHG-042",
  "change_type": "limit_changed",
  "old_value": "100 V/m",
  "new_value": "150 V/m",
  "tester_action": "Update EMC test level",
  "retest_likely": true,
  "status": "Needs human review",
  "extraction_confidence": 0.82,
  "alignment_confidence": 0.76,
  "change_confidence": 0.91,
  "impact_confidence": 0.64
}
```

## Confidence Types

Track separately:

- extraction confidence
- structure confidence
- table confidence
- alignment confidence
- change confidence
- impact confidence

## Why Separate Scores Matter

The system may be confident that text changed but uncertain whether retesting is required.

---

# 15. Phase 13 — Critical Accuracy Guardrails

## 15.1 Normative vs Informative

AI systems often mistake notes and examples for requirements.

Rule:

```text
Only normative content can directly create tester obligations.
```

## 15.2 Scope and Applicability Changes

Small wording changes can create major test impact.

Example:

```text
Old: applies to exterior components
New: applies to exterior and interior components
```

Classify as:

```text
scope_changed
applicability_changed
product_class_changed
```

## 15.3 Referenced Standard Changes

Track references as first-class atoms.

Example:

```json
{
  "reference": "DIN EN ISO 9227",
  "old_revision": "2017",
  "new_revision": "2022",
  "reference_type": "normative"
}
```

## 15.4 Units and Tolerances

Do not rely on LLMs for unit interpretation.

Watch for:

- 1000 h → 1000 ± 24 h
- 5 % → 5 percentage points
- °C/min → K/min
- RMS → peak
- nominal → minimum

Use deterministic parsers.

## 15.5 Footnotes

Footnotes often modify limits.

Example:

```text
* Applies only for Class B components
```

Persist as linked constraints:

```json
{
  "base_requirement": "REQ-123",
  "constraint": "Applies only to Class B",
  "source": "table footnote"
}
```

## 15.6 Definition Changes

Definition changes can affect many requirements.

Example:

```text
Definition of “outdoor use” changed.
```

Required behavior:

```text
Definition changed
→ identify potentially affected requirements
→ mark downstream impact for review
```

## 15.7 Annex Status

Do not ignore annexes blindly.

Detect:

```text
Annex A normative
Annex B informative
```

Comparison rule:

```text
normative annex → compare as requirements
informative annex → lower priority or metadata only
```

---

# 16. Phase 14 — Human Review Gating

## Objective

Prevent confident but wrong compliance conclusions.

## Required Status Values

Every change record must have one status:

```text
Accepted
Needs human review
Low-confidence extraction
Potentially editorial
Potentially safety-critical
```

## Review Triggers

Require human review when:

- table extraction confidence is low
- merged cells are detected
- clause alignment is uncertain
- requirement may have moved
- footnote affects the requirement
- annex status is unclear
- definition changed
- referenced standard changed
- unit conversion is ambiguous
- semantic match confidence is low

## Rule

Never output uncertain changes as facts.

---

# 17. Phase 15 — Tester-Facing Reporting

## Objective

Generate actionable change intelligence.

## Report Sections

- executive summary
- high-risk changes
- tester action list
- retest likelihood
- uncertain changes
- no-change coverage
- evidence appendix
- extraction quality summary

## Change Card Format

```markdown
## CHG-042 — Salt Spray Duration Changed

Severity: High  
Retest likely: Yes  
Status: Needs human review  

What changed:  
Salt spray duration appears to increase from 96 h to 240 h.

Why it matters:  
Existing environmental validation may no longer be sufficient.

Tester action:  
Verify the table row and update the environmental test plan if confirmed.

Evidence:  
Old: Section 6.2, Table 4  
New: Section 6.2, Table 4  

Review reason:  
Requirement came from a table with merged-cell extraction risk.
```

## Streaming Output

During long jobs, stream incremental status:

```text
CHG-001 generated
CHG-002 generated
CHG-003 requires review
CHG-004 classified as editorial
```

Then produce final consolidated report.

---

# 18. Phase 16 — No-Change Coverage Report

## Objective

Show what was checked and found unchanged.

## Example

```markdown
## No Change Detected

Checked with confidence:

- EMC radiated emissions limits — High
- ESD test levels — Medium
- Salt spray acceptance criteria — High
- Sample quantity requirements — Medium
```

## Why This Matters

Testers need to know whether the system checked key areas, not only what changed.

---

# 19. Phase 17 — Agent Architecture

## Recommended Agents

```text
Upload Agent
Classification Agent
Extraction Agent
Structure Repair Agent
Front Matter Filter Agent
Chunking Agent
Table Normalization Agent
Atom Extraction Agent
Reference Extraction Agent
Definition Extraction Agent
Embedding Agent
Comparison Agent
Change Classification Agent
Impact Reasoning Agent
Report Generation Agent
Review Gate Agent
```

## Agent Contract Rule

Each agent must have:

- explicit input schema
- explicit output schema
- confidence score
- error state
- evidence references
- retry policy

---

# 20. SOLID Principles for This System

## Single Responsibility

Each agent performs one major task.

Bad:

```text
One agent extracts, compares, explains, and reports.
```

Good:

```text
Extraction Agent extracts.
Comparison Agent compares.
Report Agent explains.
```

## Open / Closed

The system should allow new standards and atom types without rewriting the pipeline.

Add via:

- new atom schema
- new parser rule
- new comparison rule
- new report template

## Liskov Substitution

Any extractor must return the minimum required schema.

Different extractors may be swapped if they preserve:

- text
- structure
- page references
- confidence
- evidence

## Interface Segregation

Agents should not receive entire documents when they only need atoms or table rows.

## Dependency Inversion

Business logic must depend on internal normalized schemas, not directly on:

- Docling
- LLM provider
- vector DB
- OCR engine

---

# 21. Recommended Database Model

## documents

```text
id
file_name
standard_family
standard_id
revision
issuer
language
document_type
created_at
```

## sections

```text
id
document_id
section_number
title
section_path
page_start
page_end
front_matter_flag
annex_status
confidence
```

## tables

```text
id
document_id
section_id
caption
page_start
page_end
raw_table
normalized_table
confidence
```

## table_rows

```text
id
table_id
row_index
raw_row
normalized_row
header_path
footnotes
confidence
```

## atoms

```text
id
document_id
section_id
table_id
row_id
type
subject
value
unit
condition
normativity
source_page
confidence
```

## change_records

```text
id
comparison_job_id
change_type
old_atom_id
new_atom_id
old_value
new_value
tester_action
retest_likely
status
extraction_confidence
alignment_confidence
change_confidence
impact_confidence
evidence
```

---

# 22. Implementation Milestones

## Milestone 1 — Ingestion MVP

- upload PDFs
- store raw files
- create document records
- run Docling extraction
- persist raw extracted text

## Milestone 2 — Classification MVP

- detect standard ID
- detect revision
- detect language
- group comparable documents
- block mixed-language comparison

## Milestone 3 — Structure MVP

- remove headers/footers
- detect headings
- detect front matter
- detect basic sections
- preserve page references

## Milestone 4 — Table MVP

- extract tables
- persist rows and cells
- normalize simple rows
- capture captions and footnotes
- assign table confidence

## Milestone 5 — Atom MVP

- extract requirement atoms
- extract test limit atoms
- extract acceptance criteria
- classify normativity
- persist evidence links

## Milestone 6 — Deterministic Comparison MVP

- compare same-section atoms
- compare numeric values
- compare units
- compare table row records
- produce basic change records

## Milestone 7 — Semantic Alignment

- add embeddings
- detect moved clauses
- detect rewritten requirements
- support fuzzy matching
- keep deterministic evidence primary

## Milestone 8 — Impact Reasoning

- generate tester action
- estimate retest likelihood
- classify severity
- separate impact confidence from change confidence

## Milestone 9 — Reporting

- generate change cards
- generate executive summary
- generate no-change coverage
- generate evidence appendix
- flag review items

## Milestone 10 — Hardening

- handle large documents
- support long-running jobs
- retry failed steps
- add audit logs
- regression-test known standards
- benchmark extraction quality

---

# 23. Minimum Viable Product Scope

## MVP Should Support

- same-language comparison
- two revisions of the same standard
- Docling extraction
- structure repair
- front matter filtering
- table normalization
- compliance atom extraction
- deterministic comparison
- basic semantic alignment
- tester-facing report
- human review flags

## MVP Should Not Attempt

- cross-language comparison
- fully automatic legal conclusions
- certification decisions
- unsupported OCR-heavy documents without review
- LLM-only comparison

---

# 24. Acceptance Criteria

The system is acceptable when it can:

- ingest two revisions of a standard
- identify comparable documents
- exclude TOC and front matter from requirement comparison
- extract clauses and tables with page evidence
- normalize compliance atoms
- compare limits deterministically
- detect changed references
- flag uncertain table extraction
- generate tester-facing change cards
- produce no-change coverage
- preserve traceability from report back to source pages

---

# 25. Final System Rule

Every conclusion must be:

```text
structured
traceable
evidence-backed
confidence-scored
review-gated when uncertain
```

The system should optimize for correctness, auditability, and tester usefulness over speed or conversational fluency.
