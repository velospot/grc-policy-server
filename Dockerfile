# ── GRC Policy Server — CPU Image ──────────────────────────────────────────
# Runs fully on CPU. Ollama runs as a separate process/container on the host.
# GPU inference (Docling OCR, BGE-M3 embedding) not available in this image.
# Use Dockerfile.gpu for GPU-accelerated builds.
#
# Build:
#   docker build -t grc-policy-server:cpu .
#   docker build --build-arg EXTRAS=table-extraction -t grc-policy-server:cpu .
#
# Optional: bake BGE-M3 model into the image (eliminates first-run download):
#   docker build --build-arg PREFETCH_BGE_M3=1 -t grc-policy-server:cpu .

FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV="/app/.venv" \
    PATH="/app/.venv/bin:/root/.local/bin:$PATH"

# Optional dependency groups (e.g. "table-extraction" for camelot support).
ARG EXTRAS=""

# Set to "1" to download BAAI/bge-m3 (~2.3 GB) during build for offline deployments.
ARG PREFETCH_BGE_M3=""

WORKDIR /app

# ghostscript — PDF rendering for Docling
RUN apt-get update && \
    apt-get install -y --no-install-recommends ghostscript && \
    rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

# Install Python dependencies (separate step for layer caching)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project ${EXTRAS:+--extra $EXTRAS}

# Install application source
COPY src/ src/
RUN uv sync --frozen --no-dev ${EXTRAS:+--extra $EXTRAS}

# Optional: pre-cache BGE-M3 for air-gapped / offline environments.
# Adds ~2.3 GB to image size; model is cached at /app/.cache/huggingface.
RUN if [ -n "$PREFETCH_BGE_M3" ]; then \
        uv run python -c \
          "from sentence_transformers import SentenceTransformer; \
           SentenceTransformer('BAAI/bge-m3', cache_folder='/app/.cache/huggingface')"; \
    fi

# ── Runtime defaults ────────────────────────────────────────────────────────
ENV PORT=8500 \
    HOST=0.0.0.0 \
    UPLOAD_ROOT=/app/data/uploads \
    # HuggingFace/sentence-transformers cache (CPU inference only)
    SENTENCE_TRANSFORMERS_HOME=/app/.cache/huggingface \
    HF_HOME=/app/.cache/huggingface \
    # Comparison engine
    COMPARISON_BACKEND=auto \
    OFFLINE_FALLBACK=true \
    # Docling: CPU mode (no CUDA in this image)
    DOCLING_ACCELERATOR_DEVICE=cpu \
    DOCLING_CUDA_USE_FLASH_ATTENTION2=false \
    # Audit + agent defaults
    AUDIT_LOG_ENABLED=true \
    EXPLANATION_AGENT_ENABLED=true \
    MAX_EXPLANATION_TOKENS=80 \
    EXPLANATION_BATCH_SIZE=10 \
    # Circuit breaker
    CIRCUIT_BREAKER_THRESHOLD=3 \
    CIRCUIT_BREAKER_TIMEOUT_S=60

EXPOSE ${PORT}

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c \
      "import os,urllib.request; \
       urllib.request.urlopen(f\"http://localhost:{os.environ.get('PORT','8500')}/health\")" \
    || exit 1

CMD ["python", "-m", "grc_policy_server.main"]
