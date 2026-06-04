# EMC/EMV Compliance Platform — Build Skills

> Modular implementation skills for each subsystem. Each skill is a self-contained unit that can be built and tested independently.
>
> **Critical:** Build the orchestration state machine FIRST. Adding pipeline stages without a working state machine produces an uncontrolled chain of LLM calls, not a compliance system.

---

## Skill Index

| # | Skill | Module Path | Priority | Phase |
|---|-------|------------|----------|-------|
| 1 | Orchestration State Machine | `services/orchestration/` | P0 | 0 |
| 2 | Typed Artifact Contracts | `services/contracts/` | P0 | 0 |
| 3 | Tool Registry | `services/orchestration/tool_registry.py` | P0 | 0 |
| 4 | Audit Log System | `services/audit/` | P0 | 0 |
| 5 | Document Quality Gate | `services/ingestion/quality_gate.py` | P0 | 1 |
| 6 | Document Ingestion + Noise Filter | `services/ingestion/` | P0 | 1 |
| 7 | DoclingGraph Adapter | `services/ingestion/docling_adapter.py` | P0 | 1 |
| 8 | Table Extraction + EMV Schema | `services/ingestion/tables.py` | P0 | 1 |
| 9 | Standard Registry Service | `services/standards/` | P0 | 1 |
| 10 | Ontology Classification (Agent A1) | `services/ontology/` | P0 | 1 |
| 11 | Stable Identity Resolver | `services/ontology/identity.py` | P0 | 1 |
| 12 | Tri-layer Graph Builder | `services/graph/` | P0 | 1 |
| 13 | 4-layer Relationship Builder | `services/graph/relationships.py` | P0 | 1 |
| 14 | Evidence Chain Validator | `services/validation/evidence_chain.py` | P0 | 1 |
| 15 | BGE-M3 Embedding Engine | `services/embeddings/` | P0 | 1 |
| 16 | Deterministic Rules Engine | `services/rules/` | P0 | 1 |
| 17 | Semantic Alignment Engine | `services/comparison/alignment.py` | P1 | 2 |
| 18 | Split/Merge Alignment | `services/comparison/split_merge.py` | P1 | 2 |
| 19 | Graph Diff Engine | `services/comparison/diff.py` | P1 | 2 |
| 20 | Review Queue Writer | `services/review/` | P1 | 2 |
| 21 | FastAPI Gateway | `api/` | P1 | 2 |
| 22 | Celery Job Queue | `workers/` | P1 | 2 |
| 23 | WebSocket Progress Streaming | `api/ws.py` | P1 | 2 |
| 24 | Explanation + Report Agents | `services/reporting/` | P2 | 3 |
| 25 | Frontend — Dashboard | `frontend/` | P2 | 3 |
| 26 | Frontend — Diff Viewer | `frontend/components/DiffViewer/` | P2 | 3 |
| 27 | Frontend — Evidence Panel | `frontend/components/EvidencePanel/` | P2 | 3 |
| 28 | Ontology Governance Service | `services/ontology/governance.py` | P2 | 3 |

---

## Skill 1: Orchestration State Machine

**Purpose:** The pipeline controller. A Python state machine — not an LLM. Manages job lifecycle, stage transitions, resume capability, and failure routing.

**Paths:**
```
services/orchestration/ingestion_orchestrator.py
services/orchestration/comparison_orchestrator.py
services/orchestration/job_state.py
services/orchestration/events.py
```

```python
# services/orchestration/job_state.py
from enum import Enum
from dataclasses import dataclass, field
from typing import Optional
import json

class IngestionState(str, Enum):
    UPLOADED              = "UPLOADED"
    QUALITY_CHECKED       = "QUALITY_CHECKED"
    PARSED                = "PARSED"
    EXTRACTED             = "EXTRACTED"
    CANONICALIZED         = "CANONICALIZED"
    TABLES_NORMALIZED     = "TABLES_NORMALIZED"
    STANDARDS_RESOLVED    = "STANDARDS_RESOLVED"
    ONTOLOGY_MAPPED       = "ONTOLOGY_MAPPED"
    IDENTITIES_RESOLVED   = "IDENTITIES_RESOLVED"
    GRAPH_BUILT           = "GRAPH_BUILT"
    RELATIONSHIPS_BUILT   = "RELATIONSHIPS_BUILT"
    EVIDENCE_VALIDATED    = "EVIDENCE_VALIDATED"
    EMBEDDED              = "EMBEDDED"
    READY_FOR_COMPARISON  = "READY_FOR_COMPARISON"
    FAILED                = "FAILED"

class ComparisonState(str, Enum):
    COMPARISON_REQUESTED  = "COMPARISON_REQUESTED"
    ALIGNED               = "ALIGNED"
    SPLIT_MERGE_ALIGNED   = "SPLIT_MERGE_ALIGNED"
    DIFFED                = "DIFFED"
    RULES_EVALUATED       = "RULES_EVALUATED"
    REVIEW_TRIAGED        = "REVIEW_TRIAGED"
    REPORTED              = "REPORTED"
    COMPLETED             = "COMPLETED"
    FAILED                = "FAILED"

@dataclass
class JobRisk:
    extraction_risk: str = "LOW"       # LOW | MEDIUM | HIGH
    ocr_used: bool = False
    review_required: bool = False
    review_reason: Optional[str] = None

@dataclass
class JobState:
    job_id: str
    doc_id: str
    state: IngestionState = IngestionState.UPLOADED
    progress: int = 0
    artifacts: dict = field(default_factory=dict)
    risk: JobRisk = field(default_factory=JobRisk)
    resume_from: Optional[str] = None
    failure_artifact_uri: Optional[str] = None

    def can_resume(self) -> bool:
        return self.state == IngestionState.FAILED and self.resume_from is not None

    def to_json(self) -> str:
        return json.dumps({
            "job_id": self.job_id,
            "doc_id": self.doc_id,
            "state": self.state.value,
            "progress": self.progress,
            "artifacts": self.artifacts,
            "risk": {
                "extraction_risk": self.risk.extraction_risk,
                "ocr_used": self.risk.ocr_used,
                "review_required": self.risk.review_required,
                "review_reason": self.risk.review_reason,
            },
            "resume_from": self.resume_from,
        })
```

