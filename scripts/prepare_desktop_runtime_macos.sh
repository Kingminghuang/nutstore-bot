#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
RUNTIME_ROOT="${REPO_ROOT}/src-tauri/runtime"
BINARIES_ROOT="${REPO_ROOT}/src-tauri/binaries"
FRONTEND_ROOT="${REPO_ROOT}/frontend"
SIDECAR_ROOT="${REPO_ROOT}/sidecar"
NEXT_HELPER_DIR="${RUNTIME_ROOT}/next-helper"
NEXT_HELPER_EXECUTABLE="${NEXT_HELPER_DIR}/next-runtime-helper"

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

echo "[3.5/6] Prepare macOS Next helper bundle"
rm -rf "${NEXT_HELPER_DIR}"
mkdir -p "${NEXT_HELPER_DIR}"

cp "${NODE_BIN}" "${NEXT_HELPER_DIR}/node-runtime"
chmod +x "${NEXT_HELPER_DIR}/node-runtime"

HELPER_SOURCE="$(mktemp "${TMPDIR:-/tmp}/next-runtime-helper.XXXXXX.c")"
trap 'rm -f "${HELPER_SOURCE}"' EXIT
cat > "${HELPER_SOURCE}" <<'EOF'
#include <libgen.h>
#include <limits.h>
#include <mach-o/dyld.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

static int ensure_regular_file(const char *path) {
  struct stat st;
  return stat(path, &st) == 0 && S_ISREG(st.st_mode);
}

int main(void) {
  uint32_t raw_size = 0;
  _NSGetExecutablePath(NULL, &raw_size);
  char *raw_path = (char *)malloc(raw_size + 1);
  if (raw_path == NULL) {
    fprintf(stderr, "alloc executable path failed\n");
    return 1;
  }

  if (_NSGetExecutablePath(raw_path, &raw_size) != 0) {
    fprintf(stderr, "resolve executable path failed\n");
    free(raw_path);
    return 1;
  }

  char executable_path[PATH_MAX];
  if (realpath(raw_path, executable_path) == NULL) {
    perror("realpath executable");
    free(raw_path);
    return 1;
  }
  free(raw_path);

  char executable_dir[PATH_MAX];
  strncpy(executable_dir, executable_path, sizeof(executable_dir));
  executable_dir[sizeof(executable_dir) - 1] = '\0';
  char *executable_dirname = dirname(executable_dir);
  if (executable_dirname == NULL) {
    fprintf(stderr, "dirname executable failed\n");
    return 1;
  }

  char node_path[PATH_MAX];
  snprintf(node_path, sizeof(node_path), "%s/node-runtime", executable_dirname);

  char server_path[PATH_MAX];
  snprintf(server_path, sizeof(server_path), "%s/../next-standalone/server.js", executable_dirname);

  char server_realpath[PATH_MAX];
  if (realpath(server_path, server_realpath) == NULL) {
    perror("realpath server");
    return 1;
  }

  if (!ensure_regular_file(node_path)) {
    fprintf(stderr, "missing helper node runtime: %s\n", node_path);
    return 1;
  }
  if (!ensure_regular_file(server_realpath)) {
    fprintf(stderr, "missing bundled Next standalone server: %s\n", server_realpath);
    return 1;
  }

  char server_dir[PATH_MAX];
  strncpy(server_dir, server_realpath, sizeof(server_dir));
  server_dir[sizeof(server_dir) - 1] = '\0';
  char *server_dirname = dirname(server_dir);
  if (server_dirname == NULL) {
    fprintf(stderr, "dirname server failed\n");
    return 1;
  }

  if (chdir(server_dirname) != 0) {
    perror("chdir server");
    return 1;
  }

  char *const argv[] = { node_path, server_realpath, NULL };
  execv(node_path, argv);
  perror("execv node-runtime");
  return 1;
}
EOF

clang -O2 -Wall -Wextra "${HELPER_SOURCE}" -o "${NEXT_HELPER_EXECUTABLE}"
chmod +x "${NEXT_HELPER_EXECUTABLE}"

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
