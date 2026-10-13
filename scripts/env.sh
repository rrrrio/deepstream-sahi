#!/usr/bin/env bash

if [ -z "${BASH_SOURCE[0]:-}" ]; then
    printf '%s\n' 'env.sh must be sourced from bash' >&2
    return 1 2>/dev/null || exit 1
fi
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
DEEPSTREAM_SAHI_ROOT=$(cd -- "${SCRIPT_DIR}/.." && pwd)
export DEEPSTREAM_SAHI_ROOT
export VIRTUAL_ENV="${DEEPSTREAM_SAHI_ROOT}/.venv"
export PATH="${VIRTUAL_ENV}/bin:${PATH}"
export PYTHONPATH="${DEEPSTREAM_SAHI_ROOT}${PYTHONPATH:+:${PYTHONPATH}}"
export LOCAL_DIR="${DEEPSTREAM_SAHI_ROOT}/.local"
export GST_PLUGIN_PATH="${LOCAL_DIR}/lib/gstreamer-1.0${GST_PLUGIN_PATH:+:${GST_PLUGIN_PATH}}"
export ROS_LOG_DIR="${DEEPSTREAM_SAHI_ROOT}/.state/ros-logs"
mkdir -p "${ROS_LOG_DIR}"

# ROS setup scripts read optional variables that may be unset under `set -u`.
had_nounset=false
case $- in
    *u*) had_nounset=true; set +u ;;
esac
ros_setup_ready=false
if [ -f /opt/ros/humble/setup.bash ] && [ -f /opt/ros/humble/local_setup.bash ]; then
    # shellcheck disable=SC1091
    source /opt/ros/humble/setup.bash
    ros_setup_ready=true
elif [ -f /opt/ros/humble/setup.bash ]; then
    printf '%s\n' 'ROS 2 Humble setup is incomplete; run install_inplace.sh or source a complete ROS installation.' >&2
fi
# The ROS setup may restore shell options inherited from its caller.
set +u
if [ "${ros_setup_ready}" = true ] && [ -f "${DEEPSTREAM_SAHI_ROOT}/.colcon/install/setup.bash" ]; then
    # shellcheck disable=SC1091
    source "${DEEPSTREAM_SAHI_ROOT}/.colcon/install/setup.bash"
fi
set +u
if [ "${had_nounset}" = true ]; then
    set -u
fi

# ROS console scripts use the system interpreter, while NVIDIA pyds is
# installed in the repository virtual environment.
if [ -x "${VIRTUAL_ENV}/bin/python" ]; then
    VENV_SITE_PACKAGES=$("${VIRTUAL_ENV}/bin/python" -c \
        'import site; print(site.getsitepackages()[0])')
    export PYTHONPATH="${VENV_SITE_PACKAGES}:${PYTHONPATH}"
fi
printf 'DeepStream SAHI environment loaded from %s\n' "${DEEPSTREAM_SAHI_ROOT}" >&2
