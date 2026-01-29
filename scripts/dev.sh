#!/usr/bin/env bash
set -e

uv sync
uv run uvicorn grc-policy-server.main:app --reload
