#!/usr/bin/env python3
"""
File: node.py
Description: ROS2 entry node for multi-source DeepStream and native SAHI mode.
Author: Yuhao Ding
"""

import os
import threading
from typing import Generator, Optional

import gi
import numpy as np
import rclpy
from ament_index_python.packages import get_package_share_directory
from builtin_interfaces.msg import Time
from geometry_msgs.msg import Point
from rclpy.executors import ExternalShutdownException
from rclpy.node import Node
from rclpy.parameter import Parameter
from sensor_msgs.msg import Image
from std_msgs.msg import String

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

import pyds  # noqa: E402

from .config_validator import (  # noqa: E402
    prepare_runtime_infer_config,
    prepare_runtime_preprocess_config,
    resolve_runtime_config_path,
    validate_runtime_configs,
)
from .msg_adapter import (  # noqa: E402
    HAS_VISION_MSGS,
    Detection2DArray,
    build_best_target_msg,
    build_detection_array_msg,
    extract_detections,
    update_osd_labels,
)
from .pipeline_base import build_pipeline as build_base_pipeline  # noqa: E402
from .metadata_utils import ntp_timestamp_parts  # noqa: E402
from .pipeline_common import (  # noqa: E402
    MergeConfig,
    PipelineBuildParams,
    compute_sahi_rois,
)
from .pipeline_sahi import build_pipeline as build_sahi_pipeline  # noqa: E402
from .source_utils import is_file_source, normalize_source_config  # noqa: E402


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
        self.declare_parameter("sources", Parameter.Type.STRING_ARRAY)
        self.declare_parameter("source_names", Parameter.Type.STRING_ARRAY)
        self.declare_parameter("file_loop", True)
        self.declare_parameter("camera_fps", 30)
        self.declare_parameter("v4l2_pixel_format", "UYVY")
        self.declare_parameter("infer_config", "")
        self.declare_parameter("preprocess_config", "")
        self.declare_parameter("engine_cache_dir", "")
        self.declare_parameter("tracker_enabled", True)
        self.declare_parameter("tracker_config", "")
        self.declare_parameter("display", True)
        self.declare_parameter("publish_annotated", False)
        self.declare_parameter("publish_detections", False)
        self.declare_parameter("publish_best_target", False)
        self.declare_parameter("merge_enabled", True)
        self.declare_parameter("merge_metric", "ios")
        self.declare_parameter("merge_threshold", 0.5)
        self.declare_parameter("det_interval", 2)
        self.declare_parameter("pgie_batch_size", 0)
        self.declare_parameter("width", 1920)
        self.declare_parameter("height", 1080)
        self.declare_parameter("tiled_width", 1280)
        self.declare_parameter("tiled_height", 720)
        self.declare_parameter("batched_push_timeout", 40000)
        self.declare_parameter("slice_width", 640)
        self.declare_parameter("slice_height", 640)
        self.declare_parameter("overlap_h", 0.2)
        self.declare_parameter("overlap_v", 0.2)

    def _resolve_runtime_config(self):
        self.mode = str(self.get_parameter("mode").value).strip().lower()
        if self.mode not in ("base", "sahi"):
            raise ValueError("mode must be one of: base, sahi")

        legacy_source = str(self.get_parameter("source").value).strip()
        sources_value = list(self.get_parameter("sources").value or [])
        names_value = list(self.get_parameter("source_names").value or [])
        self.sources, self.source_names = normalize_source_config(
            legacy_source,
            sources_value,
            names_value,
        )
        self.source = self.sources[0]

        share_dir = get_package_share_directory("deepstream")
        default_infer = (
            f"{share_dir}/config/config_infer_primary_yolov11s_sahi.txt"
            if self.mode == "sahi"
            else f"{share_dir}/config/config_infer_primary_yolov11s.txt"
        )
        default_preprocess = (
            f"{share_dir}/config/config_preprocess_yolov11s_sahi.txt"
            if self.mode == "sahi"
            else ""
        )
        default_tracker = f"{share_dir}/config/deepstream_tracker_config.txt"

        infer_config = (
            str(self.get_parameter("infer_config").value).strip()
            or default_infer
        )
        preprocess_config_raw = (
            str(self.get_parameter("preprocess_config").value).strip()
            or default_preprocess
        )
        tracker_config = (
            str(self.get_parameter("tracker_config").value).strip()
            or default_tracker
        )

        infer_config = resolve_runtime_config_path(infer_config, share_dir)
        preprocess_config_raw = resolve_runtime_config_path(
            preprocess_config_raw,
            share_dir,
        )
        tracker_config = resolve_runtime_config_path(tracker_config, share_dir)

        preprocess_config = preprocess_config_raw or None

        self.tracker_enabled = bool(
            self.get_parameter("tracker_enabled").value
        )
        self.display = bool(self.get_parameter("display").value)
        self.publish_annotated = bool(
            self.get_parameter("publish_annotated").value
        )
        self.publish_detections = bool(
            self.get_parameter("publish_detections").value
        )
        self.publish_best_target = bool(
            self.get_parameter("publish_best_target").value
        )
        merge_enabled = bool(self.get_parameter("merge_enabled").value)
        merge_metric = (
            str(self.get_parameter("merge_metric").value).strip().lower()
        )
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

        slice_width = int(self.get_parameter("slice_width").value)
        slice_height = int(self.get_parameter("slice_height").value)
        overlap_h = float(self.get_parameter("overlap_h").value)
        overlap_v = float(self.get_parameter("overlap_v").value)
        width = int(self.get_parameter("width").value)
        height = int(self.get_parameter("height").value)
        if slice_width <= 0 or slice_height <= 0:
            raise ValueError("slice dimensions must be positive")
        if slice_width > width or slice_height > height:
            raise ValueError(
                "slice dimensions must not exceed the mux dimensions"
            )
        if not 0.0 <= overlap_h < 1.0 or not 0.0 <= overlap_v < 1.0:
            raise ValueError("overlap_h and overlap_v must be in [0, 1)")
        if int(self.get_parameter("camera_fps").value) <= 0:
            raise ValueError("camera_fps must be positive")

        pgie_batch_size = int(self.get_parameter("pgie_batch_size").value)
        expected_batch_size = len(self.sources)
        if self.mode == "sahi":
            roi_count = len(
                compute_sahi_rois(
                    width,
                    height,
                    slice_width,
                    slice_height,
                    overlap_h,
                    overlap_v,
                )
            )
            expected_batch_size = len(self.sources) * roi_count
        if pgie_batch_size <= 0:
            pgie_batch_size = expected_batch_size
        elif (
            self.mode == "sahi" and expected_batch_size % pgie_batch_size != 0
        ):
            raise ValueError(
                "pgie_batch_size must divide source count times SAHI ROI count: "
                f"configured={pgie_batch_size}, total_rois={expected_batch_size}"
            )

        engine_cache_dir = str(
            self.get_parameter("engine_cache_dir").value
        ).strip()
        if not engine_cache_dir:
            project_root = os.environ.get("DEEPSTREAM_SAHI_ROOT")
            engine_cache_dir = (
                os.path.join(project_root, ".cache", "engines")
                if project_root
                else os.path.expanduser("~/.cache/deepstream-sahi/engines")
            )
        infer_config = prepare_runtime_infer_config(
            infer_config,
            engine_cache_dir,
            pgie_batch_size,
        )
        if self.mode == "sahi" and preprocess_config:
            preprocess_config = prepare_runtime_preprocess_config(
                preprocess_config,
                engine_cache_dir,
                pgie_batch_size,
                expected_batch_size,
            )

        self.pipeline_params = PipelineBuildParams(
            infer_config=infer_config,
            sources=self.sources,
            source_names=self.source_names,
            source=legacy_source,
            preprocess_config=preprocess_config,
            tracker_enabled=self.tracker_enabled,
            tracker_config=tracker_config,
            display=self.display,
            publish_annotated=self.publish_annotated,
            width=width,
            height=height,
            tiled_width=int(self.get_parameter("tiled_width").value),
            tiled_height=int(self.get_parameter("tiled_height").value),
            batched_push_timeout=int(
                self.get_parameter("batched_push_timeout").value
            ),
            pgie_batch_size=pgie_batch_size,
            det_interval=int(self.get_parameter("det_interval").value),
            file_loop=bool(self.get_parameter("file_loop").value),
            camera_fps=int(self.get_parameter("camera_fps").value),
            v4l2_pixel_format=str(
                self.get_parameter("v4l2_pixel_format").value
            )
            .strip()
            .upper(),
            slice_width=slice_width,
            slice_height=slice_height,
            overlap_h=overlap_h,
            overlap_v=overlap_v,
            merge_config=self._merge_cfg,
        )

        validate_runtime_configs(
            mode=self.mode,
            infer_config=self.pipeline_params.infer_config,
            preprocess_config=self.pipeline_params.preprocess_config,
            tracker_enabled=self.tracker_enabled,
            tracker_config=self.pipeline_params.tracker_config,
            tensor_batch_size=(pgie_batch_size if self.mode == "sahi" else 0),
            total_roi_count=(
                expected_batch_size if self.mode == "sahi" else 0
            ),
            logger=self.get_logger(),
        )

    def _setup_publishers(self):
        self.status_pub = self.create_publisher(
            String, "/deepstream/status", 10
        )
        self.detection_publishers = {}
        self.target_pub = None
        self.annotated_pub = None

        if self.publish_detections:
            if HAS_VISION_MSGS:
                for source_name in self.source_names:
                    topic = f"/deepstream/{source_name}/detections"
                    self.detection_publishers[source_name] = (
                        self.create_publisher(
                            Detection2DArray,
                            topic,
                            10,
                        )
                    )
            else:
                self.get_logger().warning(
                    "vision_msgs is not available. Disabling detection publishers."
                )
                self.publish_detections = False

        if self.publish_best_target:
            self.target_pub = self.create_publisher(
                Point, "/deepstream/target_pixel", 10
            )
        if self.publish_annotated:
            self.annotated_pub = self.create_publisher(
                Image, "/deepstream/image/annotated", 10
            )

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
            f"mode={self.mode}, sources={self.sources}, tracker={self.tracker_enabled}, "
            f"merge={self._merge_cfg.enabled}"
        )

        if self.mode == "sahi":
            result = build_sahi_pipeline(self.pipeline_params)
        else:
            result = build_base_pipeline(self.pipeline_params)

        self._pipeline = result.pipeline
        self._pgie = result.pgie
        self._appsink = result.appsink

        if result.has_native_postprocess:
            self.get_logger().info(
                "Using native nvsahipostprocess for NMM (C++ plugin)"
            )

        metadata_pad = result.metadata_probe_element.get_static_pad("src")
        if not metadata_pad:
            raise RuntimeError("Unable to get metadata probe src pad")
        metadata_pad.add_probe(
            Gst.PadProbeType.BUFFER,
            self._metadata_probe,
            None,
        )

        if self._appsink is not None and self.publish_annotated:
            self._appsink.connect("new-sample", self._on_new_sample)

        self._bus = self._pipeline.get_bus()

        ret = self._pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start DeepStream pipeline")

        self._running = True
        self._bus_thread = threading.Thread(
            target=self._bus_loop, name="deepstream-bus", daemon=True
        )
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

    def _frame_stamp(self, frame_meta):
        parts = ntp_timestamp_parts(getattr(frame_meta, "ntp_timestamp", 0))
        if parts is None:
            return self.get_clock().now().to_msg()
        sec, nanosec = parts
        return Time(sec=sec, nanosec=nanosec)

    def _metadata_probe(self, pad, info, user_data):
        gst_buffer = info.get_buffer()
        if not gst_buffer:
            return Gst.PadProbeReturn.OK

        batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
        if not batch_meta:
            return Gst.PadProbeReturn.OK

        for frame_meta in self._iter_frames(batch_meta):
            source_id = int(frame_meta.source_id)
            if source_id < 0 or source_id >= len(self.source_names):
                self.get_logger().error(
                    f"Unknown DeepStream source_id: {source_id}"
                )
                continue
            source_name = self.source_names[source_id]
            stamp = self._frame_stamp(frame_meta)
            update_osd_labels(frame_meta, source_name)
            detections = extract_detections(frame_meta)

            publisher = self.detection_publishers.get(source_name)
            if publisher is not None:
                det_msg = build_detection_array_msg(
                    detections, stamp, source_name
                )
                if det_msg is not None:
                    publisher.publish(det_msg)

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
        mask = (
            Gst.MessageType.ERROR
            | Gst.MessageType.EOS
            | Gst.MessageType.WARNING
        )
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
                loop_files = self.pipeline_params.file_loop and all(
                    is_file_source(source) for source in self.sources
                )
                if loop_files:
                    self.get_logger().info(
                        "Looping file sources from the beginning."
                    )
                    flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT
                    if self._pipeline.seek_simple(Gst.Format.TIME, flags, 0):
                        continue
                    self.get_logger().warning(
                        "Unable to seek file pipeline for looping."
                    )
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
        raise
    finally:
        if node is not None:
            node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
