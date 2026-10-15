#!/usr/bin/env python3
"""
File: pipeline_common.py
Description: Shared DeepStream multi-source pipeline utilities.
Author: Yuhao Ding
"""

import configparser
import json
import os
import math
import pathlib
import sys
from dataclasses import dataclass, field, replace
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from .source_utils import (  # noqa: E402
    argus_sensor_id,
    is_argus_source,
    is_file_source,
    is_live_source,
    is_v4l2_source,
    normalize_source_config,
    normalize_source_rotations,
    v4l2_device_path,
)

MUXER_BATCH_TIMEOUT_USEC = 40000
GST_CAPS_FEATURES_NVMM = "memory:NVMM"


@dataclass
class MergeConfig:
    enabled: bool
    metric: str
    threshold: float
    class_agnostic: bool


@dataclass
class PipelineBuildParams:
    infer_config: str
    sources: List[str] = field(default_factory=list)
    source_names: List[str] = field(default_factory=list)
    source: str = ""
    preprocess_config: Optional[str] = None
    tracker_enabled: bool = False
    tracker_config: Optional[str] = None
    display: bool = True
    publish_annotated: bool = False
    allowed_class_ids: List[int] = field(default_factory=list)
    width: int = 1920
    height: int = 1080
    tiled_width: int = 1280
    tiled_height: int = 720
    batched_push_timeout: int = MUXER_BATCH_TIMEOUT_USEC
    pgie_batch_size: int = 0
    det_interval: int = -1
    file_loop: bool = True
    camera_fps: int = 30
    v4l2_pixel_format: str = "UYVY"

    # SAHI slice parameters
    slice_width: int = 640
    slice_height: int = 640
    overlap_h: float = 0.2
    overlap_v: float = 0.2
    rois: List[Tuple[int, int, int, int]] = field(default_factory=list)
    include_full_frame: bool = True

    # Merge config for nvsahipostprocess plugin
    merge_config: Optional[MergeConfig] = None
    display_sink: str = "nv3dsink"
    source_rotations: List[int] = field(default_factory=list)


@dataclass
class PipelineBuildResult:
    pipeline: Gst.Pipeline
    pgie: Gst.Element
    annotated_appsinks: Dict[str, Gst.Element]
    metadata_probe_element: Gst.Element
    class_filter_element: Optional[Gst.Element] = None
    has_native_postprocess: bool = False


def normalize_pipeline_build_params(
    params: PipelineBuildParams,
) -> PipelineBuildParams:
    """Return builder-safe source lists, retaining the legacy ``source`` API."""
    sources, source_names = normalize_source_config(
        params.source,
        params.sources,
        params.source_names,
    )
    return replace(
        params,
        sources=sources,
        source_names=source_names,
        source_rotations=normalize_source_rotations(
            params.source_rotations, len(sources)
        ),
    )


# ─── SAHI ROI computation ─────────────────────────────────────────────


def validate_sahi_rois(
    rois: object,
    frame_w: int,
    frame_h: int,
) -> List[Tuple[int, int, int, int]]:
    """Validate and normalize explicit ``[left, top, width, height]`` ROIs."""
    if rois is None:
        return []
    if not isinstance(rois, (list, tuple)):
        raise ValueError("rois must be a list of [left, top, width, height]")

    normalized = []
    seen = set()
    for index, roi in enumerate(rois):
        if not isinstance(roi, (list, tuple)) or len(roi) != 4:
            raise ValueError(
                f"rois[{index}] must contain exactly four integers: "
                "[left, top, width, height]"
            )
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in roi
        ):
            raise ValueError(f"rois[{index}] must contain only integers")

        left, top, width, height = roi
        if left < 0 or top < 0 or width <= 0 or height <= 0:
            raise ValueError(
                f"rois[{index}] must have non-negative origin and positive size"
            )
        if left + width > frame_w or top + height > frame_h:
            raise ValueError(
                f"rois[{index}] must stay within the {frame_w}x{frame_h} frame"
            )

        normalized_roi = (left, top, width, height)
        if normalized_roi in seen:
            raise ValueError(f"rois[{index}] duplicates an earlier ROI")
        seen.add(normalized_roi)
        normalized.append(normalized_roi)
    return normalized


