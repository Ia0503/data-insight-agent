#!/usr/bin/env bash
set -euo pipefail
mkdir -p test/results/ci
.venv/bin/python -m ruff check backend deploy test --config test/config/ruff.toml
.venv/bin/python -m ruff format --check backend deploy test --config test/config/ruff.toml
frontend/node_modules/.bin/prettier --check frontend/src frontend/vite.config.ts frontend/tsconfig.json README.md deploy/README.md test/README.md compose.yaml .github/workflows/checks.yaml test/e2e/call-protection.spec.ts test/e2e/step4.spec.ts test/deployment/*.mjs
.venv/bin/python -m pytest -c test/pytest.ini --basetemp=test/results/ci/pytest-temp --junitxml=test/results/ci/pytest.xml
npm --prefix frontend run build
node test/node_modules/@playwright/test/cli.js test --config=test/config/playwright.config.ts