```python
# services/orchestration/ingestion_orchestrator.py
from services.orchestration.job_state import IngestionState, JobState
from services.orchestration.events import emit_event
from services.audit.log import write_audit_event

class AgentFailure(Exception):
    """Probabilistic agent failed — route to review, continue pipeline."""
    pass

class DeterministicFailure(Exception):
    """Deterministic tool failed — halt pipeline, write failure artifact."""
    pass

INGESTION_STAGES = [
    ("quality_gate",             IngestionState.QUALITY_CHECKED,    False),  # (stage_name, next_state, is_agent)
    ("parse",                    IngestionState.PARSED,              False),
    ("extract_docling_graph",    IngestionState.EXTRACTED,           False),
    ("canonicalize",             IngestionState.CANONICALIZED,       False),
    ("normalize_tables",         IngestionState.TABLES_NORMALIZED,   False),
    ("resolve_standards",        IngestionState.STANDARDS_RESOLVED,  True),   # agent optional
    ("map_ontology",             IngestionState.ONTOLOGY_MAPPED,     True),   # agent optional
    ("resolve_stable_identities",IngestionState.IDENTITIES_RESOLVED, True),   # agent optional
    ("build_graph",              IngestionState.GRAPH_BUILT,         False),
    ("build_relationships",      IngestionState.RELATIONSHIPS_BUILT, True),   # agent optional
    ("validate_evidence_chains", IngestionState.EVIDENCE_VALIDATED,  False),
    ("embed_nodes",              IngestionState.EMBEDDED,            False),
    ("mark_ready",               IngestionState.READY_FOR_COMPARISON,False),
]

class ComplianceIngestionOrchestrator:
    """Deterministic state machine. Not an LLM. Controls the ingestion pipeline."""

    def __init__(self, tool_registry, artifact_store, audit_log, review_queue):
        self.tools = tool_registry
        self.artifacts = artifact_store
        self.audit = audit_log
        self.review = review_queue

    def run(self, job_id: str) -> JobState:
        job = self.load_job(job_id)
        start_idx = self._resume_index(job)

        for stage_name, next_state, has_agent in INGESTION_STAGES[start_idx:]:
            try:
                result = self.tools.run(stage_name, job_id, job)
                self.artifacts.save(job_id, stage_name, result)
                self._transition(job, next_state)
                self.audit.write(job_id, stage_name, "SUCCESS", result)
                emit_event(f"document.{stage_name.replace('_', '')}", job_id)

            except AgentFailure as e:
                # Agent failures route items to review — pipeline continues
                self.review.add(job_id, stage_name, str(e))
                job.risk.review_required = True
                job.risk.review_reason = str(e)
                self._transition(job, next_state)  # advance anyway
                self.audit.write(job_id, stage_name, "AGENT_FAILURE_REVIEW_ROUTED", str(e))

            except DeterministicFailure as e:
                # Deterministic failures halt the pipeline
                job.state = IngestionState.FAILED
                job.resume_from = stage_name
                self._write_failure_artifact(job_id, stage_name, e)
                self.audit.write(job_id, stage_name, "DETERMINISTIC_FAILURE", str(e))
                return job

        return job

    def _transition(self, job: JobState, new_state: IngestionState):
        job.state = new_state
        job.progress = self._progress_pct(new_state)
        self.save_job(job)

    def _resume_index(self, job: JobState) -> int:
        if not job.can_resume():
            return 0
        for i, (stage_name, _, _) in enumerate(INGESTION_STAGES):
            if stage_name == job.resume_from:
                return i
        return 0
```

**Test criteria:**
- Job resumes from last successful stage after failure
- Agent failure does not halt pipeline — routes to review and continues
- Deterministic failure halts pipeline and writes structured failure artifact
- Rules engine stage completes without any LLM available
- Progress percentage is monotonically increasing

---

## Skill 2: Typed Artifact Contracts

**Purpose:** Pydantic schemas for every inter-stage artifact. Prevents loose text from passing between stages.

**Paths:**
```
services/contracts/artifacts.py
services/contracts/tool_results.py
services/contracts/review.py
```

