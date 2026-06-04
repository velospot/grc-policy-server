# EMC/EMV Compliance Platform — System Architecture

> A deterministic compliance evidence reconciliation engine with AI-assisted explanation.
> Designed for offline SaaS deployment on: 64GB RAM, Intel Core Ultra 9 285, RTX 5070 Ti 16GB.

---

## Core Design Principles

```
1. Orchestrate around evidence state transitions, not LLM calls.
2. Every stage produces a typed artifact. No stage passes loose text downstream.
3. Deterministic tools run before agents. Agents are called only for ambiguity.
4. The rules engine must run without any LLM available.
5. Ignored artifacts are never silently dropped — auditors must see what was excluded.
```

The system compares **typed compliance entities in a governed ontology**, not raw text or parsed documents.

---

## 1. High-Level System Map

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend UI                             │
│               React / Next.js — Unified Compliance UI           │
└───────────────────────────┬─────────────────────────────────────┘
                            │ REST / WebSocket
                            ▼
┌─────────────────────────────────────────────────────────────────┐
│                       API Gateway                               │
│                    FastAPI (Python 3.12)                        │
└──────────┬──────────────────┬────────────────┬──────────────────┘
           │                  │                │
    ┌──────▼──────┐    ┌──────▼──────┐  ┌──────▼──────┐
    │Celery Queue │    │ Query Svc   │  │Auth/Audit   │
    │ (Redis)     │    │ (read APIs) │  │Log (PG)     │
    └──────┬──────┘    └─────────────┘  └─────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────────┐
│          ComplianceIngestionOrchestrator (state machine)        │
│                                                                 │
│  Stage 0: Job Intake                                            │
│  Stage 1: Document Quality Gate         ← deterministic        │
│  Stage 2: Structural Parsing (Docling)  ← deterministic        │
│  Stage 3: DoclingGraph Object Extract   ← deterministic        │
│  Stage 4: Noise Filter + Canonicalize   ← deterministic        │
│  Stage 5: Table Classification + Norm   ← deterministic        │
│  Stage 6: Standard Registry Resolution  ← hybrid              │
│  Stage 7: Ontology Mapping              ← hybrid              │
│  Stage 8: Stable Identity Resolution    ← hybrid              │
│  Stage 9: Tri-layer Graph Build         ← deterministic        │
│  Stage 10: Relationship Building        ← hybrid              │
│  Stage 11: Evidence Chain Validation    ← deterministic        │
│  Stage 12: Embedding + Vector Index     ← deterministic        │
│  → mark_ready_for_comparison                                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │ (both docs ready)
                               ▼
┌─────────────────────────────────────────────────────────────────┐
│        ComplianceComparisonOrchestrator (state machine)         │
│                                                                 │
│  Stage 13: Semantic Alignment           ← hybrid              │
│  Stage 14: Split/Merge Alignment        ← hybrid              │
│  Stage 15: Graph Diff                   ← deterministic        │
│  Stage 16: Deterministic Rules Engine   ← deterministic        │
│  Stage 17: Review Queue Triage          ← hybrid              │
│  Stage 18: Explanation + Report         ← agent               │
│  → COMPLETED                                                    │
└──────────────────────────────┬──────────────────────────────────┘
                               │
           ┌───────────────────┼───────────────────┐
           ▼                   ▼                   ▼
     ┌───────────┐      ┌────────────┐      ┌────────────┐
     │PostgreSQL │      │   Neo4j    │      │  Qdrant    │
     │jobs,      │      │tri-layer   │      │embeddings  │
     │findings,  │      │ontology    │      │similarity  │
     │audit log  │      │graph       │      │search      │
     └───────────┘      └────────────┘      └────────────┘
```

---

## 2. Job State Machine

### Ingestion States
```
UPLOADED
  → QUALITY_CHECKED
  → PARSED
  → EXTRACTED
  → CANONICALIZED
  → TABLES_NORMALIZED
  → STANDARDS_RESOLVED
  → ONTOLOGY_MAPPED
  → IDENTITIES_RESOLVED
  → GRAPH_BUILT
  → RELATIONSHIPS_BUILT
  → EVIDENCE_VALIDATED
  → EMBEDDED
  → READY_FOR_COMPARISON
  → FAILED (any stage)