def parse_sahi_rois(
    value: object,
    frame_w: int,
    frame_h: int,
) -> List[Tuple[int, int, int, int]]:
    """Parse the ROS string parameter containing a JSON-style ROI list.

    ROS 2 parameters do not support nested primitive arrays, so the user-facing
    value is a string such as ``"[[0,220,640,640], [640,220,640,640]]"``.
    An empty string or ``"[]"`` selects the automatic grid.
    """
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return []
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "rois must be a JSON-style list, for example "
                "[[0,220,640,640], [640,220,640,640]]"
            ) from exc
    return validate_sahi_rois(value, frame_w, frame_h)


def serialize_sahi_rois(
    rois: Sequence[Tuple[int, int, int, int]],
) -> str:
    """Serialize validated ROIs for the native GStreamer property."""
    return ";".join(",".join(str(value) for value in roi) for roi in rois)


def compute_sahi_rois(
    frame_w: int,
    frame_h: int,
    slice_w: int,
    slice_h: int,
    overlap_h: float = 0.2,
    overlap_v: float = 0.2,
    rois: Optional[Sequence[Tuple[int, int, int, int]]] = None,
    include_full_frame: bool = True,
) -> List[Tuple[int, int, int, int]]:
    """Compute explicit or automatic SAHI ROIs.

    Uses stride = slice_size - int(slice_size * overlap) to match
    nvdspreprocess convention. A non-empty explicit ROI list bypasses this
    automatic grid and is reused for every source.
    """
    if rois:
        normalized = validate_sahi_rois(rois, frame_w, frame_h)
        if include_full_frame and (0, 0, frame_w, frame_h) not in normalized:
            normalized.append((0, 0, frame_w, frame_h))
        return normalized

    stride_x = max(1, slice_w - int(slice_w * overlap_h))
    stride_y = max(1, slice_h - int(slice_h * overlap_v))

    xs = [x for x in range(0, frame_w - slice_w + 1, stride_x)]
    edge_x = frame_w - slice_w
    if edge_x >= 0 and (not xs or xs[-1] != edge_x):
        xs.append(edge_x)

    ys = [y for y in range(0, frame_h - slice_h + 1, stride_y)]
    edge_y = frame_h - slice_h
    if edge_y >= 0 and (not ys or ys[-1] != edge_y):
        ys.append(edge_y)

    computed_rois = [(x, y, slice_w, slice_h) for y in ys for x in xs]

    if include_full_frame:
        computed_rois.append((0, 0, frame_w, frame_h))
    return computed_rois


# ─── element helpers ──────────────────────────────────────────────────


def to_uri(path_or_uri: str) -> str:
    if "://" in path_or_uri:
        return path_or_uri
    return pathlib.Path(path_or_uri).expanduser().resolve().as_uri()


def _flip_method_for_rotation(rotation: int) -> int:
    return {0: 0, 90: 1, 180: 2, 270: 3}[rotation]


def cb_newpad(decodebin, decoder_src_pad, data):
    caps = decoder_src_pad.get_current_caps() or decoder_src_pad.query_caps(
        None
    )
    if not caps:
        return

    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    features = caps.get_features(0)

    if "video" in gstname and features.contains(GST_CAPS_FEATURES_NVMM):
        source_bin, rotation = data
        bin_ghost_pad = source_bin.get_static_pad("src")
        if rotation == 0:
            target_pad = decoder_src_pad
        else:
            converter = Gst.ElementFactory.make(
                "nvvideoconvert", f"uri-rotate-{source_bin.get_name()}"
            )
            if not converter:
                sys.stderr.write("Failed to create URI rotation converter.\n")
                return
            converter.set_property(
                "flip-method", _flip_method_for_rotation(rotation)
            )
            source_bin.add(converter)
            sink_pad = converter.get_static_pad("sink")
            if (
                not sink_pad
                or decoder_src_pad.link(sink_pad) != Gst.PadLinkReturn.OK
            ):
                sys.stderr.write("Failed to link URI rotation converter.\n")
                return
            converter.sync_state_with_parent()
            target_pad = converter.get_static_pad("src")
        if not target_pad or not bin_ghost_pad.set_target(target_pad):
            sys.stderr.write(
                "Failed to link decoder src pad to source bin ghost pad\n"
            )
    elif "video" in gstname:
        sys.stderr.write("Decodebin did not pick an NVMM decoder.\n")


