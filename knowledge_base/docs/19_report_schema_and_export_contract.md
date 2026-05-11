# 19 - Report Schema and Export Contract

## Purpose

This document defines the canonical export contract for comparison reports produced by the offline GRC Auditor.

The canonical report format is JSON. Other exports, such as CSV, HTML, PDF, or spreadsheet files, must be derived from the canonical JSON and must not introduce new facts.

Core principle:

```text
The report is an audit artifact, not a UI screenshot.
```

---

## 1. Export format hierarchy

Recommended hierarchy:

```text
Canonical JSON report
  -> CSV change register
  -> HTML review package
  -> PDF review package
  -> optional spreadsheet workbook
```

Only the canonical JSON should be treated as the source of truth. Human-friendly exports must include the canonical report hash or manifest hash.

---

## 2. Versioning policy

Every exported report must include:

```json
{
  "reportSchemaVersion": "1.0.0",
  "reportContract": "grc_auditor_offline.comparison_report",
  "generator": {
    "name": "grc-auditor-offline",
    "version": "0.1.0"
  }
}
```

Schema versioning rules:

```text
- Patch version: documentation-only or non-breaking validation clarification.
- Minor version: additive fields, new optional enum values, new export metadata.
- Major version: field removal, field rename, changed meaning, incompatible enum behavior.
```

Reports must remain readable after application upgrades. If a report is migrated, the original report must be preserved or its hash must remain verifiable.

---

## 3. Canonical report object

Top-level report schema:

```json
{
  "reportId": "RPT-2026-000001",
  "reportSchemaVersion": "1.0.0",
  "comparisonId": "CMP-2026-000001",
  "projectId": "PRJ-001",
  "generatedAt": "2026-05-11T12:00:00Z",
  "generatedBy": {
    "userId": "usr_001",
    "role": "auditor"
  },
  "comparisonMode": "auditor_grade",
  "sourceLanguage": "en",
  "inputDocuments": [],
  "pipelineVersions": {},
  "settings": {},
  "summary": {},
  "changes": [],
  "hiddenDiffSummary": {},
  "validation": {},
  "reviewState": {},
  "exportManifest": {}
}
```

---

## 4. Input document object

```json
{
  "documentId": "doc-v1",
  "releaseLabel": "v1",
  "filename": "OEM_EMC_v1.pdf",
  "sha256": "...",
  "sizeBytes": 12345678,
  "pageCount": 120,
  "language": {
    "dominant": "en",
    "confidence": 0.98
  },
  "documentFamilyProfileId": "automotive_emc_oem_generic@1.0.0",
  "cirHash": "...",
  "ingestedAt": "2026-05-11T11:30:00Z"
}
```

The report must not rely on filename alone. File hash and document ID are required.

---

## 5. Pipeline versions object

```json
{
  "extractor": {
    "name": "docling",
    "version": "pinned-version",
    "configHash": "..."
  },
  "ocr": {
    "name": "tesseract",
    "version": "pinned-version",
    "languagePacks": ["eng", "deu", "fra"]
  },
  "normalization": {
    "version": "1.0.0",
    "configHash": "..."
  },
  "alignment": {
    "version": "1.0.0",
    "configHash": "..."
  },
  "comparison": {
    "version": "1.0.0",
    "configHash": "..."
  },
  "severityPolicy": {
    "policyId": "automotive_emc_default",
    "version": "1.0.0",
    "sha256": "..."
  },
  "ontology": {
    "ontologyId": "automotive_emc",
    "version": "1.0.0",
    "sha256": "..."
  },
  "promptTemplates": [
    {
      "promptId": "auditor_change_explanation",
      "version": "1.0.0",
      "sha256": "..."
    }
  ],
  "models": [
    {
      "registryId": "llm.granite-8b-instruct.q4@local",
      "usage": "change_explanation",
      "sha256": "..."
    }
  ]
}
```

---

## 6. Summary object

