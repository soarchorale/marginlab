#!/usr/bin/env bash
set -euo pipefail
umask 077
PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR"
if [[ ! -x .venv/bin/python || ! -f instance/server.json ]]; then
  echo 'Run scripts/setup-server.sh first.' >&2
  exit 1
fi
exec .venv/bin/python scripts/serve.py
