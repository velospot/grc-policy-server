# EMC/EMV Compliance Platform — System Architecture

> A deterministic compliance evidence reconciliation engine with AI-assisted explanation.
> Designed for offline SaaS deployment on: 64GB RAM, i9, RTX 5070 Ti 16GB.

---

## Core Design Principle

```
You do NOT solve document comparison by better diffing.
You solve it by eliminating ambiguity BEFORE comparison begins.
```

The system compares **typed compliance entities in a governed ontology**, not text.

---

## 1. High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        Frontend UI                          │
│              React / Next.js — Unified Compliance UI        │
└─────────────────────────┬───────────────────────────────────┘
                          │ REST / WebSocket
                          ▼
┌─────────────────────────────────────────────────────────────┐
│                     API Gateway                             │
│                  FastAPI (Python 3.12)                      │
└──────────┬──────────────┬───────────────┬───────────────────┘
           │              │               │
    ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼────────┐
    │Celery Queue │ │Query Svc   │ │Auth/Audit Log│
    │(async jobs) │ │(read APIs) │ │(immutable)   │
    └──────┬──────┘ └────────────┘ └──────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                   Processing Pipeline                       │
│                                                             │
│  Docling Parser                                             │
│       ↓                                                     │
│  Noise Filter (TOC, headers, figures list, pagination)      │
│       ↓                                                     │
│  Table → EMV Schema Extractor                               │
│       ↓                                                     │
│  Ontology Classifier (Claude Agent + rules)                 │
│       ↓                                                     │
│  BGE-M3 Embedding Engine                                    │
│       ↓                                                     │
│  EMV/Compliance Graph Builder                               │
└─────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────┐
│                      Data Layer                             │
│                                                             │
│  PostgreSQL        Neo4j              Qdrant                │
│  (measurements,    (ontology graph,   (semantic             │
│   job state,       relationships,      embeddings,          │
│   audit log)       clause mapping)     similarity search)   │
└─────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────┐
│                   Comparison Engine                         │
│                                                             │
│  Semantic Alignment Engine (BGE-M3 + Claude Agent)          │
│       ↓                                                     │
│  Graph Diff Engine (deterministic)                          │
│       ↓                                                     │
│  EMV Rules Engine (deterministic PASS/FAIL)                 │
│       ↓                                                     │
│  Evidence Extraction (Claude Agent)                         │
│       ↓                                                     │
│  LLM Explanation Layer (Qwen3 32B / claude-sonnet)          │
└─────────────────────────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────────────────────────┐
│               Caching Layer (3-tier)                        │
│  Document fingerprint | Node-level | Diff result            │
└─────────────────────────────────────────────────────────────┘
```

---

## 2. Processing Pipeline (Detailed)

### Stage 1 — Document Ingestion
```
INPUT: raw PDF / DOCX / scanned report
TOOL:  Docling (structure extraction, table detection, hierarchy reconstruction)
OUTPUT: raw structured JSON with sections, tables, headings, page refs
```

### Stage 2 — Noise Filter
```
REMOVE (never enter comparison graph):
  - Table of contents
  - List of figures / tables
  - Page numbers
  - Headers / footers
  - Document metadata blocks
  - Revision logs (unless required)
  - Pagination artifacts

NORMALIZE (canonicalize before comparison):
  - Extra spaces       → collapse whitespace
  - Line breaks        → sentence reconstruction
  - Font size changes  → ignore
  - Bullet formatting  → normalize to list
  - Hyphenation        → de-hyphenate
  - Capitalization     → preserve semantic case only

LOG: all ignored items → ignored_changes[] (mandatory for audit trust)
```

### Stage 3 — Table Extraction + EMV Schema Conversion
```python
# Table pipeline
Step 1: detect table type
  → measurement_table | configuration_table | reference_table | noise_table

Step 2: classify relevance
  if table_type in ["TOC", "figures", "indexes"]:
    discard  # log in ignored[]

Step 3: schema conversion
  # Convert raw table:
  | Frequency | Limit | Measured |
  # Into structured object:
  {
    "frequency_hz": 300_000_000,
    "limit_dbuv_m": 46,
    "measured_dbuv_m": 41,
    "margin_db": 5,
    "result": "PASS"
  }

Step 4: canonical ordering
  sort by: frequency → test_case_id → measurement_index
  # Ensures stable diff regardless of row order changes
```

### Stage 4 — Ontology Classification (Claude Agent)
```
INPUT:  normalized nodes from Stage 2/3
AGENT:  Ontology Mapping Agent (see AGENTS.md)
OUTPUT: typed compliance entities mapped to universal ontology