```json
{
  "totalReportedChanges": 52,
  "bySeverity": {
    "high": 8,
    "medium": 17,
    "low": 27
  },
  "byChangeType": {
    "numeric_threshold_changed": 4,
    "table_row_added": 5
  },
  "requiresHumanReview": 11,
  "accepted": 0,
  "pendingReview": 52,
  "ignoredArtifacts": 43,
  "hiddenLowSeverity": 148
}
```

Summary values must be derived from the `changes` array and hidden-diff records. Do not allow the LLM to create summary counts.

---

## 7. Change object

Required fields for Auditor Grade Mode:

```json
{
  "changeId": "CHG-000017",
  "changeType": "numeric_threshold_changed",
  "objectType": "table_cell",
  "semanticImpact": "technical",
  "changeSeverity": "high",
  "severityConfidence": 0.96,
  "severityReasonCodes": [
    "TEST_LEVEL_CHANGED",
    "NUMERIC_LIMIT_CHANGED",
    "EVIDENCE_MAY_BE_INVALIDATED"
  ],
  "alignment": {
    "mappingType": "one_to_one",
    "alignmentScore": 0.94,
    "v1ObjectIds": ["cell-v1-0042-12-03"],
    "v2ObjectIds": ["cell-v2-0047-12-03"]
  },
  "sectionPathV1": ["5", "5.3", "5.3.2"],
  "sectionPathV2": ["5", "5.3", "5.3.2"],
  "oldValue": "30 V/m",
  "newValue": "60 V/m",
  "normalizedOldFacts": [
    {"type": "field_strength", "value": 30, "unit": "V/m"}
  ],
  "normalizedNewFacts": [
    {"type": "field_strength", "value": 60, "unit": "V/m"}
  ],
  "title": "Radiated immunity test level changed",
  "summary": "The required test level changed from 30 V/m to 60 V/m.",
  "auditImpact": "Existing evidence at 30 V/m may not be sufficient for this frequency band.",
  "citations": {
    "v1": [],
    "v2": []
  },
  "llm": {
    "promptId": "auditor_change_explanation",
    "promptVersion": "1.0.0",
    "modelRegistryId": "llm.granite-8b-instruct.q4@local",
    "outputHash": "...",
    "validationStatus": "passed"
  },
  "review": {
    "requiresHumanReview": false,
    "disposition": "pending_review"
  }
}
```

Simple Mode may omit audit-only fields from the user-facing view, but the canonical report should retain internal traceability where available.

---

## 8. Citation object

```json
{
  "evidenceId": "EV-v2-0047-row-12-cell-03",
  "documentId": "doc-v2",
  "releaseLabel": "v2",
  "sourceFileSha256": "...",
  "page": 47,
  "objectType": "table_cell",
  "objectId": "cell-v2-0047-12-03",
  "sectionNumber": "5.3.2",
  "sectionTitle": "Radiated immunity test levels",
  "tableId": "TBL-v2-0047-01",
  "tableLabel": "Table 12",
  "rowKey": "200-400 MHz | AM 80%",
  "columnKey": "Field strength",
  "bbox": [70, 218, 522, 268],
  "quote": "200-400 MHz | 60 V/m | AM 80% | Class A",
  "confidence": 0.89
}
```

Citation validation rules:

```text
- evidenceId must exist in the comparison evidence store.
- documentId must match one of the input documents.
- page must be within pageCount.
- bbox must be within page bounds when present.
- quote must be derived from the cited CIR object.
- objectId must resolve to a CIR object.
```

---

## 9. Hidden diff summary

Auditor reports may include low-value changes in an appendix or summary.

```json
{
  "hiddenDiffSummary": {
    "extractionArtifacts": 43,
    "formattingOnly": 81,
    "punctuationOnly": 17,
    "moveOnlySameScope": 29,
    "renumberedOnly": 21,
    "policy": "Hidden from default view; available in appendix when configured."
  }
}
```

Do not hide medium or high severity changes from the canonical JSON.

---

## 10. Validation object

