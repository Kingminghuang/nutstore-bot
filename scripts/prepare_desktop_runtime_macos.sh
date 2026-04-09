#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_ROOT="${REPO_ROOT}/src-tauri/runtime"
BINARIES_ROOT="${REPO_ROOT}/src-tauri/binaries"
SIDECAR_ROOT="${REPO_ROOT}/sidecar"

TARGET_TRIPLE="aarch64-apple-darwin"

echo "[1/4] Clean runtime root and target binaries"
rm -rf "${RUNTIME_ROOT}"
mkdir -p "${RUNTIME_ROOT}/search-tools" "${RUNTIME_ROOT}/templates"

mkdir -p "${BINARIES_ROOT}"
rm -f "${BINARIES_ROOT}/nsbot-sidecar-${TARGET_TRIPLE}"

echo "[2/4] Build Python sidecar with PyInstaller (onefile)"
bash "${SIDECAR_ROOT}/scripts/build_pyinstaller_sidecar.sh"

SIDECAR_DIST_BIN="${SIDECAR_ROOT}/dist/nsbot-sidecar"
if [[ ! -f "${SIDECAR_DIST_BIN}" && -f "${SIDECAR_DIST_BIN}.exe" ]]; then
  SIDECAR_DIST_BIN="${SIDECAR_DIST_BIN}.exe"
fi

if [[ ! -f "${SIDECAR_DIST_BIN}" ]]; then
  echo "Missing sidecar executable: ${SIDECAR_DIST_BIN}" >&2
  exit 1
fi

PYTHON_SIDECAR_BIN="${BINARIES_ROOT}/nsbot-sidecar-${TARGET_TRIPLE}"
cp "${SIDECAR_DIST_BIN}" "${PYTHON_SIDECAR_BIN}"
chmod +x "${PYTHON_SIDECAR_BIN}"

echo "[3/4] Prepare fd/rg runtime"
(
  cd "${SIDECAR_ROOT}"
  uv run python scripts/prepare_search_tools.py --target "${TARGET_TRIPLE}"
)

FD_SOURCE="${SIDECAR_ROOT}/vendor/search-tools/${TARGET_TRIPLE}/fd/fd"
RG_SOURCE="${SIDECAR_ROOT}/vendor/search-tools/${TARGET_TRIPLE}/rg/rg"

if [[ ! -f "${FD_SOURCE}" || ! -f "${RG_SOURCE}" ]]; then
  echo "Missing search tools after prepare_search_tools.py" >&2
  exit 1
fi
cp "${FD_SOURCE}" "${RUNTIME_ROOT}/search-tools/fd"
cp "${RG_SOURCE}" "${RUNTIME_ROOT}/search-tools/rg"
chmod +x "${RUNTIME_ROOT}/search-tools/fd" "${RUNTIME_ROOT}/search-tools/rg"

echo "[4/4] Copy templates"
cp -R "${REPO_ROOT}/templates/." "${RUNTIME_ROOT}/templates/"

echo "Runtime prepared at ${RUNTIME_ROOT}"
echo "Sidecar binaries prepared at ${BINARIES_ROOT}"
