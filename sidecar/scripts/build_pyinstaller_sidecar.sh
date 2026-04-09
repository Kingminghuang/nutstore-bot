#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SIDECAR_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
DIST_DIR="${SIDECAR_ROOT}/dist"
BUILD_DIR="${SIDECAR_ROOT}/build/pyinstaller"
SPEC_DIR="${SIDECAR_ROOT}/build/spec"

mkdir -p "${DIST_DIR}" "${BUILD_DIR}" "${SPEC_DIR}"
rm -rf "${DIST_DIR}/nsbot-sidecar" "${DIST_DIR}/nsbot-sidecar.exe"

cd "${SIDECAR_ROOT}"

# Isolate PyInstaller's cache to avoid shared global cache races.
PYI_CONFIG_DIR="${BUILD_DIR}/pyinstaller-config"
rm -rf "${PYI_CONFIG_DIR}"
mkdir -p "${PYI_CONFIG_DIR}"
export PYINSTALLER_CONFIG_DIR="${PYI_CONFIG_DIR}"

uv run --project "${SIDECAR_ROOT}" --with pyinstaller pyinstaller \
  --clean \
  --target-arch arm64 \
  --paths "${SIDECAR_ROOT}/src" \
  --collect-data litellm \
  --collect-data smolagents \
  --hidden-import smolagents.prompts \
  --exclude-module tensorflow \
  --exclude-module torch \
  --exclude-module transformers \
  --exclude-module jax \
  --exclude-module flax \
  --exclude-module bitsandbytes \
  --exclude-module sentence_transformers \
  --exclude-module cv2 \
  --exclude-module scipy \
  --onefile \
  --name nsbot-sidecar \
  --distpath "${DIST_DIR}" \
  --workpath "${BUILD_DIR}" \
  --specpath "${SPEC_DIR}" \
  "${SIDECAR_ROOT}/src/nsbot_sidecar/api/api_server.py"

echo "Built sidecar binary at: ${DIST_DIR}/nsbot-sidecar"
