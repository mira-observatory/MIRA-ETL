#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -x .venv/bin/python ]]; then
    echo "Missing .venv. Run scripts/install.sh first." >&2
    exit 1
fi

.venv/bin/python -m pytest tests -q
