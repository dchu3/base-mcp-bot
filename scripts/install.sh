#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

if [[ -n "${PYTHON_BIN:-}" ]]; then
  :
elif command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "Unable to find python interpreter. Set PYTHON_BIN to the path of python3." >&2
  exit 1
fi

cd "$ROOT_DIR"

if [[ ! -d ".venv" ]]; then
  echo "Creating virtual environment at .venv using ${PYTHON_BIN}"
  "$PYTHON_BIN" -m venv .venv
fi

source .venv/bin/activate

echo "Upgrading pip"
pip install --upgrade pip

echo "Installing project dependencies"
pip install --no-cache-dir -r requirements.txt

if [[ ! -f ".env" && -f ".env.example" ]]; then
  cp .env.example .env
  echo "Created .env from .env.example. Update credentials before running the bot."
fi

echo "Setup complete. Activate the environment with 'source .venv/bin/activate'."
