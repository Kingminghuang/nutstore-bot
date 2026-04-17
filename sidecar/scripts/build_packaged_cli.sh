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
  rm -f "${DIST_ROOT}/${PAYLOAD_NAME}" "${DIST_ROOT}/${PAYLOAD_NAME}.exe"

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
    --onefile
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
  local target_triple payload_path
  target_triple="$(resolve_target_triple)"

  mkdir -p "${DIST_ROOT}/binaries"
  build_python_cli_payload

  payload_path="${DIST_ROOT}/${PAYLOAD_NAME}"
  if [[ ! -f "${payload_path}" && -f "${payload_path}.exe" ]]; then
    payload_path="${payload_path}.exe"
  fi
  [[ -f "${payload_path}" ]] || {
    echo "missing CLI payload after PyInstaller build: ${payload_path}" >&2
    exit 1
  }

  rm -f "${DIST_ROOT}/binaries/${PAYLOAD_NAME}" "${DIST_ROOT}/binaries/${PAYLOAD_NAME}.exe"
  cp "${payload_path}" "${DIST_ROOT}/binaries/$(basename "${payload_path}")"
  chmod +x "${DIST_ROOT}/binaries/$(basename "${payload_path}")"

  copy_runtime_tree "${target_triple}"
  build_rust_launcher

  echo "Packaged CLI ready at ${DIST_ROOT}/${LAUNCHER_NAME}"
}

main "$@"