```python
# services/contracts/artifacts.py
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from enum import Enum

class OntologyType(str, Enum):
    REQUIREMENT  = "Requirement"
    OBSERVATION  = "Observation"
    MEASUREMENT  = "Measurement"
    THRESHOLD    = "Threshold"
    CONTROL      = "Control"
    EVIDENCE     = "Evidence"
    DEVIATION    = "Deviation"
    RISK         = "Risk"
    STANDARD     = "Standard"
    SECTION      = "Section"

class SourceLocation(BaseModel):
    doc_id: str
    page: int
    bbox: Optional[List[float]] = None

class IgnoredArtifact(BaseModel):
    type: str                          # "TOC" | "list_of_figures" | "page_header" | ...
    source_page: int
    reason: str                        # "non_compliance_artifact" | "noise_section" | ...
    title: Optional[str] = None

class MeasurementProperties(BaseModel):
    frequency_hz: Optional[float] = None
    limit_dbuv_m: Optional[float] = None
    measured_dbuv_m: Optional[float] = None
    margin_db: Optional[float] = None
    result: Optional[Literal["PASS", "FAIL", "MARGINAL"]] = None
    unit: Optional[str] = None
    standard_ref: Optional[str] = None
    standard_version: Optional[str] = None
    clause: Optional[str] = None
    extraction_confidence: float = 1.0

class CanonicalNode(BaseModel):
    node_id: str
    stable_id: Optional[str] = None
    ontology_type: OntologyType = OntologyType.SECTION
    title: str
    content: str
    domain: str
    source: SourceLocation
    properties: MeasurementProperties = Field(default_factory=MeasurementProperties)
    classification_method: Literal["keyword", "agent", "default"] = "default"
    classification_confidence: float = 1.0
    review_flag: bool = False

class AgentOutput(BaseModel):
    """Base class for all agent outputs. Enforces audit trail fields."""
    confidence: float
    review_flag: bool
    model_version: str
    input_hash: str
    output_hash: str
    schema_version: str = "1.0"

class OntologyClassificationOutput(AgentOutput):
    node_id: str
    ontology_type: OntologyType
    domain: str
    properties: MeasurementProperties = Field(default_factory=MeasurementProperties)
    links: dict = Field(default_factory=dict)

class AlignmentDecision(str, Enum):
    SAME_NODE              = "SAME_NODE"
    MODIFIED_NODE          = "MODIFIED_NODE"
    NEW_NODE               = "NEW_NODE"
    DELETED_NODE           = "DELETED_NODE"
    AMBIGUOUS_UNRESOLVABLE = "AMBIGUOUS_UNRESOLVABLE"

class AlignmentResult(BaseModel):
    node_a_id: Optional[str]
    node_b_id: Optional[str]
    decision: AlignmentDecision
    composite_score: float
    adjudication_basis: Optional[str] = None
    review_flag: bool = False

class Finding(BaseModel):
    finding_id: str
    comparison_id: str
    type: str
    severity: Literal["HIGH", "MEDIUM", "LOW", "INFO"]
    result: Literal["FAIL", "PASS", "MARGINAL"]
    rule_id: str
    rule_version: str
    clause: str
    standard_id: str
    standard_version: str
    domain: str
    alignment_decision: AlignmentDecision
    evidence: dict
    source_a: SourceLocation
    source_b: SourceLocation
    evidence_chain_id: str
    extraction_confidence: float
    explanation: Optional[str] = None
    ignored: bool = False
```

```python
# services/contracts/review.py
from pydantic import BaseModel
from typing import List, Literal

class ReviewItem(BaseModel):
    item_id: str
    job_id: str
    stage: str
    category: Literal[
        "MEASUREMENT_EXTRACTION_RISK",
        "AMBIGUOUS_ALIGNMENT",
        "LOW_CONFIDENCE_CLASSIFICATION",
        "INCOMPLETE_EVIDENCE_CHAIN",
        "UNRESOLVED_STANDARD_VERSION",
        "AGENT_RELATIONSHIP_PROPOSAL"
    ]
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    description: str
    node_ids: List[str] = []
    resolved: bool = False
    resolver: str = ""

class ReviewGroup(BaseModel):
    priority: Literal["HIGH", "MEDIUM", "LOW"]
    category: str
    count: int
    explanation: str
    item_ids: List[str]
```

---

## Skill 3: Tool Registry

**Purpose:** Central registry mapping stage names to tool implementations. Enables deterministic-first policy enforcement and testability.

```python
# services/orchestration/tool_registry.py
from typing import Callable, Dict, Optional
from services.orchestration.job_state import JobState

class ToolRegistry:
    """
    Maps pipeline stage names to tool callables.
    Enforces deterministic-first execution: deterministic tool runs first,
    agent invoked only if deterministic tool returns insufficient confidence.
    """

    def __init__(self):
        self._deterministic: Dict[str, Callable] = {}
        self._agents: Dict[str, Callable] = {}
        self._confidence_threshold: Dict[str, float] = {}

    def register_deterministic(self, stage: str, tool: Callable):
        self._deterministic[stage] = tool

    def register_agent(self, stage: str, agent: Callable, confidence_threshold: float = 0.70):
        self._agents[stage] = agent
        self._confidence_threshold[stage] = confidence_threshold

    def run(self, stage: str, job_id: str, job: JobState) -> dict:
        if stage not in self._deterministic:
            raise ValueError(f"No deterministic tool registered for stage: {stage}")

        result = self._deterministic[stage](job_id, job)

        # Invoke agent only if deterministic result signals ambiguity
        if (stage in self._agents and
                result.get("requires_agent") is True):
            agent_result = self._agents[stage](job_id, job, deterministic_result=result)

            # Validate agent output (raises on schema failure)
            validated = self._validate_agent_output(stage, agent_result)

            if validated.confidence < self._confidence_threshold[stage]:
                validated.review_flag = True
                # Merge: agent result augments, never replaces deterministic result
            result["agent_result"] = validated.dict()

        return result

    def _validate_agent_output(self, stage: str, raw_output: dict):
        """Pydantic validation — raises AgentOutputSchemaError on failure."""
        from services.contracts.artifacts import AgentOutput
        # Stage-specific schema validation would be looked up here
        return AgentOutput(**raw_output)
```

---

## Skill 4: Audit Log System

**Purpose:** Immutable append-only event log for every stage transition and agent call.

```sql
-- Append-only audit log (no UPDATE/DELETE permitted)
CREATE TABLE audit_log (
    id             BIGSERIAL PRIMARY KEY,
    event_type     TEXT NOT NULL,
    stage          TEXT,
    entity_id      TEXT NOT NULL,
    entity_type    TEXT NOT NULL,
    job_id         TEXT,
    comparison_id  TEXT,
    actor          TEXT NOT NULL DEFAULT 'system',
    model_version  TEXT,
    input_hash     TEXT,
    output_hash    TEXT,
    confidence     FLOAT,
    status         TEXT,        -- SUCCESS | AGENT_FAILURE_REVIEW_ROUTED | DETERMINISTIC_FAILURE
    payload        JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE RULE no_audit_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE RULE no_audit_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;

CREATE INDEX idx_audit_job        ON audit_log(job_id);
CREATE INDEX idx_audit_comparison ON audit_log(comparison_id);
CREATE INDEX idx_audit_entity     ON audit_log(entity_id, entity_type);
CREATE INDEX idx_audit_stage      ON audit_log(stage, status);
CREATE INDEX idx_audit_created    ON audit_log(created_at);
```

