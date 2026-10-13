"""GPU-free contracts for SAHI geometry and metadata normalization.

The production modules import DeepStream bindings, so this file installs the
smallest import stubs needed to exercise their pure helpers in CI containers.
"""

import sys
import types

import pytest


class _GstStub:
    Element = object

    class Pipeline:
        pass


gi_stub = types.ModuleType("gi")
gi_stub.require_version = lambda *_args: None
gi_repository = types.ModuleType("gi.repository")
gi_repository.Gst = _GstStub
gi_stub.repository = gi_repository
sys.modules.setdefault("gi", gi_stub)
sys.modules.setdefault("gi.repository", gi_repository)
sys.modules.setdefault("pyds", types.ModuleType("pyds"))

geometry_msgs = types.ModuleType("geometry_msgs")
geometry_msgs_msg = types.ModuleType("geometry_msgs.msg")
geometry_msgs_msg.Point = type("Point", (), {})
geometry_msgs.msg = geometry_msgs_msg
sys.modules.setdefault("geometry_msgs", geometry_msgs)
sys.modules.setdefault("geometry_msgs.msg", geometry_msgs_msg)

std_msgs = types.ModuleType("std_msgs")
std_msgs_msg = types.ModuleType("std_msgs.msg")
std_msgs_msg.Header = type("Header", (), {})
std_msgs.msg = std_msgs_msg
sys.modules.setdefault("std_msgs", std_msgs)
sys.modules.setdefault("std_msgs.msg", std_msgs_msg)

from deepstream.pipeline_common import compute_sahi_rois  # noqa: E402
from deepstream.pipeline_common import compute_tiler_layout  # noqa: E402


def test_1080p_trafficcamnet_profile_produces_ten_rois():
    rois = compute_sahi_rois(1920, 1080, 960, 544, 0.2, 0.2)

    assert len(rois) == 10
    assert rois[-1] == (0, 0, 1920, 1080)
    assert all(width == 960 and height == 544 for _, _, width, height in rois[:-1])


def test_1080p_yolov11s_profile_produces_nine_rois():
    rois = compute_sahi_rois(1920, 1080, 640, 640, 0.2, 0.2)

    assert len(rois) == 9
    assert rois[-1] == (0, 0, 1920, 1080)
    assert all(width == 640 and height == 640 for _, _, width, height in rois[:-1])


def test_sahi_rois_have_expected_overlap_and_stay_in_frame():
    rois = compute_sahi_rois(1920, 1080, 960, 544, 0.2, 0.2)

    for left, top, width, height in rois:
        assert 0 <= left <= 1920 - width
        assert 0 <= top <= 1080 - height

    horizontal = sorted({left for left, top, width, height in rois[:-1] if top == 0})
    vertical = sorted({top for left, top, width, height in rois[:-1] if left == 0})
    assert horizontal == [0, 768, 960]
    assert vertical == [0, 436, 536]


def test_three_sources_use_a_two_by_two_tiler():
    assert compute_tiler_layout(3) == (2, 2)


def test_tracking_id_and_confidence_contract():
    from deepstream.metadata_utils import select_confidence, tracking_id_string

    assert tracking_id_string(42) == "42"
    assert tracking_id_string(-1) == ""
    assert select_confidence(0.4, 0.8) == 0.4
    assert select_confidence(-1.0, 0.75) == 0.75


def test_ntp_timestamp_conversion_uses_nanoseconds():
    from deepstream.metadata_utils import ntp_timestamp_parts

    assert ntp_timestamp_parts(1_234_567_890) == (1, 234_567_890)
    assert ntp_timestamp_parts(0) is None


def test_runtime_infer_config_uses_writable_batch_specific_engine(tmp_path):
    from deepstream.config_validator import prepare_runtime_infer_config, read_ini

    model = tmp_path / "model.onnx"
    labels = tmp_path / "labels.txt"
    calib = tmp_path / "cal.bin"
    for path in (model, labels, calib):
        path.write_bytes(b"test")

    source_config = tmp_path / "infer.txt"
    source_config.write_text(
        "[property]\n"
        "onnx-file=model.onnx\n"
        "labelfile-path=labels.txt\n"
        "int8-calib-file=cal.bin\n"
        "network-mode=1\n"
        "batch-size=1\n",
        encoding="utf-8",
    )

    runtime_path = prepare_runtime_infer_config(
        str(source_config),
        str(tmp_path / "engines"),
        30,
    )
    runtime = read_ini(runtime_path)["property"]

    assert runtime.getint("batch-size") == 30
    assert runtime["onnx-file"] == str(model)
    assert runtime["model-engine-file"].endswith("model.onnx_b30_gpu0_int8.engine")


def test_runtime_preprocess_config_matches_active_roi_batch(tmp_path):
    from deepstream.config_validator import prepare_runtime_preprocess_config, read_ini

    source_config = tmp_path / "preprocess.txt"
    source_config.write_text(
        "[property]\n"
        "processing-width=960\n"
        "processing-height=544\n"
        "tensor-name=input_1:0\n"
        "network-input-shape=30;3;544;960\n",
        encoding="utf-8",
    )

    runtime_path = prepare_runtime_preprocess_config(
        str(source_config),
        str(tmp_path / "runtime"),
        10,
    )

    runtime = read_ini(runtime_path)["property"]
    assert runtime["network-input-shape"] == "10;3;544;960"


