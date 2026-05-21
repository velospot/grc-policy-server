# LLM Resource Constraints and Deployment Guidance

## llamacpp (CPU deployment)

### Throughput

Approximately 2–5 tokens/sec for a quantized 8B model on a modern desktop CPU.
A comparison with 50 diffs × ~200 token LLM calls = ~10 000 tokens → **30–80 minutes** without batching.
With semantic batch extraction (all clauses in one call), actual LLM invocations per comparison
are ~5–8 calls; total wall time: **5–15 minutes** depending on document size.

### Parallelism

llamacpp server handles one request at a time by default.
Set `CELERY_WORKER_CONCURRENCY=1` to avoid request pile-up.

### Two server instances required

- Chat server: granite4, e.g. port 8080, model alias `local`
- Embedding server: qwen3, e.g. port 8081, model alias `local`

Both must be running before ingestion or comparison.

### Memory

- Q4_K_M quantization of 8B model ≈ 5 GB RAM
- Qwen3-0.6B embedding model ≈ 0.5 GB RAM
- Total: ~6 GB RAM for both servers

### Context window

Quantized Granite 4 8B ≈ 8K–32K tokens depending on build.
`SEMANTIC_EXTRACTION_BATCH_SIZE=8` (default) keeps individual LLM calls well under 4K tokens.

### Environment variables

```ini
LLM_PRIMARY_PROVIDER=llamacpp
LLAMACPP_CHAT_URL=http://192.168.x.x:8080
LLAMACPP_EMBED_URL=http://192.168.x.x:8081
LLAMACPP_CHAT_MODEL=local
LLAMACPP_EMBED_MODEL=local
# Switch back to Ollama: LLM_PRIMARY_PROVIDER=ollama
# Switch back to vLLM:   LLM_PRIMARY_PROVIDER=vllm
```

---

## Celery configuration for llamacpp

```ini
CELERY_WORKER_CONCURRENCY=1        # one task at a time; llamacpp is single-threaded
CELERY_TASK_SOFT_TIME_LIMIT=1800   # 30 min for large documents
CELERY_TASK_HARD_TIME_LIMIT=2100   # 35 min
```

---

## Weaviate

- HNSW index: 1–4 GB RAM per collection depending on vector count.
- For typical policy collections (2 000–10 000 chunks), plan for 2 GB.
- `weaviate_embedded=True` is only for development; use a persistent instance for production.

---

## Comparison pipeline bottlenecks

| Step | Bottleneck | llamacpp single-threaded estimate |
|------|-----------|----------------------------------|
| Semantic enrichment | ~25 LLM calls for 200 clauses (batch size 8) | 5–10 min |
| Markdown diff generation | 1 LLM call per diff, semaphore=4 | ~2.5 min for 50 diffs |
| Vector search (Weaviate) | Fast; adds latency only when section matching fails | <1 min |
| O(n²) move detection | Negligible for <500 nodes | <5 sec |

---

## Provider switching

All three providers share the same `VllmClient` code path (OpenAI-compatible API).
Only the environment variables need to change:

| Provider | `LLM_PRIMARY_PROVIDER` | URL variable | Model variable |
|----------|------------------------|--------------|----------------|
| vLLM | `vllm` | `VLLM_CHAT_URL` | `VLLM_CHAT_MODEL` |
| llamacpp | `llamacpp` | `LLAMACPP_CHAT_URL` | `LLAMACPP_CHAT_MODEL` |
| OpenAI-compatible | `openai_compatible` | `OPENAI_CHAT_URL` | `OPENAI_CHAT_MODEL` |
| Ollama | `ollama` | `OLLAMA_URL` | `OLLAMA_CHAT_MODEL` |

Setting `LLM_PRIMARY_PROVIDER=ollama` bypasses the OpenAI-compatible path entirely.
