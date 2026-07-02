#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="${ARCHE_PROJECT_ROOT:-$(cd "${SCRIPT_DIR}/../.." && pwd)}"
export ARCHE_PROJECT_ROOT="${PROJECT_ROOT}"
export PYTHONPATH="${PROJECT_ROOT}/src:${PYTHONPATH:-}"

python "${PROJECT_ROOT}/tests/scripts/run_controller_integration_test.py" \
  --mode bounded \
  --question "Identify a plausible transition-state search workflow for a simple nucleophilic addition reaction and specify how to validate the TS."
