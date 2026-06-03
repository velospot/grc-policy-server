# EMC/EMV Compliance Platform — Build Skills

> Modular implementation skills for each subsystem. Each skill is a self-contained unit that can be built and tested independently.

---

## Skill Index

| # | Skill | Technology | Priority |
|---|-------|-----------|----------|
| 1 | Document Ingestion & Noise Filter | Python + Docling | P0 |
| 2 | Table Extraction + EMV Schema | Python + Docling | P0 |
| 3 | Ontology Classification | FastAPI + Claude API | P0 |
| 4 | BGE-M3 Embedding Engine | Python + Qdrant | P0 |
| 5 | Neo4j Graph Builder | Python + Neo4j | P0 |
| 6 | Semantic Alignment Engine | Python | P1 |
| 7 | Deterministic Rules Engine | Python | P0 |
| 8 | Graph Diff Engine | Python + Neo4j | P1 |
| 9 | Celery Job Queue | Python + Redis | P1 |
| 10 | FastAPI Gateway | Python | P0 |
| 11 | WebSocket Progress Streaming | FastAPI + WebSocket | P1 |
| 12 | Caching Layer | Redis + PostgreSQL | P1 |
| 13 | Explanation + Report Agent | Claude API | P2 |
| 14 | Frontend — Dashboard | React/Next.js | P1 |
| 15 | Frontend — Diff Viewer | React | P1 |
| 16 | Frontend — Evidence Panel | React | P2 |
| 17 | Ontology Governance Service | Python + PostgreSQL | P2 |
| 18 | Audit Log System | PostgreSQL | P0 |

---

## Skill 1: Document Ingestion & Noise Filter

**Purpose:** Convert raw documents into clean structured JSON, with all noise removed and logged.

**Stack:** Python 3.12, Docling, FastAPI

**Key implementation pattern:**
```python
from docling.document_converter import DocumentConverter
from hashlib import sha256
import re

NOISE_SECTION_TITLES = [
    "table of contents", "list of figures", "list of tables",
    "revision history", "document history", "index", "abbreviations"
]

def is_noise_section(title: str) -> bool:
    return any(noise in title.lower() for noise in NOISE_SECTION_TITLES)

def normalize_text(text: str) -> str:
    text = re.sub(r'\s+', ' ', text)          # collapse whitespace
    text = re.sub(r'(\w)-\n(\w)', r'\1\2', text)  # fix hyphenation
    text = text.strip()
    return text

def stable_node_id(title: str, content: str, parent_title: str) -> str:
    raw = f"{title.lower()}|{normalize_text(content)[:200]}|{parent_title.lower()}"
    return sha256(raw.encode()).hexdigest()[:16]

def parse_document(file_path: str, doc_type: str) -> dict:
    converter = DocumentConverter()
    result = converter.convert(file_path)
    
    nodes = []
    ignored = []
    
    for section in result.document.sections:
        if is_noise_section(section.title):
            ignored.append({"reason": "noise_section", "title": section.title})
            continue
        
        node = {
            "node_id": stable_node_id(section.title, section.text, section.parent_title or ""),
            "type": "section",
            "title": section.title,
            "content": normalize_text(section.text),
            "parent_id": None,  # resolved in graph builder
            "page_ref": section.page_no,
            "raw_table": section.table if hasattr(section, 'table') else None
        }
        nodes.append(node)
    
    return {"nodes": nodes, "ignored": ignored}
```

**Test criteria:**
- TOC sections never appear in output nodes
- All ignored items appear in `ignored[]`
- Node IDs are stable across re-parsing of the same document
- Whitespace normalization is idempotent

---

## Skill 2: Table Extraction + EMV Schema

**Purpose:** Detect measurement tables and convert them to structured EMV objects with normalized units.

**Stack:** Python, pandas, pint (unit normalization)

**Table type classifier:**
```python
EMV_TABLE_SIGNALS = ["frequency", "limit", "measured", "margin", "pass", "fail", "dbuv", "dbµv", "mhz", "ghz"]
NOISE_TABLE_SIGNALS = ["figure", "page", "table of", "revision", "author", "date"]

def classify_table(headers: list[str], sample_row: list) -> str:
    header_str = " ".join(str(h).lower() for h in headers)
    
    if any(sig in header_str for sig in NOISE_TABLE_SIGNALS):
        return "noise_table"
    
    if sum(1 for sig in EMV_TABLE_SIGNALS if sig in header_str) >= 2:
        return "measurement_table"
    
    if "requirement" in header_str or "clause" in header_str:
        return "requirement_table"
    
    return "configuration_table"
```