def decodebin_child_added(child_proxy, obj, name, user_data):
    if name.find("decodebin") != -1:
        obj.connect("child-added", decodebin_child_added, user_data)


def cb_camera_newpad(decodebin, decoder_src_pad, converter):
    """Link decoded camera video to the converter once decodebin exposes it."""
    caps = decoder_src_pad.get_current_caps() or decoder_src_pad.query_caps(
        None
    )
    if not caps or caps.get_size() == 0:
        return
    if "video" not in caps.get_structure(0).get_name():
        return

    sink_pad = converter.get_static_pad("sink")
    if sink_pad and not sink_pad.is_linked():
        if decoder_src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            sys.stderr.write(
                "Failed to link camera decoder to nvvideoconvert.\n"
            )


def create_v4l2_source_bin(
    index: int,
    source: str,
    width: int,
    height: int,
    fps: int,
    pixel_format: str,
    rotation: int = 0,
):
    """Create a raw V4L2 source bin that exposes NV12 video in NVMM memory."""
    source_bin = Gst.Bin.new(f"camera-source-bin-{index:02d}")
    if not source_bin:
        return None

    camera = Gst.ElementFactory.make("v4l2src", f"v4l2-camera-{index}")
    input_capsfilter = Gst.ElementFactory.make(
        "capsfilter", f"camera-input-caps-{index}"
    )
    converter = Gst.ElementFactory.make(
        "nvvideoconvert", f"camera-convert-{index}"
    )
    capsfilter = Gst.ElementFactory.make("capsfilter", f"camera-caps-{index}")
    if not all((camera, input_capsfilter, converter, capsfilter)):
        return None

    camera.set_property("device", v4l2_device_path(source))
    camera.set_property("io-mode", 2)
    camera.set_property("do-timestamp", True)
    input_capsfilter.set_property(
        "caps",
        Gst.Caps.from_string(
            f"video/x-raw,format={pixel_format},"
            f"width={width},height={height},framerate={fps}/1"
        ),
    )
    converter.set_property("flip-method", _flip_method_for_rotation(rotation))
    capsfilter.set_property(
        "caps", Gst.Caps.from_string("video/x-raw(memory:NVMM),format=NV12")
    )

    for element in (camera, input_capsfilter, converter, capsfilter):
        source_bin.add(element)
    if not camera.link(input_capsfilter) or not input_capsfilter.link(
        converter
    ):
        return None
    if not converter.link(capsfilter):
        return None

    source_pad = capsfilter.get_static_pad("src")
    if not source_pad or not source_bin.add_pad(
        Gst.GhostPad.new("src", source_pad)
    ):
        return None
    return source_bin


def local_file_path(source: str) -> str:
    """Return a local path for a plain path or ``file://`` URI."""
    value = source.strip()
    if value.startswith("file://"):
        parsed = urlparse(value)
        if parsed.netloc not in ("", "localhost"):
            raise ValueError(f"Unsupported non-local file URI: {source!r}")
        return unquote(parsed.path)
    return os.path.abspath(os.path.expanduser(value))


def is_h264_mp4_file(source: str) -> bool:
    """Return whether the simulation source can use the zero-audio NVDEC path."""
    return is_file_source(source) and local_file_path(source).lower().endswith(
        ".mp4"
    )


def cb_mp4_demux_pad(demux, demux_src_pad, parser):
    """Link only the H.264 video pad from an MP4 demuxer."""
    caps = demux_src_pad.get_current_caps() or demux_src_pad.query_caps(None)
    if not caps or caps.get_size() == 0:
        return
    if caps.get_structure(0).get_name() != "video/x-h264":
        return
    sink_pad = parser.get_static_pad("sink")
    if sink_pad and not sink_pad.is_linked():
        if demux_src_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
            sys.stderr.write("Failed to link MP4 video pad to h264parse.\n")


