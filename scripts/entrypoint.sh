#!/usr/bin/env bash
set -e

export PORT="${PORT:-${1:-8500}}"

exec python -m grc_policy_server.main
