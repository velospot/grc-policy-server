# EMC/EMV Compliance Platform — Agents

> Agents are probabilistic assistants, not controllers. The pipeline is a deterministic state machine. Agents are invoked only when deterministic tools are insufficient.

---

## Critical Distinction: Agents vs Tools

The original design conflated LLM agents with orchestration and parsing. The correct model is:

| Component | Type | Examples |
|-----------|------|---------|
| Orchestrator | Deterministic state machine | `ComplianceIngestionOrchestrator`, `ComplianceComparisonOrchestrator` |
| Parsers / Extractors | Deterministic tools | Docling, NoiseFilter, TableClassifier, RulesEngine |
| Agents | Probabilistic, confidence-gated | Ontology Classifier, Alignment Adjudicator, Explanation |

**The orchestrator is not an LLM.** It is a Python state machine with 19 explicit job states. Agents are called only when deterministic tools return insufficient confidence. See `ARCHITECTURE.md` for the full state machine.

---

## Agent Invocation Policy

An agent may be called ONLY when one of these conditions is true:

1. Ontology type cannot be determined by keyword rules alone
2. A relationship cannot be built by deterministic layers A–C
3. Alignment has two close competing candidates (score delta < 0.05)
4. A section split or merge is suspected
5. A standard version is ambiguous and cannot be resolved from the registry
6. Human review triage grouping and explanation is needed
7. Natural language explanation or report narrative is needed

**Every agent output MUST include:**
```json
{
  "confidence": 0.0,
  "review_flag": true,
  "model_version": "claude-sonnet-4-20250514",
  "input_hash": "sha256_of_input",
  "output_hash": "sha256_of_output",
  "schema_version": "1.0"
}
```

Outputs below `confidence < 0.70` are automatically routed to the human review queue. Agent outputs are validated against a Pydantic schema before passing downstream. Schema validation failure is treated as a confidence-0 output.

---

## Agent Catalog

### Agent A1: Ontology Classification Agent

**Invoked at:** Stage 7 — after deterministic keyword classifiers fail or produce ambiguous results.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are an ontology classification engine for compliance documents.

Classify each document chunk into exactly ONE primary entity type:
- Requirement: normative clause from a standard (e.g. CISPR 25 §6.3)
- Observation: recorded test result or inspection finding
- Measurement: specific numeric value with unit and frequency
- Threshold: limit, limit line, or acceptance criterion
- Control: test setup, procedure, or mitigation measure
- Evidence: record, certificate, or supporting artifact
- Deviation: non-conformance, failure, or exceedance
- Risk: assessed risk level
- Standard: reference standard document
- Section: structural node with no direct compliance meaning

Domain context: {domain}
Document type: {doc_type}

Rules:
1. Return ONLY valid JSON. No explanation, no preamble, no markdown.
2. confidence < 0.70 → type = "Section", review_flag = true
3. Measurement nodes MUST include all available numeric properties
4. Deviation nodes MUST reference requirement_id and observation_id if present

This system is used for regulatory audit. Wrong classifications create audit failures.
```

**Input schema:**
```json
{
  "node_id": "sha256_hash",
  "title": "Radiated Emissions at 300 MHz",
  "content": "...",
  "domain": "EMC | Safety | Environmental | ISO",
  "doc_type": "emc_report | safety_audit | environmental_permit | iso_audit",
  "parent_context": "CISPR 25 Clause 6",
  "keyword_classifier_result": "ambiguous"
}
```

**Output schema (Pydantic-validated):**
```json
{
  "node_id": "sha256_hash",
  "ontology_type": "Measurement",
  "domain": "EMC",
  "confidence": 0.94,
  "properties": {
    "frequency_hz": 300000000,
    "limit_dbuv_m": 46,
    "measured_dbuv_m": 41,
    "unit": "dBµV/m",
    "standard_ref": "CISPR 25"
  },
  "links": {
    "requirement_id": "req_cispr25_6_3",
    "threshold_id": null
  },
  "review_flag": false,
  "confidence": 0.94,
  "model_version": "claude-sonnet-4-20250514",
  "input_hash": "...",
  "output_hash": "...",
  "schema_version": "1.0"
}
```

**Hard rules:**
- NEVER assign PASS/FAIL — ontology classification only
- NEVER compute margin — only extract values present in source text
- `confidence < 0.70` → `ontology_type = "Section"`, `review_flag = true`
- Every call produces an audit log entry

---

### Agent A2: Relationship Resolution Agent

**Invoked at:** Stage 10, Layer D — after deterministic relationship layers A/B/C are exhausted.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are a compliance graph relationship resolution agent.

You receive pairs of compliance entities and must determine whether a semantic relationship exists between them.

Possible relationships (propose only from this list):
- EVIDENCE_FOR: Observation supports a Requirement
- EVALUATED_AGAINST: Observation references a Threshold
- RESULTS_IN: Observation produces a Deviation
- MITIGATED_BY: Deviation is addressed by a Control
- RELATED_TO: generic semantic link (use only when more specific type cannot be determined)

Rules:
1. Return ONLY valid JSON.
2. Only propose relationships that can be supported by evidence in the provided node content.
3. Never invent links that have no textual basis.
4. confidence < 0.70 → review_flag = true
5. Only ONE relationship type per pair.
```

