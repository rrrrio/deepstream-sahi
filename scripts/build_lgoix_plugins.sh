#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
DEPS_DIR="${REPO_ROOT}/.deps"
LOCAL_DIR="${REPO_ROOT}/.local"
LGOIX_SOURCE_DIR="${LGOIX_SOURCE_DIR:-/workspace/deepstream-sahi-lgoix}"
LGOIX_BUILD_SOURCE_DIR="${DEPS_DIR}/lgoix-ds7-source"
LGOIX_COMMIT="114a5c66075e842b1a1dd23b40ef5541fc7bcb7a"
CUDA_DIR=${CUDA_DIR:-${CUDA_HOME:-/usr/local/cuda}}
DEEPSTREAM_DIR=${DEEPSTREAM_DIR:-/opt/nvidia/deepstream/deepstream}

fail() { printf 'build_lgoix_plugins: %s\n' "$1" >&2; exit 1; }
[ -w "${REPO_ROOT}" ] || fail "repository root is not writable: ${REPO_ROOT}"
[ -f "${CUDA_DIR}/include/cuda_runtime_api.h" ] || fail "CUDA runtime headers are missing: ${CUDA_DIR}/include/cuda_runtime_api.h"
[ -f "${CUDA_DIR}/include/cuda.h" ] || fail "CUDA driver headers are missing: ${CUDA_DIR}/include/cuda.h"
if [ ! -f "${CUDA_DIR}/include/host_config.h" ] \
    && [ ! -f "${CUDA_DIR}/include/crt/host_config.h" ]; then
    fail "CUDA CRT headers are missing under ${CUDA_DIR}/include"
fi
[ -d "${DEEPSTREAM_DIR}/sources/includes" ] || fail "DeepStream headers are missing: ${DEEPSTREAM_DIR}/sources/includes"
command -v git >/dev/null 2>&1 || fail "git is required"
command -v g++ >/dev/null 2>&1 || fail "g++ is required"
command -v pkg-config >/dev/null 2>&1 || fail "pkg-config is required"
pkg-config --exists gstreamer-1.0 gstreamer-base-1.0 gstreamer-video-1.0 || fail "GStreamer development packages are required"

mkdir -p "${DEPS_DIR}" "${LOCAL_DIR}/lib/gstreamer-1.0"
[ -d "${LGOIX_SOURCE_DIR}/.git" ] || fail "lgoix DS7 source is not a Git checkout: ${LGOIX_SOURCE_DIR}"
source_commit=$(git -C "${LGOIX_SOURCE_DIR}" rev-parse HEAD)
[ "${source_commit}" = "${LGOIX_COMMIT}" ] || fail "lgoix source must be pinned to ${LGOIX_COMMIT}, found ${source_commit}"

SOURCE_ROOT="${LGOIX_BUILD_SOURCE_DIR}"
if [ ! -d "${SOURCE_ROOT}/gst-plugins" ]; then
    cp -a "${LGOIX_SOURCE_DIR}/deepstream_source" "${SOURCE_ROOT}"
fi
PLUGIN_ROOT="${SOURCE_ROOT}/gst-plugins"
PREPROCESS_DIR="${PLUGIN_ROOT}/gst-nvsahipreprocess"
POSTPROCESS_DIR="${PLUGIN_ROOT}/gst-nvsahipostprocess"
[ -f "${PREPROCESS_DIR}/Makefile" ] || fail "missing lgoix preprocess Makefile"
[ -f "${POSTPROCESS_DIR}/Makefile" ] || fail "missing lgoix postprocess Makefile"

if [ ! -e "${SOURCE_ROOT}/includes" ]; then
    ln -s "${DEEPSTREAM_DIR}/sources/includes" "${SOURCE_ROOT}/includes"
fi
if [ ! -e "${PLUGIN_ROOT}/gst-nvdspreprocess" ]; then
    ln -s "${DEEPSTREAM_DIR}/sources/gst-plugins/gst-nvdspreprocess" \
        "${PLUGIN_ROOT}/gst-nvdspreprocess"
fi

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
    [ -f "${LOCAL_DIR}/lib/gstreamer-1.0/libnvdsgst_${plugin}.so" ] || fail "expected plugin was not installed: ${plugin}"
done
printf 'Built SAHI plugins at %s\n' "${LOCAL_DIR}/lib/gstreamer-1.0"
