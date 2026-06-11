# Docling Graph + Neo4j Runbook

This note documents the current implementation of the phased architecture from
`.agent-skillsv2/ARCHITECTURE.md` for DoclingGraph-style extraction and Neo4j
persistence.

## What Changed

- Added a deterministic orchestration package under `src/grc_policy_server/services/orchestration/`.
- Refactored ingestion so `DocumentIngestionService.ingest_upload()` runs through explicit stages instead of one monolithic flow.
- Added `DoclingGraphAdapter`, which converts normalized Docling hierarchy records into validated Pydantic graph artifacts.
- Added tri-layer Neo4j writes:
  - `MetaGraphNode`: document provenance and language.
  - `LayoutGraphNode`: pages, sections, clauses, tables, figures, and source layout.
  - `ComplianceNode`: requirements, measurements, thresholds, observations, evidence, risks, standards, and extracted facts.
- Kept the existing API request/response schemas unchanged.
- Kept the existing Neo4j hierarchy write for backward compatibility, then writes the new tri-layer graph.
- Writes `docling_graph.json` into each uploaded document directory for local inspection.

## Why Not Add `docling-graph` Directly

Upstream `docling-graph` uses the pattern we want: Pydantic templates, stable graph IDs, explicit edge metadata, and graph export to CSV/Cypher/JSON.

The package currently documents Python `3.10–3.12` support, while this project targets Python `3.13`. To avoid pin conflicts, the implementation uses a compatible internal adapter:

- Pydantic graph artifact validation.
- Stable IDs.
- Explicit edge metadata.
- Neo4j-ready node and edge writes.
- Deterministic first; no agent controls graph construction.

Reference: https://github.com/docling-project/docling-graph/tree/main

## Enable Neo4j Writes

Set these environment variables in `.env` or `.env.local`:

```bash
NEO4J_ENABLED=true
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=password
NEO4J_DATABASE=neo4j
```

If using Docker Compose, start Neo4j before ingestion:

```bash
docker compose up -d neo4j
```

Then start the API normally:

```bash
uv run uvicorn grc_policy_server.main:app --reload
```

## Upload and Observe the Response

Synchronous upload:

```bash
curl -X POST http://localhost:8000/documents/upload \
  -H "Authorization: Bearer ${API_BEARER_TOKEN}" \
  -F "file=@/path/to/document.pdf"
```

Expected response shape is unchanged:

```json
{
  "acceptedCount": 1,
  "rejectedCount": 0,
  "results": [
    {
      "filename": "document.pdf",
      "contentType": "application/pdf",
      "accepted": true,
      "documentId": "generated-document-id",
      "chunksStored": 42,
      "error": null
    }
  ]
}
```

Async upload v2:

```bash
curl -X POST http://localhost:8000/documents/upload/v2 \
  -H "Authorization: Bearer ${API_BEARER_TOKEN}" \
  -F "file=@/path/to/document.pdf"
```

Expected initial response:

```json
{
  "jobId": "celery-job-id",
  "status": "queued"
}
```

Poll the job:

```bash
curl http://localhost:8000/documents/upload/v2/response/{jobId} \
  -H "Authorization: Bearer ${API_BEARER_TOKEN}"
```

Finished response keeps the existing schema:

```json
{
  "jobId": "celery-job-id",
  "status": "finished",
  "done": true,
  "result": {
    "acceptedCount": 1,
    "rejectedCount": 0,
    "results": [
      {
        "filename": "document.pdf",
        "accepted": true,
        "documentId": "generated-document-id",
        "chunksStored": 42
      }
    ]
  },
  "error": null
}
```

## Inspect Local Artifacts

For a returned `documentId`, inspect:

```text
${UPLOAD_ROOT}/{documentId}/metadata.json
${UPLOAD_ROOT}/{documentId}/hierarchy.json
${UPLOAD_ROOT}/{documentId}/docling_graph.json
```

`docling_graph.json` contains:

```json
{
  "document_id": "...",
  "document_stable_id": "...",
  "ontology_version": "1.2",
  "nodes": [],
  "edges": [],
  "ignored_nodes": []
}
```