**Measurement schema conversion:**
```python
from pint import UnitRegistry

ureg = UnitRegistry()

FREQUENCY_ALIASES = {
    "freq": "frequency_hz", "f (hz)": "frequency_hz",
    "frequency (mhz)": "frequency_hz", "freq. (ghz)": "frequency_hz"
}

LIMIT_ALIASES = {
    "limit": "limit_dbuv_m", "class a": "limit_dbuv_m",
    "class b": "limit_dbuv_m", "lim (dbμv/m)": "limit_dbuv_m"
}

def normalize_frequency(value, header: str) -> float:
    """Convert any frequency to Hz"""
    header_lower = header.lower()
    if "mhz" in header_lower:
        return float(value) * 1_000_000
    if "ghz" in header_lower:
        return float(value) * 1_000_000_000
    if "khz" in header_lower:
        return float(value) * 1_000
    return float(value)

def convert_measurement_table(raw_table: dict) -> list[dict]:
    """Convert raw table to list of structured measurement objects"""
    measurements = []
    headers = [h.lower().strip() for h in raw_table["headers"]]
    
    for row in raw_table["rows"]:
        try:
            m = {}
            for i, header in enumerate(headers):
                if any(alias in header for alias in ["freq", "f ("]):
                    m["frequency_hz"] = normalize_frequency(row[i], header)
                elif any(alias in header for alias in ["limit", "lim"]):
                    m["limit_dbuv_m"] = float(row[i])
                elif "measured" in header or "meas" in header:
                    m["measured_dbuv_m"] = float(row[i])
                elif "margin" in header:
                    m["margin_db"] = float(row[i])
                elif "result" in header or "pass" in header or "fail" in header:
                    m["result"] = str(row[i]).upper().strip()
            
            # Compute margin if not present
            if "margin_db" not in m and "limit_dbuv_m" in m and "measured_dbuv_m" in m:
                m["margin_db"] = round(m["limit_dbuv_m"] - m["measured_dbuv_m"], 2)
            
            # Compute result if not present
            if "result" not in m and "margin_db" in m:
                m["result"] = "PASS" if m["margin_db"] >= 0 else "FAIL"
            
            measurements.append(m)
        except (ValueError, IndexError, KeyError):
            continue  # log and skip malformed rows
    
    # Canonical ordering: sort by frequency for stable diff
    return sorted(measurements, key=lambda x: x.get("frequency_hz", 0))
```

**Validation rules (EMV-specific):**
```python
def validate_measurements(measurements: list[dict]) -> list[str]:
    """Return list of validation warnings"""
    warnings = []
    freqs = [m["frequency_hz"] for m in measurements if "frequency_hz" in m]
    
    # Frequency should be generally monotonic in standard test reports
    if freqs != sorted(freqs):
        warnings.append("Non-monotonic frequency sequence — verify table extraction")
    
    # dB values must be in plausible range for EMC
    for m in measurements:
        if "measured_dbuv_m" in m:
            if not (-20 <= m["measured_dbuv_m"] <= 120):
                warnings.append(f"Implausible dBµV/m value: {m['measured_dbuv_m']} at {m.get('frequency_hz')} Hz")
    
    return warnings
```

---

## Skill 3: Ontology Classification (Claude Agent)

**Purpose:** Classify each document node into the universal compliance ontology using the Claude API.

**Stack:** Python, httpx, Claude API (`claude-sonnet-4-20250514`)

