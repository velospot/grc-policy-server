# SKILL — AG-09: TEST
## QA & Evaluation Harness
### Compliance Intelligence Engine · v1.0

---

## Identity & Scope

You are **AG-09 TEST**. You own all tests — unit, integration, performance, and
AI evaluation harnesses. You write tests that prove the platform works correctly
against real compliance document scenarios. You do not implement features.

**You own**: `tests/` entirely.
**You write tests for**: all eight other agents' modules.
**You must never**: modify production code in `services/`. If a test cannot pass
without changing production code, raise it as a bug — do not patch around it.

---

## Before You Write Any Tests

1. Read the relevant agent's SKILL file to understand its exact behaviour contracts.
   Tests must verify the contract, not your assumptions about the contract.
2. Check `tests/fixtures/sample_reports/` for available test PDFs before requesting
   new ones. Use the smallest fixture that exercises the behaviour under test.
3. Every test that involves LLM output must have a deterministic fallback assertion —
   do not write tests that can only pass if the LLM happens to produce certain text.
4. Performance tests must run on the target hardware (Intel i9, 64 GB RAM, RTX 5070 Ti)
   and measure actual wall-clock time. Never estimate or mock performance.

---

## Test Fixture Requirements

All sample documents go in `tests/fixtures/sample_reports/`. Anonymise all real
customer data. Required fixtures:

```
emc_report_v1.pdf
  - 50 pages, text-native
  - Contains: conducted emissions table (limits, measured, margin, pass/fail)
  - References: CISPR 32, IEC 61000-4-2
  - Margins: all > 3 dB

emc_report_v2.pdf
  - Same structure as v1
  - Changes vs v1:
      * 2 rows: margin reduced by > 3 dB (must trigger High risk)
      * 1 row: PASS → FAIL transition (must trigger Critical)
      * Version number changed in metadata

iec62368_safety_report.pdf
  - 80 pages, mixed (some scanned pages)
  - References: IEC 62368-1:2023 clauses 5.1, 5.4, 6.1, 8.2
  - Contains: hazard energy table, protection means table
  - Has: 2 clauses with MISSING evidence mapping

rohs_declaration.pdf
  - 10 pages, text-native
  - Contains: substance declaration table (6 RoHS substances, concentrations)
  - Supplier names anonymised

standard_ed3.pdf
  - 30 pages, text-native
  - IEC-style document structure with numbered clauses
  - 10 "shall" requirements extractable deterministically

standard_ed4.pdf
  - Same document, N+1 revision
  - Changes: 3 clauses modified (1 modal verb change, 1 limit change, 1 editorial)
  - 1 clause added, 1 clause removed
```

---

## Unit Tests

Location: each service has `tests/unit/{service_name}/`. Run independently.

### AG-01 INGEST

```python
# test_pdf_classification.py
def test_text_native_classification():
    """PDFs with > 100 chars/page classified as text_native."""

def test_scanned_classification():
    """PDFs with < 20 chars/page classified as scanned."""

def test_mixed_classification():
    """PDFs with mixed pages classified as mixed."""

def test_ocr_confidence_threshold_flags_not_discards():
    """Page with OCR confidence 0.65 is stored with needs_review=True, not dropped."""

def test_ocr_page_image_saved():
    """Every OCR'd page image saved to /data/ocr_images/{doc_id}/page_{n}.png"""

def test_table_extracted_as_structure_not_text():
    """Table with 4 columns extracted as ParsedTable with headers and rows."""

def test_memory_limit():
    """200-page PDF processing does not exceed 8 GB RAM peak."""
    # Use tracemalloc; assert peak < 8 * 1024**3

def test_size_bytes_is_integer():
    """ParsedDocument.size_bytes is int, not string."""
```

### AG-02 EXTRACT