```

### Comparison States
```
COMPARISON_REQUESTED
  → ALIGNED
  → SPLIT_MERGE_ALIGNED
  → DIFFED
  → RULES_EVALUATED
  → REVIEW_TRIAGED
  → REPORTED
  → COMPLETED
  → FAILED (any stage)
```

### Job State Object
```json
{
  "job_id": "job_123",
  "doc_id": "doc_456",
  "state": "ONTOLOGY_MAPPED",
  "progress": 52,
  "artifacts": {
    "raw_uri": "storage://docs/doc_456.pdf",
    "parsed_uri": "storage://artifacts/doc_456_parsed.json",
    "canonical_nodes_uri": "storage://artifacts/doc_456_canonical.json",
    "ontology_nodes_uri": "storage://artifacts/doc_456_ontology.json",
    "ignored_changes_uri": "storage://artifacts/doc_456_ignored.json"
  },
  "risk": {
    "extraction_risk": "MEDIUM",
    "ocr_used": false,
    "review_required": true,
    "review_reason": "2 low-confidence ontology classifications"
  },
  "resume_from": "ONTOLOGY_MAPPED"
}
```

**Hard rule:** A failed stage writes a structured failure artifact and sets `resume_from`. Jobs can resume from the last successful state without reprocessing earlier stages.

---

## 3. Ingestion Pipeline (Detailed)

### Stage 0 — Job Intake
```
TOOL:   FastAPI Gateway
TYPE:   Deterministic

Input:  { file: binary, doc_type, standard_hint, domain }
Output: { doc_id, job_id, status: "queued" }

Actions:
- Compute raw document hash
- Deduplicate (same hash = skip re-ingestion)
- Store raw file to object storage
- Create job record in PostgreSQL
- Write audit event: DOCUMENT_UPLOADED
- Push to Celery queue

Do NOT:
- Parse document synchronously
- Call any agent
- Create compliance findings
```

### Stage 1 — Document Quality Gate
```
TOOL:   DocumentQualityGate
TYPE:   Deterministic (agent optional for ambiguous summaries)

Output:
{
  "quality": {
    "native_pdf": true,
    "ocr_required": false,
    "table_density": 0.42,
    "numeric_density": 0.35,
    "extraction_risk": "LOW | MEDIUM | HIGH",
    "review_flag": false
  }
}

Actions:
- Detect native PDF vs scanned (OCR required)
- Estimate table density and numeric density
- Detect language
- Classify extraction risk

Rule: if OCR used on measurement-heavy pages →
  downstream measurement findings carry extraction_confidence
  and may require review_required = true
```

### Stage 2 — Structural Parsing
```
TOOL:   DoclingParser
TYPE:   Deterministic extraction

Actions:
- Extract sections, headings, tables, figures
- Extract page references and bounding boxes
- Preserve source traceability (page + bbox)
- Output raw structure — do NOT filter or classify

Do NOT:
- Decide what is compliance-relevant
- Drop content silently
- Convert measurements
```

### Stage 3 — DoclingGraph Object Extraction
```
TOOL:   DoclingGraphAdapter
TYPE:   Hybrid (output must be schema-validated)

Actions:
- Convert Docling structure into typed candidate objects
- Emit CandidateMeasurementTable, CandidateSection, CandidateClause
- Preserve layout traceability to source page/bbox
- Output candidate objects — NOT final ontology entities

Rule: DoclingGraph output is NOT trusted until it passes CanonicalObjectValidator
```

### Stage 4 — Noise Filter + Canonicalization
```
TOOLS:  NoiseFilter, CanonicalTextNormalizer, IgnoredChangeLogger
TYPE:   Deterministic

REMOVE (never enter comparison graph):
  - Table of contents
  - List of figures / tables
  - Page numbers
  - Headers / footers
  - Document metadata blocks
  - Revision logs (unless domain-required)
  - Pagination artifacts

NORMALIZE:
  - Extra spaces       → collapse whitespace
  - Line breaks        → sentence reconstruction
  - Bullet formatting  → normalize to list
  - Hyphenation        → de-hyphenate
  - Capitalization     → preserve semantic case only