```python
import httpx
import json
from typing import Optional

ONTOLOGY_TYPES = [
    "Requirement", "Observation", "Measurement", "Threshold",
    "Control", "Evidence", "Deviation", "Risk", "Standard", "Section"
]

SYSTEM_PROMPT = """You are an ontology classification engine for compliance documents.
Classify each chunk into ONE primary entity type:
[Requirement, Observation, Measurement, Threshold, Control, Evidence, Deviation, Risk, Standard, Section]

Rules:
1. Return ONLY valid JSON. No preamble, no explanation.
2. confidence < 0.70 → type = "Section", set review_flag = true
3. Measurement nodes MUST include available numeric properties
4. Deviation nodes MUST reference requirement and observation IDs if available

Return format:
{
  "ontology_type": "...",
  "confidence": 0.94,
  "domain": "EMC|Safety|Environmental|ISO",
  "properties": {},
  "review_flag": false
}"""

async def classify_node(
    node: dict,
    domain: str,
    doc_type: str,
    api_key: str,
    model: str = "claude-sonnet-4-20250514"
) -> dict:
    
    user_content = f"""Classify the following compliance document node:

TITLE: {node['title']}
CONTENT: {node['content'][:1500]}
DOMAIN: {domain}
DOC_TYPE: {doc_type}
PARENT_CONTEXT: {node.get('parent_title', 'unknown')}"""

    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json"
            },
            json={
                "model": model,
                "max_tokens": 512,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}]
            }
        )
    
    raw = response.json()["content"][0]["text"].strip()
    
    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"ontology_type": "Section", "confidence": 0.0, "review_flag": True}
    
    # Audit log entry
    result["_audit"] = {
        "node_id": node["node_id"],
        "model": model,
        "input_hash": sha256(user_content.encode()).hexdigest()[:12],
        "timestamp": datetime.utcnow().isoformat()
    }
    
    return result


async def classify_batch(nodes: list[dict], domain: str, doc_type: str, api_key: str) -> list[dict]:
    """Classify nodes concurrently with rate limiting"""
    import asyncio
    
    semaphore = asyncio.Semaphore(5)  # max 5 concurrent requests
    
    async def classify_with_semaphore(node):
        async with semaphore:
            return await classify_node(node, domain, doc_type, api_key)
    
    results = await asyncio.gather(
        *[classify_with_semaphore(node) for node in nodes],
        return_exceptions=True
    )
    
    # Replace exceptions with fallback classification
    return [
        r if not isinstance(r, Exception)
        else {"ontology_type": "Section", "confidence": 0.0, "review_flag": True, "error": str(r)}
        for r in results
    ]
```

---

## Skill 4: BGE-M3 Embedding Engine

**Purpose:** Generate semantic embeddings for all document nodes. Powers the semantic alignment engine.

**Stack:** Python, sentence-transformers, Qdrant, CUDA (RTX 5070 Ti)

```python
from sentence_transformers import SentenceTransformer
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct
import torch

class EmbeddingEngine:
    def __init__(self, model_name: str = "BAAI/bge-m3"):
        device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = SentenceTransformer(model_name, device=device)
        self.client = QdrantClient(host="localhost", port=6333)
        self.collection_name = "compliance_nodes"
        self._ensure_collection()
    
    def _ensure_collection(self):
        collections = [c.name for c in self.client.get_collections().collections]
        if self.collection_name not in collections:
            self.client.create_collection(
                collection_name=self.collection_name,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE)
            )
    
    def embed_node(self, node: dict) -> list[float]:
        """
        3-level embedding: content + parent context + child aggregation
        Combined into single representation for section-level matching
        """
        text_parts = [
            f"TITLE: {node['title']}",
            f"CONTENT: {node['content'][:1000]}",
            f"PARENT: {node.get('parent_title', '')}",
            f"DOMAIN: {node.get('domain', '')}",
            f"TYPE: {node.get('ontology_type', '')}"
        ]
        combined = " | ".join(p for p in text_parts if p)
        embedding = self.model.encode(combined, normalize_embeddings=True)
        return embedding.tolist()
    
    def index_node(self, node: dict, doc_id: str):
        """Store node embedding in Qdrant"""
        embedding = self.embed_node(node)
        
        self.client.upsert(
            collection_name=self.collection_name,
            points=[PointStruct(
                id=abs(hash(node["node_id"])) % (2**63),
                vector=embedding,
                payload={
                    "node_id": node["node_id"],
                    "doc_id": doc_id,
                    "title": node["title"],
                    "ontology_type": node.get("ontology_type", "Section"),
                    "domain": node.get("domain", "")
                }
            )]
        )
        return embedding
    
    def find_similar(self, node: dict, doc_id_filter: str, top_k: int = 5) -> list[dict]:
        """Find top-k similar nodes from a specific document"""
        embedding = self.embed_node(node)
        
        results = self.client.search(
            collection_name=self.collection_name,
            query_vector=embedding,
            query_filter={"must": [{"key": "doc_id", "match": {"value": doc_id_filter}}]},
            limit=top_k,
            with_payload=True,
            score_threshold=0.50
        )
        
        return [{"node_id": r.payload["node_id"], "score": r.score, "title": r.payload["title"]}
                for r in results]
```

