# SKILL — AG-05: REASON
## Compliance Reasoning Engine
### Compliance Intelligence Engine · v1.0

---

## Identity & Scope

You are **AG-05 REASON**. You receive a `ComparisonResult` from COMPARE and turn
it into auditor-ready findings: compliance impact statements, risk scores, and
answers to auditor questions. You are the only service that uses the 14B model.
You are the only service that holds the GPU lock for extended periods (copilot session).

**You own**: `services/reason/` entirely, including all prompt files in
`services/reason/prompts/`.
**You read from**: STORE (AG-03) — comparisons, requirements, evidence, graph, vectors.
**You write to**: STORE (AG-03) — audit_results table.
**You must never touch**: raw PDF bytes, `services/ingest/`, `services/extract/`,
`services/compare/`.

---

## Before You Write Any Code

1. Confirm `extract:complete:{document_id}` event received for both documents.
2. Confirm the `ComparisonResult` exists in STORE with status `"finished"`.
3. Load `testingDepartment` from the comparison record — this is your primary
   routing key for prompt selection and domain rules.
4. Verify domain-specific prompt files exist for the `testingDepartment` before
   making any LLM call.
5. Acquire `acquire_gpu("reason")` before loading the 14B model. Hold it for the
   duration of a copilot session (release on 5-minute idle timeout).

---

## Sub-Module 1: Compliance Impact Engine

Determine what the `ComparisonResult` means for the auditor's compliance obligations.
Apply deterministic domain rules first, then use LLM only for the narrative.

### Domain Rules (deterministic — no LLM)

```python
EMC_RULES = {
    "retest_required": lambda diff: (
        diff.l5_tables.has_limit_changes or
        diff.l5_tables.margin_drop_db > 3.0 or
        diff.l3_requirements.has_changes
    ),
    "certification_impact": lambda diff: (
        diff.l5_tables.has_failing_tests or
        diff.l5_tables.has_zero_margin
    )
}

SAFETY_RULES = {
    "re_evaluation_required": lambda diff: (
        diff.l3_requirements.has_changes or
        diff.l4_evidence.has_missing_mandatory
    ),
    "clause_evidence_changed": lambda diff: (
        diff.l4_evidence.has_changes
    )
}

ENVIRONMENT_RULES = {
    "supplier_revalidation": lambda diff: any(
        "substance" in kd.section.lower() or "declaration" in kd.section.lower()
        for kd in diff.key_differences
        if kd.change_type != "ADDED"  # new substances always require revalidation
    ),
    "rohs_impact": lambda diff: any(
        "rohs" in kd.section.lower() for kd in diff.key_differences
    )
}

RULES = {"EMC": EMC_RULES, "Safety": SAFETY_RULES, "Environment": ENVIRONMENT_RULES}

def apply_domain_rules(diff: ComparisonResult, dept: str) -> ImpactFlags:
    rules = RULES[dept]
    return ImpactFlags(**{k: v(diff) for k, v in rules.items()})
```

### LLM for Narrative Only

After deterministic flags are computed, call the 14B model to produce the human-
readable `impact_statement`. The LLM is not making the compliance decision — it is
writing the sentence that explains the decision already made.

```python
# Prompt selection:
prompt = select_prompt("impact_assessment", testing_department)

# Context passed to LLM:
context = {
    "testing_department": testing_department,
    "risk_level": diff.overall_risk,
    "impact_flags": impact_flags.model_dump(),     # already determined
    "key_differences_summary": top_10_differences, # top 10 by severity
    "affected_clauses": affected_clauses
}

# LLM config:
temperature = 0.2   # slightly creative for narrative; still conservative
max_tokens = 512    # impact statements are concise
```

Output structure:

```json
{
  "impact_id": "uuid",
  "domain": "EMC|Safety|Environmental",
  "risk": "Low|Medium|High|Critical",
  "impact_statement": "string — plain English, one paragraph",
  "retesting_required": true,
  "affected_clauses": ["8.2", "9.1"],
  "evidence_refs": [{"page": 73, "section": "8.4", "document_id": "..."}]
}
```

**Rule**: Every `impact_statement` must reference at least one clause or page number
from the actual documents. Never produce a generic statement with no specific reference.

---

## Sub-Module 2: Risk Scorer

Produce a numeric risk score 0–100 alongside the categorical level.

```python
# Weights — configurable in config/risk_weights.yaml
WEIGHTS = {
    "requirement_change": 0.40,
    "evidence_gap":       0.30,
    "numeric_margin":     0.20,
    "metadata_change":    0.10
}

def compute_risk_score(diff: ComparisonResult) -> float:
    score = 0.0
    # requirement_change component
    req_changes = len(diff.l3_requirements.changes)
    score += min(req_changes / 5, 1.0) * 100 * WEIGHTS["requirement_change"]
    # evidence_gap component
    missing = diff.l4_evidence.missing_count
    score += min(missing / 3, 1.0) * 100 * WEIGHTS["evidence_gap"]
    # numeric_margin component (worst margin drop, normalised to 0-1 over 10 dB scale)
    worst_drop = diff.l5_tables.worst_margin_drop_db
    score += min(worst_drop / 10.0, 1.0) * 100 * WEIGHTS["numeric_margin"]
    # metadata component
    meta_changes = len(diff.l1_metadata.changes)
    score += min(meta_changes / 5, 1.0) * 100 * WEIGHTS["metadata_change"]
    return round(min(score, 100.0), 1)
```

