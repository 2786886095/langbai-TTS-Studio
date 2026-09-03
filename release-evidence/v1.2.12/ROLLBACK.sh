#!/usr/bin/env bash
set -euo pipefail

BASE_COMMIT="e6b3b1f201e5fb6d7b238cafeea931df45c8eb6a"
git restore --source "$BASE_COMMIT" -- \
  CHANGELOG.md \
  backend/app/__init__.py \
  backend/app/adapters/subprocess_adapter.py \
  backend/app/main.py \
  backend/engine_worker.py \
  backend/pyproject.toml \
  backend/tests/test_subprocess_environment.py \
  electron/main.cjs \
  frontend/src/App.tsx \
  package-lock.json \
  package.json
rm -f tests/acceptance/test_runtime_resilience.py
echo "ROLLBACK_RESULT=restored BASE_COMMIT=$BASE_COMMIT"