---

## Skill 5: Semantic Alignment Engine

**Purpose:** Match nodes from Doc A to nodes from Doc B. The bridge between embedding similarity and the diff engine.

**Stack:** Python

```python
from dataclasses import dataclass
from enum import Enum

class AlignmentDecision(Enum):
    SAME_NODE    = "SAME_NODE"       # moved or renamed, same content
    MODIFIED     = "MODIFIED_NODE"   # same clause, content changed
    NEW_NODE     = "NEW_NODE"        # only in doc_b
    DELETED_NODE = "DELETED_NODE"    # only in doc_a
    AMBIGUOUS    = "AMBIGUOUS"       # multiple candidates within threshold

@dataclass
class AlignmentResult:
    node_a_id: str
    node_b_id: str | None
    decision: AlignmentDecision
    composite_score: float
    reason: str

def compute_composite_score(
    semantic_score: float,
    structural_score: float,
    measurement_overlap: float
) -> float:
    return (
        semantic_score     * 0.50
        + structural_score * 0.30
        + measurement_overlap * 0.20
    )

def align_documents(
    nodes_a: list[dict],
    nodes_b: list[dict],
    embedding_engine,
    claude_agent=None
) -> list[AlignmentResult]:
    
    results = []
    matched_b_ids = set()
    
    for node_a in nodes_a:
        candidates = embedding_engine.find_similar(node_a, doc_id_filter="doc_b", top_k=3)
        
        if not candidates:
            results.append(AlignmentResult(
                node_a_id=node_a["node_id"],
                node_b_id=None,
                decision=AlignmentDecision.DELETED_NODE,
                composite_score=0.0,
                reason="No match found in doc_b"
            ))
            continue
        
        # Check for ambiguity (top 2 candidates within 0.05 of each other)
        if len(candidates) >= 2 and (candidates[0]["score"] - candidates[1]["score"]) < 0.05:
            results.append(AlignmentResult(
                node_a_id=node_a["node_id"],
                node_b_id=None,
                decision=AlignmentDecision.AMBIGUOUS,
                composite_score=candidates[0]["score"],
                reason=f"Ambiguous: candidates {candidates[0]['node_id']} ({candidates[0]['score']:.2f}) and {candidates[1]['node_id']} ({candidates[1]['score']:.2f})"
            ))
            continue
        
        best = candidates[0]
        score = best["score"]
        
        # Structural similarity: same parent section?
        structural_score = 1.0 if node_a.get("parent_id") == best.get("parent_id") else 0.3
        
        composite = compute_composite_score(score, structural_score, score)
        
        if composite > 0.85:
            decision = AlignmentDecision.SAME_NODE
            reason = f"Semantic match {composite:.2f} — likely moved or renamed"
        elif composite > 0.60:
            decision = AlignmentDecision.MODIFIED
            reason = f"Semantic match {composite:.2f} — content likely modified"
        else:
            decision = AlignmentDecision.DELETED_NODE
            reason = f"Best match score {composite:.2f} below threshold"
        
        matched_b_ids.add(best["node_id"])
        results.append(AlignmentResult(
            node_a_id=node_a["node_id"],
            node_b_id=best["node_id"],
            decision=decision,
            composite_score=composite,
            reason=reason
        ))
    
    # Unmatched doc_b nodes → NEW_NODE
    all_b_ids = {n["node_id"] for n in nodes_b}
    for new_id in (all_b_ids - matched_b_ids):
        results.append(AlignmentResult(
            node_a_id=None,
            node_b_id=new_id,
            decision=AlignmentDecision.NEW_NODE,
            composite_score=0.0,
            reason="No corresponding node in doc_a"
        ))
    
    return results
```

---

## Skill 6: Deterministic Rules Engine

**Purpose:** PASS/FAIL computation. The only system that makes compliance determinations.

**Stack:** Python

