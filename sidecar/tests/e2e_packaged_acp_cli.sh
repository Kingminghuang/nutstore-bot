#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SIDECAR_ROOT}/.." && pwd)"

DEFAULT_BIN_PATH="${SIDECAR_ROOT}/dist/nsbot"
DEFAULT_ENV_FILE="${REPO_ROOT}/.env"
HARNESS_DIR="${SIDECAR_ROOT}/tests/acp_ts_client"

BIN_PATH="${NSBOT_PACKAGED_CLI_BIN:-${DEFAULT_BIN_PATH}}"
ENV_FILE="${NSBOT_PACKAGED_ACP_E2E_ENV_FILE:-${DEFAULT_ENV_FILE}}"
PYTHON_BIN="${NSBOT_E2E_PYTHON_BIN:-${SIDECAR_ROOT}/.venv/bin/python}"
TMP_DIR="$(mktemp -d /tmp/nsbot-packaged-acp-cli-e2e.XXXXXX)"
ARTIFACT_DIR="${SIDECAR_ROOT}/build/e2e-packaged-acp-cli/$(date +%Y%m%d-%H%M%S)-$$"

NS_BOT_HOME="${TMP_DIR}/nsbot-home"
WORKSPACE_DIR="${TMP_DIR}/workspace"
CREATE_OUT="${ARTIFACT_DIR}/models-create.json"
SET_DEFAULT_OUT="${ARTIFACT_DIR}/models-set-default.json"
VALIDATOR_OUT="${ARTIFACT_DIR}/validator-summary.json"
ACP_WIRE_LOG="${ARTIFACT_DIR}/acp-wire.ndjson"
ACP_STDERR_LOG="${ARTIFACT_DIR}/acp-stderr.log"

PROVIDER_ID=""

