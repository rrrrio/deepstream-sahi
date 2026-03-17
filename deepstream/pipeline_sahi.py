#!/usr/bin/env python3
"""
File: pipeline_sahi.py
Description: Builds the DeepStream SAHI-mode GStreamer pipeline.

             Preferred layout (with C plugins):
               source → mux → nvsahipreprocess → pgie → nvsahipostprocess → tracker → tiler → osd → sink

             Fallback layout (Python-only):
               source → mux → nvdspreprocess(dynamic config) → pgie → [tracker] → tiler → osd → sink
               (+ Python probe NMM on pgie src pad)

Author: Yuhao Ding
"""

import sys

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from .pipeline_common import (
    PipelineBuildParams,
    PipelineBuildResult,
    add_output_branches,
    attach_source_to_streammux,
    configure_primary_gie,
    configure_streammux,
    configure_tiler,
    configure_tracker,
    create_source_bin,
    generate_preprocess_config,
    link_elements,
    make_element,
    to_uri,
    try_make_element,
)


def _setup_native_preprocess(pipeline, params):
    """Try nvsahipreprocess (C plugin). Returns (element, True) or falls back to nvdspreprocess."""
    preprocess = try_make_element("nvsahipreprocess", "sahi-preprocess")
    if preprocess is not None:
        preprocess.set_property("slice-width", params.slice_width)
        preprocess.set_property("slice-height", params.slice_height)
        preprocess.set_property("overlap-h", params.overlap_h)
        preprocess.set_property("overlap-v", params.overlap_v)
        preprocess.set_property("frame-width", params.width)
        preprocess.set_property("frame-height", params.height)
        return preprocess, True

    # Fallback: standard nvdspreprocess with dynamic or static config
    preprocess = make_element("nvdspreprocess", "preprocess")
    config_path = params.preprocess_config
    if not config_path:
        config_path = generate_preprocess_config(
            frame_w=params.width,
            frame_h=params.height,
            slice_w=params.slice_width,
            slice_h=params.slice_height,
            overlap_h=params.overlap_h,
            overlap_v=params.overlap_v,
        )
        sys.stderr.write(f"[SAHI] Auto-generated preprocess config: {config_path}\n")

    preprocess.set_property("config-file", config_path)
    return preprocess, False


def _setup_native_postprocess(params):
    """Try nvsahipostprocess (C plugin). Returns element or None."""
    merge_cfg = params.merge_config
    if merge_cfg is None or not merge_cfg.enabled:
        return None

    postprocess = try_make_element("nvsahipostprocess", "sahi-postprocess")
    if postprocess is None:
        return None

    metric_enum = 0 if merge_cfg.metric == "ios" else 1
    postprocess.set_property("metric", metric_enum)
    postprocess.set_property("threshold", merge_cfg.threshold)
    postprocess.set_property("class-agnostic", merge_cfg.class_agnostic)
    return postprocess


def build_pipeline(params: PipelineBuildParams) -> PipelineBuildResult:
    Gst.init(None)

    pipeline = Gst.Pipeline.new("deepstream-sahi-pipeline")
    if not pipeline:
        raise RuntimeError("Unable to create GstPipeline")

    uri = to_uri(params.source)
    source_bin = create_source_bin(0, uri)
    if not source_bin:
        raise RuntimeError("Unable to create source bin")

    streammux = make_element("nvstreammux", "streammux")
    queue1 = make_element("queue", "queue-preprocess-in")

    preprocess, is_native_pre = _setup_native_preprocess(pipeline, params)

    queue2 = make_element("queue", "queue-pgie-in")
    pgie = make_element("nvinfer", "primary-gie")
    queue3 = make_element("queue", "queue-post-pgie")

    # Try native NMM postprocess
    postprocess = _setup_native_postprocess(params)
    queue_post = None
    if postprocess is not None:
        queue_post = make_element("queue", "queue-postprocess")

    tracker = None
    queue_tracker = None
    if params.tracker_enabled:
        tracker = make_element("nvtracker", "tracker")
        queue_tracker = make_element("queue", "queue-tracker")

    tiler = make_element("nvmultistreamtiler", "tiler")
    queue4 = make_element("queue", "queue-tiler")
    nvvidconv = make_element("nvvideoconvert", "nvvidconv")
    queue5 = make_element("queue", "queue-conv")
    nvosd = make_element("nvdsosd", "nvosd")
    queue6 = make_element("queue", "queue-output")

    # Add all elements to pipeline
    pipeline.add(source_bin)
    for el in [streammux, queue1, preprocess, queue2, pgie, queue3,
               tiler, queue4, nvvidconv, queue5, nvosd, queue6]:
        pipeline.add(el)

    if postprocess is not None:
        pipeline.add(postprocess)
        pipeline.add(queue_post)
    if tracker:
        pipeline.add(tracker)
        pipeline.add(queue_tracker)

    # Configure elements
    configure_streammux(streammux, params, uri)
    configure_primary_gie(
        pgie=pgie,
        infer_config=params.infer_config,
        pgie_batch_size=params.pgie_batch_size,
        det_interval=params.det_interval,
        input_tensor_meta=True,
    )
    configure_tiler(tiler, params.tiled_width, params.tiled_height)
    if tracker and params.tracker_config:
        configure_tracker(tracker, params.tracker_config)

    attach_source_to_streammux(source_bin, streammux)

    # Link: mux → preprocess → pgie
    link_elements(streammux, queue1, preprocess, queue2, pgie, queue3)

    # Build the chain after pgie: [postprocess] → [tracker] → tiler → osd
    post_pgie = queue3
    if postprocess is not None:
        link_elements(queue3, postprocess, queue_post)
        post_pgie = queue_post

    if tracker:
        link_elements(post_pgie, tracker, queue_tracker, tiler, queue4, nvvidconv, queue5, nvosd, queue6)
    else:
        link_elements(post_pgie, tiler, queue4, nvvidconv, queue5, nvosd, queue6)

    appsink = add_output_branches(
        pipeline=pipeline,
        upstream=queue6,
        display=params.display,
        publish_annotated=params.publish_annotated,
    )

    return PipelineBuildResult(
        pipeline=pipeline,
        pgie=pgie,
        appsink=appsink,
        has_native_postprocess=(postprocess is not None),
    )
