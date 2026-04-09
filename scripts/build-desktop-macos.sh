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
  --debug   Build with Tauri debug profile.
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
else
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

if [[ -d "${APP_BUNDLE}" ]]; then
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

if [[ "${BUILD_DMG}" == "true" && -n "${DMG_PATH:-}" ]]; then
  echo "- dmg:     ${DMG_PATH}"
fi