def test_runtime_preprocess_config_expands_pools_for_all_tensor_batches(tmp_path):
    from deepstream.config_validator import prepare_runtime_preprocess_config, read_ini

    source_config = tmp_path / "preprocess.txt"
    source_config.write_text(
        "[property]\n"
        "processing-width=640\n"
        "processing-height=640\n"
        "tensor-name=images\n"
        "network-input-shape=1;3;640;640\n"
        "scaling-buf-pool-size=6\n"
        "tensor-buf-pool-size=6\n",
        encoding="utf-8",
    )

    runtime_path = prepare_runtime_preprocess_config(
        str(source_config),
        str(tmp_path / "runtime"),
        batch_size=1,
        total_roi_count=27,
    )
    runtime = read_ini(runtime_path)["property"]

    assert runtime.getint("scaling-buf-pool-size") == 27
    assert runtime.getint("tensor-buf-pool-size") == 27


def test_pipeline_params_retain_legacy_single_source_contract():
    from deepstream.pipeline_common import (
        PipelineBuildParams,
        normalize_pipeline_build_params,
    )

    params = normalize_pipeline_build_params(
        PipelineBuildParams(
            infer_config="/tmp/infer.txt",
            source="file:///tmp/input.mp4",
        )
    )

    assert params.sources == ["file:///tmp/input.mp4"]
    assert params.source_names == ["camera_0"]


def test_pipeline_params_reject_missing_sources_before_building():
    from deepstream.pipeline_common import (
        PipelineBuildParams,
        normalize_pipeline_build_params,
    )

    with pytest.raises(ValueError, match="at least one source"):
        normalize_pipeline_build_params(PipelineBuildParams(infer_config="/tmp/infer.txt"))


class _FakePad:
    def __init__(self):
        self.peer = None

    def link(self, peer):
        self.peer = peer
        return 0


class _FakeElement:
    def __init__(self, name, supported_properties=()):
        self.name = name
        self.properties = {}
        self.supported_properties = set(supported_properties)
        self.pads = {"src": _FakePad()}
        self.requested_pads = []

    def set_property(self, name, value):
        self.properties[name] = value

    def find_property(self, name):
        return object() if name in self.supported_properties else None

    def get_name(self):
        return self.name

    def get_static_pad(self, name):
        return self.pads.get(name)

    def request_pad_simple(self, name):
        self.requested_pads.append(name)
        pad = _FakePad()
        self.pads[name] = pad
        return pad


def test_three_source_streammux_and_tiler_configuration(monkeypatch):
    from deepstream import pipeline_common

    params = pipeline_common.PipelineBuildParams(
        infer_config="/tmp/infer.txt",
        sources=["file:///a.mp4", "file:///b.mp4", "file:///c.mp4"],
        source_names=["camera_0", "camera_1", "camera_2"],
    )
    mux = _FakeElement("streammux", {"attach-sys-ts", "sync-inputs"})
    tiler = _FakeElement("tiler")

    pipeline_common.configure_streammux(mux, params)
    pipeline_common.configure_tiler(tiler, 1280, 720, 3)

    assert mux.properties == {
        "width": 1920,
        "height": 1080,
        "batch-size": 3,
        "batched-push-timeout": 40000,
        "live-source": 0,
        "attach-sys-ts": True,
        "sync-inputs": False,
    }
    assert tiler.properties == {"rows": 2, "columns": 2, "width": 1280, "height": 720}

    source = _FakeElement("source")
    monkeypatch.setattr(
        pipeline_common.Gst,
        "PadLinkReturn",
        types.SimpleNamespace(OK=0),
        raising=False,
    )
    pipeline_common.attach_source_to_streammux(source, mux, 2)
    assert mux.requested_pads == ["sink_2"]
    assert source.pads["src"].peer is mux.pads["sink_2"]


def test_live_source_marks_streammux_live():
    from deepstream.pipeline_common import PipelineBuildParams, configure_streammux

    params = PipelineBuildParams(
        infer_config="/tmp/infer.txt",
        sources=["file:///a.mp4", "argus://1", "rtsp://camera/live"],
        source_names=["camera_0", "camera_1", "camera_2"],
    )
    mux = _FakeElement("streammux")

    configure_streammux(mux, params)

    assert mux.properties["batch-size"] == 3
    assert mux.properties["live-source"] == 1


