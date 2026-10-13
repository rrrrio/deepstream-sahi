#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
    cat <<'EOF'
Usage: host_preflight_simulation.sh [--require-display] [workspace]

Run this on the Jetson Docker host before starting the three-file simulation.
It checks Docker, the device nodes needed by DeepStream file decoding/inference,
and the workspace that will be mounted at /workspace. CSI/Argus is deliberately
not required. --require-display additionally checks the X11 socket for OSD.
EOF
}

require_display=false
workspace="${WORKSPACE:-$HOME/wp}"
while [ "$#" -gt 0 ]; do
    case "$1" in
        --require-display) require_display=true ;;
        -h|--help) usage; exit 0 ;;
        -*) printf 'host_preflight_simulation: unknown option: %s\n' "$1" >&2; exit 2 ;;
        *) workspace=$1 ;;
    esac
    shift
done

failed=false
report() {
    printf '%-24s %s\n' "$1:" "$2"
}

if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    runtimes=$(docker info --format '{{json .Runtimes}}')
    if [[ "${runtimes}" == *'"nvidia"'* ]]; then
        report docker "ready (nvidia runtime)"
    else
        report docker "nvidia runtime missing"
        failed=true
    fi
else
    report docker unavailable
    failed=true
fi

for path in /dev/nvmap /dev/nvhost-gpu; do
    if [ -e "${path}" ]; then
        report "${path}" present
    else
        report "${path}" missing
        failed=true
    fi
done

if [ -r "${workspace}" ] && [ -d "${workspace}/deepstream-sahi" ]; then
    report workspace "${workspace}"
else
    report workspace "missing ${workspace}/deepstream-sahi"
    failed=true
fi

if [ "${require_display}" = true ]; then
    display_number=${DISPLAY:-:1}
    display_number=${display_number#*:}
    display_number=${display_number%%.*}
    socket="/tmp/.X11-unix/X${display_number}"
    if [ -S "${socket}" ]; then
        report x11_socket "present (${socket})"
    else
        report x11_socket "missing (${socket})"
        failed=true
    fi
fi

if [ "${failed}" = true ]; then
    printf '%s\n' 'host_preflight_simulation: simulation prerequisites are not ready.' >&2
    exit 1
fi
printf '%s\n' 'host_preflight_simulation: simulation prerequisites are ready.'