```python
from dataclasses import dataclass
from enum import Enum

class Severity(Enum):
    HIGH   = "HIGH"    # margin <= -3 dB
    MEDIUM = "MEDIUM"  # -3 < margin < 0 dB
    LOW    = "LOW"     # informational finding
    INFO   = "INFO"    # non-compliance change

class ComplianceResult(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    MARGINAL = "MARGINAL"  # 0 to +3 dB margin

@dataclass
class FindingResult:
    test_case_id: str
    frequency_hz: float
    result: ComplianceResult
    margin_db: float
    severity: Severity
    limit_dbuv_m: float
    measured_dbuv_m: float
    standard_ref: str
    clause: str

def evaluate_measurement(
    frequency_hz: float,
    measured_dbuv_m: float,
    limit_dbuv_m: float,
    standard_ref: str,
    clause: str,
    test_case_id: str,
    uncertainty_db: float = 0.0,
    use_uncertainty: bool = False
) -> FindingResult:
    """
    Deterministic PASS/FAIL evaluation.
    Never calls LLM. Never uses probabilistic logic.
    """
    effective_measured = measured_dbuv_m
    if use_uncertainty:
        # Conservative: add uncertainty to measured value
        # Coverage factor k=2 (95% confidence)
        effective_measured = measured_dbuv_m + (2 * uncertainty_db)
    
    margin = round(limit_dbuv_m - effective_measured, 2)
    
    if margin >= 3.0:
        result = ComplianceResult.PASS
        severity = Severity.INFO
    elif margin >= 0.0:
        result = ComplianceResult.MARGINAL
        severity = Severity.LOW
    elif margin >= -3.0:
        result = ComplianceResult.FAIL
        severity = Severity.MEDIUM
    else:
        result = ComplianceResult.FAIL
        severity = Severity.HIGH
    
    return FindingResult(
        test_case_id=test_case_id,
        frequency_hz=frequency_hz,
        result=result,
        margin_db=margin,
        severity=severity,
        limit_dbuv_m=limit_dbuv_m,
        measured_dbuv_m=measured_dbuv_m,
        standard_ref=standard_ref,
        clause=clause
    )


def compute_diff_finding(
    measurement_a: dict,
    measurement_b: dict,
    standard_ref: str,
    clause: str
) -> dict | None:
    """
    Compare two measurements from Doc A and Doc B.
    Returns a finding only if there is a meaningful compliance change.
    """
    result_a = evaluate_measurement(**measurement_a, standard_ref=standard_ref, clause=clause)
    result_b = evaluate_measurement(**measurement_b, standard_ref=standard_ref, clause=clause)
    
    # No meaningful change if both pass/fail status identical and margin delta < 1dB
    margin_delta = abs(result_b.margin_db - result_a.margin_db)
    status_changed = result_a.result != result_b.result
    
    if not status_changed and margin_delta < 1.0:
        return None  # No meaningful change
    
    change_type = "status_change" if status_changed else "margin_change"
    
    # Severity is the worse of the two, or MEDIUM for any status change
    severity = max(result_a.severity, result_b.severity, key=lambda s: list(Severity).index(s))
    if status_changed:
        severity = max(severity, Severity.MEDIUM, key=lambda s: list(Severity).index(s))
    
    return {
        "change_type": change_type,
        "severity": severity.value,
        "standard_ref": standard_ref,
        "clause": clause,
        "frequency_hz": measurement_a["frequency_hz"],
        "evidence": {
            "doc_a": {
                "measured": result_a.measured_dbuv_m,
                "limit": result_a.limit_dbuv_m,
                "margin": result_a.margin_db,
                "result": result_a.result.value
            },
            "doc_b": {
                "measured": result_b.measured_dbuv_m,
                "limit": result_b.limit_dbuv_m,
                "margin": result_b.margin_db,
                "result": result_b.result.value
            },
            "margin_delta_db": round(result_b.margin_db - result_a.margin_db, 2)
        }
    }
```

---

## Skill 7: FastAPI Gateway

**Purpose:** REST API surface for the frontend and external integrations.

**Stack:** Python 3.12, FastAPI, Pydantic v2

