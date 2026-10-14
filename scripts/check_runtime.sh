#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: check_runtime.sh [--require-gpu] [--require-display] [--require-argus]

Without options, report all DeepStream runtime prerequisites without failing for
optional display and CSI resources. Each --require-* option makes its missing
resources a failure.
EOF
}

require_gpu=false
require_display=false
require_argus=false

while [ "$#" -gt 0 ]; do
    case "$1" in
        --require-gpu) require_gpu=true ;;
        --require-display) require_display=true ;;
        --require-argus) require_argus=true ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'check_runtime: unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

failed=false
report() {
    local label=$1
    local state=$2
    printf '%-20s %s\n' "${label}:" "${state}"
}

gpu_ready=true
deepstream_dir=${DEEPSTREAM_DIR:-/opt/nvidia/deepstream/deepstream}
deepstream_ready=false
if [ -d "${deepstream_dir}" ]; then
    report deepstream "present (${deepstream_dir})"
    deepstream_ready=true
else
    report deepstream "missing (${deepstream_dir})"
fi

for path in /dev/nvmap /dev/nvhost-gpu; do
    if [ ! -e "${path}" ]; then
        report "${path}" "missing"
        gpu_ready=false
    else
        report "${path}" "present"
    fi
done

if [ -e /dev/dri/card0 ]; then
    report /dev/dri/card0 present
else
    report /dev/dri/card0 missing
fi

argus_plugin_ready=false
if command -v gst-inspect-1.0 >/dev/null 2>&1 \
    && gst-inspect-1.0 nvarguscamerasrc >/dev/null 2>&1; then
    report nvarguscamerasrc present
    argus_plugin_ready=true
else
    report nvarguscamerasrc missing
fi

argus_ready=false
if [ -S /tmp/argus_socket ]; then
    report /tmp/argus_socket present
    argus_ready=true
else
    report /tmp/argus_socket missing
fi

display_socket=""
if [ -n "${DISPLAY:-}" ]; then
    display_number=${DISPLAY#*:}
    display_number=${display_number%%.*}
    if [ -S "/tmp/.X11-unix/X${display_number}" ]; then
        display_socket="/tmp/.X11-unix/X${display_number}"
    fi
fi
if [ -n "${display_socket}" ]; then
    report x11_socket "present (${display_socket})"
    display_ready=true
else
    report x11_socket "missing (DISPLAY=${DISPLAY:-unset})"
    display_ready=false
fi

if [ "${require_gpu}" = true ] && { [ "${deepstream_ready}" = false ] || [ "${gpu_ready}" = false ]; }; then
    printf '%s\n' 'check_runtime: DeepStream and GPU device mappings are required.' >&2
    failed=true
fi
if [ "${require_argus}" = true ] && { [ "${deepstream_ready}" = false ] || [ "${gpu_ready}" = false ] || [ "${argus_plugin_ready}" = false ] || [ "${argus_ready}" = false ]; }; then
    printf '%s\n' 'check_runtime: CSI requires DeepStream, GPU devices, nvarguscamerasrc, and /tmp/argus_socket.' >&2
    failed=true
fi
if [ "${require_display}" = true ] && { [ "${deepstream_ready}" = false ] || [ "${gpu_ready}" = false ] || [ "${display_ready}" = false ]; }; then
    printf '%s\n' 'check_runtime: OSD display requires DeepStream, GPU device mappings, and an X11 socket.' >&2
    failed=true
fi

if [ "${failed}" = true ]; then
    exit 1
fi