```python
# services/audit/log.py
from hashlib import sha256
from datetime import datetime, timezone
import json

def write_audit_event(
    job_id: str,
    stage: str,
    status: str,
    payload: dict,
    model_version: str = None,
    confidence: float = None
):
    payload_json = json.dumps(payload, sort_keys=True, default=str)
    output_hash = sha256(payload_json.encode()).hexdigest()[:16]

    event = {
        "event_type": f"stage.{stage}.{status.lower()}",
        "stage": stage,
        "entity_id": job_id,
        "entity_type": "job",
        "job_id": job_id,
        "actor": "system",
        "model_version": model_version,
        "output_hash": output_hash,
        "confidence": confidence,
        "status": status,
        "payload": payload,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    # Write to PostgreSQL audit_log table (insert only)
    _db_insert_audit(event)
```

---

## Skill 5: Document Quality Gate

**Purpose:** Assess document quality before committing processing resources. Detect OCR risk, table density, and extraction confidence.

```python
# services/ingestion/quality_gate.py
from dataclasses import dataclass
import fitz  # PyMuPDF

@dataclass
class DocumentQuality:
    native_pdf: bool
    ocr_required: bool
    table_density: float
    numeric_density: float
    extraction_risk: str   # LOW | MEDIUM | HIGH
    language: str
    review_flag: bool

def assess_document_quality(file_path: str) -> DocumentQuality:
    doc = fitz.open(file_path)
    total_chars = 0
    total_numeric_chars = 0
    table_page_count = 0

    for page in doc:
        text = page.get_text()
        total_chars += len(text)
        total_numeric_chars += sum(1 for c in text if c.isdigit() or c in '.-+')

        # Heuristic: table density from block layout
        blocks = page.get_text("blocks")
        if len(blocks) > 8:
            table_page_count += 1

    total_pages = len(doc)
    is_native = total_chars > (total_pages * 100)  # > 100 chars/page = likely native

    numeric_density = total_numeric_chars / max(total_chars, 1)
    table_density = table_page_count / max(total_pages, 1)

    # Risk classification
    if not is_native and numeric_density > 0.15:
        risk = "HIGH"    # OCR + measurement-heavy = highest risk
        review_flag = True
    elif not is_native:
        risk = "MEDIUM"
        review_flag = True
    else:
        risk = "LOW"
        review_flag = False

    return DocumentQuality(
        native_pdf=is_native,
        ocr_required=not is_native,
        table_density=round(table_density, 3),
        numeric_density=round(numeric_density, 3),
        extraction_risk=risk,
        language="en",  # extend with langdetect if needed
        review_flag=review_flag
    )
```

---

## Skill 6: Document Ingestion + Noise Filter

**Purpose:** Docling parsing, noise removal, and canonical text normalization.

```python
# services/ingestion/parser.py
from docling.document_converter import DocumentConverter
from hashlib import sha256
import re
from services.contracts.artifacts import IgnoredArtifact

NOISE_SECTION_TITLES = [
    "table of contents", "list of figures", "list of tables",
    "revision history", "document history", "index", "abbreviations",
    "contents", "figures", "tables", "acronyms"
]

def normalize_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)   # fix hyphenation
    text = re.sub(r'-\s*\n\s*', '', text)            # mid-word line breaks
    return text.strip()

def stable_node_id(title: str, content: str, parent_title: str) -> str:
    """Identity based on semantic content, never page number."""
    raw = f"{title.lower().strip()}|{normalize_text(content)[:200]}|{parent_title.lower().strip()}"
    return "n_" + sha256(raw.encode()).hexdigest()[:16]

def is_noise(title: str) -> bool:
    return any(noise in title.lower() for noise in NOISE_SECTION_TITLES)

def parse_and_filter(file_path: str, doc_id: str, doc_type: str) -> dict:
    converter = DocumentConverter()
    result = converter.convert(file_path)

    nodes = []
    ignored: list[IgnoredArtifact] = []

    for section in result.document.sections:
        if is_noise(section.title or ""):
            ignored.append(IgnoredArtifact(
                type="noise_section",
                source_page=getattr(section, 'page_no', 0),
                reason="non_compliance_artifact",
                title=section.title
            ))
            continue

        node = {
            "node_id": stable_node_id(
                section.title or "",
                getattr(section, 'text', '') or "",
                getattr(section, 'parent_title', '') or ""
            ),
            "type": "section",
            "title": section.title or "",
            "content": normalize_text(getattr(section, 'text', '') or ""),
            "parent_title": getattr(section, 'parent_title', None),
            "page_ref": getattr(section, 'page_no', None),
            "bbox": getattr(section, 'bbox', None),
            "raw_table": getattr(section, 'table', None),
        }
        nodes.append(node)

    return {
        "doc_id": doc_id,
        "nodes": nodes,
        "ignored": [i.dict() for i in ignored]
    }
```

---

## Skill 7: DoclingGraph Adapter

**Purpose:** Converts Docling raw output into typed candidate objects. Separate from noise filtering so each concern is independently testable.

