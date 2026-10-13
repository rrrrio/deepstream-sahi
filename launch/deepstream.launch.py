"""Launch the DeepStream node from a ROS parameter profile."""

import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


_OPTIONAL_OVERRIDES = (
    "display",
    "publish_detections",
    "publish_annotated",
    "publish_best_target",
)


def _jetson_gpu_devices_available():
    """Return whether the Jetson device nodes required by DeepStream are mapped."""
    return all(
        os.path.exists(path) for path in ("/dev/nvmap", "/dev/nvhost-gpu")
    )


def _parse_bool(value):
    normalized = value.strip().lower()
    if normalized in ("true", "1", "yes", "on"):
        return True
    if normalized in ("false", "0", "no", "off"):
        return False
    raise ValueError(f"Boolean launch override has invalid value: {value!r}")


def _create_node(context):
    """Apply launch overrides only when the caller supplied a non-empty value."""
    parameters = [LaunchConfiguration("params_file")]
    overrides = {}
    for name in _OPTIONAL_OVERRIDES:
        value = LaunchConfiguration(name).perform(context).strip()
        if value:
            overrides[name] = _parse_bool(value)
    if overrides:
        parameters.append(overrides)

    return [
        Node(
            package="deepstream",
            executable="deepstream_node",
            name="deepstream_node",
            output="screen",
            parameters=parameters,
        )
    ]


def _preflight_runtime(context):
    """Reject launches that cannot produce NVMM frames before starting an OSD."""
    enabled = _parse_bool(
        LaunchConfiguration("runtime_preflight").perform(context)
    )
    if not enabled or _jetson_gpu_devices_available():
        return []

    raise RuntimeError(
        "DeepStream cannot start because this container is missing Jetson GPU "
        "device mappings (/dev/nvmap and /dev/nvhost-gpu). Recreate the "
        "container with the NVIDIA runtime, then run "
        "./scripts/check_runtime.sh --require-gpu before launching a pipeline; "
        "use --require-display as well for OSD or the headless SAHI profile. "
        "Set runtime_preflight:=false only when using a non-Jetson runtime."
    )


def generate_launch_description():
    default_params = PathJoinSubstitution(
        [FindPackageShare("deepstream"), "config", "sim_3cam_headless.yaml"]
    )
    return LaunchDescription(
        [
            DeclareLaunchArgument(
                "params_file",
                default_value=default_params,
                description="ROS parameter YAML for the DeepStream node",
            ),
            DeclareLaunchArgument("display", default_value=""),
            DeclareLaunchArgument("publish_detections", default_value=""),
            DeclareLaunchArgument("publish_annotated", default_value=""),
            DeclareLaunchArgument("publish_best_target", default_value=""),
            DeclareLaunchArgument("runtime_preflight", default_value="true"),
            OpaqueFunction(function=_preflight_runtime),
            OpaqueFunction(function=_create_node),
        ]
    )
