#!/usr/bin/env bash
# Produce an exportable source archive of the Vigline project.
# Excludes: .git, __pycache__, virtualenvs, local databases, test caches.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${1:-$ROOT/vigline-source-$(date +%Y%m%d-%H%M%S).tar.gz}"

tar -czf "$OUT" \
  --exclude='./.git' \
  --exclude='./.venv' \
  --exclude='./venv' \
  --exclude='*/__pycache__' \
  --exclude='*.egg-info' \
  --exclude='./data/*.db' \
  --exclude='./data/*.db-journal' \
  --exclude='./.pytest_cache' \
  --exclude='./dist' \
  --exclude='./build' \
  -C "$ROOT" .

echo "wrote $OUT"
