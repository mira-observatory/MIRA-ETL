#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .

if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "Created $project_dir/.env from .env.example"
else
    echo ".env already exists; leaving it unchanged"
fi

echo "MIRA ETL installed in $project_dir/.venv"
