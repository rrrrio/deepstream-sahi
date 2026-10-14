#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)

IMAGE=${IMAGE:-deepstream-sahi:7.1-env}
NAME=${NAME:-deepstream-sahi-7.1}
WORKSPACE=${WORKSPACE:-${REPO_ROOT}}
DISPLAY_VALUE=${DISPLAY_VALUE:-${DISPLAY:-}}
EGL_PLATFORM_VALUE=${EGL_PLATFORM:-surfaceless}
X11_SOCKET_DIR=${X11_SOCKET_DIR:-/tmp/.X11-unix}
MOUNT_ARGUS_SOCKET=${MOUNT_ARGUS_SOCKET:-0}
ARGUS_SOCKET=${ARGUS_SOCKET:-/tmp/argus_socket}
NVIDIA_DRIVER_CAPABILITIES_VALUE=${NVIDIA_DRIVER_CAPABILITIES:-all}
EGL_VENDOR_LIBRARY_FILENAMES_VALUE=${__EGL_VENDOR_LIBRARY_FILENAMES:-/usr/share/glvnd/egl_vendor.d/10_nvidia.json}

fail() {
    printf 'run_docker: %s\n' "$1" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: ./docker/run_docker.sh

The default starts a headless container with surfaceless EGL. For an OSD
window, set DISPLAY_VALUE to the host display and EGL_PLATFORM=x11.

Environment overrides:
  IMAGE                image tag (default: deepstream-sahi:7.1-env)
  NAME                 container name (default: deepstream-sahi-7.1)
  WORKSPACE            checkout to mount (default: repository root)
  DISPLAY_VALUE       host X display, required for EGL_PLATFORM=x11
  EGL_PLATFORM        surfaceless or x11 (default: surfaceless)
  MOUNT_ARGUS_SOCKET   set to 1 to mount ARGUS_SOCKET (default: 0)
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
    usage
    exit 0
fi

[ -d "${WORKSPACE}" ] || fail "workspace does not exist: ${WORKSPACE}"
docker image inspect "${IMAGE}" >/dev/null 2>&1 \
    || fail "image does not exist: ${IMAGE}; build it with docker/Dockerfile first"
docker container inspect "${NAME}" >/dev/null 2>&1 \
    && fail "container already exists: ${NAME}; choose NAME or remove that explicit container"

case "${EGL_PLATFORM_VALUE}" in
    surfaceless)
        display_args=()
        ;;
    x11)
        [ -n "${DISPLAY_VALUE}" ] \
            || fail "DISPLAY_VALUE is required when EGL_PLATFORM=x11"
        display_number=${DISPLAY_VALUE#*:}
        display_number=${display_number%%.*}
        [[ "${display_number}" =~ ^[0-9]+$ ]] \
            || fail "DISPLAY_VALUE must use :<display-number>, got ${DISPLAY_VALUE}"
        [ -S "${X11_SOCKET_DIR}/X${display_number}" ] \
            || fail "missing X11 socket: ${X11_SOCKET_DIR}/X${display_number}"
        command -v xhost >/dev/null 2>&1 \
            || fail "xhost is required for EGL_PLATFORM=x11"
        DISPLAY="${DISPLAY_VALUE}" xhost +SI:localuser:root >/dev/null \
            || fail "cannot authorize container root on ${DISPLAY_VALUE}"
        cleanup_xhost() {
            DISPLAY="${DISPLAY_VALUE}" xhost -SI:localuser:root >/dev/null 2>&1 || true
        }
        trap cleanup_xhost EXIT
        display_args=(
            -e "DISPLAY=${DISPLAY_VALUE}"
            -v "${X11_SOCKET_DIR}:/tmp/.X11-unix:rw"
        )
        ;;
    *)
        fail "EGL_PLATFORM must be surfaceless or x11, got ${EGL_PLATFORM_VALUE}"
        ;;
esac

argus_args=()
if [ "${MOUNT_ARGUS_SOCKET}" = 1 ]; then
    [ -S "${ARGUS_SOCKET}" ] || fail "missing Argus socket: ${ARGUS_SOCKET}"
    argus_args=(-v "${ARGUS_SOCKET}:${ARGUS_SOCKET}")
fi

docker run -it --rm \
    --name "${NAME}" \
    --runtime nvidia \
    --privileged \
    --ipc=host \
    --network=host \
    -v /dev:/dev \
    -e "EGL_PLATFORM=${EGL_PLATFORM_VALUE}" \
    -e "NVIDIA_DRIVER_CAPABILITIES=${NVIDIA_DRIVER_CAPABILITIES_VALUE}" \
    -e "__EGL_VENDOR_LIBRARY_FILENAMES=${EGL_VENDOR_LIBRARY_FILENAMES_VALUE}" \
    "${display_args[@]}" \
    "${argus_args[@]}" \
    -v "${WORKSPACE}:/workspace/deepstream-sahi" \
    -w /workspace/deepstream-sahi \
    "${IMAGE}" \
    bash -lc 'source /usr/local/bin/deepstream-sahi-env; exec bash'