Use this file first when debugging Neo4j output. If the local graph file is correct but Neo4j is empty, the issue is connection/configuration. If the local graph file is wrong, the issue is extraction, canonicalization, table enrichment, or ontology classification.

## Inspect Neo4j

Count graph nodes by layer:

```cypher
MATCH (n:DoclingGraphNode)
RETURN n.layer AS layer, count(*) AS count
ORDER BY layer;
```

Inspect compliance nodes:

```cypher
MATCH (n:ComplianceNode)
RETURN n.ontology_type AS ontology_type, count(*) AS count
ORDER BY count DESC;
```

Check source traceability:

```cypher
MATCH (c:ComplianceNode)-[:SOURCED_FROM]->(l:LayoutGraphNode)
RETURN c.ontology_type, c.title, l.label, l.page
LIMIT 25;
```

Check multilingual extracted facts:

```cypher
MATCH (m:ComplianceNode)
WHERE m.ontology_type IN ["Measurement", "Threshold", "Requirement"]
RETURN m.ontology_type, m.title, m.text, m.properties_json
LIMIT 25;
```

Check ignored artifacts:

```cypher
MATCH (l:LayoutGraphNode)
WHERE l.properties_json CONTAINS "exclusion_reason"
RETURN l.title, l.text, l.properties_json
LIMIT 25;
```

## Compare Document Graph Trees

The graph-first comparison endpoints are separate from the existing text/chunk
comparison endpoints.

REST:

```bash
curl -X POST http://localhost:8000/graph-compare \
  -H "Authorization: Bearer ${API_BEARER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "doc1Id": "old-document-id",
    "doc2Id": "new-document-id",
    "testingDepartment": "EMC",
    "includeUnchanged": false
  }'
```

Expected response:

```json
{
  "summary": "Graph-tree comparison of old-document-id vs new-document-id: 1 compliance graph change(s) detected (0 added, 0 removed, 1 modified). Severity breakdown: 1 high, 0 medium, 0 low.",
  "keyDifferences": [
    {
      "changeType": "MODIFIED",
      "section": "6 EMC",
      "doc1Content": "field_strength | ontology=Measurement | value=30 | unit=V/m | fact_type=field_strength",
      "doc2Content": "field_strength | ontology=Measurement | value=40 | unit=V/m | fact_type=field_strength",
      "impact": "HIGH Measurement graph change: 1 typed property changed.",
      "changeSeverity": "high",
      "doc1Reference": {
        "section": "6 EMC",
        "page": 12,
        "sourceText": "field_strength | ontology=Measurement | value=30 | unit=V/m | fact_type=field_strength",
        "nodeId": "compliance:fact:..."
      },
      "doc2Reference": {
        "section": "6 EMC",
        "page": 12,
        "sourceText": "field_strength | ontology=Measurement | value=40 | unit=V/m | fact_type=field_strength",
        "nodeId": "compliance:fact:..."
      },
      "nodeType": "graph:Measurement",
      "changes": [
        {
          "type": "modified",
          "text": "value: 30 → 40",
          "oldValue": "30",
          "newValue": "40",
          "location": "6 EMC"
        }
      ],
      "requiresHumanReview": true,
      "severityConfidence": 1.0,
      "complianceExplanation": "HIGH Measurement graph change: 1 typed property changed."
    }
  ],
  "actionPlan": [],
  "followUpQuestions": [],
  "accuracyMetrics": null,
  "comparisonMode": "auditor_grade",
  "requireHumanReview": true,
  "warnings": []
}
```

Streaming:

```bash
curl -N -X POST http://localhost:8000/graph-compare/stream \
  -H "Authorization: Bearer ${API_BEARER_TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "doc1Id": "old-document-id",
    "doc2Id": "new-document-id",
    "testingDepartment": "EMC"
  }'
```

SSE event types:

```text
payload
progress
diff
done
error
```

