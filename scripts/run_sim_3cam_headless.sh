#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)

"${SCRIPT_DIR}/check_runtime.sh" --require-gpu

# shellcheck disable=SC1091
source "${SCRIPT_DIR}/env.sh"
if [ ! -f "${REPO_ROOT}/.colcon/install/setup.bash" ]; then
    printf '%s\n' 'run_sim_3cam_headless: ROS package is not built; run colcon first.' >&2
    exit 1
fi
exec env EGL_PLATFORM="${SIM_EGL_PLATFORM:-surfaceless}" \
    ros2 launch deepstream deepstream.launch.py "$@"
