# Offline GenAI GRC Auditor Knowledge Base

This repository is a platform-agnostic development knowledge base for building a local, offline, multilingual GenAI-based compliance release comparison tool for tabular-heavy automotive EMC compliance documents.

The design assumes:

- Users upload multiple compliance PDF documents.
- Each document belongs to a release/version, for example v1 or v2.
- The user compares two documents of the same language.
- The system extracts sections, paragraphs, lists, tables, table rows, footnotes, and citations.
- The system performs a deterministic compliance diff first, then uses a local LLM to explain the change.
- The final answer is in the same language as the compared documents.
- The system runs fully offline on a local workstation or local server.
- Target hardware: i9-class CPU, Nvidia 16 GB GPU, 64 GB RAM.
- Target concurrency: at least five local web users, with queued heavy jobs.

## Design principle

Do not build a chatbot over PDF chunks. Build a structured compliance comparison engine.

Bad pipeline:

```text
PDF -> chunks -> vectors -> prompt -> comparison
```

Target pipeline:

```text
PDF -> structure extraction -> Compliance Intermediate Representation -> alignment -> deterministic diff -> cited evidence pack -> LLM explanation
```

## Directory map

```text
README.md
GRC_Auditor_Offline_KB_All_In_One.md

docs/
  00_system_overview.md
  01_reference_architecture.md
  02_compliance_intermediate_representation.md
  03_pdf_structure_and_extraction.md
  04_multilingual_design.md
  05_comparison_engine.md
  06_llm_and_rag_design.md
  07_storage_api_and_workers.md
  08_dev_build_runbook.md
  09_prod_build_runbook.md
  10_security_audit_ops.md
  11_testing_evaluation.md
  12_agent_neutral_development_playbook.md
  13_prompt_templates.md
  14_implementation_backlog.md
  15_challenges_debugging_accuracy_plan.md
  16_comparison_modes_severity_policy.md
  17_threat_model.md
  18_auditor_grade_acceptance_criteria.md
  19_report_schema_and_export_contract.md
  20_severity_policy_engine.md
  21_evaluation_harness_and_gold_dataset.md
  22_offline_model_and_dependency_registry.md
  23_document_family_profiles.md
  24_automotive_emc_domain_ontology.md
  references.md

blueprints/
  api_contract.md
  docker_compose_dev.md
  docker_compose_prod.md
  sql_schema.md

agent/
  AGENT_BOOTSTRAP.md
  DEFINITION_OF_DONE.md
```

## Recommended default stack

```text
Frontend: React or Next.js
Backend: FastAPI
Jobs: Redis + Celery, RQ, or Dramatiq
Metadata DB: PostgreSQL
Vector search: Weaviate or Qdrant; pgvector is acceptable for smaller deployments
File store: local filesystem or MinIO
PDF extraction: Docling primary, OpenDataLoader PDF secondary, PyMuPDF/pdfplumber validation
OCR: Tesseract eng/deu/fra; PaddleOCR or EasyOCR optional fallback
Language ID: fastText lid.176
Embedding: Qwen3-Embedding-0.6B or BGE-M3
Reranking: Qwen3-Reranker-0.6B or bge-reranker-v2-m3
LLM: Granite 3.3 8B Instruct, with optional benchmark against Qwen/Mistral-family local models
Serving: Ollama for MVP; llama.cpp server or vLLM for production tuning
```

## Non-negotiable product requirements

1. Every generated comparison item must have citations to v1 and/or v2 source objects.
2. Citations must include document, version, page, section/table/row identifier, and bounding box when available.
3. The LLM may explain and classify; it must not be the only diff engine.
4. Table rows, cells, units, ranges, and footnotes must be normalized before comparison.
5. The user-facing explanation language must match the document language.
6. No internet calls at runtime.
7. Reproducibility must be possible from stored PDF hashes, parser versions, model versions, prompt versions, and comparison algorithm versions.
