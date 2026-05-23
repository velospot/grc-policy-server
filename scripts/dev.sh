#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"
mkdir -p "${PROJECT_ROOT}/data/uploads"

DEFAULT_ENV_FILE="${PROJECT_ROOT}/.env.local"
read -r -p "Path to .env file [${DEFAULT_ENV_FILE}]: " ENV_FILE_INPUT

if [[ -z "${ENV_FILE_INPUT}" ]]; then
  ENV_FILE="${DEFAULT_ENV_FILE}"
else
  if [[ "${ENV_FILE_INPUT}" == ~* ]]; then
    ENV_FILE_INPUT="${ENV_FILE_INPUT/#\~/${HOME}}"
  fi
  if [[ "${ENV_FILE_INPUT}" == /* ]]; then
    ENV_FILE="${ENV_FILE_INPUT}"
  else
    ENV_FILE="${PROJECT_ROOT}/${ENV_FILE_INPUT}"
  fi
fi

if [[ ! -f "${ENV_FILE}" ]]; then
  echo "Error: .env file not found at '${ENV_FILE}'"
  exit 1
fi

UV_ENV_ARGS=(--env-file "${ENV_FILE}")

uv sync

detect_docling_device() {
  if [[ -n "${DOCLING_ACCELERATOR_DEVICE:-}" ]]; then
    echo "${DOCLING_ACCELERATOR_DEVICE}"
    return
  fi

  if command -v nvidia-smi >/dev/null 2>&1; then
    if nvidia-smi -L >/dev/null 2>&1; then
      echo "cuda"
      return
    fi
  fi

  if [[ "$(uname -s)" == "Darwin" ]]; then
    if uv run python - <<'PY' >/dev/null 2>&1
import importlib
torch = importlib.import_module("torch")
raise SystemExit(0 if bool(getattr(torch.backends, "mps", None) and torch.backends.mps.is_available()) else 1)
PY
    then
      echo "mps"
      return
    fi
  fi

  echo "auto"
}

if [[ -z "${CELERY_WORKER_POOL:-}" ]]; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    CELERY_WORKER_POOL="solo"
  else
    CELERY_WORKER_POOL="prefork"
  fi
fi

if [[ -z "${CELERY_WORKER_CONCURRENCY:-}" ]]; then
  if [[ "${CELERY_WORKER_POOL}" == "solo" ]]; then
    CELERY_WORKER_CONCURRENCY=1
  else
    CPU_COUNT="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)"
    if [[ "${CPU_COUNT}" -lt 2 ]]; then
      CELERY_WORKER_CONCURRENCY=1
    elif [[ "${CPU_COUNT}" -lt 8 ]]; then
      CELERY_WORKER_CONCURRENCY=2
    else
      CELERY_WORKER_CONCURRENCY=4
    fi
  fi
fi

DOCLING_ACCELERATOR_DEVICE="$(detect_docling_device)"
export CELERY_WORKER_POOL CELERY_WORKER_CONCURRENCY DOCLING_ACCELERATOR_DEVICE

if [[ "${DOCLING_ACCELERATOR_DEVICE}" == "mps" ]]; then
  export PYTORCH_ENABLE_MPS_FALLBACK=1
fi

echo "Starting API + Celery"
echo "Env file=${ENV_FILE}"
echo "Celery pool=${CELERY_WORKER_POOL} concurrency=${CELERY_WORKER_CONCURRENCY}"
echo "Docling accelerator=${DOCLING_ACCELERATOR_DEVICE}"

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "${API_PID:-}" ]] && kill -0 "${API_PID}" 2>/dev/null; then
    kill "${API_PID}" 2>/dev/null || true
  fi
  if [[ -n "${CELERY_PID:-}" ]] && kill -0 "${CELERY_PID}" 2>/dev/null; then
    kill "${CELERY_PID}" 2>/dev/null || true
  fi
  wait >/dev/null 2>&1 || true
  exit "${exit_code}"
}

trap cleanup EXIT INT TERM

uv run "${UV_ENV_ARGS[@]}" celery -A grc_policy_server.worker:celery_app worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" \
  --concurrency="${CELERY_WORKER_CONCURRENCY}" \
  --pool="${CELERY_WORKER_POOL}" &
CELERY_PID=$!

uv run "${UV_ENV_ARGS[@]}" uvicorn grc_policy_server.main:app \
  --reload \
  --host "${HOST:-0.0.0.0}" \
  --port "${PORT:-8500}" \
  --log-level "${LOG_LEVEL:-info}" &
API_PID=$!

wait_for_first_exit() {
  while true; do
    if ! kill -0 "${CELERY_PID}" 2>/dev/null; then
      wait "${CELERY_PID}"
      return $?
    fi
    if ! kill -0 "${API_PID}" 2>/dev/null; then
      wait "${API_PID}"
      return $?
    fi
    sleep 1
  done
}

wait_for_first_exit
