#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_TRIPLE="aarch64-apple-darwin"
BUILD_MODE="release"

sync_debug_sidecars() {
  local debug_root="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/debug"
  local binaries_dir="${debug_root}/binaries"
  local next_target="${debug_root}/next-sidecar"
  local python_target="${debug_root}/nsbot-sidecar"

  if [[ ! -x "${next_target}" || ! -x "${python_target}" ]]; then
    echo "Missing debug sidecars under ${debug_root}" >&2
    echo "- expected: ${next_target}" >&2
    echo "- expected: ${python_target}" >&2
    exit 1
  fi

  mkdir -p "${binaries_dir}"
  ln -sfn "../next-sidecar" "${binaries_dir}/next-sidecar"
  ln -sfn "../nsbot-sidecar" "${binaries_dir}/nsbot-sidecar"
}

sync_bundle_sidecars() {
  local bundle_root="$1"
  local macos_dir="${bundle_root}/Contents/MacOS"
  local binaries_dir="${macos_dir}/binaries"
  local next_target="${macos_dir}/next-sidecar"
  local python_target="${macos_dir}/nsbot-sidecar"

  if [[ ! -x "${next_target}" || ! -x "${python_target}" ]]; then
    echo "Missing bundled sidecars under ${macos_dir}" >&2
    echo "- expected: ${next_target}" >&2
    echo "- expected: ${python_target}" >&2
    exit 1
  fi

  mkdir -p "${binaries_dir}"
  ln -sfn "../next-sidecar" "${binaries_dir}/next-sidecar"
  ln -sfn "../nsbot-sidecar" "${binaries_dir}/nsbot-sidecar"
}

usage() {
  cat <<'EOF'
Usage: bash ./scripts/build-desktop-macos.sh [--debug]

Options:
  --debug   Build with Tauri debug profile and print a backtrace-friendly run command.
  -h, --help
            Show this help message.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug)
      BUILD_MODE="debug"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 1
      ;;
  esac
done

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "This script is intended for macOS (Darwin)." >&2
  exit 1
fi

if [[ "${BUILD_MODE}" == "debug" ]]; then
  APP_BUNDLE="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/debug/bundle/macos/Nutstore Bot.app"
  APP_BIN="${APP_BUNDLE}/Contents/MacOS/nutstore-bot-desktop"
  RAW_DEBUG_BIN="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/debug/nutstore-bot-desktop"
  RAW_DEBUG_BINARIES_DIR="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/debug/binaries"
else
  APP_BIN="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/release/nutstore-bot-desktop"
  APP_BUNDLE="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/release/bundle/macos/Nutstore Bot.app"
fi

echo "[1/3] Clean Python cache files"
find "${REPO_ROOT}" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "${REPO_ROOT}" -type f \( -name "*.pyc" -o -name "*.pyo" \) -delete

echo "[2/3] Prepare desktop runtime"
bash "${REPO_ROOT}/scripts/prepare_desktop_runtime_macos.sh"

echo "[3/3] Build Tauri app (${TARGET_TRIPLE}, ${BUILD_MODE})"
(
  cd "${REPO_ROOT}/src-tauri"
  if [[ "${BUILD_MODE}" == "debug" ]]; then
    RUST_BACKTRACE=full cargo tauri build --debug --target "${TARGET_TRIPLE}"
  else
    cargo tauri build --target "${TARGET_TRIPLE}"
  fi
)

if [[ -d "${APP_BUNDLE}" ]]; then
  echo "[post] Sync bundle sidecars into Contents/MacOS/binaries"
  sync_bundle_sidecars "${APP_BUNDLE}"
fi

if [[ "${BUILD_MODE}" == "debug" ]]; then
  echo "[post] Sync debug sidecars into target debug/binaries"
  sync_debug_sidecars
fi

echo "Desktop build finished."
echo "- profile: ${BUILD_MODE}"
echo "- binary:  ${APP_BIN}"

if [[ -d "${APP_BUNDLE}" ]]; then
  echo "- bundle:  ${APP_BUNDLE}"
fi

if [[ "${BUILD_MODE}" == "debug" ]]; then
  echo
  echo "Debug run command (recommended for startup failures):"
  echo "RUST_BACKTRACE=full \"${APP_BIN}\""
  echo
  echo "Raw debug binary is also available after syncing debug/binaries:"
  echo "RUST_BACKTRACE=full \"${RAW_DEBUG_BIN}\""
  echo "- raw debug sidecars: ${RAW_DEBUG_BINARIES_DIR}"
  echo "- bundled sidecars: ${APP_BUNDLE}/Contents/MacOS/binaries"
  echo
  echo "This is more useful than only enabling backtrace during build because your"
  echo "current failure happens when the packaged runtime starts, not while Rust compiles."
fi