```python
# test_requirement_extraction.py
def test_shall_pattern_detected():
    """'shall' in sentence → requirement extracted with confidence ≥ 0.90."""

def test_must_not_pattern_detected():
    """'must not' in sentence → modal_verb = 'must not', source = 'deterministic'."""

def test_deterministic_before_llm():
    """Section with 'shall' does not trigger LLM call (confidence ≥ 0.85)."""

def test_llm_called_for_ambiguous_section():
    """Section with implicit requirement (confidence 0.75) triggers LLM."""

def test_prompt_selection_emc():
    """testingDepartment='EMC' selects requirement_extraction_EMC.txt."""

def test_prompt_selection_safety():
    """testingDepartment='Safety' selects requirement_extraction_Safety.txt."""

def test_prompt_selection_missing_raises():
    """Missing prompt file raises FileNotFoundError — does not silently continue."""

def test_llm_clause_validation():
    """LLM-returned clause '99.99' not in known_clauses → set to null, logged."""

def test_low_confidence_llm_discarded():
    """LLM output with confidence 0.65 discarded, not stored."""

def test_evidence_exact_match_priority():
    """Clause match used before semantic match — semantic not called if exact succeeds."""

def test_evidence_semantic_threshold():
    """Semantic similarity 0.80 < 0.82 → not accepted; result = MISSING."""

def test_evidence_result_missing():
    """No matching evidence → requirement_evidence_map.result = 'MISSING'."""

def test_embeddings_before_gpu_lock():
    """BGE-M3 embedding completes before acquire_gpu() is called."""
```

### AG-03 STORE

```python
# test_repositories.py
def test_size_bytes_stored_as_integer():
    """documents.size_bytes column accepts int, rejects string."""

def test_evidence_refs_check_constraint():
    """INSERT into audit_results with empty evidence_refs[] raises IntegrityError."""

def test_save_to_db_false_no_comparisons_row():
    """compare job with save_to_db=False writes to jobs table only, not comparisons."""

def test_job_cache_hit_24h():
    """Same doc pair + dept + finished job < 24h → cache hit returned."""

def test_job_cache_miss_expired():
    """Same doc pair + dept + finished job > 24h → no cache hit."""

def test_bulk_create_sections_transaction():
    """Partial failure in bulk section insert rolls back all rows."""

def test_repository_returns_pydantic_not_raw_row():
    """get_document() returns ParsedDocument instance, not asyncpg Record."""
```

### AG-04 COMPARE

```python
# test_comparison_levels.py
def test_l5_margin_drop_3db_high():
    """Margin drop of 3.5 dB → change_severity = 'high'."""

def test_l5_margin_zero_critical():
    """Margin = 0 dB → has_zero_margin = True → overall_risk = 'Critical'."""

def test_l5_pass_to_fail_critical():
    """PASS → FAIL in result column → overall_risk = 'Critical'."""

def test_l5_unit_normalisation():
    """dBµV/m and dBuV/m compared as equal (unit alias)."""

def test_l5_incomparable_units_flagged():
    """dBm vs dBµV/m → flagged as incomparable, not assigned severity."""

def test_l3_modal_verb_change_high():
    """Requirement text change: 'shall' → 'should' → change_severity = 'high'."""

def test_l3_editorial_change_low():
    """Requirement text change: capitalisation only → change_severity = 'low'."""

def test_no_hidden_diffs_count():
    """ComparisonResult has no hiddenDiffsCount attribute."""

def test_all_differences_surfaced():
    """Number of KeyDifferences matches number of actual changes detected."""

def test_cache_hit_returns_without_recompute():
    """Existing finished job for same pair → returned without running comparison."""

def test_parallel_levels():
    """All 5 levels run concurrently (verify via timing: total ≈ slowest level)."""

def test_risk_rollup_deterministic():
    """roll_up_risk() called with no LLM involvement (mock LLM, assert not called)."""
```

### AG-05 REASON

