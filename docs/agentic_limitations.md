# Agentic Orchestration — Local Ollama Limitations

This document describes the practical constraints of running the GRC comparison
agent pipeline against a local Ollama instance (e.g., `granite3.3:8b` on an
RTX 5070 Ti 16GB). It is intended for developers tuning the system for
on-premise deployments.

---

## Hardware Reference Configuration

| Component | Spec | Notes |
|-----------|------|-------|
| GPU | NVIDIA RTX 5070 Ti 16GB VRAM | Primary inference device |
| System RAM | 64GB | Node graph + Qdrant/Weaviate cache |
| Chat model | `granite3.3:8b` | ~8GB VRAM when loaded |
| Embed model | `qwen3-embedding:0.6b` | ~0.6GB VRAM |
| BGE-M3 (local) | `BAAI/bge-m3` | ~4GB VRAM — conflicts with 8B chat model |

---

## Limitation Table

| # | Limitation | Impact | Mitigation |
|---|-----------|--------|-----------|
| L1 | **No true token streaming** | `OllamaClient` calls `/api/generate` with `stream: False` — full response buffered, then yielded as a single SSE token. Users see the diff explanation appear all at once. | Override `generate_diff_table_row_stream()` to use `stream: true` NDJSON streaming (Phase 5). |
| L2 | **Sequential per-diff processing** | `RealDiffEngineStream` loops over diffs sequentially. On Ollama, concurrent inference requests serialize anyway — no benefit from parallelism. Total latency = N_diffs × per_diff_latency. | Accept sequential execution. Set `LLM_STREAM_INTER_DIFF_DELAY_MS=0`. |
| L3 | **Effective concurrency = 1** | Ollama serves one inference request at a time on single GPU. `asyncio.Semaphore(5)` in `OntologyClassifier` serializes to Semaphore(1) in practice. | Use `Semaphore(1)` explicitly for Ollama-targeting agents to avoid queuing overhead. |
| L4 | **Context window budget** | Target: ≤ 8k tokens per orchestrator call (AGENTS.md). 40 MODIFIED diffs × ~200 tokens/diff = 8k tokens input alone, before system prompt. For large documents with 80+ diffs this budget is exceeded. | Batch diffs in groups of `EXPLANATION_BATCH_SIZE=10`. Generate one summary per batch, then aggregate. |
| L5 | **Per-diff generation speed** | `granite3.3:8b` generates ~20–50 tokens/sec on RTX 5070 Ti. Per-diff explanation capped at `MAX_EXPLANATION_TOKENS=80` tokens ≈ 1.6–4 seconds per diff. 25 diffs ≈ 40–100 seconds total. | Cap `MAX_EXPLANATION_TOKENS=80`. Show SSE `progress` events every 5 diffs so the client doesn't appear frozen. |
| L6 | **VRAM contention** | `BGE-M3` (4GB) + `granite3.3:8b` (8GB) = 12GB on 16GB GPU. Running both simultaneously leaves only 4GB headroom (insufficient for flash attention). Ollama swaps to CPU if both are loaded. | Use `qwen3-embedding:0.6b` (0.6GB) instead of BGE-M3 for Ollama deployments. Or unload chat model before embedding pass. |
| L7 | **JSON output reliability** | Local 8B models produce malformed JSON more frequently than cloud 70B+ models, especially under temperature=0 with complex schemas (ontology classification, evidence extraction). | Retry up to 3× on JSON parse failure. Default to safe fallback values (`type="Section"`, `has_meaningful_change=False`). Validate against Pydantic model before downstream use. |
| L8 | **Multilingual quality** | `granite3.3:8b` quality degrades for German/French compliance obligation detection vs English. False negatives on `darf nicht` (DE) or `ne doit pas` (FR) obligation patterns. | Rule-based `ObligationPatternRegistry` handles multilingual obligation detection deterministically. LLM is used for narrative explanation only, not for obligation decisions. |
| L9 | **Large document throughput** | A 500-page compliance document produces ~300 nodes → ~80 MODIFIED diffs → ~4–7 minutes total explanation time on Ollama. | Use `/v2/compare` (async Celery) for large documents. Reserve `/v4/compare/stream` for interactive comparison of targeted sections (<50 diffs). |
| L10 | **Model unloading delay** | Ollama keeps the model loaded for 5 minutes after last request. Switching models (e.g., chat → embed) causes a 10–30s reload penalty. | Run chat-heavy workloads (comparisons) and embed-heavy workloads (document ingestion) in separate time windows. |