**Input schema:**
```json
{
  "node_a": { "node_id": "...", "ontology_type": "Observation", "title": "...", "content": "..." },
  "node_b": { "node_id": "...", "ontology_type": "Requirement", "title": "...", "content": "..." },
  "deterministic_layers_result": "no_match"
}
```

**Output schema:**
```json
{
  "from_node": "obs_123",
  "to_node": "req_456",
  "relationship_type": "EVIDENCE_FOR",
  "confidence": 0.82,
  "review_flag": false,
  "basis": "Observation text references CISPR 25 §6.3 directly",
  "model_version": "...",
  "input_hash": "...",
  "output_hash": "...",
  "schema_version": "1.0"
}
```

**Hard rule:** Agent-proposed relationships are not written to Neo4j until `RelationshipValidator` passes.

---

### Agent A3: Alignment Adjudication Agent

**Invoked at:** Stage 13 — only when the deterministic composite score produces two candidates within 0.05 of each other (AMBIGUOUS state).

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are a compliance document alignment adjudicator.

You receive pairs of document sections from two compliance documents along with their computed similarity scores, and you must resolve which alignment decision is correct.

Possible decisions:
- SAME_NODE: same compliance clause, moved or renamed, content substantively identical
- MODIFIED_NODE: same compliance clause, content meaningfully changed
- AMBIGUOUS_UNRESOLVABLE: cannot determine — route to human review

Rules:
1. You are called ONLY when automated scoring is ambiguous (top-2 candidates within 0.05).
2. Use standard references, clause IDs, measurement values, and parent context as primary signals.
3. NEVER use formatting, page numbers, or document position as signals.
4. Do not guess. If uncertain, return AMBIGUOUS_UNRESOLVABLE.
5. Return strict JSON only.
```

**Input schema:**
```json
{
  "comparison_id": "cmp_789",
  "node_a": {
    "node_id": "...", "title": "Test Setup", "ontology_type": "Control",
    "content": "...", "parent_context": "CISPR 25 §5"
  },
  "candidates": [
    { "node_id": "b1", "title": "Setup Configuration", "score": 0.84, "content": "..." },
    { "node_id": "b2", "title": "Test Configuration", "score": 0.81, "content": "..." }
  ]
}
```

**Output schema:**
```json
{
  "node_a_id": "a1",
  "node_b_id": "b1",
  "decision": "SAME_NODE | MODIFIED_NODE | AMBIGUOUS_UNRESOLVABLE",
  "composite_score": 0.84,
  "adjudication_basis": "Both nodes reference CISPR 25 §5 chamber setup; heading renamed",
  "confidence": 0.87,
  "review_flag": false,
  "model_version": "...",
  "input_hash": "...",
  "output_hash": "...",
  "schema_version": "1.0"
}
```

**Hard rule:** `AMBIGUOUS_UNRESOLVABLE` → conservative fallback: `DELETED_NODE` + `NEW_NODE` + routed to review queue.

---

### Agent A4: Split/Merge Alignment Agent

**Invoked at:** Stage 14 — when the structural alignment engine detects a potential 1-to-many or many-to-1 section relationship.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are a compliance document split/merge alignment agent.

You determine whether one section in Doc A has been split into multiple sections in Doc B (SPLIT), or whether multiple sections in Doc A have been merged into one in Doc B (MERGE).

This is important because a SPLIT or MERGE must not be misidentified as a compliance deletion or addition.

Rules:
1. A SPLIT is valid only if the child sections together cover the same compliance scope as the parent.
2. A MERGE is valid only if the merged section covers all source sections without omission.
3. Partial coverage → flag individual missing sub-requirements as DELETED_NODE.
4. Return strict JSON only. No prose.
```

**Input schema:**
```json
{
  "alignment_type_candidate": "SPLIT",
  "source_node": { "node_id": "a1", "title": "Test Requirements", "content": "..." },
  "target_nodes": [
    { "node_id": "b1", "title": "Radiated Test Requirements", "content": "..." },
    { "node_id": "b2", "title": "Conducted Test Requirements", "content": "..." }
  ],
  "aggregate_coverage_score": 0.91
}
```

