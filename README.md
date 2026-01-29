# grc-policy-server

Production‑grade Python service template using **uv**, designed to run consistently across local development, CI, and containerized environments.

This repository provides:

* Modern dependency management with `uv`
* Typed, environment‑driven configuration with sensible defaults
* Docker and Docker Compose support
* A clean FastAPI service skeleton
* Optional, open‑source LLM observability and monitoring

The goal is not cleverness. The goal is *predictability under pressure*.

---

## Requirements

Before you begin, ensure you have:

* Python **3.11+**
* Docker (optional, required for containerized runs)
* Git (recommended)

Verify Python:

```bash
python --version
```

---

## Install `uv`

`uv` replaces `pip`, `virtualenv`, and most waiting.

```bash
pip install --upgrade uv
```

Verify:

```bash
uv --version
```

---

## Project Structure

```
my_uv_project/
├── pyproject.toml        # Dependencies and project metadata
├── uv.lock               # Locked, reproducible dependency graph
├── Dockerfile
├── docker-compose.yml
├── .env.example          # Documented environment variables
├── src/
│   └── my_uv_project/
│       ├── main.py       # Application entrypoint
│       ├── config.py     # Environment-based configuration
│       └── health.py     # Health check endpoint
│       └── logging.py    # logging
├── tests/
│   └── test_health.py
└── README.md
```

---

## Setup (Local Development)

### 1. Clone the repository

```bash
git clone <repo-url>
cd my_uv_project
```

### 2. Install dependencies

```bash
uv sync --dev
```

This will:

* Resolve dependencies
* Create an isolated environment
* Generate `uv.lock`

Commit `uv.lock`. It is the source of truth for production.

---

## Environment Configuration

All configuration is driven by environment variables with defaults.

### 1. Create a local `.env` file

```bash
cp .env.example .env
```

Edit as needed:

```env
APP_NAME=my-uv-project
ENVIRONMENT=development
LOG_LEVEL=info
PORT=8000
DEBUG=true
```

If a variable is not provided, the application falls back to its default value.

---

## Run the Service Locally

```bash
uv run uvicorn my_uv_project.main:app --reload
```

Verify:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status": "ok"}
```

---

## Run Tests

```bash
uv run pytest
```

---

## Containerized Execution

### Build the image

```bash
docker build -t my-uv-project .
```

### Run with environment variables

```bash
docker run \
  -e PORT=9000 \
  -e LOG_LEVEL=debug \
  -p 9000:9000 \
  my-uv-project
```

Defaults apply for any variables not provided.

---

## Docker Compose (Recommended for Teams)

```bash
docker-compose up --build
```

Docker Compose reads from `.env` and ensures local and container runs behave identically.

---

## Optional: LLM Observability & Monitoring

This project supports **optional, open‑source observability** for applications that call LLMs.

You can enable this later without restructuring the app.

### Supported tooling

* **OpenTelemetry** – request tracing and latency
* **Opik (by Comet ML)** – LLM‑specific observability (prompts, outputs, evaluations)

### When to enable observability

Enable observability if you need:

* Prompt and response inspection
* Latency and cost tracking
* Regression detection
* Debugging non‑deterministic LLM behavior

### How to enable (high level)

1. Add the Opik and OpenTelemetry dependencies
2. Run the Opik backend (Docker Compose or Kubernetes)
3. Configure environment variables pointing to the observability backend
4. Wrap LLM calls with Opik tracing decorators

This setup is entirely optional. If you do nothing, the application runs normally.

---

## Configuration Precedence

From strongest to weakest:

1. Runtime environment variables
2. `.env` file
3. Defaults defined in `config.py`

This ensures predictable behavior across environments.

---

## Production Notes

* Stateless by design
* Configuration via environment variables only
* Health endpoint suitable for load balancers and orchestration
* Deterministic dependency resolution via `uv.lock`

This template is intentionally conservative. It is meant to survive growth, audits, and on‑call rotations.

---
