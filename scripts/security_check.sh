#!/usr/bin/env bash
set -euo pipefail

export SECRET_KEY="${SECRET_KEY:-test-secret-for-local-security-scan-only}"
export ENCRYPTION_KEY="${ENCRYPTION_KEY:-ICkoftk-wbOx89vzo2nuGkPatHZCQ1IKBVpFdRJ1F4k=}"
# HTTP security tests use Starlette's fixed TestClient hostname. Do not let a
# production TRUSTED_HOSTS value turn every test request into an unrelated 400.
export TRUSTED_HOSTS="${SECURITY_TEST_TRUSTED_HOSTS:-testserver}"
PYTHON_BIN="${PYTHON:-python3}"
USER_BIN="$("${PYTHON_BIN}" -c 'import site; print(site.USER_BASE + "/bin")')"
export PATH="${USER_BIN}:${PATH}"

find_tool() {
  local name="$1"
  if command -v "${name}" >/dev/null 2>&1; then
    command -v "${name}"
  elif [[ -x "${USER_BIN}/${name}" ]]; then
    printf '%s\n' "${USER_BIN}/${name}"
  else
    echo "Required security tool is not installed: ${name}" >&2
    return 1
  fi
}

PIP_AUDIT_BIN="$(find_tool pip-audit)"
SEMGREP_BIN="$(find_tool semgrep)"
BANDIT_BIN="$(find_tool bandit)"

"${PYTHON_BIN}" -m compileall app scripts/doctor.py migrations
"${PYTHON_BIN}" -m unittest test_security_access.py test_http_security.py test_file_upload_security.py test_health.py -v

"${PIP_AUDIT_BIN}" -r requirements.txt
"${SEMGREP_BIN}" --no-git-ignore --config p/security-audit --config p/python app
"${BANDIT_BIN}" -q -r app -x app/models/advice_model.pkl,app/models/advice_scaler.pkl -s B608 --severity-level medium --confidence-level medium

if rg -n --glob '!scripts/security_check.sh' --glob '!app/core/security.py' \
  'admin/admin|12345678|rootpassword|gradebook_password|attachment/file\?token|ws\?token|token=\$token' \
  app scripts docker-compose*.yml .env.example .env.production.example requirements.txt; then
  echo "Forbidden insecure pattern found"
  exit 1
fi
