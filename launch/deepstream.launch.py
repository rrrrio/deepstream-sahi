"""
File: deepstream.launch.py
Description: Launch file for running the deepstream_node with configurable parameters.
Author: Yuhao Ding
"""

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("mode", default_value="base"),
            DeclareLaunchArgument("source", default_value=""),
            DeclareLaunchArgument("infer_config", default_value=""),
            DeclareLaunchArgument("preprocess_config", default_value=""),
            DeclareLaunchArgument("tracker_enabled", default_value="false"),
            DeclareLaunchArgument("tracker_config", default_value=""),
            DeclareLaunchArgument("display", default_value="true"),
            DeclareLaunchArgument("publish_annotated", default_value="false"),
            DeclareLaunchArgument("publish_detections", default_value="true"),
            DeclareLaunchArgument("publish_best_target", default_value="true"),
            DeclareLaunchArgument("merge_enabled", default_value="true"),
            DeclareLaunchArgument("merge_metric", default_value="ios"),
            DeclareLaunchArgument("merge_threshold", default_value="0.5"),
            DeclareLaunchArgument("det_interval", default_value="-1"),
            DeclareLaunchArgument("pgie_batch_size", default_value="0"),
            DeclareLaunchArgument("width", default_value="1920"),
            DeclareLaunchArgument("height", default_value="1080"),
            DeclareLaunchArgument("tiled_width", default_value="1280"),
            DeclareLaunchArgument("tiled_height", default_value="720"),
            DeclareLaunchArgument("batched_push_timeout", default_value="40000"),
            Node(
                package="deepstream",
                executable="deepstream_node",
                name="deepstream_node",
                output="screen",
                parameters=[
                    {
                        "mode": LaunchConfiguration("mode"),
                        "source": LaunchConfiguration("source"),
                        "infer_config": LaunchConfiguration("infer_config"),
                        "preprocess_config": LaunchConfiguration("preprocess_config"),
                        "tracker_enabled": LaunchConfiguration("tracker_enabled"),
                        "tracker_config": LaunchConfiguration("tracker_config"),
                        "display": LaunchConfiguration("display"),
                        "publish_annotated": LaunchConfiguration("publish_annotated"),
                        "publish_detections": LaunchConfiguration("publish_detections"),
                        "publish_best_target": LaunchConfiguration("publish_best_target"),
                        "merge_enabled": LaunchConfiguration("merge_enabled"),
                        "merge_metric": LaunchConfiguration("merge_metric"),
                        "merge_threshold": LaunchConfiguration("merge_threshold"),
                        "det_interval": LaunchConfiguration("det_interval"),
                        "pgie_batch_size": LaunchConfiguration("pgie_batch_size"),
                        "width": LaunchConfiguration("width"),
                        "height": LaunchConfiguration("height"),
                        "tiled_width": LaunchConfiguration("tiled_width"),
                        "tiled_height": LaunchConfiguration("tiled_height"),
                        "batched_push_timeout": LaunchConfiguration("batched_push_timeout"),
                    }
                ],
            ),
        ]
    )
