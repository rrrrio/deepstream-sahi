#!/usr/bin/env python3
"""
File: pipeline_sahi.py
Description: Builds the DeepStream SAHI-mode GStreamer pipeline.
Author: Yuhao Ding
"""

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
    link_elements,
    make_element,
    to_uri,
)


def build_pipeline(params: PipelineBuildParams) -> PipelineBuildResult:
    if not params.preprocess_config:
        raise ValueError("preprocess_config is required for SAHI mode")

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
    preprocess = make_element("nvdspreprocess", "preprocess")
    queue2 = make_element("queue", "queue-pgie-in")
    pgie = make_element("nvinfer", "primary-gie")
    queue3 = make_element("queue", "queue-post-pgie")

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

    pipeline.add(source_bin)
    for element in [streammux, queue1, preprocess, queue2, pgie, queue3, tiler, queue4, nvvidconv, queue5, nvosd, queue6]:
        pipeline.add(element)
    if tracker:
        pipeline.add(tracker)
        pipeline.add(queue_tracker)

    configure_streammux(streammux, params, uri)
    preprocess.set_property("config-file", params.preprocess_config)
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

    link_elements(streammux, queue1, preprocess, queue2, pgie, queue3)
    if tracker:
        link_elements(queue3, tracker, queue_tracker, tiler, queue4, nvvidconv, queue5, nvosd, queue6)
    else:
        link_elements(queue3, tiler, queue4, nvvidconv, queue5, nvosd, queue6)

    appsink = add_output_branches(
        pipeline=pipeline,
        upstream=queue6,
        display=params.display,
        publish_annotated=params.publish_annotated,
    )

    return PipelineBuildResult(pipeline=pipeline, pgie=pgie, appsink=appsink)
