# 18 - Auditor Grade Acceptance Criteria

## Purpose

This document defines what "auditor grade" means for the offline LLM-assisted GRC Auditor.

Auditor grade does not mean the model is always right. It means the system produces a traceable, reproducible, evidence-grounded change register that an auditor can review, challenge, and defend.

Core acceptance principle:

```text
A change is not auditor-grade unless a reviewer can trace it from report -> evidence ID -> CIR object -> source PDF page and bounding box -> original file hash.
```

---

## 1. Auditor-grade definition

A comparison result is auditor-grade only when it satisfies all of these conditions:

```text
- The source files are hashed and immutable.
- Extraction output is stored as structured CIR, not only chunks.
- Changes are detected by deterministic comparison before LLM explanation.
- Every reported change has source evidence.
- Auditor Mode assigns low, medium, or high severity to every reported change.
- Severity has reason codes and confidence.
- Uncertain or high-impact items enter a human review workflow.
- Report exports include schema version, model version, policy version, and citation manifest.
- The system can reproduce or explain result drift using stored versions.
```

---

## 2. Release gates

Use these gates for a production release.

| Gate | Blocks release when |
|---|---|
| G1 ingestion | PDFs cannot be safely parsed, hashed, and linked to CIR |
| G2 citations | Reported changes lack valid source citations |
| G3 alignment | Equivalent sections/tables/rows are frequently marked as add/remove |
| G4 severity | Medium/high changes are missed or reason codes are absent |
| G5 LLM validation | Model output violates schema, language, or citation rules |
| G6 export | JSON/CSV/HTML/PDF exports are inconsistent or non-reproducible |
| G7 offline | Runtime makes unexpected network calls |
| G8 security | Unauthorized users can access other projects or alter reports |
| G9 evaluation | Gold dataset metrics fall below release threshold |
| G10 review | Human overrides cannot be audited |

---

## 3. Non-negotiable acceptance criteria

### 3.1 Evidence and citations

MUST:

```text
- Every reported change has at least one citation to v1, v2, or both depending on change type.
- Modified changes normally have both v1 and v2 citations.
- Added changes have v2 citations.
- Removed changes have v1 citations.
- Citations include document ID, release label, page number, object ID, and bbox when available.
- Citation IDs used by the LLM must exist in the evidence pack.
- The UI can open the source page and highlight the cited object.
```

Blocking defects:

```text
- Report includes a citation ID that does not exist.
- A high-severity change has no visual evidence.
- The same citation points to a different object after export.
```

---

### 3.2 Extraction acceptance

MUST:

```text
- Detect document language with confidence and store it.
- Preserve page numbers and coordinates.
- Detect section hierarchy sufficiently for comparison.
- Extract tables into rows, columns, cells, and footnotes where possible.
- Store extraction confidence per object.
- Mark low-confidence extraction areas for review.
- Exclude repeated headers and footers from semantic comparison while retaining extraction audit data.
```

SHOULD:

```text
- Reconstruct multi-page tables.
- Distinguish normative body text from informative annexes when the document structure supports it.
- Detect rotated text, side notes, and table continuation captions.
```

Minimum release threshold for the gold dataset:

```text
- No systematic page-number offset errors.
- No known high-severity gold change lost due to extraction failure without a review flag.
- Table extraction failures are surfaced as extraction risk, not silently ignored.
```

---

### 3.3 Alignment acceptance

MUST:

```text
- Align equivalent sections, requirements, tables, rows, and footnotes before diffing.
- Detect moved content separately from removed/added content.
- Represent split and merged requirements explicitly.
- Store alignment score and alignment rationale.
- Mark ambiguous alignments for review.
```

Blocking defects:

```text
- A moved unchanged section is reported as a removed requirement and added requirement without move rationale.
- A table row is matched only by row index when row order changed.
- Ambiguous candidate matches are resolved silently with low confidence.
```

---

### 3.4 Diff and severity acceptance

MUST:

```text
- Detect changes to normative terms, numeric values, units, ranges, applicability, exceptions, references, acceptance criteria, and test methods.
- Assign changeSeverity = low, medium, or high in Auditor Grade Mode.
- Assign severityReasonCodes for every medium and high item.
- Assign severityConfidence.
- Escalate high-impact technical changes using deterministic policy rules before LLM explanation.
- Require review when severity or alignment confidence is below policy threshold.
```

High-severity misses are release blockers unless the item is explicitly marked as uncertain and sent to review.

---

### 3.5 LLM acceptance

MUST:

```text
- Use local model inference only.
- Use evidence packs, not full unbounded PDFs.
- Treat source text as untrusted data.
- Return valid JSON for structured fields.
- Use only allowed citation IDs.
- Write user-facing fields in the source document language.
- Avoid unsupported legal or compliance conclusions.
```

The LLM may:

```text
- Explain a detected change.
- Summarize audit impact based on provided evidence.
- Suggest human review when evidence is ambiguous.
- Challenge a severity candidate only through a structured field.
```

The LLM must not:

```text
- Invent changes.
- Invent citations.
- Hide deterministic high-severity changes.
- Override policy without validator and audit workflow.
- Use internet knowledge at runtime.
```

---

### 3.6 Report acceptance

MUST:

```text
- Export canonical JSON.
- Include report_schema_version.
- Include source document hashes.
- Include parser, comparison, severity policy, ontology, prompt, and model versions.
- Include report generation settings.
- Include summary counts by severity and review status.
- Include validation status.
- Include a manifest hash over export contents.
```

SHOULD:

```text
- Export CSV for change-register workflows.
- Export HTML or PDF for human review packages.
- Include appendices for hidden low-severity changes if Auditor Mode configuration requires it.
```

---

## 4. Human review acceptance

A reviewer must be able to:

```text
- Open side-by-side source evidence.
- Confirm, dispute, or override severity.
- Add a reviewer note.
- Mark a change as accepted, rejected, duplicate, extraction artifact, or needs escalation.
- See the machine recommendation and reason codes.
- Preserve an audit trail for every override.
```

Required review dispositions:

```text
pending_review
accepted
accepted_with_override
rejected_extraction_artifact
rejected_duplicate
needs_subject_matter_expert
closed
```

Required override object:

```json
{
  "overrideId": "OVR-00017",
  "changeId": "CHG-00017",
  "actorUserId": "usr_003",
  "timestamp": "2026-05-11T12:00:00Z",
  "field": "changeSeverity",
  "oldValue": "medium",
  "newValue": "high",
  "reason": "Footnote changes applicability for all HV components.",
  "supportingEvidenceIds": ["EV-v2-0047-note-a"]
}
```

---

## 5. Offline acceptance

MUST:

```text
- Disable runtime calls to cloud LLMs, remote OCR, remote embeddings, telemetry, and external search.
- Pin all models and dependencies in the offline registry.
- Log any attempted network access by model-serving, extraction, or worker processes.
- Support installation and update from a verified offline bundle.
```

Blocking defects:

```text
- The application downloads a model or tokenizer during normal operation.
- A dependency manager reaches the internet in production mode.
- A report depends on a remote service response.
```

---

## 6. Reproducibility acceptance

A completed comparison must store:

```text
- source file sha256 values
- CIR object hashes
- extractor name and version
- OCR engine and language pack versions
- normalization version
- alignment algorithm version
- comparison algorithm version
- severity policy version and hash
- ontology version and hash
- prompt template version and hash
- model registry ID and model file hashes
- decoding parameters
- report schema version
```

Re-run policy:

```text
same inputs + same versions + same settings -> same structured diff and severity result
```

LLM text may vary slightly if deterministic decoding is not guaranteed, but the report must expose this and preserve the original generated output.

---

## 7. Multilingual acceptance

MUST:

```text
- Compare documents in the source language.
- Avoid translating source documents before comparison.
- Keep internal enum values language-neutral.
- Provide user-facing explanations in the document language.
- Preserve original source quotes exactly.
- Use language-specific normative term dictionaries.
```

Blocking defects:

```text
- German source documents produce English user-facing summaries in final report.
- A translated quote is presented as if it were original source text.
- Normative term strength is classified using English-only rules on German or French documents.
```

---

## 8. Performance and capacity acceptance

Default target environment:

```text
- i9-class CPU
- Nvidia GPU with 16 GB VRAM
- 64 GB RAM
- At least five local web users
- Heavy comparison jobs queued
```

MUST:

```text
- Keep UI responsive while extraction/comparison jobs run.
- Queue heavy jobs rather than blocking API workers.
- Show job state and failure reason.
- Enforce per-job timeouts and memory limits.
```

SHOULD measure:

```text
- extraction time per page
- table reconstruction time per table
- alignment time per object count
- LLM explanation time per change
- report export time
- peak memory and VRAM
```

---

## 9. Evaluation thresholds

Set thresholds per document family, because PDF quality differs.

Recommended initial gates for a controlled gold dataset:

```text
- 100% schema-valid report exports
- 100% valid citation IDs for reported changes
- 0 known high-severity gold changes silently missed
- High-severity recall target: >= 0.95
- Medium/high combined recall target: >= 0.90
- Citation page accuracy target: >= 0.98 for reported changes
- Severity exact-match target: >= 0.85 after excluding adjudicated ambiguous cases
- Human-review flag recall: >= 0.95 for intentionally ambiguous cases
```

These thresholds are starting gates. Tighten them as the gold dataset grows.

---

## 10. Auditor-grade definition of done

A feature that affects comparison output is done only when:

```text
[ ] CIR schema changes are documented.
[ ] Report schema changes are versioned.
[ ] Severity policy impact is tested.
[ ] Gold dataset expected outputs are updated.
[ ] Citation validation still passes.
[ ] Prompt changes are versioned and hashed.
[ ] Human review workflow still preserves overrides.
[ ] Offline registry entries are updated where needed.
[ ] All new warnings are visible in the UI or report.
```