---

## Sub-Module 3: Auditor Copilot

Answers auditor questions grounded in the loaded documents.

### Query Routing (deterministic — pattern match first)

```python
ROUTED_PATTERNS = {
    r"what.{0,20}changed":                    "diff_summary_handler",
    r"which.{0,30}requirement.{0,20}impact":  "requirement_impact_handler",
    r"which.{0,20}evidence.{0,20}support":    "evidence_lookup_handler",
    r"is.{0,15}retest(ing)?.{0,10}required":  "retest_recommendation_handler",
    r"which.{0,20}report.{0,20}(reassess|review|impact)": "report_impact_handler",
}

def route_query(question: str) -> str:
    """
    Try each pattern (case-insensitive). Return handler name on first match.
    If no pattern matches: return "llm_copilot_handler".
    Never call the LLM to decide routing.
    """
    q = question.lower()
    for pattern, handler in ROUTED_PATTERNS.items():
        if re.search(pattern, q):
            return handler
    return "llm_copilot_handler"
```

### LLM Copilot Handler — RAG Pipeline

For free-form questions that don't match a deterministic handler:

```python
async def llm_copilot_handler(question: str, context: CopilotContext) -> CopilotResponse:
    """
    Step 1: Embed question with BGE-M3 (CPU; GPU lock already held by 14B)
            Note: run embedding BEFORE the 14B model is loaded to avoid VRAM conflict.
            If 14B is already loaded, BGE-M3 runs on CPU (~40% speed — acceptable
            for a single query vector).

    Step 2: Hybrid search Qdrant (vector + keyword)
            - collection: sections_v1 and requirements_v1
            - filter: domain = context.domain
            - filter: document_id IN [doc_a_id, doc_b_id]
            - limit: 10 results per collection

    Step 3: Graph context (Neo4j 2-hop neighbourhood)
            - Start from requirement nodes referenced in top search results
            - Retrieve SUPPORTED_BY evidence and FOUND_IN report relationships
            - Add graph nodes to context window

    Step 4: Assemble context window
            - System: domain-specific system prompt (from prompts/)
            - Context: top 10 section chunks + graph nodes
            - User: auditor question

    Step 5: Call 14B model (already loaded, GPU lock held)
            temperature = 0.2
            max_tokens  = 1024

    Step 6: Validate response contains at least one citation.
            If no citation: return "Insufficient evidence in loaded documents."
            Never return an unsupported compliance conclusion.
    """
```

### Citation Format

Every LLM response must include inline citations:

```
"The ESD test requirement in clause 8.4 [p.73, doc_b] changed from ±4 kV to ±8 kV,
which exceeds the previous test setup limit and requires retesting."

citation = DocumentReference(
    section="8.4",
    page=73,
    source_text="ESD test voltage: ±8 kV",
    node_id="req_uuid_here"
)
```

If the LLM output contains no extractable citation, do not return it to the auditor.
Return the fallback message and log the failure.

### Copilot Session GPU Management

```python
SESSION_IDLE_TIMEOUT = 300  # 5 minutes

# On first copilot query in a session:
#   acquire_gpu("reason") → load 14B model → answer query

# On subsequent queries within session:
#   Reset idle timer. Model stays loaded. No GPU re-acquire needed.

# On idle timeout:
#   Unload 14B model → release GPU lock
#   Next query will re-acquire and reload (~20s load time; acceptable)
```

---

## Prompt Files (all must exist)

```
services/reason/prompts/
  impact_assessment_EMC.txt
  impact_assessment_Safety.txt
  impact_assessment_Environment.txt
  auditor_summary_EMC.txt
  auditor_summary_Safety.txt
  auditor_summary_Environment.txt
  copilot_system_EMC.txt
  copilot_system_Safety.txt
  copilot_system_Environment.txt
```

Each system prompt must include domain vocabulary guidance:
- EMC: dBµV/m, CISPR limits, conducted/radiated, frequency bands
- Safety: hazard energy levels, protection means, IEC 62368 structure
- Environment: substance thresholds (RoHS 1000/100 ppm), REACH SVHC list

---

## What You Must Never Do

| Prohibited Action | Why |
|---|---|
| Use LLM to make the compliance decision | Decisions are deterministic; LLM writes narrative only |
| Return a response with no citation | Traceability principle |
| Produce generic statements with no page/clause reference | Evidence-driven principle |
| Load 14B model without `acquire_gpu()` | GPU OOM risk |
| Use a generic fallback prompt | Missing domain prompt is a hard error |
| Route copilot queries using LLM | Routing is deterministic pattern matching |

---

## Output Checklist

- [ ] Domain rules applied deterministically before any LLM narrative
- [ ] Every `impact_statement` references at least one clause and page
- [ ] Risk score computed from weights in `config/risk_weights.yaml`
- [ ] Copilot routing uses pattern match (not LLM)
- [ ] RAG pipeline filters by `domain` and `document_id` in Qdrant
- [ ] Every LLM copilot response validated for citation presence
- [ ] No citation → fallback message returned, not hallucinated answer
- [ ] `acquire_gpu("reason")` used; released on 5-min idle timeout
- [ ] Audit results written to STORE with `evidence_refs` non-empty
- [ ] All domain-specific prompt files confirmed present at startup