Domain mappings:
  EMC: Requirement=CISPR clause, Observation=test result, Threshold=emission limit,
       Deviation=failed test, Control=test setup
  Safety: Requirement=ISO 45001 clause, Observation=incident/inspection,
          Threshold=risk level, Control=PPE/procedure
  Environmental: Requirement=ISO 14001/permit, Observation=emission measurement,
                 Threshold=regulatory limit, Control=mitigation
```

### Stage 5 — Embedding Index
```
MODEL:  BGE-M3 (local, GPU-accelerated on RTX 5070 Ti)
STORE:  Qdrant
PER NODE:
  - content_embedding
  - parent_context_embedding
  - child_aggregation_embedding
PURPOSE: semantic alignment, moved/renamed section detection
```

### Stage 6 — Graph Storage
```
Neo4j schema:
  (Document)-[:CONTAINS]->(Section)
  (Section)-[:IMPLEMENTS]->(Requirement)
  (Observation)-[:EVIDENCE_FOR]->(Requirement)
  (Observation)-[:EVALUATED_AGAINST]->(Threshold)
  (Observation)-[:RESULTS_IN]->(Deviation)
  (Deviation)-[:MITIGATED_BY]->(Control)
  (Requirement)-[:PART_OF]->(Standard)
  (Risk)-[:DERIVED_FROM]->(Deviation)

EMV extension:
  (Measurement)-[:HAS_FREQUENCY]->(Frequency)
  (Measurement)-[:HAS_LIMIT]->(Threshold)
  (Measurement)-[:HAS_RESULT]->(PassFail)
  (TestSetup)-[:INFLUENCES]->(Measurement)
```

### Stage 7 — Semantic Alignment Engine
```
ALGORITHM:
  composite_score = (
    semantic_similarity     * 0.50  # BGE-M3 cosine
    + structural_similarity * 0.30  # parent/child context match
    + measurement_overlap   * 0.20  # numeric value overlap
  )

DECISIONS:
  score > 0.85  → SAME_NODE (moved or renamed)
  0.60 – 0.85  → MODIFIED_NODE (content changed)
  < 0.60        → NEW_NODE or DELETED_NODE
```

### Stage 8 — Deterministic Rules Engine
```python
# PASS/FAIL is always deterministic — never LLM
def evaluate_measurement(obs, threshold, standard):
    margin = threshold.limit - obs.measured
    result = "PASS" if margin >= 0 else "FAIL"
    severity = compute_severity(margin, standard.tolerance_model)
    return ComplianceResult(result=result, margin=margin, severity=severity)

# Uncertainty model (optional advanced)
def evaluate_with_uncertainty(obs, threshold, k=2):
    # Coverage factor k=2 → 95% confidence interval
    combined_uncertainty = sqrt(obs.u_meas**2 + threshold.u_limit**2)
    lower_bound = obs.measured - k * combined_uncertainty
    return evaluate_measurement_with_bound(lower_bound, threshold)
```

### Stage 9 — LLM Explanation Layer
```
MODEL:  Qwen3 32B (Ollama, local) OR claude-sonnet (API)
ROLE:   explanation and report generation ONLY
NEVER:  compliance decisions, measurement computation, threshold evaluation

Used for:
  ✓ Natural language explanation of findings
  ✓ Audit report narrative
  ✓ Ontology classification of ambiguous nodes
  ✗ PASS/FAIL decisions
  ✗ Numeric computation
  ✗ Standard interpretation
