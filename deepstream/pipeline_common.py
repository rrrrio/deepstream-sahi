#!/usr/bin/env python3
"""
File: pipeline_common.py
Description: Shared DeepStream multi-source pipeline utilities.
Author: Yuhao Ding
"""

import configparser
import os
import math
import pathlib
import sys
from dataclasses import dataclass, field, replace
from typing import List, Optional, Tuple
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

    # Merge config for nvsahipostprocess plugin
    merge_config: Optional[MergeConfig] = None


@dataclass
class PipelineBuildResult:
    pipeline: Gst.Pipeline
    pgie: Gst.Element
    appsink: Optional[Gst.Element]
    metadata_probe_element: Gst.Element
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
    return replace(params, sources=sources, source_names=source_names)


# ─── SAHI ROI computation ─────────────────────────────────────────────


def compute_sahi_rois(
    frame_w: int,
    frame_h: int,
    slice_w: int,
    slice_h: int,
    overlap_h: float = 0.2,
    overlap_v: float = 0.2,
) -> List[Tuple[int, int, int, int]]:
    """Compute SAHI ROI grid as list of (left, top, width, height).

    Uses stride = slice_size - int(slice_size * overlap) to match
    nvdspreprocess convention. The last ROI is always the full frame.
    """
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

    rois = [(x, y, slice_w, slice_h) for y in ys for x in xs]
    rois.append((0, 0, frame_w, frame_h))
    return rois


# ─── element helpers ──────────────────────────────────────────────────


def to_uri(path_or_uri: str) -> str:
    if "://" in path_or_uri:
        return path_or_uri
    return pathlib.Path(path_or_uri).expanduser().resolve().as_uri()


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
        source_bin = data
        bin_ghost_pad = source_bin.get_static_pad("src")
        if not bin_ghost_pad.set_target(decoder_src_pad):
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


def create_h264_file_source_bin(index: int, source: str):
    """Create an audio-free local H.264 MP4 source that stays in NVMM memory."""
    source_bin = Gst.Bin.new(f"file-source-bin-{index:02d}")
    if not source_bin:
        return None

    filesrc = Gst.ElementFactory.make("filesrc", f"file-source-{index}")
    demux = Gst.ElementFactory.make("qtdemux", f"file-demux-{index}")
    parser = Gst.ElementFactory.make("h264parse", f"file-parser-{index}")
    decoder = Gst.ElementFactory.make("nvv4l2decoder", f"file-decoder-{index}")
    if not all((filesrc, demux, parser, decoder)):
        return None

    filesrc.set_property("location", local_file_path(source))
    for element in (filesrc, demux, parser, decoder):
        source_bin.add(element)
    if not filesrc.link(demux) or not parser.link(decoder):
        return None
    demux.connect("pad-added", cb_mp4_demux_pad, parser)

    source_pad = decoder.get_static_pad("src")
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
):
    """Create a Jetson CSI source that remains in NVMM memory."""
    source_bin = Gst.Bin.new(f"argus-source-bin-{index:02d}")
    if not source_bin:
        return None

    camera = Gst.ElementFactory.make(
        "nvarguscamerasrc", f"argus-camera-{index}"
    )
    capsfilter = Gst.ElementFactory.make("capsfilter", f"argus-caps-{index}")
    if not camera or not capsfilter:
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
    source_bin.add(camera)
    source_bin.add(capsfilter)
    if not camera.link(capsfilter):
        return None

    source_pad = capsfilter.get_static_pad("src")
    if not source_pad or not source_bin.add_pad(
        Gst.GhostPad.new("src", source_pad)
    ):
        return None
    return source_bin


def create_uri_source_bin(index: int, source: str, file_loop: bool):
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
    uri_source.connect("pad-added", cb_newpad, source_bin)
    uri_source.connect("child-added", decodebin_child_added, source_bin)

    source_bin.add(uri_source)
    if not source_bin.add_pad(
        Gst.GhostPad.new_no_target("src", Gst.PadDirection.SRC)
    ):
        return None
    return source_bin


def create_source_bin(index: int, source: str, params: PipelineBuildParams):
    if is_argus_source(source):
        return create_argus_source_bin(
            index=index,
            source=source,
            width=params.width,
            height=params.height,
            fps=params.camera_fps,
        )
    if is_v4l2_source(source):
        return create_v4l2_source_bin(
            index=index,
            source=source,
            width=params.width,
            height=params.height,
            fps=params.camera_fps,
            pixel_format=params.v4l2_pixel_format,
        )
    if is_h264_mp4_file(source):
        return create_h264_file_source_bin(index, source)

    return create_uri_source_bin(index, source, params.file_loop)


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
    if input_tensor_meta:
        _set_if_supported("input-tensor-meta", True)
        _set_if_supported("tensor-meta-gie-id", tensor_meta_gie_id)
    _set_if_supported("display-tracking-id", True)


def add_output_branches(
    pipeline: Gst.Pipeline,
    upstream: Gst.Element,
    display: bool,
    publish_annotated: bool,
) -> Optional[Gst.Element]:
    tee = make_element("tee", "tee-output")
    queue_display = make_element("queue", "queue-display")
    sink = (
        make_element("nv3dsink", "display-sink")
        if display
        else make_element("fakesink", "display-sink")
    )

    pipeline.add(tee)
    pipeline.add(queue_display)
    pipeline.add(sink)

    # Rendering may be unavailable when a container loses X11/EGL access.  Keep
    # that optional branch from applying backpressure to the metadata probe and
    # per-camera ROS publishers.
    queue_display.set_property("leaky", 2)
    queue_display.set_property("max-size-buffers", 1)
    sink.set_property("async", False)
    sink.set_property("sync", False)
    sink.set_property("qos", False)

    if not upstream.link(tee):
        raise RuntimeError(
            f"Unable to link {upstream.get_name()} -> {tee.get_name()}"
        )
    if not tee.link(queue_display):
        raise RuntimeError("Unable to link tee -> display queue")
    if not queue_display.link(sink):
        raise RuntimeError("Unable to link display queue -> sink")

    appsink = None
    if publish_annotated:
        queue_annotated = make_element("queue", "queue-annotated")
        nvconv_annotated = make_element("nvvideoconvert", "nvconv-annotated")
        caps_rgba = make_element("capsfilter", "caps-rgba")
        videoconv = make_element("videoconvert", "videoconv-annotated")
        caps_bgr = make_element("capsfilter", "caps-bgr")
        appsink = make_element("appsink", "annotated-appsink")

        caps_rgba.set_property(
            "caps", Gst.Caps.from_string("video/x-raw,format=RGBA")
        )
        caps_bgr.set_property(
            "caps", Gst.Caps.from_string("video/x-raw,format=BGR")
        )
        appsink.set_property("emit-signals", True)
        appsink.set_property("sync", False)
        appsink.set_property("drop", True)
        appsink.set_property("max-buffers", 1)

        for element in [
            queue_annotated,
            nvconv_annotated,
            caps_rgba,
            videoconv,
            caps_bgr,
            appsink,
        ]:
            pipeline.add(element)

        if not tee.link(queue_annotated):
            raise RuntimeError("Unable to link tee -> annotated queue")
        link_elements(
            queue_annotated,
            nvconv_annotated,
            caps_rgba,
            videoconv,
            caps_bgr,
            appsink,
        )

    return appsink


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