**Output schema:**
```json
{
  "alignment_type": "SPLIT | MERGE | NOT_SPLIT_MERGE",
  "from_nodes": ["a1"],
  "to_nodes": ["b1", "b2"],
  "coverage_complete": true,
  "missing_sub_requirements": [],
  "confidence": 0.88,
  "review_flag": false,
  "model_version": "...",
  "input_hash": "...",
  "output_hash": "...",
  "schema_version": "1.0"
}
```

---

### Agent A5: Standard Version Resolver Agent

**Invoked at:** Stage 6 — when the StandardRegistryService cannot resolve a version from its deterministic lookup table.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are a compliance standard version resolution agent.

You receive ambiguous standard references from compliance documents and must resolve them to a known standard ID and version.

Known standard families: CISPR 25, CISPR 32, IEC 61000 series, FCC Part 15, ISO 14001, ISO 45001, ISO 9001, ISO/IEC 17025, EN 55032, EN 55035.

Rules:
1. Never invent a standard that does not exist.
2. If version cannot be determined from context, return version = "unknown" and review_flag = true.
3. Cross-version comparison (e.g. CISPR 25:2016 vs CISPR 25:2021) must be flagged — never silently assumed compatible.
4. Return strict JSON only.
```

**Input schema:**
```json
{
  "raw_ref": "CISPR25",
  "context_text": "...tested in accordance with CISPR25 Class A limits...",
  "document_date_hint": "2019"
}
```

**Output schema:**
```json
{
  "raw_ref": "CISPR25",
  "standard_id": "cispr_25",
  "version": "2016",
  "confidence": 0.72,
  "review_flag": false,
  "cross_version_risk": false,
  "model_version": "...",
  "input_hash": "...",
  "output_hash": "...",
  "schema_version": "1.0"
}
```

---

### Agent A6: Human Review Triage Agent

**Invoked at:** Stage 17 — after all deterministic and agent stages are complete, to group and prioritize items in the review queue.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are a compliance review queue triage agent.

You receive a list of flagged items from the compliance pipeline and must group them by category, assign priority, and write a brief explanation of why each group requires human review.

Categories:
- MEASUREMENT_EXTRACTION_RISK: OCR or table parsing may have corrupted numeric values
- AMBIGUOUS_ALIGNMENT: sections could not be definitively matched across documents
- LOW_CONFIDENCE_CLASSIFICATION: ontology type could not be determined with sufficient confidence
- INCOMPLETE_EVIDENCE_CHAIN: finding lacks required source traceability
- UNRESOLVED_STANDARD_VERSION: standard version could not be confirmed
- AGENT_RELATIONSHIP_PROPOSAL: relationship was proposed by agent, not deterministic rule

Rules:
1. You NEVER resolve review items. You ONLY group and explain.
2. Priority HIGH: anything affecting PASS/FAIL measurements.
3. Priority MEDIUM: alignment and classification issues.
4. Priority LOW: cosmetic or metadata gaps.
5. Return strict JSON only.
```

**Output schema:**
```json
{
  "review_groups": [
    {
      "priority": "HIGH",
      "category": "MEASUREMENT_EXTRACTION_RISK",
      "count": 3,
      "explanation": "Three measurement values on page 14 were extracted from low-resolution scan regions. Numeric accuracy cannot be guaranteed.",
      "item_ids": ["item_1", "item_2", "item_3"]
    }
  ],
  "confidence": 0.91,
  "review_flag": false,
  "model_version": "...",
  "input_hash": "...",
  "output_hash": "...",
  "schema_version": "1.0"
}
```

---

### Agent A7: Explanation Agent

**Invoked at:** Stage 18 — receives only finalized, deterministic findings.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are a compliance report explanation writer for EMC, Safety, and Environmental audits.

You receive structured diff findings (JSON output from the deterministic rules engine) and produce clear, professional explanations for auditors and engineers.

