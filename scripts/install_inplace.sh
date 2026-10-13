#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
VENV_DIR="${REPO_ROOT}/.venv"
LOCAL_DIR="${REPO_ROOT}/.local"
DEPS_DIR="${REPO_ROOT}/.deps"
STATE_DIR="${REPO_ROOT}/.state"

fail() { printf 'install_inplace: %s\n' "$1" >&2; exit 1; }
[ -d "${REPO_ROOT}" ] && [ -w "${REPO_ROOT}" ] || fail "repository root is not writable: ${REPO_ROOT}"
[ -w / ] || fail "container root filesystem is not writable; apt-based installation cannot run here; recreate the container with a writable root filesystem or use an image that already contains the dependencies"
[ "$(id -u)" -eq 0 ] || fail "apt installation requires root; run this script as root"
command -v apt-get >/dev/null 2>&1 || fail "apt-get is required"
command -v python3 >/dev/null 2>&1 || fail "python3 is required before installation"

mkdir -p "${LOCAL_DIR}" "${DEPS_DIR}" "${STATE_DIR}"
export PIP_CACHE_DIR="${DEPS_DIR}/pip-cache"

"${SCRIPT_DIR}/capture_environment.sh"

apt-get update

required_packages=(
    build-essential cmake pkg-config git-lfs
    cuda-cudart-dev-12-6 cuda-crt-12-6
    libglib2.0-dev libgstreamer1.0-dev
    libgstreamer-plugins-base1.0-dev gstreamer1.0-tools
    libnvinfer-dev libnvonnxparsers-dev
    python3-dev python3-gst-1.0 python3-numpy python3-onnx python3-pytest
    python3-venv python3-colcon-common-extensions
    ros-humble-ros-base ros-humble-ros2cli ros-humble-ros2launch
    ros-humble-launch-ros ros-humble-vision-msgs
)
missing_packages=()
for package in "${required_packages[@]}"; do
    if ! apt-cache policy "${package}" | awk '$1 == "Candidate:" && $2 != "(none)" { found = 1 } END { exit !found }'; then
        missing_packages+=("${package}")
    fi
done
if [ "${#missing_packages[@]}" -gt 0 ]; then
    fail "required apt packages are unavailable: ${missing_packages[*]}; configure the ROS 2 Humble and JetPack-matched NVIDIA repositories, then rerun"
fi

DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    build-essential cmake pkg-config git-lfs \
    cuda-cudart-dev-12-6 cuda-crt-12-6 \
    libglib2.0-dev libgstreamer1.0-dev \
    libgstreamer-plugins-base1.0-dev gstreamer1.0-tools \
    libnvinfer-dev libnvonnxparsers-dev \
    python3-dev python3-gst-1.0 python3-numpy python3-onnx python3-pytest \
    python3-venv python3-colcon-common-extensions \
    ros-humble-ros-base ros-humble-ros2cli ros-humble-ros2launch \
    ros-humble-launch-ros ros-humble-vision-msgs

if [ ! -x "${VENV_DIR}/bin/python" ]; then
    python3 -m venv --system-site-packages "${VENV_DIR}"
fi
"${VENV_DIR}/bin/python" -m pip install --upgrade pip 'setuptools<80' wheel
"${SCRIPT_DIR}/install_pyds.sh"

"${VENV_DIR}/bin/python" - <<'PY'
import numpy
import pyds
print(f"numpy={numpy.__version__}")
print(f"pyds={getattr(pyds, '__version__', 'installed')}")
PY

printf 'In-place dependencies installed under %s\n' "${REPO_ROOT}"
