#!/usr/bin/env bash
set -euo pipefail
.venv/bin/python test/deployment/prepare.py --output test/results/ci/compose.env
trap 'docker compose --env-file test/results/ci/compose.env -p newai-ci -f compose.yaml -f test/config/compose.deployment.yaml down' EXIT
docker compose --env-file test/results/ci/compose.env -p newai-ci -f compose.yaml -f test/config/compose.deployment.yaml up -d --build --wait --wait-timeout 180
.venv/bin/python test/deployment/smoke.py --output test/results/ci/compose-smoke.json