```python
# services/ingestion/docling_adapter.py
from pydantic import BaseModel
from typing import List, Optional, Literal

class CandidateObject(BaseModel):
    object_type: Literal[
        "CandidateMeasurementTable", "CandidateClause",
        "CandidateSection", "CandidateTestSetup", "CandidateNoise"
    ]
    source_doc_id: str
    source_page: int
    bbox: Optional[List[float]]
    raw_content: dict
    confidence: float = 1.0

class DoclingGraphAdapter:
    """
    Converts Docling output into typed candidate objects.
    Output is NOT trusted until CanonicalObjectValidator passes.
    """

    def adapt(self, docling_output: dict, doc_id: str, domain: str) -> dict:
        objects = []
        edge_candidates = []
        extraction_trace = []

        for section in docling_output.get("sections", []):
            obj_type = self._classify_candidate(section, domain)
            objects.append(CandidateObject(
                object_type=obj_type,
                source_doc_id=doc_id,
                source_page=section.get("page_no", 0),
                bbox=section.get("bbox"),
                raw_content=section,
                confidence=0.95 if obj_type != "CandidateNoise" else 1.0
            ).dict())

            extraction_trace.append({
                "source_page": section.get("page_no"),
                "candidate_type": obj_type,
                "title": section.get("title", "")
            })

        return {
            "objects": objects,
            "edge_candidates": edge_candidates,
            "extraction_trace": extraction_trace
        }

    def _classify_candidate(self, section: dict, domain: str) -> str:
        title = (section.get("title") or "").lower()
        has_table = section.get("table") is not None

        if any(t in title for t in ["table of contents", "list of figures"]):
            return "CandidateNoise"
        if has_table:
            return "CandidateMeasurementTable"
        if any(t in title for t in ["clause", "requirement", "§", "section"]):
            return "CandidateClause"
        if any(t in title for t in ["setup", "configuration", "method"]):
            return "CandidateTestSetup"
        return "CandidateSection"
```

---

## Skill 8: Table Extraction + EMV Schema

**Purpose:** Detect measurement tables and convert to structured, unit-normalized EMV objects.

```python
# services/ingestion/tables.py
import re
from typing import Optional

EMV_TABLE_SIGNALS = [
    "frequency", "limit", "measured", "margin", "pass", "fail",
    "dbuv", "dbµv", "dbμv", "mhz", "ghz", "khz", "emission"
]
NOISE_TABLE_SIGNALS = [
    "figure", "table of", "revision", "author", "date", "page"
]

def classify_table(headers: list[str]) -> str:
    header_str = " ".join(str(h).lower() for h in headers)
    if any(s in header_str for s in NOISE_TABLE_SIGNALS):
        return "noise_table"
    if sum(1 for s in EMV_TABLE_SIGNALS if s in header_str) >= 2:
        return "measurement_table"
    if any(s in header_str for s in ["requirement", "clause", "standard"]):
        return "requirement_table"
    return "configuration_table"

def normalize_to_hz(value: str, header: str) -> Optional[float]:
    try:
        v = float(re.sub(r'[^\d.]', '', str(value)))
        h = header.lower()
        if "ghz" in h: return v * 1e9
        if "mhz" in h: return v * 1e6
        if "khz" in h: return v * 1e3
        return v
    except (ValueError, TypeError):
        return None

def convert_measurement_table(raw_table: dict, extraction_confidence: float = 1.0) -> list[dict]:
    headers = [str(h).lower().strip() for h in raw_table.get("headers", [])]
    measurements = []

    for row in raw_table.get("rows", []):
        m = {"extraction_confidence": extraction_confidence}
        for i, header in enumerate(headers):
            if i >= len(row): continue
            cell = row[i]
            if any(s in header for s in ["freq", "f (", "f["]):
                m["frequency_hz"] = normalize_to_hz(cell, header)
            elif any(s in header for s in ["limit", "lim", "class a", "class b"]):
                try: m["limit_dbuv_m"] = float(re.sub(r'[^\d.-]', '', str(cell)))
                except ValueError: pass
            elif any(s in header for s in ["measured", "meas", "reading"]):
                try: m["measured_dbuv_m"] = float(re.sub(r'[^\d.-]', '', str(cell)))
                except ValueError: pass
            elif "margin" in header:
                try: m["margin_db"] = float(re.sub(r'[^\d.-]', '', str(cell)))
                except ValueError: pass
            elif any(s in header for s in ["result", "pass", "fail", "status"]):
                m["result"] = str(cell).upper().strip()

        # Derived fields
        if "margin_db" not in m and "limit_dbuv_m" in m and "measured_dbuv_m" in m:
            m["margin_db"] = round(m["limit_dbuv_m"] - m["measured_dbuv_m"], 2)
        if "result" not in m and "margin_db" in m:
            m["result"] = "PASS" if m["margin_db"] >= 0 else "FAIL"

        if "frequency_hz" in m:
            measurements.append(m)

    return sorted(measurements, key=lambda x: x.get("frequency_hz", 0))

def validate_measurements(measurements: list[dict]) -> list[str]:
    warnings = []
    freqs = [m["frequency_hz"] for m in measurements if "frequency_hz" in m]
    if freqs and freqs != sorted(freqs):
        warnings.append("Non-monotonic frequency sequence — verify table extraction")
    for m in measurements:
        v = m.get("measured_dbuv_m")
        if v is not None and not (-20 <= v <= 120):
            warnings.append(f"Implausible dBµV/m: {v} at {m.get('frequency_hz')} Hz")
    return warnings
```

---

## Skill 9: Standard Registry Service