```python
# test_reason.py
def test_domain_rules_applied_before_llm():
    """EMC retest flag set by rule before LLM narrative call."""

def test_impact_statement_contains_clause():
    """impact_statement includes at least one clause reference (regex check)."""

def test_copilot_routing_deterministic():
    """'Is retesting required?' → retest_recommendation_handler (no LLM for routing)."""

def test_copilot_rag_filters_by_domain():
    """Qdrant search called with domain filter matching testingDepartment."""

def test_copilot_no_citation_returns_fallback():
    """LLM response with no citation → fallback message, not hallucinated answer."""

def test_gpu_lock_acquired_before_14b():
    """acquire_gpu() called before llama-server process starts."""

def test_copilot_session_idle_unloads_model():
    """After 5min idle, model unloaded and GPU lock released."""

def test_risk_score_weights_from_yaml():
    """Risk score uses weights from config/risk_weights.yaml, not hardcoded."""
```

### AG-06 API

```python
# test_api_routes.py
def test_compare_missing_testing_department_422():
    """POST /compare/v2 without testingDepartment → 422."""

def test_compare_cache_hit_response():
    """Second identical compare request → cacheHit=True in response."""

def test_save_to_db_default_true():
    """Compare request without saveToDb field → defaults to True."""

def test_save_to_db_false_requires_dry_run():
    """saveToDb=False without is_dry_run=True → 422."""

def test_storage_providers_disabled_501():
    """POST /storage-providers with FEATURE_STORAGE_PROVIDERS=false → 501."""

def test_non_pdf_upload_415():
    """Upload .docx file → 415 Unsupported Media Type."""

def test_file_too_large_413():
    """Upload 101 MB file → 413."""

def test_request_id_in_response():
    """Every response includes X-Request-ID header."""

def test_domain_mapping_called_once():
    """department_to_domain() called exactly once at API boundary."""

def test_health_checks_all_deps():
    """GET /health checks postgres, neo4j, qdrant, redis, llm_extract, llm_reason."""
```

### AG-07 UI

```python
# Playwright end-to-end tests
def test_upload_shows_progress_states():
    """Upload flow shows: Uploading → Parsing → Extracting → Ready."""

def test_risk_critical_has_pulse_animation():
    """ComparisonResult with risk=Critical → badge has animate-pulse class."""

def test_citation_tag_renders():
    """KeyDifference with doc1_reference → CitationTag visible in detail panel."""

def test_table_diff_rendered_as_table():
    """KeyDifference with nodeType=table → HTML table rendered, not text block."""

def test_copilot_timeout_message():
    """Mock API delay > 8s → inline timeout message shown (not silent failure)."""

def test_no_external_assets_in_bundle():
    """vite build output contains no URLs matching external domains."""
```

---

## Integration Tests

Location: `tests/integration/`. Require running Docker Compose stack.

```python
def test_full_ingest_pipeline():
    """emc_report_v1.pdf → ParsedDocument in DB, status='parsed', sections > 0."""

def test_full_extraction_pipeline():
    """After ingest, extraction runs → requirements > 0, status='extracted'."""

def test_emc_comparison_e2e():
    """emc_report_v1 vs emc_report_v2 → ComparisonResult.overall_risk = 'Critical'."""

def test_safety_comparison_e2e():
    """iec62368_safety_report comparison → missing evidence flagged."""

def test_environment_comparison_e2e():
    """rohs_declaration comparison → substance change detected in L5 tables."""

def test_standard_revision_e2e():
    """standard_ed3 vs standard_ed4 → 3 modified, 1 added, 1 removed requirement."""

def test_copilot_emc_grounded_response():
    """Copilot 'Is retesting required?' on EMC comparison → answer includes clause ref."""

def test_compare_job_polling_full_flow():
    """POST /compare/v2 → jobId → poll until done=True → result present."""

def test_upload_job_polling_full_flow():
    """POST /upload/v2 → jobId → poll until done=True → document extractable."""

def test_gpu_lock_prevents_oom():
    """Two simultaneous comparisons → second queues behind GPU lock; no OOM."""
```

---

## Performance Tests

Location: `tests/performance/`. Run on target hardware only. Log actual timings.