def create_h264_file_source_bin(index: int, source: str, rotation: int = 0):
    """Create an audio-free local H.264 MP4 source that stays in NVMM memory."""
    source_bin = Gst.Bin.new(f"file-source-bin-{index:02d}")
    if not source_bin:
        return None

    filesrc = Gst.ElementFactory.make("filesrc", f"file-source-{index}")
    demux = Gst.ElementFactory.make("qtdemux", f"file-demux-{index}")
    parser = Gst.ElementFactory.make("h264parse", f"file-parser-{index}")
    decoder = Gst.ElementFactory.make("nvv4l2decoder", f"file-decoder-{index}")
    converter = None
    if rotation:
        converter = Gst.ElementFactory.make(
            "nvvideoconvert", f"file-rotate-{index}"
        )
        if converter:
            converter.set_property(
                "flip-method", _flip_method_for_rotation(rotation)
            )
    if not all((filesrc, demux, parser, decoder)) or (
        rotation and converter is None
    ):
        return None

    filesrc.set_property("location", local_file_path(source))
    elements = [filesrc, demux, parser, decoder]
    if converter:
        elements.append(converter)
    for element in elements:
        source_bin.add(element)
    if not filesrc.link(demux) or not parser.link(decoder):
        return None
    output = decoder
    if converter:
        if not decoder.link(converter):
            return None
        output = converter
    demux.connect("pad-added", cb_mp4_demux_pad, parser)

    source_pad = output.get_static_pad("src")
    if not source_pad or not source_bin.add_pad(
        Gst.GhostPad.new("src", source_pad)
    ):
        return None
    return source_bin


def create_argus_source_bin(
    index: int,
    source: str,
    width: int,
    height: int,
    fps: int,
    rotation: int = 0,
):
    """Create a Jetson CSI source that remains in NVMM memory."""
    source_bin = Gst.Bin.new(f"argus-source-bin-{index:02d}")
    if not source_bin:
        return None

    camera = Gst.ElementFactory.make(
        "nvarguscamerasrc", f"argus-camera-{index}"
    )
    capsfilter = Gst.ElementFactory.make("capsfilter", f"argus-caps-{index}")
    converter = None
    output_capsfilter = None
    if rotation:
        converter = Gst.ElementFactory.make(
            "nvvideoconvert", f"argus-rotate-{index}"
        )
        output_capsfilter = Gst.ElementFactory.make(
            "capsfilter", f"argus-output-caps-{index}"
        )
        if converter:
            converter.set_property(
                "flip-method", _flip_method_for_rotation(rotation)
            )
    if (
        not camera
        or not capsfilter
        or (rotation and (converter is None or output_capsfilter is None))
    ):
        return None

    camera.set_property("sensor-id", argus_sensor_id(source))
    camera.set_property("do-timestamp", True)
    capsfilter.set_property(
        "caps",
        Gst.Caps.from_string(
            "video/x-raw(memory:NVMM),"
            f"format=NV12,width={width},height={height},framerate={fps}/1"
        ),
    )
    elements = [camera, capsfilter]
    if converter and output_capsfilter:
        elements.extend((converter, output_capsfilter))
    for element in elements:
        source_bin.add(element)
    if not camera.link(capsfilter):
        return None
    output = capsfilter
    if converter and output_capsfilter:
        output_capsfilter.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw(memory:NVMM),format=NV12"),
        )
        if not capsfilter.link(converter) or not converter.link(
            output_capsfilter
        ):
            return None
        output = output_capsfilter

    source_pad = output.get_static_pad("src")
    if not source_pad or not source_bin.add_pad(
        Gst.GhostPad.new("src", source_pad)
    ):
        return None
    return source_bin


def create_uri_source_bin(
    index: int, source: str, file_loop: bool, rotation: int = 0
):
    """Create a DeepStream URI source with NVDEC and optional file looping."""
    uri = to_uri(source)
    source_bin = Gst.Bin.new(f"uri-source-bin-{index:02d}")
    if not source_bin:
        return None

    uri_source = Gst.ElementFactory.make("nvurisrcbin", f"uri-source-{index}")
    if not uri_source:
        return None
    uri_source.set_property("uri", uri)
    uri_source.set_property("source-id", index)
    uri_source.set_property("disable-audio", True)
    if is_file_source(source):
        uri_source.set_property("file-loop", file_loop)
    uri_source.connect("pad-added", cb_newpad, (source_bin, rotation))
    uri_source.connect("child-added", decodebin_child_added, source_bin)

    source_bin.add(uri_source)
    if not source_bin.add_pad(
        Gst.GhostPad.new_no_target("src", Gst.PadDirection.SRC)
    ):
        return None
    return source_bin


