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

  local smoke_root ns_bot_home seed_json
  local openai_model_1 openai_model_2
  local out_use out_status out_disable out_openai_list
  local out_custom_list out_remove out_delete out_provider_list

  smoke_root="$(mktemp -d "${TMPDIR:-/tmp}/nsbot-cli-provider-smoke.XXXXXX")"
  ns_bot_home="${smoke_root}/ns-bot-home"
  mkdir -p "${ns_bot_home}"
  trap 'rm -rf "${smoke_root}"' EXIT

  echo "[smoke] Running provider/model smoke checks with NS_BOT_HOME=${ns_bot_home}"

  seed_json="$(
    PYTHONPATH="${SIDECAR_ROOT}/src${PYTHONPATH:+:${PYTHONPATH}}" \
    uv run --project "${SIDECAR_ROOT}" python - "${ns_bot_home}" <<'PY'
from __future__ import annotations

import json
import sys

from nsbot_sidecar.infrastructure.repositories import create_repositories
from nsbot_sidecar.infrastructure.secret_store import LocalSecretStore, ProviderSecretPayload
from nsbot_sidecar.infrastructure.storage import connect_database
from nsbot_sidecar.providers.provider_catalog import list_providers

ns_bot_home = sys.argv[1]
database = connect_database(ns_bot_home)
repositories = create_repositories(database)
secret_store = LocalSecretStore(ns_bot_home)

openai_models = []
for provider in list_providers():
  if str(provider.get("id") or "") == "openai":
    openai_models = [str(item.get("id") or "") for item in provider.get("models", [])]
    break

if len(openai_models) < 2:
  raise RuntimeError("expected at least 2 OpenAI catalog models")

repositories.providers.save_bundle(
  provider_data={
    "id": "openai",
    "kind": "builtin",
    "runtime_provider": "openai",
    "catalog_provider_id": "openai",
    "display_name": "OpenAI Demo",
    "base_url": None,
    "secret_ref": "sec_prov_openai_demo",
    "api_key_configured": True,
    "model_policy": "restricted",
    "preferred_model_id": openai_models[1],
    "is_enabled": True,
  },
  models=[
    {
      "id": "pmod_openai_1",
      "source": "catalog",
      "model_id": openai_models[0],
      "display_name": None,
      "enabled": True,
      "sort_order": 0,
    },
    {
      "id": "pmod_openai_2",
      "source": "catalog",
      "model_id": openai_models[1],
      "display_name": None,
      "enabled": True,
      "sort_order": 1,
    },
  ],
)

repositories.providers.save_bundle(
  provider_data={
    "id": "prov_custom_demo",
    "kind": "custom",
    "runtime_provider": "custom",
    "catalog_provider_id": None,
    "custom_slug": "demo-gateway",
    "display_name": "Demo Gateway",
    "base_url": "https://llm.example.com/v1",
    "secret_ref": "sec_prov_custom_demo",
    "api_key_configured": True,
    "model_policy": "custom_only",
    "preferred_model_id": "demo-model-alpha",
    "is_enabled": True,
  },
  models=[
    {
      "id": "pmod_custom_1",
      "source": "custom",
      "model_id": "demo-model-alpha",
      "display_name": "Demo Model Alpha",
      "enabled": True,
      "sort_order": 0,
    },
    {
      "id": "pmod_custom_2",
      "source": "custom",
      "model_id": "demo-model-beta",
      "display_name": "Demo Model Beta",
      "enabled": True,
      "sort_order": 1,
    },
  ],
)

secret_store.save_provider_secret(
  "sec_prov_openai_demo",
  ProviderSecretPayload(version=1, api_key="sk-openai-demo"),
)
secret_store.save_provider_secret(
  "sec_prov_custom_demo",
  ProviderSecretPayload(version=1, api_key="sk-custom-demo"),
)

database.close()

print(
  json.dumps(
    {
      "openai_model_1": openai_models[0],
      "openai_model_2": openai_models[1],
    },
    ensure_ascii=False,
  )
)
PY
  )"

  openai_model_1="$(uv run --project "${SIDECAR_ROOT}" python -c 'import json,sys; print(json.loads(sys.argv[1])["openai_model_1"])' "${seed_json}")"
  openai_model_2="$(uv run --project "${SIDECAR_ROOT}" python -c 'import json,sys; print(json.loads(sys.argv[1])["openai_model_2"])' "${seed_json}")"

  out_use="${smoke_root}/providers-use.json"
  out_status="${smoke_root}/models-openai-status.json"
  out_disable="${smoke_root}/models-disable.json"
  out_openai_list="${smoke_root}/models-openai-list.json"
  out_custom_list="${smoke_root}/models-custom-list.json"
  out_remove="${smoke_root}/models-remove.json"
  out_delete="${smoke_root}/providers-delete.json"
  out_provider_list="${smoke_root}/providers-list.json"

  echo "[smoke] providers use openai (auto-select first model)"
  run_capture "${out_use}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" providers use openai
  assert_json_path_equals "${out_use}" "ok" "True"
  assert_json_path_equals "${out_use}" "providerId" "openai"
  assert_json_path_equals "${out_use}" "modelId" "${openai_model_1}"

  run_capture "${out_status}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" models list --provider-id openai
  assert_model_enabled_state "${out_status}" "openai" "${openai_model_1}" true

  echo "[smoke] models disable on OpenAI second model"
  run_capture "${out_disable}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" models disable --provider-id openai --model "${openai_model_2}"
  assert_json_path_equals "${out_disable}" "ok" "True"
  assert_json_path_equals "${out_disable}" "action" "disabled"

  run_capture "${out_openai_list}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" models list --provider-id openai
  assert_model_disabled_or_absent "${out_openai_list}" "openai" "${openai_model_2}"

  echo "[smoke] models remove custom model"
  run_capture "${out_remove}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" models remove --provider-id prov_custom_demo --model demo-model-alpha
  assert_json_path_equals "${out_remove}" "ok" "True"
  assert_json_path_equals "${out_remove}" "action" "removed"

  run_capture "${out_custom_list}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" models list --provider-id prov_custom_demo
  assert_model_absent "${out_custom_list}" "prov_custom_demo" "demo-model-alpha"

  echo "[smoke] providers delete custom provider"
  run_capture "${out_delete}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" providers delete --provider-id prov_custom_demo
  assert_json_path_equals "${out_delete}" "ok" "True"
  assert_json_path_equals "${out_delete}" "deletedProviderId" "prov_custom_demo"

  run_capture "${out_provider_list}" "${launcher_path}" --ns-bot-home "${ns_bot_home}" providers list
  assert_configured_provider_absent "${out_provider_list}" "prov_custom_demo"

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
    "${SIDECAR_ROOT}/src/nsbot_sidecar/cli.py"
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
