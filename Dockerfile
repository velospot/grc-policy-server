FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    VIRTUAL_ENV="/app/.venv" \
    PATH="/app/.venv/bin:/root/.local/bin:$PATH"

# Optional extras to install at build time (e.g. table-extraction).
# Usage: docker build --build-arg EXTRAS=table-extraction .
ARG EXTRAS=""

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ghostscript && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir uv

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project ${EXTRAS:+--extra $EXTRAS}

COPY src/ src/
RUN uv sync --frozen --no-dev ${EXTRAS:+--extra $EXTRAS}

EXPOSE 8500

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f\"http://localhost:{os.environ.get('PORT','8500')}/health\")" || exit 1

CMD ["python", "-m", "grc_policy_server.main"]
