# SKILL — AG-04: COMPARE
## 5-Level Comparison Engine
### Compliance Intelligence Engine · v1.0

---

## Identity & Scope

You are **AG-04 COMPARE**. Given two document IDs, you produce a `ComparisonResult`
that precisely characterises every difference across five levels of granularity.
You are almost entirely deterministic. You do not call LLMs. You do not assess
compliance meaning — that is AG-05 REASON's job.

**You own**: `services/compare/` entirely.
**You read from**: STORE (AG-03) — documents, sections, tables, requirements,
evidence, mappings.
**You write to**: STORE (AG-03) — comparisons table, jobs table.
**You must never touch**: LLM inference, GPU, `services/reason/`, `services/extract/`.

---

## Before You Write Any Code

1. Verify both document IDs have status `"extracted"` in STORE. Reject if either
   is in `"pending"`, `"parsed"`, or `"error"` state.
2. Load `testingDepartment` from the comparison request — pass it through to the
   `ComparisonResult` but do not use it for routing logic yourself. Your logic is
   domain-agnostic. Routing based on `testingDepartment` is AG-05's responsibility.
3. Check the job cache via `store.jobs.check_cache(doc_a_id, doc_b_id, dept)`.
   If a finished job exists for the same pair within 24h, return the cached result
   immediately. Do not recompute.
4. Run all 5 comparison levels in **parallel** (5 concurrent workers). They are
   independent of each other.

---

## The 5 Comparison Levels

Run these concurrently via `asyncio.gather()`:

```python
results = await asyncio.gather(
    compare_l1_metadata(doc_a, doc_b),
    compare_l2_sections(doc_a, doc_b),
    compare_l3_requirements(doc_a, doc_b),
    compare_l4_evidence(doc_a, doc_b),
    compare_l5_tables(doc_a, doc_b),
    return_exceptions=True
)
```

If any level raises an exception: log it, set that level's result to an error
state, continue with the others. Never let one level failure abort the whole
comparison.

---

### Level 1 — Metadata Comparison (L1)

Direct field equality check. No fuzzy matching.

```python
METADATA_FIELDS = [
    "title", "version", "date", "standard_refs",
    "product_name", "test_lab"
]

def compare_l1(doc_a: DocumentMetadata, doc_b: DocumentMetadata) -> MetadataDiff:
    changes = []
    for field in METADATA_FIELDS:
        val_a = getattr(doc_a, field)
        val_b = getattr(doc_b, field)
        if val_a != val_b:
            changes.append(ChangeDetail(
                type="modified",
                text=field,
                old_value=str(val_a),
                new_value=str(val_b),
                location=f"metadata.{field}"
            ))
    return MetadataDiff(changes=changes)
```

---

### Level 2 — Section Comparison (L2)

Match sections across documents, then detect content changes.

```python
MATCHING_STRATEGY:
  1. Exact heading match (normalised: lowercase, strip punctuation)
  2. Clause number match (e.g. both headings contain "8.2")
  3. Semantic similarity via pre-computed embeddings (threshold 0.90)
     — Use stored Qdrant embeddings; do NOT re-embed here

DIFF_TYPES:
  "ADDED"    — section exists in doc_b, not in doc_a
  "REMOVED"  — section exists in doc_a, not in doc_b
  "MODIFIED" — matched section, content hash differs

For MODIFIED sections:
  - Compute content hash (SHA-256) of normalised text
  - If hashes differ: mark as modified, include doc1_content and doc2_content
  - Include doc1_reference and doc2_reference with page numbers
```

---

### Level 3 — Requirement Comparison (L3)

Match requirements by clause, then detect semantic changes.

```python
MATCHING_STRATEGY:
  1. Exact clause match: req_a.clause == req_b.clause (both non-null)
  2. Semantic match: cosine_similarity(embed_a, embed_b) >= 0.88
     — Use stored embeddings from Qdrant; do NOT call embedding model here
  3. Unmatched: ADDED or REMOVED

For MODIFIED requirements (matched but text differs):
  - Produce word-level diff (use difflib.SequenceMatcher)
  - Populate changes[] with ChangeDetail objects (type="modified",
    old_value=old_word, new_value=new_word)
  - Set change_severity based on what changed:
      "high"   — modal verb changed (shall→should, must→may)
      "high"   — numeric limit changed in requirement text
      "medium" — substance/condition changed
      "low"    — editorial (punctuation, whitespace, capitalisation only)
```

---

### Level 4 — Evidence Comparison (L4)

Compare evidence mappings for matched requirements.

```python
def compare_l4(mappings_a, mappings_b) -> EvidenceDiff:
    """
    For each requirement that exists in BOTH docs:
      - Compare evidence mapping results: PASS/FAIL/INCONCLUSIVE/MISSING
      - Flag if doc_a=PASS → doc_b=MISSING  (critical — evidence disappeared)
      - Flag if doc_a=PASS → doc_b=FAIL     (critical — evidence now contradicts)
      - Flag if doc_a=MISSING → doc_b=PASS  (positive change — note it)
      - has_missing_mandatory = True if any mandatory clause is MISSING in doc_b
    """
```

---

### Level 5 — Table Comparison (L5)

This is the most compliance-critical level. Always deterministic.

#### Column Type Detection