Hard rule: No ignored artifact may disappear silently.
Every removed item → ignored_changes[] with: type, source_page, reason
```

### Stage 5 — Table Classification + Measurement Normalization
```
TOOLS:  TableClassifier, MeasurementTableExtractor, UnitNormalizer, MeasurementValidator
TYPE:   Deterministic (agent optional for ambiguous table type)

Table types: measurement_table | configuration_table | reference_table | noise_table

For measurement_table:
  Step 1: header detection and unit extraction
  Step 2: canonical unit conversion (all frequencies → Hz, all levels → dBµV/m)
  Step 3: margin computation if absent: margin = limit - measured
  Step 4: result computation if absent: PASS if margin >= 0, else FAIL
  Step 5: canonical ordering (sort by frequency → test_case_id)
  Step 6: validation (monotonic freq check, plausible dB range check)

Hard rule: Values from OCR or low-confidence cells carry extraction_confidence.
```

### Stage 6 — Standard Registry Resolution
```
TOOLS:  StandardRegistryService, ClauseNormalizer
AGENT:  StandardVersionResolverAgent (A5) — only for unresolved versions
TYPE:   Hybrid

Actions:
- Normalize standard aliases (CISPR25 → cispr_25)
- Normalize clause references
- Detect and flag missing or ambiguous versions
- Flag cross-version comparisons (e.g. CISPR 25:2016 vs 2021)

Hard rule: Never infer a missing standard version silently.
Missing version → version = "unknown", review_flag = true
```

### Stage 7 — Ontology Mapping
```
TOOLS:  KeywordClassifier (deterministic first), OntologySchemaValidator, OntologyRegistry
AGENT:  OntologyClassifierAgent (A1) — only when keyword classifier result = "ambiguous"
TYPE:   Hybrid — deterministic validation always runs after agent

Every output validated against OntologySchema before graph write.

Hard rule: confidence < 0.70 → review_flag = true → review queue
Hard rule: Every classification → audit log entry (node_id, model_version, confidence, timestamp)
```

### Stage 8 — Stable Identity Resolution
```
TOOLS:  StableIdentityResolver (deterministic), StableIdentityResolverAgent (hybrid)
TYPE:   Hybrid

Stable ID construction priority:
  1. standard_ref + clause + semantic_title_hash  (highest confidence)
  2. standard_ref + content_hash
  3. parent_context + semantic_title_hash
  4. content_hash only  (review_flag = true)

Hard rule: Page number is NEVER used as identity.
Hard rule: Stable ID must be deterministic given the same input.
```

### Stage 9 — Tri-layer Knowledge Graph Construction
```
TOOLS:  MetaGraphBuilder, LayoutGraphBuilder, ComplianceGraphBuilder, Neo4jWriter
TYPE:   Deterministic

MetaGraph layer:
  Document, Source, Standard, Version, Language, UploadMetadata

LayoutGraph layer:
  Page, Section, Heading, Table, Row, Cell, BBox
  (preserves traceability from compliance nodes to layout source)

ComplianceGraph layer:
  Requirement, Observation, Measurement, Threshold, Control,
  Evidence, Deviation, Risk, Incident, Action, Standard

Hard rule: TOC nodes may exist in LayoutGraph. NEVER in ComplianceGraph.
Hard rule: Compliance nodes must have a traceability link to their LayoutGraph source.

Graph write is idempotent (upsert by stable_id).
```

### Stage 10 — Relationship Building
```
TOOLS:  RelationshipBuilder (layers A/B/C), RelationshipValidator, Neo4jWriter
AGENT:  RelationshipResolutionAgent (A2) — Layer D only
TYPE:   Hybrid — deterministic first, agent last

Layer A (deterministic): structural relationships
  (Document)-[:CONTAINS]->(Section)
  (Section)-[:IMPLEMENTS]->(Requirement)

Layer B (deterministic): ontology/rule relationships
  (Observation)-[:EVIDENCE_FOR]->(Requirement)
  (Observation)-[:EVALUATED_AGAINST]->(Threshold)
  (Observation)-[:RESULTS_IN]->(Deviation)
  (Deviation)-[:MITIGATED_BY]->(Control)
  (Requirement)-[:PART_OF]->(Standard)
  (Risk)-[:DERIVED_FROM]->(Deviation)

Layer C (deterministic): explicit ID cross-references in document text
  (Measurement)-[:HAS_LIMIT]->(Threshold)
  (TestSetup)-[:INFLUENCES]->(Measurement)