def test_display_queue_drops_frames_when_the_osd_sink_stalls(monkeypatch):
    from deepstream import pipeline_common

    class FakePipeline:
        def add(self, _element):
            return True

    class LinkableElement(_FakeElement):
        def link(self, _downstream):
            return True

    elements = {}

    def make(factory, name):
        element = LinkableElement(name)
        elements[name] = element
        return element

    monkeypatch.setattr(pipeline_common, "make_element", make)

    pipeline_common.add_output_branches(
        pipeline=FakePipeline(),
        upstream=LinkableElement("upstream"),
        display=True,
        publish_annotated=False,
    )

    assert elements["queue-display"].properties["leaky"] == 2
    assert elements["queue-display"].properties["max-size-buffers"] == 1
    assert elements["display-sink"].properties["async"] is False


def test_tracker_tensor_meta_is_configured_only_when_requested(tmp_path):
    from deepstream.pipeline_common import configure_tracker

    config = tmp_path / "tracker.txt"
    config.write_text("[tracker]\ntracker-width=640\n", encoding="utf-8")
    tracker = _FakeElement(
        "tracker",
        {"tracker-width", "input-tensor-meta", "tensor-meta-gie-id", "display-tracking-id"},
    )

    configure_tracker(
        tracker,
        str(config),
        input_tensor_meta=True,
        tensor_meta_gie_id=1,
    )

    assert tracker.properties["input-tensor-meta"] is True
    assert tracker.properties["tensor-meta-gie-id"] == 1


def test_native_sahi_plugin_properties_and_metric_validation(monkeypatch):
    from deepstream.pipeline_common import MergeConfig, PipelineBuildParams
    from deepstream import pipeline_sahi

    required_preprocess = {
        "config-file",
        "slice-width",
        "slice-height",
        "overlap-width-ratio",
        "overlap-height-ratio",
        "enable-full-frame",
    }
    required_postprocess = {
        "gie-ids",
        "match-metric",
        "match-threshold",
        "class-agnostic",
        "enable-merge",
        "two-phase-nmm",
    }
    preprocess = _FakeElement("sahi-preprocess", required_preprocess)
    postprocess = _FakeElement("sahi-postprocess", required_postprocess)

    def make_plugin(factory, _name):
        return preprocess if factory == "nvsahipreprocess" else postprocess

    monkeypatch.setattr(pipeline_sahi, "try_make_element", make_plugin)
    params = PipelineBuildParams(
        infer_config="/tmp/infer.txt",
        sources=["file:///a.mp4"],
        source_names=["camera_0"],
        preprocess_config="/tmp/preprocess.txt",
        slice_width=640,
        slice_height=640,
        overlap_h=0.2,
        overlap_v=0.3,
        merge_config=MergeConfig(True, "ios", 0.7, True),
    )

    assert pipeline_sahi._setup_native_preprocess(params) is preprocess
    assert preprocess.properties == {
        "config-file": "/tmp/preprocess.txt",
        "slice-width": 640,
        "slice-height": 640,
        "overlap-width-ratio": 0.2,
        "overlap-height-ratio": 0.3,
        "enable-full-frame": True,
    }
    assert pipeline_sahi._setup_native_postprocess(params) is postprocess
    assert postprocess.properties == {
        "gie-ids": "1",
        "match-metric": 1,
        "match-threshold": 0.7,
        "class-agnostic": True,
        "enable-merge": True,
        "two-phase-nmm": True,
    }

    params.merge_config = MergeConfig(True, "invalid", 0.5, False)
    with pytest.raises(ValueError, match="match metric"):
        pipeline_sahi._setup_native_postprocess(params)


def test_uri_source_bin_registers_recursive_decodebin_callback(monkeypatch):
    from deepstream import pipeline_common

    callbacks = []

    class FakeUriSource(_FakeElement):
        def connect(self, signal, callback, user_data):
            callbacks.append((signal, callback, user_data))

    class FakeBin(_FakeElement):
        def add(self, _element):
            return True

        def add_pad(self, _pad):
            return True

    fake_source = FakeUriSource("uri-source")
    monkeypatch.setattr(
        pipeline_common.Gst,
        "Bin",
        types.SimpleNamespace(new=lambda name: FakeBin(name)),
        raising=False,
    )
    monkeypatch.setattr(
        pipeline_common.Gst,
        "ElementFactory",
        types.SimpleNamespace(make=lambda _factory, _name: fake_source),
        raising=False,
    )
    monkeypatch.setattr(
        pipeline_common.Gst,
        "GhostPad",
        types.SimpleNamespace(new_no_target=lambda *_args: object()),
        raising=False,
    )
    monkeypatch.setattr(
        pipeline_common.Gst,
        "PadDirection",
        types.SimpleNamespace(SRC="src"),
        raising=False,
    )

    source_bin = pipeline_common.create_uri_source_bin(0, "file:///tmp/input.mp4", True)

    assert source_bin is not None
    assert [signal for signal, _callback, _data in callbacks] == [
        "pad-added",
        "child-added",
    ]


def test_local_file_paths_support_plain_paths_and_file_uris():
    from deepstream.pipeline_common import is_h264_mp4_file, local_file_path

    assert local_file_path("file:///tmp/camera%200.mp4") == "/tmp/camera 0.mp4"
    assert is_h264_mp4_file("/tmp/camera.mp4")
    assert not is_h264_mp4_file("rtsp://camera/live")
