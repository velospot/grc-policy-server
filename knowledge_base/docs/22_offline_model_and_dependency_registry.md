# 22 - Offline Model and Dependency Registry

## Purpose

This document defines the offline registry for models, prompts, parsers, OCR packs, dependencies, severity policies, ontology files, and runtime components.

The registry is required because the auditor must be reproducible and must not depend on live internet downloads.

Core principle:

```text
No model, tokenizer, parser, prompt, policy, or dependency should affect a report unless it is registered, hashed, and versioned.
```

---

## 1. Registry scope

The registry should track:

```text
- LLM models
- embedding models
- reranker models
- OCR engines and language packs
- PDF parsers and extraction tools
- tokenizers and model configs
- prompt templates
- severity policies
- ontology files
- document family profiles
- Python packages and system packages
- container images if used
- GPU/runtime libraries
- evaluation baselines
```

---

## 2. Registry entry schema

```json
{
  "registryId": "llm.granite-8b-instruct.q4@local",
  "kind": "llm_model",
  "name": "Granite 8B Instruct",
  "version": "pinned-version-or-release",
  "provider": "local_import",
  "sourceUri": "offline-bundle://models/granite",
  "localPath": "/opt/grc-auditor/models/granite",
  "sha256": "...",
  "sizeBytes": 1234567890,
  "license": {
    "name": "license-name",
    "filePath": "LICENSE.txt",
    "reviewStatus": "approved"
  },
  "usage": ["change_explanation"],
  "runtime": {
    "server": "ollama-or-llama-cpp-or-vllm",
    "quantization": "q4",
    "contextWindow": 8192,
    "gpuRequired": true,
    "minVramGb": 16
  },
  "approvedForProduction": false,
  "approvedBy": null,
  "approvedAt": null,
  "evaluation": {
    "baselineRunId": "RUN-2026-05-11-120000",
    "status": "pending"
  }
}
```

---

## 3. Model registry kinds

Recommended `kind` values:

```text
llm_model
embedding_model
reranker_model
ocr_engine
ocr_language_pack
pdf_parser
tokenizer
prompt_template
severity_policy
ontology
document_family_profile
python_package_lock
system_package_lock
container_image
gpu_runtime
evaluation_baseline
```

---

## 4. Model cards

Every model entry should have a local model card.

Minimum model-card fields:

```text
- intended use in this product
- prohibited use in this product
- source and import date
- license and redistribution notes
- expected languages
- context window
- quantization
- hardware requirements
- evaluation results on gold dataset
- known failure modes
- prompt templates used with this model
- approval status
```

Example intended-use statement:

```text
This LLM is approved only for explaining already-detected, cited comparison changes. It is not approved to perform primary PDF comparison or final compliance sign-off.
```

---

## 5. Prompt registry

Prompts are dependencies. Treat them as versioned artifacts.

Prompt entry:

```json
{
  "registryId": "prompt.auditor_change_explanation@1.0.0",
  "kind": "prompt_template",
  "promptId": "auditor_change_explanation",
  "version": "1.0.0",
  "sha256": "...",
  "localPath": "prompts/auditor_change_explanation_v1.md",
  "inputSchema": "evidence_pack.schema.json",
  "outputSchema": "auditor_change_explanation.schema.json",
  "approvedForProduction": true
}
```

Prompt changes must trigger:

```text
- schema validation tests
- citation behavior tests
- language behavior tests
- prompt injection tests
- gold dataset comparison for output drift
```

---

## 6. Severity policy and ontology registry

Severity policies and ontologies directly affect audit conclusions.

Required fields:

```json
{
  "registryId": "policy.automotive_emc_default@1.0.0",
  "kind": "severity_policy",
  "policyId": "automotive_emc_default",
  "version": "1.0.0",
  "sha256": "...",
  "localPath": "policies/automotive_emc_default.yaml",
  "compatibleOntologyIds": ["ontology.automotive_emc@1.0.0"],
  "approvedForProduction": true
}
```

Do not allow a report to use an unregistered or modified policy file.

---

## 7. Dependency lock registry

Python and system dependencies should be represented by lock files.

```json
{
  "registryId": "python.lock.backend@2026.05.11",
  "kind": "python_package_lock",
  "localPath": "locks/backend-requirements.lock",
  "sha256": "...",
  "packageManager": "pip",
  "createdAt": "2026-05-11T12:00:00Z",
  "sbomPath": "sbom/backend.spdx.json",
  "approvedForProduction": true
}
```