```python
# services/standards/registry.py
STANDARD_ALIASES = {
    "cispr25": "cispr_25", "cispr 25": "cispr_25",
    "cispr32": "cispr_32", "cispr 32": "cispr_32",
    "iec61000": "iec_61000", "fcc part 15": "fcc_part15",
    "iso14001": "iso_14001", "iso 14001": "iso_14001",
    "iso45001": "iso_45001", "iso 45001": "iso_45001",
    "iso9001": "iso_9001", "iso 9001": "iso_9001",
    "en55032": "en_55032", "en 55032": "en_55032",
}

KNOWN_VERSIONS = {
    "cispr_25": ["2002", "2008", "2016", "2021"],
    "cispr_32": ["2015", "2022"],
    "iso_14001": ["2004", "2015"],
    "iso_45001": ["2018"],
}

def resolve_standard(raw_ref: str) -> dict:
    normalized = raw_ref.lower().strip().replace(":", " ").replace("-", " ")
    parts = normalized.split()
    base = " ".join(parts[:2]) if len(parts) >= 2 else normalized

    standard_id = STANDARD_ALIASES.get(base) or STANDARD_ALIASES.get(normalized)
    if not standard_id:
        return {"raw_ref": raw_ref, "standard_id": None,
                "version": "unknown", "confidence": 0.0, "review_flag": True}

    # Try to extract version from parts
    version = "unknown"
    review_flag = True
    for part in parts:
        if len(part) == 4 and part.isdigit():
            known = KNOWN_VERSIONS.get(standard_id, [])
            if part in known:
                version = part
                review_flag = False
                break

    confidence = 0.95 if version != "unknown" else 0.65
    return {
        "raw_ref": raw_ref,
        "standard_id": standard_id,
        "version": version,
        "confidence": confidence,
        "review_flag": review_flag,
        "cross_version_risk": False
    }
```

---

## Skill 10: Ontology Classification (Agent A1)

See `AGENTS.md § Agent A1`. Key implementation points:

```python
# services/ontology/classifier.py
import re
from services.contracts.artifacts import OntologyType

# Deterministic keyword classifier (runs BEFORE agent)
KEYWORD_RULES = {
    OntologyType.MEASUREMENT: [
        r'\d+\s*(mhz|ghz|khz)', r'\d+\s*db', r'pass|fail',
        r'frequency.*limit', r'measured.*limit'
    ],
    OntologyType.REQUIREMENT: [
        r'shall|must|required|requirement',
        r'clause\s+\d', r'§\s*\d', r'per.*cispr|per.*iso'
    ],
    OntologyType.THRESHOLD: [
        r'limit\s+line', r'class\s+[ab]', r'emission\s+limit',
        r'\d+\s*dbuv.*limit', r'acceptance\s+criterion'
    ],
    OntologyType.DEVIATION: [
        r'non.?conformance', r'non.?compliance', r'failure', r'exceedance'
    ],
}

def keyword_classify(title: str, content: str) -> tuple[OntologyType | None, float]:
    combined = (title + " " + content[:500]).lower()
    scores = {}
    for onto_type, patterns in KEYWORD_RULES.items():
        matches = sum(1 for p in patterns if re.search(p, combined))
        if matches > 0:
            scores[onto_type] = matches / len(patterns)

    if not scores:
        return None, 0.0

    best = max(scores, key=scores.get)
    confidence = min(0.60 + scores[best] * 0.35, 0.95)
    if confidence >= 0.70:
        return best, confidence
    return None, confidence   # Return None → invoke agent

def classify_node(node: dict, domain: str, doc_type: str, agent=None) -> dict:
    onto_type, confidence = keyword_classify(node["title"], node["content"])

    if onto_type is not None:
        return {
            "ontology_type": onto_type.value,
            "confidence": confidence,
            "classification_method": "keyword",
            "review_flag": False
        }

    if agent is None:
        return {
            "ontology_type": OntologyType.SECTION.value,
            "confidence": 0.0,
            "classification_method": "default",
            "review_flag": True
        }

    # Invoke agent for ambiguous cases
    result = agent.classify(node, domain, doc_type, keyword_confidence=confidence)
    # Pydantic validation happens inside agent wrapper
    return result
```

---

## Skill 11: Stable Identity Resolver

```python
# services/ontology/identity.py
from hashlib import sha256

def resolve_stable_id(node: dict) -> dict:
    """
    Create a stable, human-readable ID from compliance semantics.
    Priority: standard+clause > standard+content > parent+title > content_only
    Page numbers are NEVER used.
    """
    props = node.get("properties", {})
    std = (props.get("standard_ref") or "").lower().replace(" ", "_").replace(".", "")
    clause = (props.get("clause") or "").lower().replace(".", "_").replace(" ", "")
    title_slug = node["title"].lower()[:40].replace(" ", "_").replace("/", "_")
    content_hash = sha256(node["content"][:300].encode()).hexdigest()[:8]
    onto = node.get("ontology_type", "section").lower()

    if std and clause:
        stable_id = f"{onto}.{std}.{clause}.{title_slug}"
        confidence = 0.95
    elif std:
        stable_id = f"{onto}.{std}.{content_hash}"
        confidence = 0.80
    elif node.get("parent_title"):
        parent_slug = node["parent_title"].lower()[:20].replace(" ", "_")
        stable_id = f"{onto}.{parent_slug}.{title_slug}"
        confidence = 0.65
    else:
        stable_id = f"{onto}.{content_hash}"
        confidence = 0.40  # review_flag = True below

    return {
        "stable_id": stable_id,
        "identity_confidence": confidence,
        "review_flag": confidence < 0.70,
        "basis": "standard_clause" if std and clause else
                 "standard" if std else
                 "parent_context" if node.get("parent_title") else
                 "content_hash"
    }
```

---

## Skill 12: Tri-layer Graph Builder