The graph-tree comparator does not call Weaviate. It loads
`${UPLOAD_ROOT}/{documentId}/docling_graph.json`, aligns compliance graph nodes,
diffs typed properties and relationships, and returns the same top-level
`ComparisonResult` shape as the current comparison API. Internally it still
uses graph-native change records.

## LLM Explanation and Enrichment Status

The graph-tree comparison path is deterministic-first with optional LLM explanation enrichment.

Current behavior:

- The graph diff itself is always deterministic.
- When `EXPLANATION_AGENT_ENABLED=true`, graph changes are passed to `GraphExplanationAgent` after deterministic diffing.
- `complianceExplanation` is populated from the LLM explanation when timeout/schema guardrails pass; otherwise the deterministic graph-diff rationale remains.
- `markdownDiffSummary` is populated from the LLM explanation when available.
- Severity, review flags, property changes, and relationship changes come from graph rules only.

The LLM is therefore an explanation layer, not a comparison engine.

Implemented flow:

```text
GraphChangeRecord
  ↓ deterministic validation
GraphExplanationAgent
  ↓ schema-validated explanation only
KeyDifference.complianceExplanation
KeyDifference.markdownDiffSummary
```

Guardrails for `GraphExplanationAgent`:

- It may explain a graph change, but must not create, remove, or modify findings.
- It must not change severity, PASS/FAIL, measurement values, thresholds, or review flags.
- It receives structured JSON only: graph node refs, property changes, relationship changes, and source citations.
- It must return schema-validated output with confidence and model metadata.
- Timeout or schema failure falls back to the deterministic rationale.
- Low confidence routes to human review without blocking the comparison result.

Runtime setting:

```bash
EXPLANATION_AGENT_ENABLED=true
GRAPH_EXPLANATION_TIMEOUT_SEC=20
```

Useful docling-graph design ideas for future enrichment:

- Use a validated Pydantic extraction template before graph conversion.
- Prefer structure-aware chunking over naive text splitting.
- Use staged extraction for complex nested compliance templates.
- Consider delta extraction for long graph-first documents: chunk → token-bounded batches → flat graph IR → normalize → merge → projection.
- Keep programmatic merging as the default; use LLM consolidation only for explicit conflict resolution.
- Keep local VLM/LLM selection document-dependent: VLM for complex layouts, LLM for text-heavy extracted Markdown.

## Multilingual and Ontology Behavior

The adapter uses the current deterministic ontology modules:

- EMC: `services/ingestion/ontology/emc_ontology.py`
- Safety: `services/ingestion/ontology/safety_ontology.py`
- Environment: `services/ingestion/ontology/environment_ontology.py`

Supported multilingual signals include English, German, and French terms already present in those modules and in the existing canonicalization/comparison code.

Examples:

- German EMC table headers like `Frequenzbereich` and `Prüfpegel` can produce `frequency_range` and `field_strength` facts.
- Safety strings such as `IP67`, `ASIL`, and `SIL` can produce safety facts.
- Environmental values such as `mg/kg`, `%w/w`, humidity, salt concentration, and temperature can produce environmental facts.

## Failure Modes

- If Neo4j is disabled, ingestion still succeeds and writes local artifacts.
- If Neo4j write fails, ingestion still succeeds after canonical storage; the warning is logged.
- TOC/header/footer/editorial content can remain in the layout graph for traceability but is not written into the compliance graph.
- Missing standard versions are not inferred. Standard nodes use `standard_version = "unknown"` and are flagged for review.

## Validation Commands

Run the targeted tests:

```bash
uv run pytest tests/test_docling_graph_adapter.py tests/test_orchestration_state_machine.py tests/test_ingestion_pipeline.py
```

Run lint on the touched implementation:

```bash
uv run --extra dev ruff check \
  src/grc_policy_server/services/graph/docling_graph_adapter.py \
  src/grc_policy_server/services/graph/graph_neo4j_client.py \
  src/grc_policy_server/services/ingestion/document_ingestion_service.py \
  tests/test_docling_graph_adapter.py \
  tests/test_orchestration_state_machine.py
```