Layer D (agent, A2): semantic relationships without explicit IDs
  reviewed_flag = true on all Layer D edges

Every edge includes:
  { source: "layerA|B|C|D", confidence, ontology_version, model_version, review_flag }

Hard rule: Agent-proposed edges do not bypass RelationshipValidator.
```

### Stage 11 — Evidence Chain Validation
```
TOOL:   EvidenceChainValidator, ReviewQueueWriter
TYPE:   Deterministic

For every finding candidate, verify:
  ✓ Measurement links to at least one Threshold
  ✓ Threshold links to at least one Requirement
  ✓ Requirement links to a Standard
  ✓ Source location (doc_id + page) exists

chain_status: COMPLETE | INCOMPLETE | PARTIAL

INCOMPLETE or PARTIAL → review queue
Hard rule: No auditor-facing finding without complete source traceability.
```

### Stage 12 — Embedding + Vector Indexing
```
TOOL:   BGE-M3EmbeddingEngine, QdrantIndexer, EmbeddingCache
TYPE:   Deterministic model inference (GPU, RTX 5070 Ti)
MODEL:  BAAI/bge-m3 (local)

Per node, generate:
  - content_embedding (title + content + ontology_type)
  - parent_context_embedding (parent section context)
  - child_aggregation_embedding (aggregated children)

Index in Qdrant by: doc_id, ontology_type, domain
Cache key: sha256(normalized_content + ontology_type + parent_context)
```

---

## 4. Comparison Pipeline (Detailed)

### Stage 13 — Semantic Alignment
```
TOOLS:  SemanticAlignmentEngine, QdrantSimilaritySearch
AGENT:  AlignmentAdjudicationAgent (A3) — AMBIGUOUS cases only
TYPE:   Hybrid

Scoring:
  composite_score = (
    semantic_similarity     * 0.50   # BGE-M3 cosine
    + structural_similarity * 0.30   # parent/sibling context match
    + measurement_overlap   * 0.20   # numeric value overlap
  )

Decisions (deterministic):
  composite > 0.85         → SAME_NODE
  0.60 – 0.85              → MODIFIED_NODE
  top-2 within 0.05        → AMBIGUOUS → invoke Agent A3
  A3 returns UNRESOLVABLE  → DELETED_NODE + NEW_NODE + review queue
  < 0.60                   → NEW_NODE or DELETED_NODE

Hard rule: Nodes of different ontology_type are never merged.
```

### Stage 14 — Split/Merge Alignment
```
TOOLS:  SplitMergeDetector (deterministic signals), SplitMergeAlignmentAgent (A4)
TYPE:   Hybrid

Structural signals (deterministic):
  - 1 parent in A matches 2+ children in B by heading prefix
  - aggregate embedding score of children vs parent > 0.75

Agent A4 confirms: SPLIT | MERGE | NOT_SPLIT_MERGE

Purpose: prevent a section reorganization from generating false DELETED_NODE + NEW_NODE findings
```

### Stage 15 — Graph Diff
```
TOOL:   GraphDiffEngine (deterministic)
TYPE:   Deterministic

Diff types produced:
  NODE_ADDED, NODE_REMOVED, NODE_MODIFIED
  EDGE_ADDED, EDGE_REMOVED, EDGE_RETARGETED
  MEASUREMENT_CHANGED, THRESHOLD_CHANGED
  STANDARD_VERSION_CHANGED, EVIDENCE_CHAIN_CHANGED

Output: diff_candidates[]

Hard rule: GraphDiffEngine detects changes. It does NOT assign compliance severity.
```

### Stage 16 — Deterministic Rules Engine
```
TOOL:   DeterministicRulesEngine, RuleRegistry, UncertaintyModel
TYPE:   Fully deterministic — never calls LLM

Input: diff_candidate (MEASUREMENT_CHANGED or THRESHOLD_CHANGED)
Output: finding with result, margin, severity, rule_id, rule_version

Severity policy (deterministic):
  margin >= +3 dB   → INFO       (comfortable pass)
  0 <= margin < +3  → LOW        (marginal pass)
  -3 <= margin < 0  → MEDIUM     (fail, close to limit)
  margin < -3 dB    → HIGH       (significant fail)

