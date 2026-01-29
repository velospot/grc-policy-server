#!/usr/bin/env bash
set -e

export PORT="${PORT:-${1:-8000}}"

exec python -m app.main
