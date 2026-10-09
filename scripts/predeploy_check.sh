#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="${PYTHON:-python3}"
ENV_FILE="${ENV_FILE:-.env.production}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

GRADEBOOK_ENV=production STRICT_PRODUCTION_CHECKS=1 "${PYTHON_BIN}" scripts/doctor.py
bash scripts/security_check.sh

"${PYTHON_BIN}" -m py_compile migrations/versions/*.py
ALEMBIC_HEADS="$("${PYTHON_BIN}" -m alembic heads)"
if [[ "$(grep -c '(head)' <<<"${ALEMBIC_HEADS}")" -ne 1 ]]; then
  echo "Expected exactly one Alembic head"
  echo "${ALEMBIC_HEADS}"
  exit 1
fi

echo "Predeploy checks passed"
echo "${ALEMBIC_HEADS}"
