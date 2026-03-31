#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET_TRIPLE="aarch64-apple-darwin"
BUILD_MODE="release"
BUILD_DMG="false"
TAURI_CONF="${REPO_ROOT}/src-tauri/tauri.conf.json"
PRODUCT_NAME="$(node -p "require(process.argv[1]).productName" "${TAURI_CONF}")"
APP_VERSION="$(node -p "require(process.argv[1]).version" "${TAURI_CONF}")"
DMG_ARCH_SUFFIX="${TARGET_TRIPLE%%-*}"

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

verify_or_resign_bundle() {
  local bundle_root="$1"
  local signature_dir="${bundle_root}/Contents/_CodeSignature"

  if codesign --verify --deep --strict --verbose=2 "${bundle_root}"; then
    echo "[post] Bundle signature verified"
    return 0
  fi

  echo "[post] Bundle signature invalid after packaging; re-signing ad hoc for internal distribution"
  rm -rf "${signature_dir}"
  codesign --force --deep --sign - "${bundle_root}"
  codesign --verify --deep --strict --verbose=2 "${bundle_root}"
}

build_dmg_from_bundle() {
  local bundle_root="$1"
  local dmg_path="$2"
  local volume_name="$3"
  local staging_dir

  staging_dir="$(mktemp -d "${TMPDIR:-/tmp}/nutstore-bot-dmg.XXXXXX")"
  rm -f "${dmg_path}"

  ditto "${bundle_root}" "${staging_dir}/$(basename "${bundle_root}")"
  ln -s /Applications "${staging_dir}/Applications"

  hdiutil create \
    -volname "${volume_name}" \
    -srcfolder "${staging_dir}" \
    -ov \
    -format UDZO \
    "${dmg_path}"

  rm -rf "${staging_dir}"
}

usage() {
  cat <<'EOF'
Usage: bash ./scripts/build-desktop-macos.sh [--debug] [--dmg]

Options:
  --debug   Build with Tauri debug profile and print a backtrace-friendly run command.
  --dmg     Build a macOS DMG installer (release only).
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
    --dmg)
      BUILD_DMG="true"
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

if [[ "${BUILD_MODE}" == "debug" && "${BUILD_DMG}" == "true" ]]; then
  echo "--dmg is only supported for release builds. Remove --debug or omit --dmg." >&2
  exit 1
fi

if [[ "${BUILD_MODE}" == "debug" ]]; then
  APP_BUNDLE="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/debug/bundle/macos/${PRODUCT_NAME}.app"
  APP_BIN="${APP_BUNDLE}/Contents/MacOS/nutstore-bot-desktop"
  RAW_DEBUG_BIN="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/debug/nutstore-bot-desktop"
  RAW_DEBUG_BINARIES_DIR="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/debug/binaries"
else
  RAW_RELEASE_BIN="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/release/nutstore-bot-desktop"
  APP_BIN="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/release/bundle/macos/${PRODUCT_NAME}.app/Contents/MacOS/nutstore-bot-desktop"
  APP_BUNDLE="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/release/bundle/macos/${PRODUCT_NAME}.app"
  if [[ "${BUILD_DMG}" == "true" ]]; then
    DMG_DIR="${REPO_ROOT}/src-tauri/target/${TARGET_TRIPLE}/release/bundle/dmg"
    DMG_PATH="${DMG_DIR}/${PRODUCT_NAME}_${APP_VERSION}_${DMG_ARCH_SUFFIX}.dmg"
  fi
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
  elif [[ "${BUILD_DMG}" == "true" ]]; then
    cargo tauri build --target "${TARGET_TRIPLE}" --bundles app
  else
    cargo tauri build --target "${TARGET_TRIPLE}"
  fi
)

if [[ "${BUILD_MODE}" == "debug" ]]; then
  if [[ -d "${APP_BUNDLE}" ]]; then
    echo "[post] Sync bundle sidecars into Contents/MacOS/binaries"
    sync_bundle_sidecars "${APP_BUNDLE}"
  fi
  echo "[post] Sync debug sidecars into target debug/binaries"
  sync_debug_sidecars
elif [[ -d "${APP_BUNDLE}" ]]; then
  verify_or_resign_bundle "${APP_BUNDLE}"
  if [[ "${BUILD_DMG}" == "true" ]]; then
    echo "[post] Build DMG from the final signed app bundle"
    mkdir -p "${DMG_DIR}"
    build_dmg_from_bundle "${APP_BUNDLE}" "${DMG_PATH}" "${PRODUCT_NAME}"
  fi
fi

echo "Desktop build finished."
echo "- profile: ${BUILD_MODE}"

if [[ -d "${APP_BUNDLE}" ]]; then
  echo "- bundle:  ${APP_BUNDLE}"
  echo "- app bin: ${APP_BIN}"
fi

if [[ "${BUILD_DMG}" == "true" ]]; then
  if [[ -n "${DMG_PATH}" ]]; then
    echo "- dmg:     ${DMG_PATH}"
  else
    echo "- dmg dir: ${DMG_DIR}"
  fi
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
else
  echo
  echo "Release artifacts are intended to be run from the packaged .app or .dmg."
  echo "The packaged app now starts Next through a background helper, so Dock should"
  echo "not show a separate node-runtime icon while the release bundle is running."
  echo "Do not run the raw release binary directly:"
  echo "\"${RAW_RELEASE_BIN}\""
  echo "It does not include the release/binaries sidecar entrypoints used by debug raw runs."
  if [[ "${BUILD_DMG}" == "true" ]]; then
    echo
    echo "Internal distribution note (without Developer ID / notarization):"
    echo "- if another Mac reports the app is blocked after download, copy NutstoreBot.app"
    echo "  out of the mounted DMG and clear quarantine on the target machine:"
    echo "  xattr -dr com.apple.quarantine \"/Applications/${PRODUCT_NAME}.app\""
    echo "- if users still launch from Downloads, they can clear quarantine in place:"
    echo "  xattr -dr com.apple.quarantine \"/path/to/${PRODUCT_NAME}.app\""
  fi
fi