def create_source_bin(index: int, source: str, params: PipelineBuildParams):
    rotations = normalize_source_rotations(
        params.source_rotations, len(params.sources)
    )
    rotation = rotations[index]
    if is_argus_source(source):
        return create_argus_source_bin(
            index=index,
            source=source,
            width=params.width,
            height=params.height,
            fps=params.camera_fps,
            rotation=rotation,
        )
    if is_v4l2_source(source):
        return create_v4l2_source_bin(
            index=index,
            source=source,
            width=params.width,
            height=params.height,
            fps=params.camera_fps,
            pixel_format=params.v4l2_pixel_format,
            rotation=rotation,
        )
    if is_h264_mp4_file(source):
        return create_h264_file_source_bin(index, source, rotation)

    return create_uri_source_bin(index, source, params.file_loop, rotation)


def make_element(factory_name: str, element_name: str):
    element = Gst.ElementFactory.make(factory_name, element_name)
    if not element:
        raise RuntimeError(
            f"Unable to create element '{element_name}' ({factory_name})"
        )
    return element


def try_make_element(factory_name: str, element_name: str):
    """Try to create a GStreamer element, return None if the plugin is not available."""
    return Gst.ElementFactory.make(factory_name, element_name)


def link_elements(*elements):
    for upstream, downstream in zip(elements[:-1], elements[1:]):
        if not upstream.link(downstream):
            raise RuntimeError(
                f"Unable to link {upstream.get_name()} -> {downstream.get_name()}"
            )


def attach_source_to_streammux(
    source_bin: Gst.Element,
    streammux: Gst.Element,
    index: int,
):
    sinkpad = streammux.request_pad_simple(f"sink_{index}")
    if not sinkpad:
        raise RuntimeError(f"Unable to request streammux sink_{index} pad")
    srcpad = source_bin.get_static_pad("src")
    if not srcpad:
        raise RuntimeError("Unable to get source bin src pad")
    if srcpad.link(sinkpad) != Gst.PadLinkReturn.OK:
        raise RuntimeError(f"Unable to link source {index} to streammux")


def configure_streammux(streammux: Gst.Element, params: PipelineBuildParams):
    streammux.set_property("width", params.width)
    streammux.set_property("height", params.height)
    streammux.set_property("batch-size", len(params.sources))
    streammux.set_property("batched-push-timeout", params.batched_push_timeout)
    streammux.set_property(
        "live-source",
        1 if any(is_live_source(source) for source in params.sources) else 0,
    )
    if streammux.find_property("attach-sys-ts") is not None:
        streammux.set_property("attach-sys-ts", True)
    if streammux.find_property("sync-inputs") is not None:
        streammux.set_property("sync-inputs", False)


def configure_primary_gie(
    pgie: Gst.Element,
    infer_config: str,
    pgie_batch_size: int = 0,
    det_interval: int = -1,
    input_tensor_meta: bool = False,
):
    pgie.set_property("config-file-path", infer_config)
    if input_tensor_meta:
        pgie.set_property("input-tensor-meta", True)
    if pgie_batch_size > 0:
        pgie.set_property("batch-size", pgie_batch_size)
    if det_interval >= 0:
        pgie.set_property("interval", det_interval)


def compute_tiler_layout(source_count: int) -> Tuple[int, int]:
    """Return rows and columns for a compact tiler layout."""
    if source_count <= 0:
        raise ValueError("source_count must be positive")
    rows = max(1, int(math.ceil(math.sqrt(source_count))))
    columns = max(1, int(math.ceil(source_count / rows)))
    return rows, columns


def configure_tiler(
    tiler: Gst.Element, width: int, height: int, source_count: int
):
    rows, columns = compute_tiler_layout(source_count)
    tiler.set_property("rows", rows)
    tiler.set_property("columns", columns)
    tiler.set_property("width", width)
    tiler.set_property("height", height)


