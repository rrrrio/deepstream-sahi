#!/usr/bin/env bash

set -e

had_nounset=false
case $- in
    *u*) had_nounset=true; set +u ;;
esac

if [ -f /opt/ros/${ROS_DISTRO:-humble}/setup.bash ]; then
    # ROS setup scripts expect variables that may be unset by strict shells.
    source "/opt/ros/${ROS_DISTRO:-humble}/setup.bash"
fi

if [ "${had_nounset}" = true ]; then
    set -u
fi

export DEEPSTREAM_DIR="${DEEPSTREAM_DIR:-/opt/nvidia/deepstream/deepstream}"
printf 'DeepStream SAHI image environment loaded (ROS_DISTRO=%s)\n' \
    "${ROS_DISTRO:-humble}" >&2
