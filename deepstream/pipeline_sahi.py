#!/usr/bin/env python3
"""
File: pipeline_sahi.py
Description: Builds the DeepStream SAHI-mode GStreamer pipeline.

             Native layout:
               source → mux → nvsahipreprocess → pgie → nvsahipostprocess → tracker → tiler → osd → sink

Author: Yuhao Ding
"""

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

from .pipeline_common import (  # noqa: E402
    PipelineBuildParams,
    PipelineBuildResult,
    add_output_branches,
    attach_source_to_streammux,
    configure_primary_gie,
    configure_streammux,
    configure_tracker,
    create_source_bin,
    link_elements,
    make_element,
    normalize_pipeline_build_params,
    serialize_sahi_rois,
    try_make_element,
)


def _require_property(element, name, value):
    if element.find_property(name) is None:
        raise RuntimeError(
            f"{element.get_name()} does not expose required property '{name}'. "
            "Check that the repository native SAHI plugin is loaded."
        )
    element.set_property(name, value)


def _setup_native_preprocess(params):
    """Create the repository native SAHI preprocessor and configure its API."""
    preprocess = try_make_element("nvsahipreprocess", "sahi-preprocess")
    if preprocess is None:
        raise RuntimeError(
            "Required GStreamer plugin 'nvsahipreprocess' is unavailable. "
            "Run scripts/build_sahi_plugins.sh and source scripts/env.sh."
        )
    if not params.preprocess_config:
        raise ValueError("preprocess_config is required for native SAHI mode")

    _require_property(preprocess, "config-file", params.preprocess_config)
    _require_property(preprocess, "slice-width", params.slice_width)
    _require_property(preprocess, "slice-height", params.slice_height)
    _require_property(preprocess, "overlap-width-ratio", params.overlap_h)
    _require_property(preprocess, "overlap-height-ratio", params.overlap_v)
    _require_property(
        preprocess,
        "roi-list",
        serialize_sahi_rois(params.rois),
    )
    _require_property(
        preprocess, "enable-full-frame", params.include_full_frame
    )
    return preprocess


def _setup_native_postprocess(params):
    """Create the repository native NMM postprocessor."""
    merge_cfg = params.merge_config
    postprocess = try_make_element("nvsahipostprocess", "sahi-postprocess")
    if postprocess is None:
        raise RuntimeError(
            "Required GStreamer plugin 'nvsahipostprocess' is unavailable. "
            "Run scripts/build_sahi_plugins.sh and source scripts/env.sh."
        )

    enabled = bool(merge_cfg and merge_cfg.enabled)
    metric = merge_cfg.metric if merge_cfg else "ios"
    if metric not in ("ios", "iou"):
        raise ValueError("SAHI match metric must be one of: ios, iou")
    threshold = merge_cfg.threshold if merge_cfg else 0.5
    class_agnostic = merge_cfg.class_agnostic if merge_cfg else False
    _require_property(postprocess, "gie-ids", "1")
    _require_property(postprocess, "match-metric", 1 if metric == "ios" else 0)
    _require_property(postprocess, "match-threshold", threshold)
    _require_property(postprocess, "class-agnostic", class_agnostic)
    _require_property(postprocess, "enable-merge", enabled)
    _require_property(postprocess, "two-phase-nmm", True)
    return postprocess


def build_pipeline(params: PipelineBuildParams) -> PipelineBuildResult:
    Gst.init(None)
    params = normalize_pipeline_build_params(params)

    pipeline = Gst.Pipeline.new("deepstream-sahi-pipeline")
    if not pipeline:
        raise RuntimeError("Unable to create GstPipeline")

    source_bins = []
    for index, source in enumerate(params.sources):
        source_bin = create_source_bin(index, source, params)
        if not source_bin:
            name = params.source_names[index]
            raise RuntimeError(
                f"Unable to create source {index} ({name}): {source}"
            )
        source_bins.append(source_bin)

    streammux = make_element("nvstreammux", "streammux")
    queue1 = make_element("queue", "queue-preprocess-in")

    preprocess = _setup_native_preprocess(params)

    queue2 = make_element("queue", "queue-pgie-in")
    pgie = make_element("nvinfer", "primary-gie")
    queue3 = make_element("queue", "queue-post-pgie")

    merge_enabled = bool(params.merge_config and params.merge_config.enabled)
    postprocess = queue_post = None
    if merge_enabled:
        postprocess = _setup_native_postprocess(params)
        queue_post = make_element("queue", "queue-postprocess")

    tracker = None
    tracker_convert = None
    tracker_caps = None
    queue_tracker = None
    if params.tracker_enabled:
        tracker_convert = make_element("nvvideoconvert", "tracker-convert")
        tracker_caps = make_element("capsfilter", "tracker-rgba-caps")
        tracker = make_element("nvtracker", "tracker")
        queue_tracker = make_element("queue", "queue-tracker")

    # Add all elements to pipeline
    for source_bin in source_bins:
        pipeline.add(source_bin)
    for el in [streammux, queue1, preprocess, queue2, pgie, queue3]:
        pipeline.add(el)
    if merge_enabled:
        pipeline.add(postprocess)
        pipeline.add(queue_post)
    if tracker:
        pipeline.add(tracker_convert)
        pipeline.add(tracker_caps)
        pipeline.add(tracker)
        pipeline.add(queue_tracker)

    # Configure elements
    configure_streammux(streammux, params)
    configure_primary_gie(
        pgie=pgie,
        infer_config=params.infer_config,
        pgie_batch_size=params.pgie_batch_size,
        det_interval=params.det_interval,
        input_tensor_meta=True,
    )
    if tracker and params.tracker_config:
        configure_tracker(tracker, params.tracker_config)
    if tracker_caps:
        tracker_caps.set_property(
            "caps",
            Gst.Caps.from_string("video/x-raw(memory:NVMM),format=RGBA"),
        )

    for index, source_bin in enumerate(source_bins):
        attach_source_to_streammux(source_bin, streammux, index)

    # Link: mux → preprocess → pgie
    link_elements(streammux, queue1, preprocess, queue2, pgie, queue3)

    # Build the chain after pgie: [postprocess] → [tracker] → tiler → osd.
    post_pgie = queue3
    if merge_enabled:
        link_elements(queue3, postprocess, queue_post)
        post_pgie = queue_post

    if tracker:
        link_elements(
            post_pgie, tracker_convert, tracker_caps, tracker, queue_tracker
        )
        post_tracker = queue_tracker
    else:
        post_tracker = post_pgie

    annotated_appsinks = add_output_branches(
        pipeline=pipeline,
        upstream=post_tracker,
        display=params.display,
        publish_annotated=params.publish_annotated,
        source_names=params.source_names,
        tiled_width=params.tiled_width,
        tiled_height=params.tiled_height,
    )

    metadata_probe_element = queue_tracker if tracker else post_pgie
    return PipelineBuildResult(
        pipeline=pipeline,
        pgie=pgie,
        annotated_appsinks=annotated_appsinks,
        metadata_probe_element=metadata_probe_element,
        class_filter_element=queue3,
        has_native_postprocess=merge_enabled,
    )
