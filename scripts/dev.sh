#!/usr/bin/env bash
set -e

uv sync
uv run uvicorn grc_policy_server.main:app --reload