def configure_tracker(
    tracker: Gst.Element,
    tracker_config_path: str,
    input_tensor_meta: bool = False,
    tensor_meta_gie_id: int = 0,
):
    def _set_if_supported(prop_name, value):
        if tracker.find_property(prop_name) is not None:
            tracker.set_property(prop_name, value)

    config = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if not config.read(tracker_config_path):
        raise FileNotFoundError(
            f"Unable to read tracker config: {tracker_config_path}"
        )
    if "tracker" not in config:
        raise ValueError("Tracker config missing [tracker] section")

    tracker_section = config["tracker"]
    if "tracker-width" in tracker_section:
        _set_if_supported(
            "tracker-width", tracker_section.getint("tracker-width")
        )
    if "tracker-height" in tracker_section:
        _set_if_supported(
            "tracker-height", tracker_section.getint("tracker-height")
        )
    if "gpu-id" in tracker_section:
        _set_if_supported("gpu-id", tracker_section.getint("gpu-id"))
    if "ll-lib-file" in tracker_section:
        _set_if_supported("ll-lib-file", tracker_section.get("ll-lib-file"))
    if "ll-config-file" in tracker_section:
        _set_if_supported(
            "ll-config-file", tracker_section.get("ll-config-file")
        )
    if "enable-batch-process" in tracker_section:
        _set_if_supported(
            "enable-batch-process",
            tracker_section.getint("enable-batch-process"),
        )
    if "enable-past-frame" in tracker_section:
        _set_if_supported(
            "enable-past-frame", tracker_section.getint("enable-past-frame")
        )
    if "compute-hw" in tracker_section:
        _set_if_supported("compute-hw", tracker_section.getint("compute-hw"))
    if "display-tracking-id" in tracker_section:
        _set_if_supported(
            "display-tracking-id",
            tracker_section.getboolean("display-tracking-id"),
        )
    else:
        _set_if_supported("display-tracking-id", True)
    if input_tensor_meta:
        _set_if_supported("input-tensor-meta", True)
        _set_if_supported("tensor-meta-gie-id", tensor_meta_gie_id)