Uncertainty model (optional):
  effective_measured = measured + (k * combined_uncertainty), k=2 (95% CI)

Finding output must include:
  rule_id, rule_version, source_doc, page, bbox, clause, standard,
  evidence_chain_id, extraction_confidence

Hard rule: This engine must run without any LLM available.
Hard rule: PASS/FAIL is NEVER computed by any agent.
```

### Stage 17 — Review Queue Triage
```
TOOLS:  ReviewQueueWriter
AGENT:  HumanReviewTriageAgent (A6)
TYPE:   Hybrid

Inputs collected from all prior stages:
  - Low-confidence ontology mappings (< 0.70)
  - Ambiguous alignments (AMBIGUOUS_UNRESOLVABLE)
  - Incomplete evidence chains
  - OCR-risk measurements
  - Unresolved standard versions
  - Agent-proposed relationships (Layer D edges)

Agent A6 groups and prioritizes, writes to review queue.

Hard rule: Agent NEVER resolves review items — only groups and explains them.
Hard rule: Comparison cannot be marked COMPLETED unless:
  - review_required = false, OR
  - all review items explicitly resolved by human
```

### Stage 18 — Explanation + Report Generation
```
AGENTS: ExplanationAgent (A7), ReportGenerationAgent (A8)
TYPE:   Agent-assisted text generation over deterministic findings

A7 produces 2–4 sentence explanation per finding.
A8 assembles full report:
  1. Executive Summary
  2. Critical Findings (HIGH severity)
  3. Warnings (MEDIUM severity)
  4. Informational Changes (LOW | INFO)
  5. Pending Review Items
  6. Ignored Changes Panel (MANDATORY)
  7. Traceability Table

Hard rule: Explanation agents describe findings — they do not create them.
Hard rule: Report without ignored_changes panel is incomplete.
```

---

## 5. Data Models

### 5.1 Canonical Node
```json
{
  "node_id": "sha256_of_content_plus_context",
  "stable_id": "req.cispr25.6_3.radiated_emissions",
  "ontology_type": "Requirement | Observation | Measurement | Threshold | Control | Evidence | Deviation",
  "title": "Radiated Emissions",
  "content": "...",
  "domain": "EMC",
  "source": {
    "doc_id": "doc_abc",
    "page": 12,
    "bbox": [x1, y1, x2, y2]
  },
  "properties": {
    "standard_ref": "CISPR 25",
    "standard_version": "2021",
    "clause": "6.3",
    "frequency_hz": 300000000,
    "limit_dbuv_m": 46,
    "measured_dbuv_m": 41,
    "margin_db": 5,
    "unit": "dBµV/m",
    "extraction_confidence": 0.97
  },
  "classification": {
    "method": "keyword | agent",
    "model_version": "claude-sonnet-4-20250514",
    "confidence": 0.94
  }
}
```

### 5.2 Graph Edge
```json
{
  "from": "measurement_123",
  "to": "threshold_456",
  "rel_type": "EVALUATED_AGAINST",
  "source": "layerB",
  "confidence": 0.95,
  "ontology_version": "v1.2",
  "model_version": null,
  "review_flag": false
}
```

### 5.3 Finding
```json
{
  "finding_id": "find_xyz",
  "comparison_id": "cmp_789",
  "type": "measurement_failure | limit_change | setup_change | requirement_change | status_change",
  "severity": "HIGH | MEDIUM | LOW | INFO",
  "result": "FAIL",
  "rule_id": "EMC_MARGIN_FAIL_LT_0DB",
  "rule_version": "1.0.0",
  "clause": "CISPR 25 §6.3",
  "standard_id": "cispr_25",
  "standard_version": "2021",
  "domain": "EMC",
  "alignment_decision": "MODIFIED_NODE",
  "evidence": {
    "doc_a": { "measured_dbuv_m": 41, "limit_dbuv_m": 46, "margin_db": 5, "result": "PASS" },
    "doc_b": { "measured_dbuv_m": 47, "limit_dbuv_m": 46, "margin_db": -1, "result": "FAIL" },
    "frequency_hz": 300000000,
    "delta_db": 6
  },
  "source_a": { "doc_id": "doc_123", "page": 12, "bbox": [...], "clause": "6.3" },
  "source_b": { "doc_id": "doc_456", "page": 14, "bbox": [...], "clause": "6.3" },
  "evidence_chain_id": "chain_abc",
  "extraction_confidence": 0.97,
  "explanation": "...",
  "ignored": false
}
```

---

## 6. Orchestrator Implementation Reference

```python
class ComplianceIngestionOrchestrator:
    """Deterministic state machine. Not an LLM."""

    STAGES = [
        "quality_gate", "parse", "extract_docling_graph",
        "canonicalize", "normalize_tables", "resolve_standards",
        "map_ontology", "resolve_stable_identities",
        "build_graph", "build_relationships",
        "validate_evidence_chains", "embed_nodes",
        "mark_ready_for_comparison"
    ]

    def run(self, job_id: str):
        job = self.load_job(job_id)
        start_stage = job.resume_from or "quality_gate"

        for stage in self.stages_from(start_stage):
            try:
                self.transition(job_id, stage)
                result = self.run_stage(stage, job_id)
                self.save_artifact(job_id, stage, result)
                self.write_audit_event(job_id, stage, "SUCCESS")
            except AgentFailure as e:
                self.route_to_review(job_id, stage, e)
                # Continue — agent failures don't crash pipeline
            except DeterministicFailure as e:
                self.mark_failed(job_id, stage, e)
                return  # Deterministic failures halt

