#!/usr/bin/env python3
"""
File: node.py
Description: ROS2 entry node for running DeepStream in base or SAHI mode.
Author: Yuhao Ding
"""

import threading
from typing import Generator, Optional

import gi
import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from geometry_msgs.msg import Point
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from sensor_msgs.msg import Image
from std_msgs.msg import String

gi.require_version("Gst", "1.0")
from gi.repository import Gst

import pyds

from .config_validator import validate_runtime_configs
from .msg_adapter import (
    HAS_VISION_MSGS,
    Detection2DArray,
    build_best_target_msg,
    build_detection_array_msg,
    extract_detections,
)
from .pipeline_base import build_pipeline as build_base_pipeline
from .pipeline_common import MergeConfig, PipelineBuildParams, merge_frame_detections
from .pipeline_sahi import build_pipeline as build_sahi_pipeline


class DeepStreamNode(Node):
    def __init__(self):
        super().__init__("deepstream_node")

        self._pipeline = None
        self._pgie = None
        self._appsink = None
        self._bus = None
        self._bus_thread = None
        self._running = False
        self._merge_cfg = None

        self._declare_parameters()
        self._resolve_runtime_config()
        self._setup_publishers()
        self._start_pipeline()

    def _declare_parameters(self):
        self.declare_parameter("mode", "base")
        self.declare_parameter("source", "")
        self.declare_parameter("infer_config", "")
        self.declare_parameter("preprocess_config", "")
        self.declare_parameter("tracker_enabled", False)
        self.declare_parameter("tracker_config", "")
        self.declare_parameter("display", True)
        self.declare_parameter("publish_annotated", False)
        self.declare_parameter("publish_detections", True)
        self.declare_parameter("publish_best_target", True)
        self.declare_parameter("merge_enabled", True)
        self.declare_parameter("merge_metric", "ios")
        self.declare_parameter("merge_threshold", 0.5)
        self.declare_parameter("det_interval", -1)
        self.declare_parameter("pgie_batch_size", 0)
        self.declare_parameter("width", 1920)
        self.declare_parameter("height", 1080)
        self.declare_parameter("tiled_width", 1280)
        self.declare_parameter("tiled_height", 720)
        self.declare_parameter("batched_push_timeout", 40000)

    def _resolve_runtime_config(self):
        self.mode = str(self.get_parameter("mode").value).strip().lower()
        if self.mode not in ("base", "sahi"):
            raise ValueError("mode must be one of: base, sahi")

        self.source = str(self.get_parameter("source").value).strip()
        if not self.source:
            raise ValueError("source parameter is required")

        share_dir = get_package_share_directory("deepstream")
        default_infer = (
            f"{share_dir}/config/config_infer_primary_yolo11_sahi.txt"
            if self.mode == "sahi"
            else f"{share_dir}/config/config_infer_primary_yolo11.txt"
        )
        default_preprocess = f"{share_dir}/config/config_preprocess_sahi.txt"
        default_tracker = f"{share_dir}/config/deepstream_tracker_config.txt"

        infer_config = str(self.get_parameter("infer_config").value).strip() or default_infer
        preprocess_config = str(self.get_parameter("preprocess_config").value).strip() or default_preprocess
        tracker_config = str(self.get_parameter("tracker_config").value).strip() or default_tracker

        self.tracker_enabled = bool(self.get_parameter("tracker_enabled").value)
        self.display = bool(self.get_parameter("display").value)
        self.publish_annotated = bool(self.get_parameter("publish_annotated").value)
        self.publish_detections = bool(self.get_parameter("publish_detections").value)
        self.publish_best_target = bool(self.get_parameter("publish_best_target").value)
        merge_enabled = bool(self.get_parameter("merge_enabled").value)
        merge_metric = str(self.get_parameter("merge_metric").value).strip().lower()
        merge_threshold = float(self.get_parameter("merge_threshold").value)

        if merge_metric not in ("ios", "iou"):
            raise ValueError("merge_metric must be one of: ios, iou")
        if merge_threshold <= 0.0:
            raise ValueError("merge_threshold must be > 0.0")

        self._merge_cfg = MergeConfig(
            enabled=(self.mode == "sahi") and merge_enabled,
            metric=merge_metric,
            threshold=merge_threshold,
            class_agnostic=False,
        )

        self.pipeline_params = PipelineBuildParams(
            source=self.source,
            infer_config=infer_config,
            preprocess_config=preprocess_config if self.mode == "sahi" else None,
            tracker_enabled=self.tracker_enabled,
            tracker_config=tracker_config,
            display=self.display,
            publish_annotated=self.publish_annotated,
            width=int(self.get_parameter("width").value),
            height=int(self.get_parameter("height").value),
            tiled_width=int(self.get_parameter("tiled_width").value),
            tiled_height=int(self.get_parameter("tiled_height").value),
            batched_push_timeout=int(self.get_parameter("batched_push_timeout").value),
            pgie_batch_size=int(self.get_parameter("pgie_batch_size").value),
            det_interval=int(self.get_parameter("det_interval").value),
        )

        validate_runtime_configs(
            mode=self.mode,
            infer_config=self.pipeline_params.infer_config,
            preprocess_config=self.pipeline_params.preprocess_config,
            tracker_enabled=self.tracker_enabled,
            tracker_config=self.pipeline_params.tracker_config,
            logger=self.get_logger(),
        )

    def _setup_publishers(self):
        self.status_pub = self.create_publisher(String, "/deepstream/status", 10)
        self.detections_pub = None
        self.target_pub = None
        self.annotated_pub = None

        if self.publish_detections:
            if HAS_VISION_MSGS:
                self.detections_pub = self.create_publisher(Detection2DArray, "/deepstream/detections", 10)
            else:
                self.get_logger().warning("vision_msgs is not available. Disabling /deepstream/detections publisher.")
                self.publish_detections = False

        if self.publish_best_target:
            self.target_pub = self.create_publisher(Point, "/deepstream/target_pixel", 10)
        if self.publish_annotated:
            self.annotated_pub = self.create_publisher(Image, "/deepstream/image/annotated", 10)

        self._publish_status("initialized")

    def _publish_status(self, text: str):
        if not rclpy.ok():
            return
        msg = String()
        msg.data = text
        try:
            self.status_pub.publish(msg)
        except Exception:
            pass

    def _start_pipeline(self):
        self.get_logger().info(
            "Starting deepstream_node: "
            f"mode={self.mode}, source={self.source}, tracker={self.tracker_enabled}, "
            f"merge={self._merge_cfg.enabled}"
        )

        if self.mode == "sahi":
            result = build_sahi_pipeline(self.pipeline_params)
        else:
            result = build_base_pipeline(self.pipeline_params)

        self._pipeline = result.pipeline
        self._pgie = result.pgie
        self._appsink = result.appsink

        pgie_src_pad = self._pgie.get_static_pad("src")
        if not pgie_src_pad:
            raise RuntimeError("Unable to get PGIE src pad")
        pgie_src_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self._pgie_src_probe,
            None,
        )

        if self._appsink is not None and self.publish_annotated:
            self._appsink.connect("new-sample", self._on_new_sample)

        self._bus = self._pipeline.get_bus()

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start DeepStream pipeline")

        self._running = True
        self._bus_thread = threading.Thread(target=self._bus_loop, name="deepstream-bus", daemon=True)
        self._bus_thread.start()
        self._publish_status("running")

    def _iter_frames(self, batch_meta) -> Generator:
        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break
            yield frame_meta
            try:
                l_frame = l_frame.next
            except StopIteration:
                break

    def _pgie_src_probe(self, pad, info, user_data):
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            return Gst.PadProbeReturn.OK

        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        if not batch_meta:
            return Gst.PadProbeReturn.OK

        if self._merge_cfg.enabled:
            pyds.nvds_acquire_meta_lock(batch_meta)
            try:
                for frame_meta in self._iter_frames(batch_meta):
                    merge_frame_detections(frame_meta, self._merge_cfg)
            finally:
                pyds.nvds_release_meta_lock(batch_meta)

        stamp = self.get_clock().now().to_msg()
        for frame_meta in self._iter_frames(batch_meta):
            detections = extract_detections(frame_meta)
            frame_id = f"source_{int(frame_meta.source_id)}"

            if self.detections_pub is not None:
                det_msg = build_detection_array_msg(detections, stamp, frame_id)
                if det_msg is not None:
                    self.detections_pub.publish(det_msg)

            if self.target_pub is not None:
                target_msg = build_best_target_msg(detections)
                if target_msg is not None:
                    self.target_pub.publish(target_msg)

        return Gst.PadProbeReturn.OK

    def _on_new_sample(self, sink):
        sample = sink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        gst_buffer = sample.get_buffer()
        caps = sample.get_caps()
        if gst_buffer is None or caps is None:
            return Gst.FlowReturn.ERROR

        structure = caps.get_structure(0)
        width = int(structure.get_value("width"))
        height = int(structure.get_value("height"))

        success, map_info = gst_buffer.map(Gst.MapFlags.READ)
        if not success:
            return Gst.FlowReturn.ERROR

        try:
            frame = np.frombuffer(map_info.data, dtype=np.uint8)
            expected = width * height * 3
            if frame.size < expected:
                return Gst.FlowReturn.OK
            frame = frame[:expected]

            msg = Image()
            msg.header.stamp = self.get_clock().now().to_msg()
            msg.header.frame_id = "deepstream_camera"
            msg.height = height
            msg.width = width
            msg.encoding = "bgr8"
            msg.is_bigendian = 0
            msg.step = width * 3
            msg.data = frame.tobytes()
            if self.annotated_pub is not None:
                self.annotated_pub.publish(msg)
        finally:
            gst_buffer.unmap(map_info)

        return Gst.FlowReturn.OK

    def _bus_loop(self):
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.WARNING
        while self._running and self._bus is not None:
            message = self._bus.timed_pop_filtered(100 * Gst.MSECOND, mask)
            if message is None:
                continue

            mtype = message.type
            if mtype == Gst.MessageType.ERROR:
                err, debug = message.parse_error()
                self.get_logger().error(f"GStreamer error: {err}")
                if debug:
                    self.get_logger().error(f"GStreamer debug: {debug}")
                self._publish_status(f"error: {err}")
                self._running = False
            elif mtype == Gst.MessageType.WARNING:
                warn, debug = message.parse_warning()
                self.get_logger().warning(f"GStreamer warning: {warn}")
                if debug:
                    self.get_logger().warning(f"GStreamer debug: {debug}")
            elif mtype == Gst.MessageType.EOS:
                self.get_logger().info("Received EOS from pipeline.")
                self._publish_status("eos")
                self._running = False

    def stop_pipeline(self):
        self._running = False
        if self._pipeline is not None:
            try:
                self._pipeline.send_event(Gst.Event.new_eos())
            except Exception:
                pass
            self._pipeline.set_state(Gst.State.NULL)

        if (
            self._bus_thread is not None
            and self._bus_thread.is_alive()
            and threading.current_thread() is not self._bus_thread
        ):
            self._bus_thread.join(timeout=1.0)

        self._bus_thread = None
        self._bus = None
        self._pipeline = None
        self._pgie = None
        self._appsink = None
        self._publish_status("stopped")

    def destroy_node(self):
        self.stop_pipeline()
        super().destroy_node()


def main(args=None):
    rclpy.init(args=args)
    node: Optional[DeepStreamNode] = None
    try:
        node = DeepStreamNode()
        rclpy.spin(node)
    except (KeyboardInterrupt, ExternalShutdownException):
        pass
    except Exception as exc:
        if node is not None:
            node.get_logger().error(f"Fatal error: {exc}")
        else:
            print(f"Fatal error: {exc}")
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