def add_output_branches(
    pipeline: Gst.Pipeline,
    upstream: Gst.Element,
    display: bool,
    publish_annotated: bool,
    source_names: Optional[List[str]] = None,
    tiled_width: int = 1280,
    tiled_height: int = 720,
    display_sink: str = "nv3dsink",
) -> Dict[str, Gst.Element]:
    """Attach display and per-source compressed annotated output branches."""
    source_names = list(source_names or ["camera_0"])
    tee = make_element("tee", "tee-output")
    queue_display = make_element("queue", "queue-display")
    if display and display_sink not in {
        "fakesink",
        "nveglglessink",
        "nv3dsink",
    }:
        raise ValueError(
            "display_sink must be one of: fakesink, nveglglessink, nv3dsink"
        )
    sink = make_element(
        display_sink if display else "fakesink",
        "display-sink",
    )

    pipeline.add(tee)
    pipeline.add(queue_display)
    pipeline.add(sink)

    # Rendering may be unavailable when a container loses X11/EGL access.  Keep
    # that optional branch from applying backpressure to the metadata probe and
    # per-camera ROS publishers.
    for element in (queue_display,):
        element.set_property("leaky", 1)
        element.set_property("max-size-buffers", 1)
        element.set_property("max-size-bytes", 0)
        element.set_property("max-size-time", 0)
    sink.set_property("async", False)
    sink.set_property("sync", False)
    sink.set_property("qos", False)

    if not upstream.link(tee):
        raise RuntimeError(
            f"Unable to link {upstream.get_name()} -> {tee.get_name()}"
        )
    if not tee.link(queue_display):
        raise RuntimeError("Unable to link tee -> display queue")
    if display:
        tiler = make_element("nvmultistreamtiler", "tiler")
        queue_tiler = make_element("queue", "queue-tiler")
        nvvidconv = make_element("nvvideoconvert", "nvvidconv")
        caps_display = make_element("capsfilter", "caps-display-rgba")
        queue_conv = make_element("queue", "queue-conv")
        nvosd = make_element("nvdsosd", "nvosd")
        queue_output = make_element("queue", "queue-output")
        configure_tiler(tiler, tiled_width, tiled_height, len(source_names))
        caps_display.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw(memory:NVMM),format=RGBA"),
        )
        # Keep the display surface in an explicit RGBA/NVMM format and make
        # the display branch lossy so a stalled sink cannot block inference.
        nvosd.set_property("process-mode", 1)
        for element in (queue_tiler, queue_conv, queue_output):
            element.set_property("leaky", 1)
            element.set_property("max-size-buffers", 1)
            element.set_property("max-size-bytes", 0)
            element.set_property("max-size-time", 0)
        for element in [
            tiler,
            queue_tiler,
            nvvidconv,
            caps_display,
            queue_conv,
            nvosd,
            queue_output,
        ]:
            pipeline.add(element)
        link_elements(
            queue_display,
            tiler,
            queue_tiler,
            nvvidconv,
            caps_display,
            queue_conv,
            nvosd,
            queue_output,
            sink,
        )
    elif not queue_display.link(sink):
        raise RuntimeError("Unable to link display queue -> sink")

    annotated_appsinks: Dict[str, Gst.Element] = {}
    if publish_annotated:
        if not source_names:
            raise ValueError(
                "source_names are required when publish_annotated is enabled"
            )

        queue_annotated = make_element("queue", "queue-annotated")
        nvconv_annotated = make_element("nvvideoconvert", "nvconv-annotated")
        caps_rgba = make_element("capsfilter", "caps-annotated-rgba")
        nvosd_annotated = make_element("nvdsosd", "nvosd-annotated")
        queue_osd = make_element("queue", "queue-annotated-osd")
        demux = make_element("nvstreamdemux", "annotated-demux")
        caps_rgba.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw(memory:NVMM),format=RGBA"),
        )
        queue_annotated.set_property("leaky", 2)
        queue_annotated.set_property("max-size-buffers", 1)
        queue_osd.set_property("leaky", 2)
        queue_osd.set_property("max-size-buffers", 1)
        for element in [
            queue_annotated,
            nvconv_annotated,
            caps_rgba,
            nvosd_annotated,
            queue_osd,
            demux,
        ]:
            pipeline.add(element)
        if not tee.link(queue_annotated):
            raise RuntimeError("Unable to link tee -> annotated queue")
        link_elements(
            queue_annotated,
            nvconv_annotated,
            caps_rgba,
            nvosd_annotated,
            queue_osd,
            demux,
        )

        for source_index, source_name in enumerate(source_names):
            queue_source = make_element(
                "queue", f"queue-annotated-{source_name}"
            )
            nvconv_source = make_element(
                "nvvideoconvert", f"nvconv-jpeg-{source_name}"
            )
            caps_nv12 = make_element(
                "capsfilter", f"caps-jpeg-input-{source_name}"
            )
            jpeg_encoder = make_element(
                "nvjpegenc", f"jpeg-encoder-{source_name}"
            )
            caps_jpeg = make_element(
                "capsfilter", f"caps-jpeg-output-{source_name}"
            )
            appsink = make_element(
                "appsink", f"annotated-appsink-{source_name}"
            )
            queue_source.set_property("leaky", 2)
            queue_source.set_property("max-size-buffers", 1)
            caps_nv12.set_property(
                "caps",
                Gst.Caps.from_string("video/x-raw(memory:NVMM),format=NV12"),
            )
            caps_jpeg.set_property("caps", Gst.Caps.from_string("image/jpeg"))
            appsink.set_property("emit-signals", True)
            appsink.set_property("sync", False)
            appsink.set_property("async", False)
            appsink.set_property("drop", True)
            appsink.set_property("max-buffers", 1)
            for element in [
                queue_source,
                nvconv_source,
                caps_nv12,
                jpeg_encoder,
                caps_jpeg,
                appsink,
            ]:
                pipeline.add(element)
            link_elements(
                queue_source,
                nvconv_source,
                caps_nv12,
                jpeg_encoder,
                caps_jpeg,
                appsink,
            )
            annotated_appsinks[source_name] = appsink
            sink_pad = queue_source.get_static_pad("sink")
            source_pad = demux.request_pad_simple(f"src_{source_index}")
            if source_pad is None or sink_pad is None:
                raise RuntimeError(
                    f"Unable to request annotated demux source {source_index}"
                )
            if source_pad.link(sink_pad) != Gst.PadLinkReturn.OK:
                raise RuntimeError(
                    f"Unable to link annotated demux source {source_index}"
                )

    return annotated_appsinks


# ─── config file helpers ──────────────────────────────────────────────


def read_ini(path: str):
    parser = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    if not parser.read(path):
        raise FileNotFoundError(f"Unable to read config file: {path}")
    return parser


def resolve_path(base_file: str, maybe_relative: str) -> str:
    expanded = os.path.expanduser(maybe_relative)
    if os.path.isabs(expanded):
        return expanded
    return os.path.abspath(os.path.join(os.path.dirname(base_file), expanded))