class ComplianceComparisonOrchestrator:
    """Deterministic state machine. Not an LLM."""

    def run(self, comparison_id: str, doc_a: str, doc_b: str):
        for stage in ["align_nodes", "detect_split_merge", "diff_graphs",
                      "evaluate_rules", "validate_findings",
                      "triage_review_items", "generate_explanations",
                      "generate_report"]:
            self.execute_stage(comparison_id, stage)
```

---

## 7. Message Bus Topics

```
# Ingestion pipeline
document.uploaded
document.quality_checked
document.parsed
document.extracted
document.canonicalized
document.tables_normalized
document.standards_resolved
document.ontology_mapped
document.identities_resolved
document.graph_built
document.relationships_built
document.evidence_validated
document.embedded
document.ready

# Comparison pipeline
comparison.requested
comparison.aligned
comparison.split_merge_aligned
comparison.diffed
comparison.rules_evaluated
comparison.review_triaged
comparison.reported
comparison.completed

# Review and error
review.item_added
review.item_resolved
job.failed
job.review_required
```

---

## 8. Tool Category Reference

### Deterministic Tools (trusted for compliance-critical operations)
DoclingParser, NoiseFilter, CanonicalTextNormalizer, IgnoredChangeLogger,
TableClassifier, MeasurementTableExtractor, UnitNormalizer, MeasurementValidator,
StandardRegistryService, OntologySchemaValidator, OntologyRegistry,
StableIdentityResolver, MetaGraphBuilder, LayoutGraphBuilder, ComplianceGraphBuilder,
RelationshipBuilder (layers A/B/C), RelationshipValidator, Neo4jWriter,
EvidenceChainValidator, BGE-M3EmbeddingEngine, QdrantIndexer,
SemanticAlignmentEngine, SplitMergeDetector,
GraphDiffEngine, DeterministicRulesEngine, RuleRegistry,
ReviewQueueWriter, AuditLogWriter

### Agent-Assisted Tools (probabilistic, confidence-gated)
OntologyClassifierAgent (A1), RelationshipResolutionAgent (A2),
AlignmentAdjudicationAgent (A3), SplitMergeAlignmentAgent (A4),
StandardVersionResolverAgent (A5), HumanReviewTriageAgent (A6),
ExplanationAgent (A7), ReportGenerationAgent (A8)

---

## 9. Caching Architecture (3-Tier)

### Tier 1 — Document Fingerprint Cache (Redis)
```
raw_hash         → skip re-upload
parsed_hash      → skip re-parsing
normalized_hash  → skip noise filter + normalization
```

### Tier 2 — Node-Level Cache (Redis)
```
sha256(content + ontology_type + parent_context) → skip re-embedding
clause_hash                                       → skip re-classification
measurement_hash                                  → skip re-extraction
```

### Tier 3 — Diff Result Cache (Redis + PostgreSQL)
```
(doc_a_id, doc_b_id, mode, ontology_version) → full diff result
TTL: 24h in-progress, indefinite for COMPLETED + review_required = false
```

---

## 10. Tri-layer Graph Design

```
MetaGraph (document provenance):
  Document → Source → Standard → Version → UploadMetadata