```python
# services/graph/builder.py
from neo4j import GraphDatabase

class TriLayerGraphBuilder:
    """
    Builds MetaGraph, LayoutGraph, and ComplianceGraph layers.
    Hard rule: TOC nodes never enter ComplianceGraph.
    """

    def __init__(self, neo4j_driver):
        self.driver = neo4j_driver

    def write_meta_node(self, doc_id: str, metadata: dict):
        with self.driver.session() as s:
            s.run("""
                MERGE (d:Document {doc_id: $doc_id})
                SET d += $props
            """, doc_id=doc_id, props=metadata)

    def write_layout_node(self, node: dict, doc_id: str):
        with self.driver.session() as s:
            s.run("""
                MERGE (l:LayoutNode {node_id: $node_id})
                SET l.title = $title, l.page = $page, l.bbox = $bbox,
                    l.doc_id = $doc_id
                WITH l
                MATCH (d:Document {doc_id: $doc_id})
                MERGE (d)-[:HAS_LAYOUT_NODE]->(l)
            """, node_id=node["node_id"], title=node["title"],
                 page=node.get("page_ref"), bbox=str(node.get("bbox")),
                 doc_id=doc_id)

    def write_compliance_node(self, node: dict, layout_node_id: str):
        """Write to ComplianceGraph with cross-layer traceability link."""
        # Hard rule: if this is a noise node, raise rather than silently ignore
        if node.get("ontology_type") == "Section" and node.get("is_noise"):
            raise ValueError(f"Noise node must not enter ComplianceGraph: {node['node_id']}")

        with self.driver.session() as s:
            s.run("""
                MERGE (c:ComplianceNode {stable_id: $stable_id})
                SET c += $props
                WITH c
                MATCH (l:LayoutNode {node_id: $layout_id})
                MERGE (c)-[:SOURCED_FROM]->(l)
            """, stable_id=node["stable_id"],
                 props={k: v for k, v in node.items()
                        if k not in ["content", "raw_table"]},
                 layout_id=layout_node_id)
```

---

## Skill 13: 4-layer Relationship Builder

```python
# services/graph/relationships.py

class RelationshipBuilder:
    """
    Builds compliance graph relationships in 4 layers.
    Deterministic layers A/B/C run first.
    Agent layer D runs only for remaining ambiguous pairs.
    """

    LAYER_A_RULES = [
        # (from_type, to_type, rel_type, match_condition)
        ("Document", "Section", "CONTAINS", "parent_id"),
        ("Section", "Requirement", "IMPLEMENTS", "standard_ref"),
    ]

    LAYER_B_RULES = [
        ("Observation", "Requirement", "EVIDENCE_FOR", "requirement_id"),
        ("Observation", "Threshold", "EVALUATED_AGAINST", "threshold_id"),
        ("Observation", "Deviation", "RESULTS_IN", "deviation_id"),
        ("Deviation", "Control", "MITIGATED_BY", "control_id"),
        ("Requirement", "Standard", "PART_OF", "standard_id"),
        ("Risk", "Deviation", "DERIVED_FROM", "deviation_id"),
        ("Measurement", "Threshold", "HAS_LIMIT", "threshold_id"),
        ("TestSetup", "Measurement", "INFLUENCES", "measurement_id"),
    ]

    def build_layer_a(self, nodes: list[dict]) -> list[dict]:
        """Structural relationships — fully deterministic."""
        edges = []
        for node in nodes:
            if node.get("parent_id"):
                edges.append({
                    "from": node["stable_id"],
                    "to": node["parent_id"],
                    "rel_type": "BELONGS_TO",
                    "source": "layerA",
                    "confidence": 1.0,
                    "review_flag": False
                })
        return edges

    def build_layer_b(self, nodes: list[dict]) -> list[dict]:
        """Ontology rule relationships — fully deterministic."""
        edges = []
        for node in nodes:
            links = node.get("links", {})
            for link_type, target_id in links.items():
                if target_id:
                    edges.append({
                        "from": node["stable_id"],
                        "to": target_id,
                        "rel_type": self._link_type_to_rel(link_type),
                        "source": "layerB",
                        "confidence": 0.95,
                        "review_flag": False
                    })
        return edges

    def build_layer_c(self, nodes: list[dict]) -> list[dict]:
        """Explicit ID cross-references in document text — deterministic."""
        # Parse standard_ref + clause patterns from node content
        # Returns edges with confidence 0.90
        return []

    def build_layer_d(self, unmatched_pairs: list[dict], agent) -> list[dict]:
        """Semantic relationships — agent-assisted, review_flag = True."""
        edges = []
        for pair in unmatched_pairs:
            result = agent.resolve_relationship(pair)
            if result and result.confidence > 0.0:
                edges.append({
                    **result.dict(),
                    "source": "layerD",
                    "review_flag": True   # always review for layer D
                })
        return edges

    def _link_type_to_rel(self, link_type: str) -> str:
        return {
            "requirement_id": "EVIDENCE_FOR",
            "threshold_id": "EVALUATED_AGAINST",
            "standard_id": "PART_OF",
        }.get(link_type, "RELATED_TO")
```

---

## Skill 14: Evidence Chain Validator

```python
# services/validation/evidence_chain.py
from services.contracts.artifacts import Finding
from services.contracts.review import ReviewItem
import uuid

def validate_evidence_chain(finding_candidate: dict) -> dict:
    """
    Every finding must have complete source traceability before reaching auditors.
    Returns chain_status and any missing links.
    """
    missing = []

    if not finding_candidate.get("measurement_id"):
        missing.append("measurement_id")
    if not finding_candidate.get("threshold_id"):
        missing.append("threshold_id")
    if not finding_candidate.get("requirement_id"):
        missing.append("requirement_id")
    if not finding_candidate.get("standard_id"):
        missing.append("standard_id")
    if not finding_candidate.get("source_refs"):
        missing.append("source_location")

    if not missing:
        return {"chain_status": "COMPLETE", "missing_links": [], "review_flag": False}
    elif len(missing) <= 1:
        return {"chain_status": "PARTIAL", "missing_links": missing, "review_flag": True}
    else:
        return {"chain_status": "INCOMPLETE", "missing_links": missing, "review_flag": True}

def build_review_item_for_chain(job_id: str, finding_candidate: dict, chain_result: dict) -> ReviewItem:
    return ReviewItem(
        item_id=f"rev_{uuid.uuid4().hex[:8]}",
        job_id=job_id,
        stage="evidence_chain_validation",
        category="INCOMPLETE_EVIDENCE_CHAIN",
        priority="HIGH",
        description=f"Missing: {', '.join(chain_result['missing_links'])}",
        node_ids=[finding_candidate.get("measurement_id", "unknown")]
    )
```

