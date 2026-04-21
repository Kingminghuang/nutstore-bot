#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIDECAR_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SIDECAR_ROOT}/.." && pwd)"

DEFAULT_BIN_PATH="${SIDECAR_ROOT}/dist/nsbot"
BIN_PATH="${NSBOT_PACKAGED_CLI_BIN:-${DEFAULT_BIN_PATH}}"
PYTHON_BIN="${NSBOT_E2E_PYTHON_BIN:-${SIDECAR_ROOT}/.venv/bin/python}"
TMP_DIR="$(mktemp -d /tmp/nsbot-agent-cli-e2e.XXXXXX)"
NS_BOT_HOME="${TMP_DIR}/nsbot-home"
WORKSPACE_DIR="${TMP_DIR}/workspace"

cleanup() {
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
  shift
  echo "+ $*"
  # Keep stderr visible in terminal, but persist only stdout for JSON parsing.
  "$@" | tee "${out_file}"
}

require_file() {
  local path="$1"
  [[ -e "${path}" ]] || fail "required path missing: ${path}"
}

build_packaged_cli_if_possible() {
  if [[ -x "${BIN_PATH}" ]]; then
    pass "using packaged CLI at ${BIN_PATH}"
    return
  fi

  if [[ -x "${SIDECAR_ROOT}/scripts/build_packaged_cli.sh" ]]; then
    run_cmd bash "${SIDECAR_ROOT}/scripts/build_packaged_cli.sh"
  elif [[ -x "${REPO_ROOT}/scripts/build-packaged-cli.sh" ]]; then
    run_cmd bash "${REPO_ROOT}/scripts/build-packaged-cli.sh"
  else
    fail "packaged CLI binary not found at ${BIN_PATH}; set NSBOT_PACKAGED_CLI_BIN or add a packaged CLI build helper"
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

seed_default_provider() {
  require_file "${PYTHON_BIN}"
  PYTHONPATH="${SIDECAR_ROOT}/src" "${PYTHON_BIN}" - "${NS_BOT_HOME}" <<'PY'
from __future__ import annotations

import sys

from nsbot.infrastructure.repositories import create_repositories
from nsbot.infrastructure.storage import connect_database

ns_bot_home = sys.argv[1]
database = connect_database(ns_bot_home)
repositories = create_repositories(database)
try:
    repositories.providers.save_bundle(
        provider_data={
            "kind": "builtin",
            "runtime_provider": "openai",
            "catalog_provider_id": "openai",
            "display_name": "OpenAI",
            "base_url": None,
            "secret_ref": "sec_test_openai",
            "api_key_configured": True,
            "model_policy": "all_catalog",
            "preferred_model_id": None,
            "is_enabled": True,
        },
        models=[],
    )
finally:
    database.close()
PY
}

echo "REPO_ROOT=${REPO_ROOT}"
echo "SIDECAR_ROOT=${SIDECAR_ROOT}"
echo "BIN_PATH=${BIN_PATH}"
echo "NS_BOT_HOME=${NS_BOT_HOME}"

mkdir -p "${WORKSPACE_DIR}"
build_packaged_cli_if_possible

run_cmd "${BIN_PATH}" --help
run_cmd "${BIN_PATH}" agent --help
run_cmd "${BIN_PATH}" agent run --help
run_cmd "${BIN_PATH}" providers --help
run_cmd "${BIN_PATH}" workspaces --help
run_cmd "${BIN_PATH}" threads --help
pass "help commands"

WORKSPACE_CREATE_OUT="${TMP_DIR}/workspace-create.json"
run_capture \
  "${WORKSPACE_CREATE_OUT}" \
  "${BIN_PATH}" \
  --ns-bot-home "${NS_BOT_HOME}" \
  workspaces create \
  --name "Agent CLI E2E" \
  --real-path "${WORKSPACE_DIR}" \
  --path-label "${WORKSPACE_DIR}"

WORKSPACE_ID="$(${PYTHON_BIN} - "${WORKSPACE_CREATE_OUT}" <<'PY'
from __future__ import annotations

import json
import sys

with open(sys.argv[1], "r", encoding="utf-8") as handle:
    payload = json.load(handle)
print(payload.get("id") or payload.get("workspaceId") or "")
PY
)"
[[ -n "${WORKSPACE_ID}" ]] || fail "failed to parse workspace id from CLI output"

run_cmd "${BIN_PATH}" --ns-bot-home "${NS_BOT_HOME}" threads list
pass "workspace lifecycle baseline"

seed_default_provider

DIAGNOSE_OUT="${TMP_DIR}/diagnose.json"
run_capture \
  "${DIAGNOSE_OUT}" \
  "${BIN_PATH}" \
  --ns-bot-home "${NS_BOT_HOME}" \
  agent run --prompt "diagnose test" --workspace "${WORKSPACE_DIR}" --background --json
assert_json_path_equals "${DIAGNOSE_OUT}" "status" "pending"
assert_json_path_equals "${DIAGNOSE_OUT}" "workspace_id" "${WORKSPACE_ID}"
pass "agent run background returns workspace/thread ids"

echo "All agent CLI e2e checks passed."