```

---

## 3. Data Model

### 3.1 Canonical Document Node
```json
{
  "node_id": "sha256_of_content_plus_context",
  "type": "clause | test_case | measurement | setup | section",
  "ontology_type": "Requirement | Observation | Measurement | Threshold | Control | Evidence | Deviation",
  "title": "Radiated Emissions",
  "content": "...",
  "semantic_embedding": [0.021, -0.134, ...],
  "domain": "EMC",
  "source": {
    "doc_id": "doc_abc",
    "page": 12,
    "bbox": [x1, y1, x2, y2]
  },
  "properties": {
    "standard_ref": "CISPR 25",
    "clause": "6.3",
    "frequency_hz": 300000000,
    "limit": 46,
    "measured": 41,
    "margin": 5,
    "result": "PASS",
    "unit": "dBµV/m"
  }
}
```

### 3.2 EMV Test Case Model
```json
{
  "test_case_id": "TC-001",
  "standard": "CISPR 25",
  "version": "2021",
  "type": "radiated_emissions",
  "setup": {
    "chamber": "ALSE",
    "distance_m": 3,
    "antenna_height_m": 1.5,
    "detector": "quasi-peak"
  },
  "measurements": [
    {
      "frequency_hz": 300000000,
      "limit_dbuv_m": 46,
      "measured_dbuv_m": 41,
      "margin_db": 5,
      "result": "PASS",
      "uncertainty_db": 3.2
    }
  ]
}
```

### 3.3 Comparison Finding Model
```json
{
  "finding_id": "find_xyz",
  "comparison_id": "cmp_789",
  "type": "measurement_failure | limit_change | setup_change | requirement_change | status_change",
  "severity": "HIGH | MEDIUM | LOW | INFO",
  "clause": "CISPR 25 §6.3",
  "domain": "EMC",
  "alignment": "MODIFIED_NODE",
  "evidence": {
    "doc_a": { "value": "41 dBµV/m", "result": "PASS", "margin": "+5 dB" },
    "doc_b": { "value": "47 dBµV/m", "result": "FAIL", "margin": "-1 dB" },
    "delta": "+6 dBµV/m"
  },
  "source_a": { "doc_id": "doc_123", "page": 12, "clause": "6.3" },
  "source_b": { "doc_id": "doc_456", "page": 14, "clause": "6.3" },
  "explanation": "...",
  "ignored": false
}
```

---

## 4. Caching Architecture (3-Tier)

### Tier 1 — Document Fingerprint Cache
```
raw_hash          → skip re-upload
parsed_hash       → skip re-parsing
normalized_hash   → skip noise filtering + normalization
```

### Tier 2 — Node-Level Cache
```
section_hash      → skip re-embedding
clause_hash       → skip re-classification
measurement_hash  → skip re-extraction
```

### Tier 3 — Diff Result Cache
```
(doc_a_id, doc_b_id, mode) → full diff result
TTL: 24h for in-progress, indefinite for completed+approved
```

---

## 5. Ontology Governance

### Version Model
```
ontology_v1.0     → core: EMC only
ontology_v1.1     → + Safety extension
ontology_v1.2     → + Environmental extension
ontology_v2.0     → breaking: restructured relationships
```

### Validation Rules (enforced before graph write)
```python
if node.type == "Measurement":
    assert node.has_link("Threshold"), "Measurement must have Threshold"

if node.type == "Deviation":
    assert node.has_link("Requirement"), "Deviation must link to Requirement"
    assert node.has_link("Observation"), "Deviation must link to Observation"

if node.type == "Requirement":
    assert node.has_link("Standard"), "Requirement must reference Standard"
```

### Change Control Workflow
```
1. Propose ontology change (JIRA ticket / PR)
2. Domain expert review (EMC engineer / Safety officer)
3. Impact simulation on existing graphs
4. Approve → version bump
5. Migrate existing mappings
6. Audit log entry
```

---

## 6. Hardware Deployment Profile

Target: 64GB RAM, Intel i9, NVIDIA RTX 5070 Ti 16GB

| Component | Deployment | VRAM / RAM |
|-----------|-----------|------------|
| BGE-M3 (embedding) | Local GPU | ~4 GB VRAM |
| Qwen3 32B (Ollama) | Local GPU | ~12 GB VRAM (Q4) |
| Qdrant | Local RAM | ~8 GB RAM |
| Neo4j | Local RAM | ~16 GB RAM |
| PostgreSQL | Local | ~4 GB RAM |
| FastAPI + Celery | Local | ~4 GB RAM |
| Frontend (Next.js) | Local | ~2 GB RAM |

**Supported throughput:**
- 5k–50k pages/day ingestion
- 100k–500k measurement points
- 2–3 concurrent document comparisons
- < 2–5 seconds for cached diff results

---

## 7. Security & Audit Requirements

| Requirement | Implementation |
|-------------|---------------|
| Immutable audit log | Append-only PostgreSQL table, signed entries |
| Every finding traceable | finding → source doc + page + clause |
| Every classification logged | model version + confidence + timestamp |
| Ontology version pinned | all graphs tagged with ontology_version |
| No PII in graph | document content hashed, not stored raw |
| Role-based access | FastAPI + JWT, scoped per domain (EMC / Safety / EHS) |

---

## 8. Key Architectural Invariants

1. **LLM never decides compliance** — only the deterministic rules engine does
2. **No position-based identity** — all node IDs are semantic hashes
3. **Ignored changes are always surfaced** — audit trust depends on it
4. **Ontology version is immutable** — changes require version bumps
5. **Embeddings + ontology are complementary** — ontology for structure, embeddings for fuzzy matching
6. **Every stage is independently cacheable** — processing is idempotent
