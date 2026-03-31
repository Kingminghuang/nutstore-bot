#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_ROOT="${REPO_ROOT}/src-tauri/runtime"
BINARIES_ROOT="${REPO_ROOT}/src-tauri/binaries"
FRONTEND_ROOT="${REPO_ROOT}/frontend"
SIDECAR_ROOT="${REPO_ROOT}/sidecar"

TARGET_TRIPLE="aarch64-apple-darwin"
NODE_BIN="$(command -v node)"

if [[ -z "${NODE_BIN}" || ! -x "${NODE_BIN}" ]]; then
  echo "Missing node executable in PATH" >&2
  exit 1
fi

echo "[1/6] Clean runtime root and target binaries"
rm -rf "${RUNTIME_ROOT}"
mkdir -p "${RUNTIME_ROOT}/search-tools" "${RUNTIME_ROOT}/templates" "${RUNTIME_ROOT}/next-standalone"

mkdir -p "${BINARIES_ROOT}"
rm -f \
  "${BINARIES_ROOT}/next-sidecar-${TARGET_TRIPLE}" \
  "${BINARIES_ROOT}/node-runtime-${TARGET_TRIPLE}" \
  "${BINARIES_ROOT}/nsbot-sidecar-${TARGET_TRIPLE}"

echo "[2/6] Build Next standalone"
(
  cd "${FRONTEND_ROOT}"
  npm run build
)

if [[ ! -f "${FRONTEND_ROOT}/.next/standalone/server.js" ]]; then
  echo "Missing Next standalone output: ${FRONTEND_ROOT}/.next/standalone/server.js" >&2
  exit 1
fi

echo "[3/6] Prepare Next desktop runtime (node + standalone launcher)"
NEXT_RUNTIME_ROOT="${RUNTIME_ROOT}/next-standalone"
rm -rf "${NEXT_RUNTIME_ROOT}"
mkdir -p "${NEXT_RUNTIME_ROOT}"
cp -R "${FRONTEND_ROOT}/.next/standalone/." "${NEXT_RUNTIME_ROOT}/"

if [[ -d "${FRONTEND_ROOT}/.next/static" ]]; then
  mkdir -p "${NEXT_RUNTIME_ROOT}/.next"
  cp -R "${FRONTEND_ROOT}/.next/static" "${NEXT_RUNTIME_ROOT}/.next/static"
fi

if [[ -d "${FRONTEND_ROOT}/public" ]]; then
  cp -R "${FRONTEND_ROOT}/public" "${NEXT_RUNTIME_ROOT}/public"
fi

NODE_RUNTIME_BIN="${BINARIES_ROOT}/node-runtime-${TARGET_TRIPLE}"
cp "${NODE_BIN}" "${NODE_RUNTIME_BIN}"
chmod +x "${NODE_RUNTIME_BIN}"

NEXT_SIDECAR_BIN="${BINARIES_ROOT}/next-sidecar-${TARGET_TRIPLE}"
cat > "${NEXT_SIDECAR_BIN}" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$0"
while [[ -L "${SCRIPT_PATH}" ]]; do
  LINK_TARGET="$(readlink "${SCRIPT_PATH}")"
  if [[ "${LINK_TARGET}" = /* ]]; then
    SCRIPT_PATH="${LINK_TARGET}"
  else
    SCRIPT_PATH="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)/${LINK_TARGET}"
  fi
done

SELF_DIR="$(cd "$(dirname "${SCRIPT_PATH}")" && pwd)"
BUNDLE_RUNTIME_ROOT="${SELF_DIR}/../Resources/runtime/next-standalone"
DEBUG_RUNTIME_ROOT="${SELF_DIR}/runtime/next-standalone"
SOURCE_RUNTIME_ROOT="${SELF_DIR}/../runtime/next-standalone"
NODE_BIN_CANDIDATES=(
  "${SELF_DIR}/node-runtime"
  "${SELF_DIR}/node-runtime-aarch64-apple-darwin"
)

NODE_BIN=""
for candidate in "${NODE_BIN_CANDIDATES[@]}"; do
  if [[ -x "${candidate}" ]]; then
    NODE_BIN="${candidate}"
    break
  fi
done

if [[ -f "${BUNDLE_RUNTIME_ROOT}/server.js" ]]; then
  NEXT_RUNTIME_ROOT="${BUNDLE_RUNTIME_ROOT}"
elif [[ -f "${DEBUG_RUNTIME_ROOT}/server.js" ]]; then
  NEXT_RUNTIME_ROOT="${DEBUG_RUNTIME_ROOT}"
elif [[ -f "${SOURCE_RUNTIME_ROOT}/server.js" ]]; then
  NEXT_RUNTIME_ROOT="${SOURCE_RUNTIME_ROOT}"
else
  NEXT_RUNTIME_ROOT="${BUNDLE_RUNTIME_ROOT}"
fi

if [[ -z "${NODE_BIN}" ]]; then
  echo "Missing bundled node runtime near ${SELF_DIR}" >&2
  exit 1
fi

if [[ ! -f "${NEXT_RUNTIME_ROOT}/server.js" ]]; then
  echo "Missing bundled Next standalone server: ${NEXT_RUNTIME_ROOT}/server.js" >&2
  exit 1
fi

cd "${NEXT_RUNTIME_ROOT}"
exec "${NODE_BIN}" "${NEXT_RUNTIME_ROOT}/server.js"
EOF
chmod +x "${NEXT_SIDECAR_BIN}"

echo "[4/6] Build Python sidecar with PyInstaller (onefile)"
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

echo "[5/6] Prepare fd/rg runtime"
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

echo "[6/6] Copy templates"
cp -R "${REPO_ROOT}/templates/." "${RUNTIME_ROOT}/templates/"

echo "Runtime prepared at ${RUNTIME_ROOT}"
echo "Sidecar binaries prepared at ${BINARIES_ROOT}"
