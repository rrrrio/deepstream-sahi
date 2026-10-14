#!/usr/bin/env python3
"""
File: pipeline_base.py
Description: Builds the DeepStream base-mode GStreamer pipeline.
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
)


def build_pipeline(params: PipelineBuildParams) -> PipelineBuildResult:
    Gst.init(None)
    params = normalize_pipeline_build_params(params)

    pipeline = Gst.Pipeline.new("deepstream-base-pipeline")
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
    queue1 = make_element("queue", "queue-pgie-in")
    pgie = make_element("nvinfer", "primary-gie")
    queue2 = make_element("queue", "queue-post-pgie")

    tracker = None
    tracker_convert = None
    tracker_caps = None
    queue_tracker = None
    if params.tracker_enabled:
        tracker_convert = make_element("nvvideoconvert", "tracker-convert")
        tracker_caps = make_element("capsfilter", "tracker-rgba-caps")
        tracker = make_element("nvtracker", "tracker")
        queue_tracker = make_element("queue", "queue-tracker")

    for source_bin in source_bins:
        pipeline.add(source_bin)
    for element in [streammux, queue1, pgie, queue2]:
        pipeline.add(element)
    if tracker:
        pipeline.add(tracker_convert)
        pipeline.add(tracker_caps)
        pipeline.add(tracker)
        pipeline.add(queue_tracker)

    configure_streammux(streammux, params)
    configure_primary_gie(
        pgie=pgie,
        infer_config=params.infer_config,
        pgie_batch_size=params.pgie_batch_size,
        det_interval=params.det_interval,
        input_tensor_meta=False,
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

    link_elements(streammux, queue1, pgie, queue2)
    post_tracker = queue2
    if tracker:
        link_elements(
            queue2, tracker_convert, tracker_caps, tracker, queue_tracker
        )
        post_tracker = queue_tracker

    annotated_appsinks = add_output_branches(
        pipeline=pipeline,
        upstream=post_tracker,
        display=params.display,
        publish_annotated=params.publish_annotated,
        source_names=params.source_names,
        tiled_width=params.tiled_width,
        tiled_height=params.tiled_height,
    )

    metadata_probe_element = queue_tracker if tracker else queue2
    return PipelineBuildResult(
        pipeline=pipeline,
        pgie=pgie,
        annotated_appsinks=annotated_appsinks,
        metadata_probe_element=metadata_probe_element,
        class_filter_element=queue2,
        has_native_postprocess=False,
    )
