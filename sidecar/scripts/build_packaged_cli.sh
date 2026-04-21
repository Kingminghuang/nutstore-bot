#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIDECAR_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd "${SIDECAR_ROOT}/.." && pwd)"
SRC_TAURI_ROOT="${REPO_ROOT}/src-tauri"
DIST_ROOT="${SIDECAR_ROOT}/dist"
BUILD_ROOT="${SIDECAR_ROOT}/build/pyinstaller-cli"
SPEC_ROOT="${SIDECAR_ROOT}/build/spec"

PAYLOAD_NAME="nsbot-sidecar-cli-payload"
LAUNCHER_NAME="nsbot"

SMOKE_FLAG="${NSBOT_RUN_PROVIDER_MODEL_SMOKE:-0}"

run_capture() {
  local out_file="$1"
  shift
  "$@" >"${out_file}"
}

assert_json_path_equals() {
  local file_path="$1"
  local dotted_path="$2"
  local expected="$3"

  uv run --project "${SIDECAR_ROOT}" python - "${file_path}" "${dotted_path}" "${expected}" <<'PY'
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

assert_model_enabled_state() {
  local file_path="$1"
  local provider_id="$2"
  local model_id="$3"
  local expected_enabled="$4"

  uv run --project "${SIDECAR_ROOT}" python - "${file_path}" "${provider_id}" "${model_id}" "${expected_enabled}" <<'PY'
from __future__ import annotations

import json
import sys

payload_path = sys.argv[1]
provider_id = sys.argv[2]
model_id = sys.argv[3]
expected_enabled = sys.argv[4].strip().lower() == "true"

with open(payload_path, "r", encoding="utf-8") as handle:
  payload = json.load(handle)

groups = payload.get("groups")
if not isinstance(groups, list):
  raise SystemExit("models list payload missing groups")

for group in groups:
  if str(group.get("providerId") or "") != provider_id:
    continue
  models = group.get("models")
  if not isinstance(models, list):
    raise SystemExit(f"models list for provider {provider_id!r} is not a list")
  for model in models:
    if str(model.get("modelId") or "") != model_id:
      continue
    enabled = bool(model.get("enabled"))
    if enabled != expected_enabled:
      raise SystemExit(
        f"model enabled mismatch for {model_id!r}: expected={expected_enabled!r} actual={enabled!r}"
      )
    raise SystemExit(0)
  raise SystemExit(f"model {model_id!r} not found for provider {provider_id!r}")

raise SystemExit(f"provider group {provider_id!r} not found")
PY
}

assert_model_disabled_or_absent() {
  local file_path="$1"
  local provider_id="$2"
  local model_id="$3"

  uv run --project "${SIDECAR_ROOT}" python - "${file_path}" "${provider_id}" "${model_id}" <<'PY'
from __future__ import annotations

import json
import sys

payload_path = sys.argv[1]
provider_id = sys.argv[2]
model_id = sys.argv[3]

with open(payload_path, "r", encoding="utf-8") as handle:
  payload = json.load(handle)

groups = payload.get("groups")
if not isinstance(groups, list):
  raise SystemExit("models list payload missing groups")

for group in groups:
  if str(group.get("providerId") or "") != provider_id:
    continue
  models = group.get("models")
  if not isinstance(models, list):
    raise SystemExit(f"models list for provider {provider_id!r} is not a list")

  for model in models:
    if str(model.get("modelId") or "") != model_id:
      continue
    enabled = bool(model.get("enabled"))
    if enabled:
      raise SystemExit(
        f"model {model_id!r} should be disabled for provider {provider_id!r}"
      )
    raise SystemExit(0)

  # Hidden from model list is also an acceptable disabled state.
  raise SystemExit(0)

raise SystemExit(f"provider group {provider_id!r} not found")
PY
}

assert_model_absent() {
  local file_path="$1"
  local provider_id="$2"
  local model_id="$3"

  uv run --project "${SIDECAR_ROOT}" python - "${file_path}" "${provider_id}" "${model_id}" <<'PY'
from __future__ import annotations

import json
import sys

payload_path = sys.argv[1]
provider_id = sys.argv[2]
model_id = sys.argv[3]

with open(payload_path, "r", encoding="utf-8") as handle:
  payload = json.load(handle)

groups = payload.get("groups")
if not isinstance(groups, list):
  raise SystemExit("models list payload missing groups")

for group in groups:
  if str(group.get("providerId") or "") != provider_id:
    continue
  models = group.get("models")
  if not isinstance(models, list):
    raise SystemExit(f"models list for provider {provider_id!r} is not a list")
  for model in models:
    if str(model.get("modelId") or "") == model_id:
      raise SystemExit(f"model {model_id!r} should be absent for provider {provider_id!r}")
  raise SystemExit(0)

raise SystemExit(f"provider group {provider_id!r} not found")
PY
}

assert_configured_provider_absent() {
  local file_path="$1"
  local provider_id="$2"

  uv run --project "${SIDECAR_ROOT}" python - "${file_path}" "${provider_id}" <<'PY'
from __future__ import annotations

import json
import sys

payload_path = sys.argv[1]
provider_id = sys.argv[2]

with open(payload_path, "r", encoding="utf-8") as handle:
  payload = json.load(handle)

providers = payload.get("configuredProviders")
if not isinstance(providers, list):
  raise SystemExit("providers list payload missing configuredProviders")

for provider in providers:
  if str(provider.get("id") or provider.get("providerId") or "") == provider_id:
    raise SystemExit(f"provider {provider_id!r} should be absent from configured providers")
PY
}

run_provider_model_smoke() {
  local launcher_path="$1"

  if [[ "${SMOKE_FLAG}" != "1" ]]; then
  echo "[smoke] Skipping provider/model smoke checks (set NSBOT_RUN_PROVIDER_MODEL_SMOKE=1 to enable)."
  return
  fi

  (
  set -euo pipefail

  local smoke_root ns_bot_home
  local out_create out_get out_set_default
  local openai_identity

  smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/nsbot-cli-provider-smoke.XXXXXX")"
  ns_bot_home="${smoke_root}/ns-bot-home"
  mkdir -p "${ns_bot_home}"
  trap 'rm -rf "${smoke_root}"' EXIT

  echo "[smoke] Running provider/model smoke checks with NS_BOT_HOME=${ns_bot_home}"

  out_create="${smoke_root}/models-create.json"
  out_get="${smoke_root}/models-get.json"
  out_set_default="${smoke_root}/models-set-default.json"

  echo "[smoke] models create openai/gpt-5.4"
  run_capture \
    "${out_create}" \
    "${launcher_path}" \
    --ns-bot-home "${ns_bot_home}" \
    models create \
    --name "OpenAI" \
    --base-url "https://api.openai.example/v1" \
    --model-id "openai/gpt-5.4" \
    --api-key "sk-build-smoke"
  assert_json_path_equals "${out_create}" "preferredModelId" "openai/gpt-5.4"
  openai_identity="$(uv run --project "${SIDECAR_ROOT}" python -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["identity"])' "${out_create}")"
  [[ -n "${openai_identity}" ]] || {
    echo "missing model identity from models create smoke output" >&2
    exit 1
  }

  echo "[smoke] models get ${openai_identity}"
  run_capture \
    "${out_get}" \
    "${launcher_path}" \
    --ns-bot-home "${ns_bot_home}" \
    models get \
    "${openai_identity}"
  assert_json_path_equals "${out_get}" "modelId" "openai/gpt-5.4"

  echo "[smoke] models set-default ${openai_identity}"
  run_capture \
    "${out_set_default}" \
    "${launcher_path}" \
    --ns-bot-home "${ns_bot_home}" \
    models set-default \
    "${openai_identity}"
  assert_json_path_equals "${out_set_default}" "action" "set-default"
  assert_json_path_equals "${out_set_default}" "modelId" "openai/gpt-5.4"

  echo "[smoke] Provider/model smoke checks passed."
  )
}

resolve_target_triple() {
  local os_name arch_name
  os_name="$(uname -s)"
  arch_name="$(uname -m)"

  case "${os_name}/${arch_name}" in
    Darwin/arm64|Darwin/aarch64)
      echo "aarch64-apple-darwin"
      ;;
    Darwin/x86_64)
      echo "x86_64-apple-darwin"
      ;;
    Linux/x86_64)
      echo "x86_64-unknown-linux-gnu"
      ;;
    Linux/aarch64)
      echo "aarch64-unknown-linux-gnu"
      ;;
    MINGW*/*|MSYS*/*|CYGWIN*/*)
      echo "x86_64-pc-windows-msvc"
      ;;
    *)
      echo "unsupported build host: ${os_name}/${arch_name}" >&2
      exit 1
      ;;
  esac
}

resolve_tool_binary_name() {
  local tool_name="$1"
  case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*)
      echo "${tool_name}.exe"
      ;;
    *)
      echo "${tool_name}"
      ;;
  esac
}

postprocess_launcher_signature() {
  local launcher_path="$1"
  if [[ "$(uname -s)" != "Darwin" ]]; then
    return
  fi

  echo "[post] Re-sign launcher for macOS local distribution: ${launcher_path}"
  codesign --force --sign - --timestamp=none "${launcher_path}"
  codesign --verify --deep --strict --verbose=2 "${launcher_path}"
}

copy_runtime_tree() {
  local target_triple="$1"
  local fd_name rg_name
  fd_name="$(resolve_tool_binary_name fd)"
  rg_name="$(resolve_tool_binary_name rg)"

  mkdir -p "${DIST_ROOT}/runtime/search-tools" "${DIST_ROOT}/runtime/templates" "${DIST_ROOT}/binaries"
  rm -rf "${DIST_ROOT}/runtime"
  mkdir -p "${DIST_ROOT}/runtime/search-tools" "${DIST_ROOT}/runtime/templates"

  cp -R "${REPO_ROOT}/templates/." "${DIST_ROOT}/runtime/templates/"

  (
    cd "${SIDECAR_ROOT}"
    uv run python scripts/prepare_search_tools.py --target "${target_triple}"
  )

  cp "${SIDECAR_ROOT}/vendor/search-tools/${target_triple}/fd/${fd_name}" "${DIST_ROOT}/runtime/search-tools/${fd_name}"
  cp "${SIDECAR_ROOT}/vendor/search-tools/${target_triple}/rg/${rg_name}" "${DIST_ROOT}/runtime/search-tools/${rg_name}"
  chmod +x "${DIST_ROOT}/runtime/search-tools/${fd_name}" "${DIST_ROOT}/runtime/search-tools/${rg_name}"
}

build_python_cli_payload() {
  mkdir -p "${DIST_ROOT}" "${BUILD_ROOT}" "${SPEC_ROOT}"
  rm -rf "${DIST_ROOT}/${PAYLOAD_NAME}" "${DIST_ROOT}/${PAYLOAD_NAME}.exe"

  local pyi_config_dir="${BUILD_ROOT}/pyinstaller-config"
  rm -rf "${pyi_config_dir}"
  mkdir -p "${pyi_config_dir}"
  export PYINSTALLER_CONFIG_DIR="${pyi_config_dir}"

  cd "${SIDECAR_ROOT}"

  local pyinstaller_args=(
    uv run --project "${SIDECAR_ROOT}" --with pyinstaller pyinstaller
    --clean
    --paths "${SIDECAR_ROOT}/src"
    --collect-data litellm
    --collect-data smolagents
    --collect-submodules websockets
    --collect-submodules wsproto
    --hidden-import smolagents.prompts
    --exclude-module tensorflow
    --exclude-module torch
    --exclude-module transformers
    --exclude-module jax
    --exclude-module flax
    --exclude-module bitsandbytes
    --exclude-module sentence_transformers
    --exclude-module cv2
    --exclude-module scipy
    --onedir
    --name "${PAYLOAD_NAME}"
    --distpath "${DIST_ROOT}"
    --workpath "${BUILD_ROOT}"
    --specpath "${SPEC_ROOT}"
    "${SIDECAR_ROOT}/src/nsbot/cli/__main__.py"
  )

  case "$(uname -s)/$(uname -m)" in
    Darwin/arm64|Darwin/aarch64)
      pyinstaller_args+=(--target-arch arm64)
      ;;
    Darwin/x86_64)
      pyinstaller_args+=(--target-arch x86_64)
      ;;
  esac

  "${pyinstaller_args[@]}"
}

build_rust_launcher() {
  cd "${SRC_TAURI_ROOT}"
  cargo build --release --bin "${LAUNCHER_NAME}"

  cp "${SRC_TAURI_ROOT}/target/release/${LAUNCHER_NAME}" "${DIST_ROOT}/${LAUNCHER_NAME}"
  chmod +x "${DIST_ROOT}/${LAUNCHER_NAME}"
  postprocess_launcher_signature "${DIST_ROOT}/${LAUNCHER_NAME}"
}

main() {
  local target_triple payload_dir payload_executable staged_payload_dir payload_binary_name
  target_triple="$(resolve_target_triple)"

  mkdir -p "${DIST_ROOT}/binaries"
  build_python_cli_payload

  payload_dir="${DIST_ROOT}/${PAYLOAD_NAME}"
  payload_binary_name="$(resolve_tool_binary_name "${PAYLOAD_NAME}")"
  payload_executable="${payload_dir}/${payload_binary_name}"
  [[ -d "${payload_dir}" ]] || {
    echo "missing CLI payload directory after PyInstaller build: ${payload_dir}" >&2
    exit 1
  }
  [[ -f "${payload_executable}" ]] || {
    echo "missing CLI payload executable after PyInstaller build: ${payload_executable}" >&2
    exit 1
  }

  staged_payload_dir="${DIST_ROOT}/binaries/${PAYLOAD_NAME}"
  rm -rf "${staged_payload_dir}" "${DIST_ROOT}/binaries/${PAYLOAD_NAME}.exe"
  cp -R "${payload_dir}" "${staged_payload_dir}"
  chmod +x "${staged_payload_dir}/${payload_binary_name}"
  rm -rf "${payload_dir}"

  copy_runtime_tree "${target_triple}"
  build_rust_launcher
  run_provider_model_smoke "${DIST_ROOT}/${LAUNCHER_NAME}"

  echo "Packaged CLI ready at ${DIST_ROOT}/${LAUNCHER_NAME}"
}

main "$@"