cleanup() {
  if [[ "${NSBOT_E2E_KEEP_TMP:-0}" == "1" ]]; then
    echo "[INFO] Keeping temporary workspace at ${TMP_DIR}"
    return
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

pass() { echo "[PASS] $*"; }
fail() { echo "[FAIL] $*" >&2; exit 1; }

run_cmd() {
  echo "+ $*"
  "$@"
}

run_capture() {
  local out_file="$1"
  local display_cmd="$2"
  shift 2
  echo "+ ${display_cmd}"
  "$@" 2>&1 | tee "${out_file}"
}

require_file() {
  local path="$1"
  [[ -e "${path}" ]] || fail "required path missing: ${path}"
}

load_env_file() {
  [[ -f "${ENV_FILE}" ]] || fail "env file not found: ${ENV_FILE}"
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
}

validate_env() {
  [[ "${MODEL_ID:-}" == "openai/gpt-5.4" ]] || fail "MODEL_ID must be openai/gpt-5.4"
  [[ -n "${MODEL_BASE_URL:-}" ]] || fail "MODEL_BASE_URL is required"
  [[ -n "${MODEL_API_KEY:-}" ]] || fail "MODEL_API_KEY is required"
}

build_packaged_cli() {
  if [[ -x "${SIDECAR_ROOT}/scripts/build_packaged_cli.sh" ]]; then
    run_cmd bash "${SIDECAR_ROOT}/scripts/build_packaged_cli.sh"
  else
    fail "packaged CLI binary not found at ${BIN_PATH}"
  fi

  [[ -x "${BIN_PATH}" ]] || fail "packaged CLI binary is still missing after build: ${BIN_PATH}"
}

assert_json_path_equals() {
  local file_path="$1"
  local dotted_path="$2"
  local expected="$3"
  PYTHONPATH="${SIDECAR_ROOT}/src" "${PYTHON_BIN}" - "${file_path}" "${dotted_path}" "${expected}" <<'PY'
from __future__ import annotations

import json
import sys

payload_path = sys.argv[1]
dotted_path = sys.argv[2]
expected = sys.argv[3]

with open(payload_path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)

current = payload
for part in dotted_path.split("."):
    if not isinstance(current, dict) or part not in current:
        raise SystemExit(f"missing JSON path: {dotted_path}")
    current = current[part]

if str(current) != expected:
    raise SystemExit(
        f"unexpected JSON value at {dotted_path}: expected={expected!r} actual={current!r}"
    )
PY
}

json_get() {
  local file_path="$1"
  local dotted_path="$2"
  PYTHONPATH="${SIDECAR_ROOT}/src" "${PYTHON_BIN}" - "${file_path}" "${dotted_path}" <<'PY'
from __future__ import annotations

import json
import sys

payload_path = sys.argv[1]
dotted_path = sys.argv[2]

with open(payload_path, "r", encoding="utf-8") as handle:
    payload = json.load(handle)

current = payload
for part in dotted_path.split("."):
    if not isinstance(current, dict) or part not in current:
        raise SystemExit(f"missing JSON path: {dotted_path}")
    current = current[part]

print(current)
PY
}

require_command() {
  local command_name="$1"
  command -v "${command_name}" >/dev/null 2>&1 || fail "required command not found: ${command_name}"
}

echo "REPO_ROOT=${REPO_ROOT}"
echo "SIDECAR_ROOT=${SIDECAR_ROOT}"
echo "BIN_PATH=${BIN_PATH}"
echo "ENV_FILE=${ENV_FILE}"
echo "ARTIFACT_DIR=${ARTIFACT_DIR}"

mkdir -p "${ARTIFACT_DIR}" "${WORKSPACE_DIR}" "${NS_BOT_HOME}"
require_file "${PYTHON_BIN}"
require_command npm

load_env_file
validate_env
build_packaged_cli
pass "using packaged CLI at ${BIN_PATH}"

run_capture \
  "${CREATE_OUT}" \
  "${BIN_PATH} --ns-bot-home ${NS_BOT_HOME} models create --base-url ${MODEL_BASE_URL} --model-id ${MODEL_ID} --api-key [REDACTED]" \
  "${BIN_PATH}" \
  --ns-bot-home "${NS_BOT_HOME}" \
  models create \
  --base-url "${MODEL_BASE_URL}" \
  --model-id "${MODEL_ID}" \
  --api-key "${MODEL_API_KEY}"
assert_json_path_equals "${CREATE_OUT}" "preferredModelId" "${MODEL_ID}"
PROVIDER_ID="$(json_get "${CREATE_OUT}" "id")"
IDENTITY="$({ json_get "${CREATE_OUT}" "identity" 2>/dev/null || true; })"
if [[ -z "${IDENTITY}" ]]; then
  IDENTITY="${PROVIDER_ID}:${MODEL_ID}"
fi
[[ -n "${IDENTITY}" ]] || fail "failed to parse identity from models create output"
[[ -n "${PROVIDER_ID}" ]] || fail "failed to parse provider id from models create output"
pass "models create via packaged CLI"

run_capture \
  "${SET_DEFAULT_OUT}" \
  "${BIN_PATH} --ns-bot-home ${NS_BOT_HOME} models set-default ${IDENTITY}" \
  "${BIN_PATH}" \
  --ns-bot-home "${NS_BOT_HOME}" \
  models set-default \
  "${IDENTITY}"
assert_json_path_equals "${SET_DEFAULT_OUT}" "action" "set-default"
assert_json_path_equals "${SET_DEFAULT_OUT}" "providerId" "${PROVIDER_ID}"
assert_json_path_equals "${SET_DEFAULT_OUT}" "modelId" "${MODEL_ID}"
pass "models set-default via packaged CLI"

run_cmd npm install --prefix "${HARNESS_DIR}" --no-fund --no-audit --package-lock=false
require_file "${HARNESS_DIR}/node_modules/.bin/tsx"

run_capture \
  "${VALIDATOR_OUT}" \
  "${HARNESS_DIR}/node_modules/.bin/tsx ${HARNESS_DIR}/src/validate-packaged-cli.ts --bin-path ${BIN_PATH} --ns-bot-home ${NS_BOT_HOME} --workspace ${WORKSPACE_DIR} --provider-id ${PROVIDER_ID} --model-id ${MODEL_ID} --base-url ${MODEL_BASE_URL} --wire-log-file ${ACP_WIRE_LOG} --stderr-log-file ${ACP_STDERR_LOG}" \
  "${HARNESS_DIR}/node_modules/.bin/tsx" \
  "${HARNESS_DIR}/src/validate-packaged-cli.ts" \
  --bin-path "${BIN_PATH}" \
  --ns-bot-home "${NS_BOT_HOME}" \
  --workspace "${WORKSPACE_DIR}" \
  --provider-id "${PROVIDER_ID}" \
  --model-id "${MODEL_ID}" \
  --base-url "${MODEL_BASE_URL}" \
  --wire-log-file "${ACP_WIRE_LOG}" \
  --stderr-log-file "${ACP_STDERR_LOG}"

assert_json_path_equals "${VALIDATOR_OUT}" "protocolVersion" "1"
assert_json_path_equals "${VALIDATOR_OUT}" "defaultSelection.providerId" "${PROVIDER_ID}"
assert_json_path_equals "${VALIDATOR_OUT}" "defaultSelection.modelId" "${MODEL_ID}"

[[ -s "${ACP_WIRE_LOG}" ]] || fail "missing ACP wire log: ${ACP_WIRE_LOG}"
[[ -f "${ACP_STDERR_LOG}" ]] || fail "missing ACP stderr log: ${ACP_STDERR_LOG}"

"${PYTHON_BIN}" - "${ACP_WIRE_LOG}" <<'PY'
from __future__ import annotations

import json
import sys

log_path = sys.argv[1]
entries = []
with open(log_path, "r", encoding="utf-8") as handle:
    for line in handle:
        line = line.strip()
        if not line:
            continue
        entries.append(json.loads(line))

required_requests = {"initialize", "authenticate", "session/new", "session/load", "session/prompt", "_nsbot/provider/catalog", "_nsbot/provider/model_options", "_nsbot/timeline/list"}
seen_requests = {str(entry.get("method") or "") for entry in entries if entry.get("direction") == "client->agent"}
missing = sorted(required_requests - seen_requests)
if missing:
    raise SystemExit(f"missing ACP requests in wire log: {', '.join(missing)}")

if not any(str(entry.get("method") or "") == "session/update" for entry in entries if entry.get("direction") == "agent->client"):
    raise SystemExit("wire log did not capture any session/update notifications")
PY

pass "ACP official TS SDK validation"
echo "Artifacts written to ${ARTIFACT_DIR}"
echo "Packaged ACP CLI E2E passed."