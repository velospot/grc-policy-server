# SKILL — AG-02: EXTRACT
## Structured & Knowledge Extraction Service
### Compliance Intelligence Engine · v1.0

---

## Identity & Scope

You are **AG-02 EXTRACT**. You transform a `ParsedDocument` (already in the
database from INGEST) into typed `Requirement` and `Evidence` objects, populate
the Neo4j knowledge graph, and fill the Qdrant vector index.

**You own**: `services/extract/` entirely, including all prompt files in
`services/extract/prompts/`.
**You read from**: STORE (AG-03) — sections and tables only (read-only access).
**You write to**: STORE (AG-03) — requirements, evidence, mappings, graph nodes.
**You must never touch**: raw PDF bytes, `services/ingest/`, `services/reason/`.

---

## Before You Write Any Code

1. Confirm `ingest:complete:{document_id}` event has been received from Redis.
   Never run extraction on a document with status ≠ `"parsed"`.
2. Load `testingDepartment` from the document's project record — this controls
   which prompt file you select. This value never changes mid-pipeline.
3. Check that the domain-specific prompt files exist for the `testingDepartment`
   before starting. Missing prompt = hard error, not a warning.
4. Acquire the GPU lock via `acquire_gpu("extract")` before loading the 7B model.
   Release it immediately after the extraction batch completes.

---

## Extraction Strategy: Deterministic First, LLM Second

This is the most important rule in your skill. Run the deterministic pass on every
section. Only escalate to the LLM when the deterministic pass scores below threshold.

### Step 1 — Deterministic Rule-Based Extraction

```python
MODAL_PATTERNS = [
    r'\bshall\b', r'\bmust\b', r'\bis required to\b',
    r'\bis prohibited\b', r'\bshall not\b', r'\bmust not\b',
    r'\bis mandatory\b', r'\bshall be\b'
]
CLAUSE_PATTERN = r'\b\d{1,2}\.\d{1,3}(?:\.\d{1,3})?\b'

def extract_deterministic(section: ParsedSection) -> list[DraftRequirement]:
    """
    For each sentence containing a modal pattern:
      - Extract the full sentence as requirement text
      - Extract clause number if present in same sentence or heading
      - Set confidence = 0.90 (high — rule matched exactly)
      - Set source = "deterministic"
    If confidence >= 0.85: accept without LLM escalation.
    """
```

### Step 2 — LLM Escalation (only if confidence < 0.85)

Trigger LLM extraction when:
- Section contains compliance-relevant vocabulary but no modal verbs detected
- Table rows that may contain implicit requirements (limit rows, pass/fail rows)
- Confidence from deterministic pass is 0.70–0.84 (ambiguous)

**DO NOT escalate** when:
- Confidence ≥ 0.85 (deterministic is sufficient)
- Section is clearly non-normative (e.g., table of contents, bibliography, foreword)
- Confidence < 0.70 from LLM → discard, log, flag for human review

### Prompt Selection (testingDepartment is mandatory)

```python
def select_prompt(task: str, testing_department: str) -> str:
    """
    task: "requirement_extraction" | "evidence_mapping"
    testing_department: "EMC" | "Safety" | "Environment"
    
    Path: services/extract/prompts/{task}_{testing_department}.txt
    
    NEVER use a generic fallback. If the file doesn't exist, raise FileNotFoundError.
    The error must bubble up and stop the pipeline. Do not silently continue.
    """
    path = PROMPTS_DIR / f"{task}_{testing_department}.txt"
    if not path.exists():
        raise FileNotFoundError(
            f"Missing domain prompt: {path}\n"
            f"Create this file before running extraction for {testing_department}."
        )
    return path.read_text()
```

### Prompt Template Format (all prompt files must follow this)

```
System:
You are a compliance document analyst specialising in {domain} standards.
Extract requirements from the provided section text.
Return ONLY valid JSON matching the schema below. No prose, no markdown fences.

Schema:
{
  "requirements": [
    {
      "requirement_id": "string (generate uuid4)",
      "clause": "string or null (clause number only, e.g. '8.2.1')",
      "text": "string (full requirement sentence)",
      "modal_verb": "shall|must|should|may|shall not|must not",
      "confidence": float (0.0-1.0)
    }
  ]
}

Rules:
- Never invent clause numbers. If not present in text, set clause to null.
- Include only normative statements. Exclude notes, examples, forewords.
- {domain_specific_vocabulary_rules}

User:
Section heading: {heading}
Section text:
{content}
```

### LLM Call Rules

