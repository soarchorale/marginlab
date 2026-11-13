#!/usr/bin/env bash
set -euo pipefail
umask 077
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
PYTHON_BIN="${PYTHON_BIN:-python3.11}"
if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
  echo "Python 3.11 was not found. Install Python 3.11 and its venv module, or set PYTHON_BIN to its absolute path." >&2
  exit 1
fi
"$PYTHON_BIN" -c 'import sys; sys.version_info[:2] == (3,11) or sys.exit("Python 3.11 is required; found " + sys.version)'
if [[ -e .venv ]]; then
  if [[ ! -x .venv/bin/python ]] || ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3,11) and sys.prefix != sys.base_prefix else 1)'; then
    echo 'The existing .venv is incompatible. Stop the service, rename .venv to preserve it, then rerun setup.' >&2
    exit 1
  fi
fi
"$PYTHON_BIN" -m venv .venv
.venv/bin/python --version
.venv/bin/python -m ensurepip --upgrade
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip --version
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python scripts/configure_server.py
