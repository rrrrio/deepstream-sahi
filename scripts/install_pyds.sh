#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
VENV_DIR="${REPO_ROOT}/.venv"
DEPS_DIR="${REPO_ROOT}/.deps"
PYDS_WHEEL_URL=${PYDS_WHEEL_URL:-https://github.com/NVIDIA-AI-IOT/deepstream_python_apps/releases/download/v1.2.0/pyds-1.2.0-cp310-cp310-linux_aarch64.whl}

fail() { printf 'install_pyds: %s\n' "$1" >&2; exit 1; }
[ -w "${REPO_ROOT}" ] || fail "repository root is not writable: ${REPO_ROOT}"
[ -x "${VENV_DIR}/bin/python" ] || fail "missing ${VENV_DIR}/bin/python; run install_inplace.sh first"
mkdir -p "${DEPS_DIR}/pip-cache"
export PIP_CACHE_DIR="${DEPS_DIR}/pip-cache"
"${VENV_DIR}/bin/python" -m pip install 'numpy<2'

if [ -n "${PYDS_WHEEL:-}" ]; then
    [ -f "${PYDS_WHEEL}" ] || fail "PYDS_WHEEL does not exist: ${PYDS_WHEEL}"
    "${VENV_DIR}/bin/python" -m pip install "${PYDS_WHEEL}"
else
    "${VENV_DIR}/bin/python" -m pip install "${PYDS_WHEEL_URL}" || fail "NVIDIA pyds wheel install failed; set PYDS_WHEEL to a downloaded wheel"
fi

"${VENV_DIR}/bin/python" -c 'import pyds; print("pyds import: ok")'