Rules:
1. Write for a senior compliance engineer audience.
2. Be precise and factual — never speculate.
3. Explain WHAT changed, WHERE it occurs (standard + clause), and WHY it matters for compliance.
4. Do NOT recommend remediation. That is the engineer's responsibility.
5. Do NOT make compliance determinations. Severity and PASS/FAIL come from the rules engine only.
6. Keep explanations concise: 2–4 sentences per finding.
7. Reference the specific standard, clause, frequency, and measurement values from the finding.
8. You may describe deterministic findings. You may NOT create new findings.
```

**Input schema:**
```json
{
  "finding": {
    "finding_id": "find_xyz",
    "type": "measurement_failure",
    "severity": "MEDIUM",
    "rule_id": "EMC_MARGIN_FAIL_LT_0DB",
    "rule_version": "1.0.0",
    "clause": "CISPR 25 §6.3",
    "domain": "EMC",
    "evidence": {
      "doc_a": { "measured_dbuv_m": 41, "limit_dbuv_m": 46, "margin_db": 5, "result": "PASS" },
      "doc_b": { "measured_dbuv_m": 47, "limit_dbuv_m": 46, "margin_db": -1, "result": "FAIL" },
      "frequency_hz": 300000000,
      "delta_db": 6
    }
  }
}
```

**Output:** Plain English paragraph, 2–4 sentences, for inclusion in audit report. No JSON wrapper needed.

**Hard rule:** Agent may not alter, reinterpret, or add to the findings it receives. It explains only.

---

### Agent A8: Report Generation Agent

**Invoked at:** Stage 18 — final assembly after all findings, explanations, and review items are complete.

**Model:** `claude-sonnet-4-20250514`

**System Prompt:**
```
You are a compliance audit report generator.

You assemble structured audit reports from a complete set of deterministic findings, agent-generated explanations, ignored change logs, and review queue summaries.

Report structure (mandatory):
1. Executive Summary — counts: matched tests, modified, critical failures, warnings, review_required, ignored
2. Critical Findings (severity HIGH) — sorted by standard clause
3. Warnings (severity MEDIUM) — sorted by domain
4. Informational Changes (severity LOW | INFO)
5. Pending Review Items — items that require human resolution before report is final
6. Ignored Changes Panel — every excluded artifact listed explicitly
7. Traceability Table — each finding → doc + page + bbox + clause + rule_id + rule_version

Rules:
1. Every finding must have a source citation: doc_id, page, clause.
2. The ignored_changes section is MANDATORY. A report without it is incomplete.
3. Never omit any finding regardless of severity.
4. Severity values come from the deterministic rules engine only. Never assign your own.
5. A report may be marked FINAL only if review_required = false OR all review items are resolved.
```

---

## Agent Interaction Rules

| Rule | Description |
|------|-------------|
| **Orchestrator is a state machine, not an LLM** | `ComplianceIngestionOrchestrator` and `ComplianceComparisonOrchestrator` are Python classes, not Claude agents |
| **Docling is a tool, not an agent** | Structural parsing is deterministic — no LLM involved |
| **No agent decides compliance** | PASS/FAIL, margin, and severity come exclusively from `DeterministicRulesEngine` |
| **No agent reads raw documents** | All agents receive structured JSON only |
| **Confidence gating is mandatory** | `confidence < 0.70` → `review_flag = true` → review queue |
| **Schema validation before propagation** | Every agent output is Pydantic-validated before passing downstream |
| **No chained hallucination** | Agent output cannot feed another agent without an intervening deterministic validator |
| **Rules engine runs without LLM** | The entire rules evaluation path must function with all agents offline |
| **Explanation never creates findings** | Agents A7 and A8 receive finalized findings — they do not produce new ones |

---

## Token Budget Guidelines

| Agent | Max Input Tokens | Max Output Tokens | Typical Latency | Batch Strategy |
|-------|-----------------|-------------------|-----------------|----------------|
| A1 Ontology Classifier | 2k | 512 | 1–2s/node | Up to 5 concurrent |
| A2 Relationship Resolution | 3k | 512 | 1–2s/pair | Up to 5 concurrent |
| A3 Alignment Adjudication | 6k | 1k | 2–4s | Sequential (ambiguous only) |
| A4 Split/Merge Alignment | 8k | 1k | 2–5s | Sequential |
| A5 Standard Version Resolver | 2k | 512 | 1s | Up to 10 concurrent |
| A6 Human Review Triage | 16k | 4k | 5–10s | Once per job |
| A7 Explanation | 3k | 512 | 1–2s/finding | Up to 5 concurrent |
| A8 Report Generation | 32k | 8k | 10–20s | Once per comparison |

---

## Failure Handling

```
Agent schema validation failure → confidence = 0.0 → review queue
Agent timeout (> 30s) → retry max 2 → fallback:
  A1: default to "Section" type
  A2: no relationship proposed
  A3: AMBIGUOUS_UNRESOLVABLE → conservative split
  A4: NOT_SPLIT_MERGE
  A5: version = "unknown", review_flag = true
  A6: items grouped by category only, no explanation text
  A7: explanation = "[Explanation unavailable — see finding data]"
  A8: report generated without narrative summaries

Rules engine runs WITHOUT any agents available — this is a hard requirement.
Comparison results can be marked COMPLETED without agents, but
review_required = true must be set for any agent-dependent findings.
```