```python
from fastapi import FastAPI, UploadFile, HTTPException, BackgroundTasks
from fastapi.websockets import WebSocket
from pydantic import BaseModel
import uuid

app = FastAPI(title="EMC Compliance Platform API", version="1.0.0")

# --- Request/Response Models ---

class UploadResponse(BaseModel):
    doc_id: str
    status: str = "queued"

class CompareRequest(BaseModel):
    doc_a: str
    doc_b: str
    mode: str = "emv_full"  # emv_full | ehs | iso | cross_domain

class CompareResponse(BaseModel):
    comparison_id: str
    status: str = "queued"

class StatusResponse(BaseModel):
    status: str
    stage: str
    progress: int

# --- Endpoints ---

@app.post("/api/v1/documents/upload", response_model=UploadResponse)
async def upload_document(
    file: UploadFile,
    doc_type: str = "emc_report",
    standard_hint: str = "CISPR 25",
    background_tasks: BackgroundTasks = None
):
    doc_id = f"doc_{uuid.uuid4().hex[:8]}"
    # Queue processing job
    background_tasks.add_task(process_document, doc_id, file, doc_type, standard_hint)
    return UploadResponse(doc_id=doc_id)


@app.get("/api/v1/documents/{doc_id}/status", response_model=StatusResponse)
async def get_document_status(doc_id: str):
    status = await get_job_status(doc_id)
    if not status:
        raise HTTPException(status_code=404, detail=f"Document {doc_id} not found")
    return status


@app.post("/api/v1/compare", response_model=CompareResponse)
async def trigger_comparison(req: CompareRequest, background_tasks: BackgroundTasks):
    comparison_id = f"cmp_{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(run_comparison, comparison_id, req.doc_a, req.doc_b, req.mode)
    return CompareResponse(comparison_id=comparison_id)


@app.get("/api/v1/compare/{comparison_id}")
async def get_comparison_result(comparison_id: str):
    result = await load_comparison_result(comparison_id)
    if not result:
        raise HTTPException(status_code=404)
    return result


@app.websocket("/ws/compare/{comparison_id}")
async def comparison_progress(websocket: WebSocket, comparison_id: str):
    await websocket.accept()
    async for event in stream_comparison_progress(comparison_id):
        await websocket.send_json(event)
        if event.get("stage") == "completed":
            break
    await websocket.close()
```

---

## Skill 8: Frontend — Compliance Diff Viewer (React)

**Purpose:** Main diff UI component. Shows structured compliance changes with severity, evidence, and ignored changes panel.

**Stack:** React, TypeScript, Tailwind CSS

**Key component pattern:**
```typescript
// ComplianceDiffViewer.tsx
import React, { useState } from 'react';

type Severity = 'HIGH' | 'MEDIUM' | 'LOW' | 'INFO';
type ChangeType = 'measurement_failure' | 'limit_change' | 'setup_change' | 'status_change';

interface Finding {
  finding_id: string;
  type: ChangeType;
  severity: Severity;
  clause: string;
  domain: string;
  evidence: {
    doc_a: { value: string; result: string; margin: string };
    doc_b: { value: string; result: string; margin: string };
    delta: string;
  };
  explanation: string;
}

interface ComparisonResult {
  summary: { critical_failures: number; warnings: number; matched_tests: number };
  findings: Finding[];
  ignored_changes: string[];
}

const SEVERITY_COLORS: Record<Severity, string> = {
  HIGH:   'bg-red-100 border-red-500 text-red-900',
  MEDIUM: 'bg-amber-100 border-amber-500 text-amber-900',
  LOW:    'bg-blue-100 border-blue-400 text-blue-900',
  INFO:   'bg-gray-100 border-gray-300 text-gray-700'
};

export function ComplianceDiffViewer({ result }: { result: ComparisonResult }) {
  const [showIgnored, setShowIgnored] = useState(false);
  const [filter, setFilter] = useState<Severity | 'ALL'>('ALL');

  const filtered = result.findings.filter(f =>
    filter === 'ALL' || f.severity === filter
  );

  return (
    <div className="flex flex-col gap-6 p-6 max-w-5xl mx-auto">
      {/* Executive Summary */}
      <div className="grid grid-cols-3 gap-4">
        <SummaryCard label="Critical Failures" value={result.summary.critical_failures} color="red" />
        <SummaryCard label="Warnings" value={result.summary.warnings} color="amber" />
        <SummaryCard label="Matched Tests" value={result.summary.matched_tests} color="green" />
      </div>

      {/* Filter Bar */}
      <div className="flex gap-2">
        {(['ALL', 'HIGH', 'MEDIUM', 'LOW'] as const).map(s => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`px-3 py-1 rounded text-sm font-medium border transition
              ${filter === s ? 'bg-slate-800 text-white border-slate-800' : 'bg-white text-slate-600 border-slate-300'}`}
          >
            {s}
          </button>
        ))}
      </div>

      {/* Findings List */}
      <div className="flex flex-col gap-3">
        {filtered.map(finding => (
          <FindingCard key={finding.finding_id} finding={finding} />
        ))}
      </div>

      {/* Ignored Changes Panel — MANDATORY FOR AUDIT TRUST */}
      <div className="border border-slate-200 rounded-lg">
        <button
          className="w-full flex justify-between items-center p-4 text-slate-600 text-sm font-medium"
          onClick={() => setShowIgnored(!showIgnored)}
        >
          <span>Ignored Changes ({result.ignored_changes.length}) — non-compliance artifacts excluded</span>
          <span>{showIgnored ? '▲' : '▼'}</span>
        </button>
        {showIgnored && (
          <ul className="px-4 pb-4 space-y-1">
            {result.ignored_changes.map((change, i) => (
              <li key={i} className="text-sm text-slate-500 flex items-center gap-2">
                <span className="text-slate-300">—</span> {change}
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function FindingCard({ finding }: { finding: Finding }) {
  const [expanded, setExpanded] = useState(finding.severity === 'HIGH');

  return (
    <div className={`border-l-4 rounded-r-lg p-4 ${SEVERITY_COLORS[finding.severity]}`}>
      <div className="flex justify-between items-start cursor-pointer" onClick={() => setExpanded(!expanded)}>
        <div>
          <span className="font-mono text-xs font-bold uppercase">{finding.severity}</span>
          <span className="mx-2 text-slate-400">·</span>
          <span className="font-medium">{finding.clause}</span>
          <span className="mx-2 text-slate-400">·</span>
          <span className="text-sm">{finding.domain}</span>
        </div>
        <span className="text-slate-400 text-sm">{expanded ? '▲' : '▼'}</span>
      </div>

      {expanded && (
        <div className="mt-3 space-y-3">
          {/* Evidence diff table */}
          <table className="w-full text-sm border-collapse bg-white rounded">
            <thead>
              <tr className="text-slate-500 text-xs uppercase">
                <th className="p-2 text-left">Metric</th>
                <th className="p-2 text-left">Doc A</th>
                <th className="p-2 text-left">Doc B</th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-slate-100">
                <td className="p-2 font-medium">Result</td>
                <td className="p-2">{finding.evidence.doc_a.result}</td>
                <td className="p-2">{finding.evidence.doc_b.result}</td>
              </tr>
              <tr className="border-t border-slate-100">
                <td className="p-2 font-medium">Margin</td>
                <td className="p-2">{finding.evidence.doc_a.margin}</td>
                <td className="p-2">{finding.evidence.doc_b.margin}</td>
              </tr>
            </tbody>
          </table>

          {/* Explanation */}
          <p className="text-sm opacity-80">{finding.explanation}</p>
        </div>
      )}
    </div>
  );
}
```

