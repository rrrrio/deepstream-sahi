#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: probe_argus_camera.sh SENSOR_ID [FRAME_COUNT]

Capture a fixed number of frames from one Argus sensor and verify the
GStreamer buffer trace. The default frame count is 120.
EOF
}

fail() {
    printf 'Argus probe failed: %s\n' "$1" >&2
    exit 1
}

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
    usage >&2
    exit 2
fi

sensor_id=$1
frame_count=${2:-120}
[[ "${sensor_id}" =~ ^[0-9]+$ ]] || fail "sensor ID must be a non-negative integer"
[[ "${frame_count}" =~ ^[1-9][0-9]*$ ]] || fail "frame count must be positive"

command -v gst-launch-1.0 >/dev/null 2>&1 \
    || fail "gst-launch-1.0 is required"

output=$(mktemp)
trap 'rm -f "${output}"' EXIT

set +e
GST_DEBUG_NO_COLOR=1 \
GST_TRACERS=stats \
GST_DEBUG=GST_TRACER:7 \
gst-launch-1.0 -e \
    nvarguscamerasrc sensor-id="${sensor_id}" num-buffers="${frame_count}" \
    ! 'video/x-raw(memory:NVMM),format=NV12,width=1920,height=1080,framerate=30/1' \
    ! fakesink sync=false >"${output}" 2>&1
pipeline_status=$?
set -e

if [ "${pipeline_status}" -ne 0 ]; then
    cat "${output}"
    fail "gst-launch returned ${pipeline_status}"
fi

if grep -Eq \
    'No cameras available|Failure to open Camera Kernel Node|Camera node not detected|Connecting to nvargus-daemon failed|Cannot create camera provider|Failed to create CameraProvider' \
    "${output}"; then
    cat "${output}"
    fail "Argus reported a camera or daemon error"
fi

# The tracer records each buffer at multiple pads; unique PTS values represent frames.
buffer_count=$({ grep 'GST_TRACER :0:: buffer,' "${output}" || true; } \
    | sed -n 's/.*buffer-pts=(guint64)\([0-9][0-9]*\).*/\1/p' \
    | sort -u \
    | wc -l)
if [ "${buffer_count}" -ne "${frame_count}" ]; then
    fail "observed ${buffer_count} buffers, expected ${frame_count}"
fi

printf 'Argus sensor %s passed: %s buffers\n' "${sensor_id}" "${buffer_count}"