LayoutGraph (document structure):
  Page → Section → Heading → Table → Row → Cell → BBox
  (preserves source traceability; used by auditors for evidence review)

ComplianceGraph (semantic compliance model):
  Requirement → Observation → Measurement → Threshold
  Deviation → Control → Risk → Evidence
  (used by diff engine and rules engine)

Cross-layer link:
  ComplianceNode -[:SOURCED_FROM]-> LayoutNode
  LayoutNode -[:BELONGS_TO]-> MetaDocument

Hard rule: Layout artifacts (TOC, headers, pagination) never appear in ComplianceGraph.
```

---

## 11. Ontology Governance

### Version Model
```
v1.0   → core: EMC only
v1.1   → + Safety extension (ISO 45001)
v1.2   → + Environmental extension (ISO 14001)
v2.0   → breaking: restructured relationships (requires migration)
```

### Validation Rules (enforced at Stage 9 before Neo4j write)
```python
VALIDATORS = {
    "Measurement": lambda n: n.has_link("Threshold"),
    "Deviation":   lambda n: n.has_link("Requirement") and n.has_link("Observation"),
    "Requirement": lambda n: n.has_link("Standard"),
    "Observation": lambda n: n.has_link("Requirement") or n.has_link("Threshold"),
}
```

### Change Control
```
1. Propose change (PR + JIRA)
2. Domain expert review
3. Impact simulation on existing graphs
4. Approve → version bump (minor or major)
5. Migrate existing graphs
6. Write ontology_change audit event
```

---

## 12. Hardware Deployment Profile

Target: 64GB RAM, Intel Core Ultra 9 285 (24-core), NVIDIA RTX 5070 Ti 16GB

| Component | Deployment | Resource |
|-----------|-----------|----------|
| BGE-M3 (embedding) | Local GPU | ~4 GB VRAM |
| Qwen3 32B (Ollama, fallback LLM) | Local GPU | ~12 GB VRAM (Q4_K_M) |
| Qdrant | Local | ~8 GB RAM |
| Neo4j | Local | ~16 GB RAM |
| PostgreSQL | Local | ~4 GB RAM |
| Redis (queue + cache) | Local | ~4 GB RAM |
| FastAPI + Celery workers | Local | ~4 GB RAM |
| Frontend (Next.js) | Local | ~2 GB RAM |

**Throughput targets:**
- 5k–50k pages/day ingestion
- 100k–500k measurement points in Qdrant
- 2–3 concurrent comparisons
- < 2–5 seconds for cached diff results

---

## 13. Security & Audit Requirements

| Requirement | Implementation |
|-------------|---------------|
| Immutable audit log | Append-only PostgreSQL table, no UPDATE/DELETE rules |
| Every finding traceable | finding → doc_id + page + bbox + clause + rule_id + rule_version |
| Every agent call logged | node_id + model_version + confidence + input_hash + output_hash + timestamp |
| Ontology version pinned | all graphs and findings tagged with ontology_version |
| Evidence chain required | no finding without chain_status = COMPLETE or explicit review_required |
| Role-based access | FastAPI + JWT, domain-scoped (EMC / Safety / EHS / Admin) |
| Resume capability | every job can restart from last successful stage |

---

## 14. Key Architectural Invariants

1. **Orchestrator is a state machine, not an LLM** — no agent controls the pipeline
2. **Deterministic-first** — agents are called only when deterministic tools are insufficient
3. **Rules engine runs without LLM** — PASS/FAIL must be computable offline
4. **No position-based identity** — page numbers are never used as node IDs
5. **Ignored changes are always surfaced** — audit trust requires explicit exclusion logs
6. **Tri-layer graph separation** — layout artifacts never contaminate the compliance graph
7. **Evidence chain is mandatory** — no finding reaches auditors without source traceability
8. **Ontology version is immutable** — changes require version bumps and migration
9. **Agent outputs are schema-validated** — hallucinated fields are rejected before propagation
10. **Every stage produces a typed artifact** — no loose text passes between stages