---

## Recommended ENV Configuration (Local Ollama)

```bash
# Comparison backend
COMPARISON_BACKEND=offline          # PostgreSQL + files + local Ollama
OFFLINE_FALLBACK=true               # degrade to zero-LLM if Ollama unreachable

# Local Ollama models
OLLAMA_CHAT_MODEL=granite3.3:8b
OLLAMA_EMBED_MODEL=qwen3-embedding:0.6b   # low VRAM, fast

# Ollama timeouts (generous for local hardware)
OLLAMA_TIMEOUT_SEC=600              # 10 min read timeout
OLLAMA_CONNECT_TIMEOUT_SEC=10

# Agent token budgets
MAX_EXPLANATION_TOKENS=80           # cap per-diff output
EXPLANATION_BATCH_SIZE=10           # diffs per summary batch

# Streaming
LLM_STREAM_INTER_DIFF_DELAY_MS=0    # no artificial delay

# Ontology classification (uses Ollama via HTTP)
ONTOLOGY_CLASSIFICATION_ENABLED=true
ONTOLOGY_CLASSIFIER_URL=http://localhost:11434
ONTOLOGY_CLASSIFIER_MODEL=granite3.3:8b
ONTOLOGY_CONFIDENCE_THRESHOLD=0.70

# Audit log
AUDIT_LOG_ENABLED=true

# Evidence extraction (opt-in — adds 1 LLM call per MODIFIED pair)
EVIDENCE_EXTRACTION_ENABLED=false
```

---

## Expected Latency by Document Size

| Document Size | Nodes | MODIFIED diffs | Explanation time (granite3.3:8b) |
|--------------|-------|----------------|----------------------------------|
| Small (20 pages) | ~30 | ~8 | ~15–30 seconds |
| Medium (60 pages) | ~90 | ~20 | ~40–80 seconds |
| Large (150 pages) | ~200 | ~50 | ~100–200 seconds |
| Very large (500 pages) | ~400 | ~100 | ~4–7 minutes (use /v2/compare) |

---

## What the LLM Is (and Is Not) Responsible For

| Responsibility | Owner | Local Ollama impact |
|---------------|-------|---------------------|
| Severity classification | `SeverityClassifier` (deterministic rules) | Zero impact — no LLM |
| Obligation change detection | `ObligationPatternRegistry` (regex) | Zero impact — no LLM |
| Numeric change detection | `detect_numeric_changes()` (regex) | Zero impact — no LLM |
| Table cell diffing | `TableDiffEngine` (deterministic) | Zero impact — no LLM |
| Node-pair semantic alignment | `ClauseMatcher` (text similarity) | Zero impact — no LLM |
| Per-diff narrative explanation | `ExplanationAgent` (LLM) | ~1–4 sec/diff |
| Executive summary | `OllamaClient.summarize_changes()` | ~5–15 sec total |
| Follow-up questions | `OllamaClient.generate_followups()` | ~3–8 sec total |
| Ontology classification | `OntologyClassifier` (LLM, opt-in) | ~1–3 sec/chunk during ingestion |

**Key insight:** Compliance PASS/FAIL decisions are ALWAYS made by the deterministic
rules engine. The local LLM is only responsible for human-readable narrative
explanations. A weak local model degrades explanation quality, not compliance accuracy.

---

## Failure Modes and Recovery

| Failure | Behaviour | Recovery |
|---------|-----------|----------|
| Ollama unreachable | `OfflineDiffEngine` returns deterministic summary (no LLM) | Set `OFFLINE_FALLBACK=true` |
| Ollama timeout mid-stream | SSE stream emits current tokens, then `error` event | Client reconnects; use `/v2/compare` for retry |
| JSON parse failure (ontology) | Node classified as `Section` with `review_flag=True` | Appears in `GET /documents/{id}/review-queue` |
| OOM / VRAM overflow | Ollama returns HTTP 500 | Reduce `MAX_EXPLANATION_TOKENS`; unload other models |
| Context window overflow | LLM truncates or refuses | Use `EXPLANATION_BATCH_SIZE=5` to reduce input size |
