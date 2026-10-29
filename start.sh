#!/usr/bin/env bash
# Linux / macOS / WSL launcher for the web UI.
set -e
cd "$(dirname "$0")"

export VLLM_URL="${VLLM_URL:-http://localhost:8000}"
export WEBUI_PORT="${WEBUI_PORT:-8090}"
export VLLM_MODEL="${VLLM_MODEL:-}"

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtual environment..."
  python3 -m venv .venv
fi
if ! cmp -s requirements.txt .venv/installed.txt; then
  echo "Installing packages..."
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
  cp requirements.txt .venv/installed.txt
fi
exec .venv/bin/python app.py