---

## Skill 15: Deterministic Rules Engine

```python
# services/rules/engine.py
from dataclasses import dataclass
from typing import Literal
import uuid

@dataclass
class FindingResult:
    finding_id: str
    result: Literal["PASS", "FAIL", "MARGINAL"]
    margin_db: float
    severity: Literal["HIGH", "MEDIUM", "LOW", "INFO"]
    rule_id: str
    rule_version: str

RULE_VERSION = "1.0.0"

def evaluate_measurement(
    measured_dbuv_m: float,
    limit_dbuv_m: float,
    standard_id: str,
    clause: str,
    test_case_id: str,
    uncertainty_db: float = 0.0,
    apply_uncertainty: bool = False
) -> FindingResult:
    """
    Fully deterministic PASS/FAIL evaluation.
    Never calls any LLM. Never uses probabilistic logic.
    """
    effective_measured = measured_dbuv_m
    if apply_uncertainty and uncertainty_db > 0:
        effective_measured = measured_dbuv_m + (2.0 * uncertainty_db)  # k=2, 95% CI

    margin = round(limit_dbuv_m - effective_measured, 2)

    if margin >= 3.0:
        result, severity, rule_id = "PASS",     "INFO",   "EMC_PASS_COMFORTABLE"
    elif margin >= 0.0:
        result, severity, rule_id = "MARGINAL",  "LOW",   "EMC_PASS_MARGINAL"
    elif margin >= -3.0:
        result, severity, rule_id = "FAIL",      "MEDIUM","EMC_MARGIN_FAIL_LT_0DB"
    else:
        result, severity, rule_id = "FAIL",      "HIGH",  "EMC_MARGIN_FAIL_LT_MINUS3DB"

    return FindingResult(
        finding_id=f"find_{uuid.uuid4().hex[:8]}",
        result=result,
        margin_db=margin,
        severity=severity,
        rule_id=rule_id,
        rule_version=RULE_VERSION
    )

def evaluate_diff_candidate(candidate: dict) -> dict | None:
    """Compare measurements from Doc A and Doc B. Returns finding only on meaningful change."""
    ma = candidate.get("measurement_a", {})
    mb = candidate.get("measurement_b", {})

    if not ma or not mb:
        return None

    result_a = evaluate_measurement(
        ma["measured_dbuv_m"], ma["limit_dbuv_m"],
        candidate["standard_id"], candidate["clause"], "diff_a"
    )
    result_b = evaluate_measurement(
        mb["measured_dbuv_m"], mb["limit_dbuv_m"],
        candidate["standard_id"], candidate["clause"], "diff_b"
    )

    status_changed = result_a.result != result_b.result
    margin_delta = abs(result_b.margin_db - result_a.margin_db)

    if not status_changed and margin_delta < 1.0:
        return None   # No meaningful compliance change

    severity = result_b.severity
    if status_changed:
        severity = "HIGH" if result_b.margin_db < -3.0 else "MEDIUM"

    return {
        "change_type": "status_change" if status_changed else "margin_change",
        "severity": severity,
        "rule_id": result_b.rule_id,
        "rule_version": RULE_VERSION,
        "result_a": result_a.result,
        "result_b": result_b.result,
        "margin_a": result_a.margin_db,
        "margin_b": result_b.margin_db,
        "margin_delta_db": round(result_b.margin_db - result_a.margin_db, 2)
    }
```

---

## Build Order & Dependencies

```
Phase 0 — Orchestration Foundation (1 week) — BUILD FIRST
  Skill 1  Orchestration State Machine
  Skill 2  Typed Artifact Contracts
  Skill 3  Tool Registry
  Skill 4  Audit Log System

Phase 1 — Core Pipeline (2 weeks)
  Skill 5  Document Quality Gate
  Skill 6  Document Ingestion + Noise Filter
  Skill 7  DoclingGraph Adapter
  Skill 8  Table Extraction + EMV Schema
  Skill 9  Standard Registry Service
  Skill 10 Ontology Classification
  Skill 11 Stable Identity Resolver
  Skill 12 Tri-layer Graph Builder
  Skill 13 4-layer Relationship Builder
  Skill 14 Evidence Chain Validator
  Skill 15 BGE-M3 Embedding Engine
  Skill 16 Deterministic Rules Engine  ← verify works with zero LLM here

Phase 2 — Comparison + API (2 weeks)
  Skill 17 Semantic Alignment Engine
  Skill 18 Split/Merge Alignment
  Skill 19 Graph Diff Engine
  Skill 20 Review Queue Writer
  Skill 21 FastAPI Gateway
  Skill 22 Celery Job Queue
  Skill 23 WebSocket Progress Streaming

Phase 3 — UI + Polish (1 week)
  Skill 24 Explanation + Report Agents
  Skill 25 Frontend — Dashboard
  Skill 26 Frontend — Diff Viewer
  Skill 27 Frontend — Evidence Panel
  Skill 28 Ontology Governance Service
```

### Acceptance Criteria Before Phase 1 → Phase 2

Before building the comparison pipeline, verify:

1. A document job can complete end-to-end through all 13 ingestion stages
2. A failed deterministic stage halts the job and writes a failure artifact
3. An agent failure routes items to review and allows the pipeline to continue
4. The rules engine computes correct PASS/FAIL with `OLLAMA_BASE_URL` unset
5. Evidence chain validation blocks a finding with missing source traceability
6. All ignored artifacts are present in `ignored_changes[]` at job completion
7. Job resume from a mid-stage failure reprocesses only from that stage forward
