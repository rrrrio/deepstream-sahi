#!/usr/bin/env python3
"""
File: pipeline_base.py
Description: Builds the DeepStream base-mode GStreamer pipeline.
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
    Gst.init(None)

    pipeline = Gst.Pipeline.new("deepstream-base-pipeline")
    if not pipeline:
        raise RuntimeError("Unable to create GstPipeline")

    uri = to_uri(params.source)
    source_bin = create_source_bin(0, uri)
    if not source_bin:
        raise RuntimeError("Unable to create source bin")

    streammux = make_element("nvstreammux", "streammux")
    queue1 = make_element("queue", "queue-pgie-in")
    pgie = make_element("nvinfer", "primary-gie")
    queue2 = make_element("queue", "queue-post-pgie")

    tracker = None
    queue_tracker = None
    if params.tracker_enabled:
        tracker = make_element("nvtracker", "tracker")
        queue_tracker = make_element("queue", "queue-tracker")

    tiler = make_element("nvmultistreamtiler", "tiler")
    queue3 = make_element("queue", "queue-tiler")
    nvvidconv = make_element("nvvideoconvert", "nvvidconv")
    queue4 = make_element("queue", "queue-conv")
    nvosd = make_element("nvdsosd", "nvosd")
    queue5 = make_element("queue", "queue-output")

    pipeline.add(source_bin)
    for element in [streammux, queue1, pgie, queue2, tiler, queue3, nvvidconv, queue4, nvosd, queue5]:
        pipeline.add(element)
    if tracker:
        pipeline.add(tracker)
        pipeline.add(queue_tracker)

    configure_streammux(streammux, params, uri)
    configure_primary_gie(
        pgie=pgie,
        infer_config=params.infer_config,
        pgie_batch_size=params.pgie_batch_size,
        det_interval=params.det_interval,
        input_tensor_meta=False,
    )
    configure_tiler(tiler, params.tiled_width, params.tiled_height)

    if tracker and params.tracker_config:
        configure_tracker(tracker, params.tracker_config)

    attach_source_to_streammux(source_bin, streammux)

    link_elements(streammux, queue1, pgie, queue2)
    if tracker:
        link_elements(queue2, tracker, queue_tracker, tiler, queue3, nvvidconv, queue4, nvosd, queue5)
    else:
        link_elements(queue2, tiler, queue3, nvvidconv, queue4, nvosd, queue5)

    appsink = add_output_branches(
        pipeline=pipeline,
        upstream=queue5,
        display=params.display,
        publish_annotated=params.publish_annotated,
    )

    return PipelineBuildResult(pipeline=pipeline, pgie=pgie, appsink=appsink)