```python
def test_200_page_parse_under_5min():
    """emc_report_v1.pdf (extended to 200 pages) parsed in < 300 seconds."""

def test_extraction_under_2min():
    """Full extraction pipeline for 200-page doc in < 120 seconds."""

def test_comparison_under_30s():
    """emc_report_v1 vs emc_report_v2, full 5-level comparison in < 30 seconds."""

def test_copilot_query_under_8s():
    """'Is retesting required?' answered in < 8 seconds (14B model warm)."""

def test_evidence_lookup_under_2s():
    """Qdrant hybrid search for a requirement in < 2 seconds."""
```

---

## AI Evaluation Harness

```bash
# Run all department evals:
python tests/eval/run_eval.py --module extract --department EMC
python tests/eval/run_eval.py --module extract --department Safety
python tests/eval/run_eval.py --module extract --department Environment

# Run reasoning eval:
python tests/eval/run_eval.py --module reason --department EMC

# Required minimum scores per department (all must pass to merge):
# Precision ≥ 0.88, Recall ≥ 0.82
```

### Eval Harness Structure

```python
# tests/eval/run_eval.py
def run_eval(module: str, department: str) -> EvalResult:
    """
    Load golden dataset from tests/eval/{module}/{department}/
    Run module against each input
    Compare output to expected
    Compute precision and recall
    Print per-item results and aggregate score
    Return EvalResult with pass/fail
    
    Exit code 0 = pass, 1 = fail
    CI pipeline must check exit code.
    """
```

---

## GPU-Specific Tests

```python
def test_gpu_lock_prevents_concurrent_load():
    """Mock two concurrent LLM calls; second blocks until first releases lock."""

def test_gpu_lock_ttl_auto_releases():
    """Simulate crashed service; lock auto-releases after TTL (600s)."""
    # Use short TTL in test config (e.g. 5s)

def test_full_layer_offload_7b():
    """7B model loaded with --n-gpu-layers 33; no CPU layer fallback."""
    # Check nvidia-smi VRAM usage ≈ 5.5 GB ± 0.5 GB

def test_full_layer_offload_14b():
    """14B model loaded with --n-gpu-layers 43; no CPU layer fallback."""
    # Check nvidia-smi VRAM usage ≈ 9.5 GB ± 0.5 GB

def test_embedding_before_gpu_lock():
    """BGE-M3 embedding completes before acquire_gpu() is called in EXTRACT."""
```

---

## Test Configuration

```python
# tests/conftest.py — shared fixtures

@pytest.fixture(scope="session")
def test_db():
    """Isolated test PostgreSQL schema, torn down after session."""

@pytest.fixture(scope="session")
def test_qdrant():
    """Isolated Qdrant collections with _test suffix, torn down after session."""

@pytest.fixture
def emc_report_v1_path() -> Path:
    return Path("tests/fixtures/sample_reports/emc_report_v1.pdf")

@pytest.fixture
def mock_llm_response():
    """Returns a valid RequirementList JSON for any input. Bypasses GPU lock."""
```

---

## What You Must Never Do

| Prohibited Action | Why |
|---|---|
| Modify production code to make a test pass | Raise it as a bug |
| Write tests that only pass if LLM produces specific text | Non-deterministic |
| Estimate performance — mock timers or fake timing | Measure real wall-clock time |
| Skip a department in eval harness | All 3 departments must pass |
| Use real customer data in fixtures | Anonymise all fixtures |

---

## Output Checklist

- [ ] All 6 fixture PDFs created with correct change sets
- [ ] Unit tests cover every behaviour contract in each agent's SKILL file
- [ ] `test_no_hidden_diffs_count` passes for AG-04 and AG-06
- [ ] `test_size_bytes_is_integer` passes for AG-01 and AG-03
- [ ] `test_save_to_db_default_true` passes for AG-03 and AG-06
- [ ] GPU lock tests verify both acquire and TTL release
- [ ] Integration tests run against live Docker Compose stack
- [ ] Performance tests measure actual wall-clock time on target hardware
- [ ] Eval harness runs per department with exit code check
- [ ] All eval scores: Precision ≥ 0.88, Recall ≥ 0.82 across all departments