```json
{
  "validationStatus": "passed",
  "validatedAt": "2026-05-11T12:01:00Z",
  "validators": [
    "json_schema",
    "citation_resolver",
    "language_check",
    "severity_policy_check",
    "report_manifest_hash"
  ],
  "errors": [],
  "warnings": [
    {
      "code": "LOW_EXTRACTION_CONFIDENCE",
      "message": "Table 14 has extraction confidence below 0.75.",
      "relatedObjectIds": ["TBL-v2-0014"]
    }
  ]
}
```

Validation statuses:

```text
passed
passed_with_warnings
failed
superseded
```

A failed report must not be presented as final.

---

## 11. Review state object

```json
{
  "reviewState": {
    "overallStatus": "pending_review",
    "reviewers": [],
    "overrides": [],
    "approval": null
  }
}
```

Final approval object:

```json
{
  "approvedBy": "usr_003",
  "approvedAt": "2026-05-12T09:00:00Z",
  "approvalStatement": "Reviewed high and medium severity items and accepted report for release impact assessment.",
  "approvedReportHash": "..."
}
```

---

## 12. Export manifest

Every export bundle should include a manifest.

```json
{
  "exportManifest": {
    "bundleId": "EXP-2026-000001",
    "createdAt": "2026-05-11T12:02:00Z",
    "files": [
      {
        "path": "comparison_report.json",
        "mediaType": "application/json",
        "sha256": "..."
      },
      {
        "path": "change_register.csv",
        "mediaType": "text/csv",
        "sha256": "..."
      },
      {
        "path": "review_package.html",
        "mediaType": "text/html",
        "sha256": "..."
      }
    ],
    "bundleSha256": "...",
    "signature": null
  }
}
```

For high-assurance deployments, `signature` should contain a detached signature reference or signer certificate metadata.

---

## 13. CSV change register contract

CSV is for spreadsheet workflows. It is not the source of truth.

Recommended columns:

```text
change_id
change_type
object_type
semantic_impact
change_severity
severity_confidence
severity_reason_codes
requires_human_review
review_disposition
section_v1
section_v2
old_value
new_value
title
summary
audit_impact
v1_citations
v2_citations
alignment_score
source_language
```

CSV rules:

```text
- Use UTF-8.
- Use stable column names.
- Serialize arrays as semicolon-separated values or JSON strings.
- Do not include raw confidential page text unless export policy allows it.
- Include report_id and comparison_id in file metadata or header comments where supported.
```

---

## 14. HTML/PDF review package contract

Human review packages should include:

```text
- cover page with report metadata
- source document hashes
- summary dashboard
- high severity changes first
- medium severity changes second
- low severity appendix if configured
- side-by-side evidence references
- validation warnings
- review status and override log
- manifest hash
```

The package must not remove a high or medium change that exists in canonical JSON.

---

## 15. Redaction and confidentiality

Export policy must define:

```text
- whether source quotes are included
- maximum quote length
- whether page images are included
- whether user names are included in review logs
- whether embeddings are ever exported
- whether hidden low-severity appendix is included
```

Default recommendation:

```text
- Do not export embeddings.
- Include short source quotes for citations.
- Include page references and bbox coordinates.
- Include rendered evidence images only in controlled review packages.
```

---

## 16. Compatibility requirements

Consumers of the export contract should rely on:

```text
- stable top-level field names
- stable enum values for severity and semanticImpact
- stable citation object shape
- additive expansion of changeType and reason codes
```

Do not break downstream consumers by renaming fields without a major schema version change.

---

## 17. Export validation checklist

Before an export is marked final:

```text
[ ] JSON validates against report schema.
[ ] All citation IDs resolve.
[ ] All source document hashes are present.
[ ] Summary counts match the changes array.
[ ] Severity is present for every Auditor Mode change.
[ ] Medium/high changes are not hidden.
[ ] LLM outputs passed schema and citation validation.
[ ] Report manifest includes all files.
[ ] File hashes match manifest.
[ ] Export was generated without internet access.
```
