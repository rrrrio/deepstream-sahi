#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
LOCAL_DIR="${REPO_ROOT}/.local"
PREPROCESS_DIR="${REPO_ROOT}/plugins/nvsahipreprocess"
POSTPROCESS_DIR="${REPO_ROOT}/plugins/nvsahipostprocess"
CUDA_DIR=${CUDA_DIR:-${CUDA_HOME:-/usr/local/cuda}}
DEEPSTREAM_DIR=${DEEPSTREAM_DIR:-/opt/nvidia/deepstream/deepstream}

fail() { printf 'build_sahi_plugins: %s\n' "$1" >&2; exit 1; }

[ -w "${REPO_ROOT}" ] || fail "repository root is not writable: ${REPO_ROOT}"
[ -f "${CUDA_DIR}/include/cuda_runtime_api.h" ] || fail "CUDA runtime headers are missing: ${CUDA_DIR}/include/cuda_runtime_api.h"
[ -f "${CUDA_DIR}/include/cuda.h" ] || fail "CUDA driver headers are missing: ${CUDA_DIR}/include/cuda.h"
if [ ! -f "${CUDA_DIR}/include/host_config.h" ] \
    && [ ! -f "${CUDA_DIR}/include/crt/host_config.h" ]; then
    fail "CUDA CRT headers are missing under ${CUDA_DIR}/include"
fi
[ -d "${DEEPSTREAM_DIR}/sources/includes" ] || fail "DeepStream headers are missing: ${DEEPSTREAM_DIR}/sources/includes"
[ -d "${DEEPSTREAM_DIR}/sources/gst-plugins/gst-nvdspreprocess/include" ] \
    || fail "DeepStream nvdspreprocess headers are missing"
command -v g++ >/dev/null 2>&1 || fail "g++ is required"
command -v pkg-config >/dev/null 2>&1 || fail "pkg-config is required"
pkg-config --exists gstreamer-1.0 gstreamer-base-1.0 gstreamer-video-1.0 \
    || fail "GStreamer development packages are required"

[ -f "${PREPROCESS_DIR}/Makefile" ] || fail "missing native SAHI preprocess Makefile"
[ -f "${POSTPROCESS_DIR}/Makefile" ] || fail "missing native SAHI postprocess Makefile"

mkdir -p "${LOCAL_DIR}/lib/gstreamer-1.0"
CUDA_VER=$(basename "$(readlink -f "${CUDA_DIR}")" | sed 's/^cuda-//')

for plugin_dir in "${PREPROCESS_DIR}" "${POSTPROCESS_DIR}"; do
    make -C "${plugin_dir}" -j"${JOBS:-$(nproc)}" \
        CUDA_VER="${CUDA_VER}" \
        DS_ROOT="${DEEPSTREAM_DIR}" \
        GST_INSTALL_DIR="${LOCAL_DIR}/lib/gstreamer-1.0" \
        LIB_INSTALL_DIR="${DEEPSTREAM_DIR}/lib"
    make -C "${plugin_dir}" install \
        CUDA_VER="${CUDA_VER}" \
        DS_ROOT="${DEEPSTREAM_DIR}" \
        GST_INSTALL_DIR="${LOCAL_DIR}/lib/gstreamer-1.0" \
        LIB_INSTALL_DIR="${DEEPSTREAM_DIR}/lib"
done

for plugin in sahipreprocess sahipostprocess; do
    [ -f "${LOCAL_DIR}/lib/gstreamer-1.0/libnvdsgst_${plugin}.so" ] \
        || fail "expected plugin was not installed: ${plugin}"
done
printf 'Built native SAHI plugins at %s\n' "${LOCAL_DIR}/lib/gstreamer-1.0"
