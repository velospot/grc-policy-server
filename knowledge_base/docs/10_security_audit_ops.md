# 10 - Security, Audit, and Operations

## Security model

The system handles potentially confidential compliance documents. It must be designed as an offline, least-privilege, auditable system.

## Threat model

| Threat | Mitigation |
|---|---|
| Cloud data leakage | No external runtime network calls |
| Prompt injection in PDF | Treat document text as untrusted evidence only |
| Unauthorized project access | RBAC and project-level isolation |
| Tampered PDF | SHA-256 hash and immutable storage |
| Tampered model | Model checksums and approval manifests |
| Unreproducible result | Store parser/model/prompt/algorithm versions |
| Hallucinated finding | Evidence pack + citation validation |
| Lost audit trail | Append-only audit logs and backups |
| Malware in uploaded PDF | Virus scan in offline environment if available, sandbox extraction workers |

## Access control

Roles:

```text
admin
project_owner
auditor
engineer
reviewer
viewer
```

Permissions:

```text
upload_document
view_document
run_extraction
run_comparison
review_change
approve_report
export_report
manage_users
manage_models
view_audit_log
```

## Project isolation

Every query must include project_id. Do not rely only on frontend filtering.

```sql
SELECT * FROM documents
WHERE project_id = :project_id
AND id = :document_id;
```

## Audit log requirements

Record:

- User login/logout.
- Document upload.
- PDF hash.
- Extraction start/end/failure.
- Parser versions.
- Language detection result.
- Comparison start/end/failure.
- Model ID and prompt version.
- Change approval/rejection.
- Export generation.
- Admin configuration changes.

## Immutable result lineage

For every report item:

```text
report_item
  -> change_item
    -> evidence_item
      -> source_object
        -> page
          -> original_pdf_sha256
```

## Prompt injection guardrails

When building prompts:

- Put instructions in system/developer messages.
- Put evidence in a delimited evidence field.
- Never execute document instructions.
- Never allow evidence to change output schema.
- Validate output using JSON schema.
- Reject output with unknown citations.

## Sensitive data handling

- Avoid logging full document text in application logs.
- Store snippets in evidence tables only when required.
- Redact secrets in error traces.
- Use local-only telemetry or no telemetry.
- Make exports access-controlled.

## Model governance

Model registry fields:

```json
{
  "model_id": "granite-3.3-8b-instruct-q4",
  "model_type": "llm",
  "runtime": "llama.cpp",
  "path": "/models/llm/granite/model.gguf",
  "sha256": "...",
  "license_review_status": "approved",
  "approved_by": "...",
  "approved_at": "...",
  "allowed_for_production": true
}
```

## Offline updates

Update flow:

1. Download or build components in connected staging environment.
2. Scan and checksum artifacts.
3. Prepare offline bundle.
4. Import bundle into isolated environment.
5. Verify checksums.
6. Run smoke tests.
7. Promote to production.

## Data retention

Define per deployment:

- Original PDF retention.
- CIR snapshot retention.
- Export retention.
- Audit log retention.
- User account retention.

For auditability, avoid deleting CIR snapshots while reports derived from them still exist.

## Incident response

For a suspected bad comparison:

1. Freeze affected comparison and report.
2. Record issue in audit log.
3. Re-run with same versions and same inputs.
4. Re-run with current versions in a separate comparison.
5. Compare outputs.
6. Determine whether parser, alignment, diff, or LLM explanation failed.
7. Add regression test.