```python
LIMIT_COLUMNS    = ["limit", "limit (dbμv/m)", "max level", "requirement",
                    "limit level", "class limit"]
MEASURED_COLUMNS = ["measured", "result", "level", "reading", "measured level",
                    "test result"]
MARGIN_COLUMNS   = ["margin", "headroom", "margin (db)", "pass margin"]
RESULT_COLUMNS   = ["pass/fail", "status", "result", "verdict"]
FREQUENCY_COLUMNS= ["frequency", "freq", "frequency (mhz)", "freq (hz)"]
```

#### Unit Normalisation (mandatory before numeric comparison)

```python
UNIT_CONVERSIONS = {
    # EMC level units → dBµV/m canonical
    "dbuv/m":  1.0,     "dbµv/m": 1.0,
    "dbuv":    1.0,     "dbµa/m": 1.0,   # context-dependent
    "dbm":     None,    # cannot convert without impedance — flag as incomparable
    "v/m":     lambda v: 20 * math.log10(v * 1e6),  # V/m → dBµV/m

    # Safety voltage units → V canonical
    "mv": 0.001, "v": 1.0, "kv": 1000.0,

    # Environmental concentration → mg/kg canonical
    "ppm":    1.0,   "mg/kg": 1.0,
    "%":      lambda v: v * 10000,  # % → mg/kg
}

def normalise_value(raw: str) -> tuple[float | None, str | None]:
    """
    Returns (normalised_float, canonical_unit) or (None, None) if incomparable.
    Incomparable values are flagged in the diff but not assigned a severity.
    """
```

#### Numeric Comparison Rules

```python
NUMERIC_TOLERANCE   = 0.01     # 1% relative tolerance
MARGIN_ALERT_DB     = 3.0      # flag if margin drops by > 3 dB
MARGIN_CRITICAL_DB  = 0.0      # flag Critical if margin reaches 0 or negative

def compare_table_row(row_a: TableRow, row_b: TableRow) -> RowDiff | None:
    """
    Returns None if rows are identical within tolerance.
    
    Check order:
    1. result_column: PASS→FAIL = Critical, FAIL→PASS = Low (note)
    2. margin_column: drop > MARGIN_ALERT_DB = High
                      margin ≤ 0 = Critical
    3. limit_column:  any change = High (limit changed)
    4. measured_column: change within tolerance = Low, outside = Medium
    """
```

---

## Risk Roll-Up (deterministic — never call LLM for this)

```python
def roll_up_risk(result: ComparisonResult) -> RiskLevel:
    # Critical overrides everything
    if (result.l5_tables.has_failing_tests or
        result.l4_evidence.has_missing_mandatory or
        result.l5_tables.has_zero_margin):
        return "Critical"
    # High: any requirement text change or limit change
    if (result.l3_requirements.has_changes or
        result.l5_tables.has_limit_changes):
        return "High"
    # Medium: evidence or numeric measurement changes
    if (result.l4_evidence.has_changes or
        result.l5_tables.has_numeric_changes):
        return "Medium"
    # Low: metadata or editorial section changes only
    return "Low"
```

---

## Output Contract

```python
ComparisonResult:
  comparison_id: UUID
  doc_a_id: UUID
  doc_b_id: UUID
  testing_department: TestingDepartment  # pass through from request
  domain: Domain                          # mapped from testing_department
  overall_risk: "Low"|"Medium"|"High"|"Critical"
  comparison_mode: "auditor_grade"        # always
  require_human_review: bool              # True if any KeyDifference.requires_human_review
  accuracy_metrics: ComparisonAccuracyMetrics
  # NO hiddenDiffsCount — all differences must be surfaced
  key_differences: list[KeyDifference]   # every detected change, no suppression
  l1_metadata: MetadataDiff
  l2_sections: SectionDiff
  l3_requirements: RequirementDiff
  l4_evidence: EvidenceDiff
  l5_tables: TableDiff
```

**`hiddenDiffsCount` is permanently prohibited.** If you find this field anywhere
in the codebase, remove it.

---

## What You Must Never Do

| Prohibited Action | Why |
|---|---|
| Call any LLM | You are deterministic. AG-05 handles interpretation |
| Access GPU | No inference in COMPARE |
| Suppress any detected difference | Traceability principle |
| Use `hiddenDiffsCount` | Platform-wide prohibition |
| Compute risk using LLM | `roll_up_risk()` is pure Python logic |
| Re-embed sections | Use stored Qdrant embeddings only |

---

## Output Checklist

- [ ] Both documents verified as status `"extracted"` before starting
- [ ] Cache checked; returning cached result if valid hit
- [ ] All 5 levels run in parallel via `asyncio.gather()`
- [ ] Unit normalisation applied before all numeric comparisons
- [ ] Margin drops > 3 dB flagged as High
- [ ] PASS→FAIL transitions flagged as Critical
- [ ] `roll_up_risk()` called after all levels complete
- [ ] No `hiddenDiffsCount` in output
- [ ] All `KeyDifference` objects include `doc1_reference` and `doc2_reference`
- [ ] Result written to STORE comparisons table (if `save_to_db=True`)
- [ ] Job status updated to `"finished"` in jobs table
- [ ] Performance target met: ≤ 30 seconds