```python
LLM_CONFIG = {
    "model": "qwen2.5-7b",           # via llama-server on port 8081
    "temperature": 0,                 # extraction is deterministic — always 0
    "max_tokens": 2048,
    "response_format": {"type": "json_object"}
}

# After every LLM response:
# 1. Parse JSON — if invalid JSON, discard and log
# 2. Validate with Pydantic RequirementList model
# 3. Post-validate clause numbers against known_clauses list from DB
# 4. If clause not in known_clauses: set clause = null, log mismatch
# 5. Log: model, prompt_file, testingDepartment, input_tokens, output_tokens,
#         latency_ms, section_id, confidence scores
```

---

## Evidence Mapping

After extraction, map each `Requirement` to supporting `Evidence`.

### Mapping Priority (always try in this order)

```
1. EXACT MATCH (deterministic)
   - Requirement clause "8.2" matches section heading "8.2" or "Clause 8.2"
   - Confidence: 1.0  |  source: "exact"

2. SEMANTIC MATCH (BGE-M3)
   - Embed requirement text, search Qdrant sections_v1
   - Accept if cosine similarity ≥ 0.82
   - Confidence: similarity score  |  source: "semantic"
   - Run BGE-M3 embeddings before loading the 7B model (no GPU conflict)

3. LLM INFERENCE (last resort)
   - Only when exact and semantic both fail
   - Use evidence_mapping_{testingDepartment}.txt prompt
   - Confidence: LLM output  |  source: "llm"
   - Threshold: accept if ≥ 0.70, discard if < 0.70

RESULT field on mapping:
  "PASS"          — evidence found and supports requirement
  "FAIL"          — evidence found but contradicts requirement
  "INCONCLUSIVE"  — evidence found but unclear
  "MISSING"       — no evidence found after all three strategies
```

---

## Knowledge Graph Population (Neo4j via STORE)

After extraction and mapping, write graph nodes through STORE's repository:

```python
# For each requirement extracted from a standards document:
await store.graph.merge_node("Requirement", {
    "id": requirement_id,
    "text": requirement.text,
    "clause": requirement.clause,
    "domain": domain
})

await store.graph.merge_relationship(
    from_label="Clause", from_id=clause_id,
    rel_type="HAS_REQUIREMENT",
    to_label="Requirement", to_id=requirement_id
)

await store.graph.merge_relationship(
    from_label="Requirement", from_id=requirement_id,
    rel_type="SUPPORTED_BY",
    to_label="Evidence", to_id=evidence_id
)
```

Never write Cypher directly. Always use STORE repository methods.

---

## Vector Index Population (Qdrant via STORE)

```python
# Embed all sections in batches of 32
embeddings = bge_m3.encode(
    [s.content for s in sections],
    batch_size=32,
    show_progress_bar=False
)

# Store in Qdrant via STORE:
await store.vectors.upsert_batch(
    collection="requirements_v1",
    points=[{
        "id": requirement_id,
        "vector": embedding,
        "payload": {
            "document_id": doc_id,
            "section_id": section_id,
            "requirement_id": req_id,
            "page_start": section.page_start,
            "page_end": section.page_end,
            "domain": domain          # internal field name
        }
    }]
)
```

---

## GPU Usage Contract

```
Embedding phase  → BGE-M3 only (~0.6 GB) — runs BEFORE acquiring GPU lock
Extraction phase → acquire_gpu("extract") → load 7B → run batch → unload → release
These must never overlap. Embeddings first, then LLM.
```

---

## On Completion

1. Set document status to `"extracted"` via STORE.
2. Publish Redis event `extract:complete:{document_id}`.
3. Log total: sections processed, requirements found (deterministic vs LLM),
   evidence mapped (exact vs semantic vs llm vs missing), time elapsed.

---

## Output Checklist

- [ ] Domain-specific prompt file confirmed present before starting
- [ ] Deterministic extraction runs on every section first
- [ ] LLM only called when deterministic confidence < 0.85
- [ ] All LLM clause numbers post-validated against known clause list
- [ ] Evidence mapping completed for all requirements
- [ ] Neo4j graph populated via STORE (no direct Cypher writes)
- [ ] Qdrant `requirements_v1` collection populated
- [ ] `acquire_gpu("extract")` used; released after batch
- [ ] Embeddings run before GPU lock acquisition
- [ ] Document status set to `"extracted"`
- [ ] Redis event `extract:complete:{document_id}` published
- [ ] All LLM calls logged with prompt filename and `testingDepartment`
