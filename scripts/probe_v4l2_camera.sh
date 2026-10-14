#!/usr/bin/env bash
set -eu

DEVICE="${1:-/dev/video0}"
FRAME_COUNT="${2:-3}"

if ! command -v gst-launch-1.0 >/dev/null 2>&1; then
    printf '%s\n' "gst-launch-1.0 is required" >&2
    exit 1
fi
if [ ! -e "$DEVICE" ]; then
    printf 'camera device does not exist: %s\n' "$DEVICE" >&2
    exit 1
fi

printf 'Camera: %s\n' "$DEVICE"
if command -v v4l2-ctl >/dev/null 2>&1; then
    v4l2-ctl --list-formats-ext --device="$DEVICE"
else
    printf '%s\n' 'v4l2-ctl is unavailable; skipping the host format listing.' >&2
fi

printf 'DeepStream caps probe (%s frames):\n' "$FRAME_COUNT"
probe_output=$(mktemp)
trap 'rm -f "$probe_output"' EXIT
if ! EGL_PLATFORM="${V4L2_EGL_PLATFORM:-surfaceless}" gst-launch-1.0 -e -v \
    nvstreammux name=mux batch-size=1 width=1920 height=1080 \
    live-source=1 batched-push-timeout=40000 ! fakesink sync=false \
    v4l2src device="$DEVICE" io-mode=2 num-buffers="$FRAME_COUNT" ! \
    'video/x-raw,format=UYVY,width=1920,height=1080,framerate=30/1' ! \
    nvvideoconvert ! 'video/x-raw(memory:NVMM),format=NV12' ! mux.sink_0 \
    >"$probe_output" 2>&1; then
    cat "$probe_output"
    printf '%s\n' 'V4L2 caps probe failed.' >&2
    exit 1
fi
cat "$probe_output"
if grep -Eq 'Signal lost|No input source was detected|not-negotiated' "$probe_output"; then
    printf '%s\n' 'V4L2 caps probe reported an invalid or missing input signal.' >&2
    exit 1
fi