---

## Skill 9: Audit Log System

**Purpose:** Immutable append-only log for every classification, comparison, and compliance decision.

**Stack:** PostgreSQL

```sql
-- Append-only audit log (no UPDATE/DELETE allowed)
CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    event_type    TEXT NOT NULL,          -- 'classification' | 'comparison' | 'finding' | 'ontology_change'
    entity_id     TEXT NOT NULL,
    entity_type   TEXT NOT NULL,
    doc_id        TEXT,
    comparison_id TEXT,
    actor         TEXT NOT NULL,         -- user_id or 'system'
    model_version TEXT,                  -- claude-sonnet-4-... or qwen3-32b-...
    input_hash    TEXT,                  -- sha256 of input
    output_hash   TEXT,                  -- sha256 of output
    confidence    FLOAT,
    payload       JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Prevent any modification to audit records
CREATE RULE no_audit_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE RULE no_audit_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;

-- Indexes for common queries
CREATE INDEX idx_audit_comparison ON audit_log(comparison_id);
CREATE INDEX idx_audit_entity     ON audit_log(entity_id, entity_type);
CREATE INDEX idx_audit_created    ON audit_log(created_at);
```

---

## Build Order & Dependencies

```
Phase 1 (Core pipeline — 2 weeks):
  Skill 1 (Ingestion) → Skill 2 (Tables) → Skill 7 (Rules Engine)
  → Skill 10 (FastAPI)  → Skill 18 (Audit Log)

Phase 2 (Intelligence — 2 weeks):
  Skill 3 (Ontology) → Skill 4 (Embeddings) → Skill 5 (Alignment)
  → Skill 8 (Diff Engine) → Skill 9 (Celery)

Phase 3 (UI & polish — 1 week):
  Skill 14 (Dashboard) → Skill 15 (Diff Viewer) → Skill 16 (Evidence Panel)
  → Skill 13 (Explanations) → Skill 17 (Governance)
```