For production, avoid installing packages from the internet. Use an offline wheelhouse or container image verified by hash.

---

## 8. Offline import workflow

Recommended workflow:

```text
1. Create an update bundle on a connected build machine.
2. Generate manifest.json with file hashes, sizes, licenses, and intended registry IDs.
3. Transfer bundle to offline environment using approved media.
4. Place bundle in quarantine import directory.
5. Registry service reads manifest without executing code.
6. Verify sha256 for every file.
7. Check license and allowlist policy.
8. Run malware scan if available in the environment.
9. Stage artifacts in immutable local storage.
10. Run evaluation smoke tests.
11. Admin approves registry activation.
12. Record audit log event.
```

Do not activate imported artifacts automatically.

---

## 9. Bundle manifest

```json
{
  "bundleId": "BND-2026-05-11-001",
  "createdAt": "2026-05-11T10:00:00Z",
  "createdBy": "build-system",
  "targetProduct": "grc-auditor-offline",
  "entries": [
    {
      "registryId": "embedding.bge-m3@local",
      "kind": "embedding_model",
      "path": "models/bge-m3/model.bin",
      "sha256": "...",
      "sizeBytes": 123456789,
      "licensePath": "licenses/bge-m3.txt"
    }
  ],
  "bundleSha256": "...",
  "signature": null
}
```

---

## 10. Runtime enforcement

The application should enforce registry usage at runtime.

Rules:

```text
- Model server may load only approved model registry IDs.
- Comparison job must store registry snapshot hash.
- Report export must include all registry IDs used.
- Unapproved prompt or policy files must fail closed.
- Modified files whose hash no longer matches registry must fail closed.
- Runtime internet downloads are disabled.
```

---

## 11. Registry snapshot

Each comparison should store a snapshot reference.

```json
{
  "registrySnapshotId": "REGSNAP-2026-05-11-120000",
  "createdAt": "2026-05-11T12:00:00Z",
  "entries": [
    "llm.granite-8b-instruct.q4@local",
    "embedding.qwen3-embedding-0.6b@local",
    "reranker.qwen3-reranker-0.6b@local",
    "parser.docling@pinned",
    "policy.automotive_emc_default@1.0.0",
    "ontology.automotive_emc@1.0.0"
  ],
  "snapshotSha256": "..."
}
```

This allows later auditors to know exactly what was used.

---

## 12. Compatibility matrix

Maintain a compatibility matrix:

| Component | Compatible with | Notes |
|---|---|---|
| severity policy 1.0.0 | ontology 1.0.0 | Uses reason-code catalog v1 |
| prompt auditor_change_explanation 1.0.0 | report schema 1.0.0 | Returns required JSON fields |
| document profile automotive_emc_oem 1.0.0 | ontology automotive_emc 1.0.0 | Uses EMC table row keys |
| embedding model A | reranker model B | Evaluated together on pair alignment |

Compatibility failures should block production activation.

---

## 13. Approval states

Registry entry states:

```text
draft
imported
quarantined
verified
evaluation_pending
approved_for_dev
approved_for_prod
deprecated
revoked
```

Revoked entries must not be used for new comparisons. Old reports should remain readable and indicate that a component was later revoked.

---

## 14. Upgrade and rollback policy

Upgrade steps:

```text
1. Import new artifact.
2. Run smoke tests.
3. Run gold evaluation.
4. Compare metrics with current baseline.
5. Review failure differences.
6. Approve for dev or prod.
7. Preserve old artifact for report reproducibility.
```

Rollback steps:

```text
1. Mark new artifact as deprecated or revoked.
2. Restore previous approved registry snapshot.
3. Re-run failed comparison if necessary.
4. Record audit log event.
```

Do not delete old models or policy files while reports depend on them.

---

## 15. Minimum registry checklist

Before production:

```text
[ ] All LLM, embedding, and reranker files are registered and hashed.
[ ] OCR engines and language packs are registered.
[ ] PDF parser versions are registered.
[ ] Prompt templates are versioned and hashed.
[ ] Severity policy and ontology files are registered.
[ ] Document family profiles are registered.
[ ] Dependency lock files and SBOMs exist.
[ ] Offline import workflow is tested.
[ ] Runtime rejects unregistered artifacts.
[ ] Reports include registry snapshot references.
```
