import pytest

from deepstream.source_utils import is_live_source, is_v4l2_source, v4l2_device_path


source_utils = pytest.importorskip("deepstream.source_utils")


def test_detects_device_path_and_uri():
    assert is_v4l2_source("/dev/video0")
    assert is_v4l2_source("v4l2:///dev/video1")


def test_rejects_non_camera_sources():
    assert not is_v4l2_source("./samples/input.mp4")
    assert not is_v4l2_source("rtsp://127.0.0.1/live")


def test_detects_live_sources():
    assert is_live_source("/dev/video0")
    assert is_live_source("v4l2:///dev/video1")
    assert is_live_source("rtsps://camera.example/live")
    assert not is_live_source("./samples/input.mp4")


def test_normalizes_v4l2_values():
    assert v4l2_device_path("v4l2:///dev/video2") == "/dev/video2"
    assert v4l2_device_path("/dev/video3") == "/dev/video3"


def test_rejects_malformed_v4l2_uri():
    try:
        v4l2_device_path("v4l2://camera0")
    except ValueError:
        pass
    else:
        raise AssertionError("malformed V4L2 URI was accepted")


def test_classifies_argus_sources_and_validates_sensor_id():
    assert source_utils.is_argus_source("argus://2")
    assert is_live_source("argus://2")
    assert source_utils.argus_sensor_id("argus://2") == 2
    with pytest.raises(ValueError):
        source_utils.argus_sensor_id("argus://camera")


def test_classifies_supported_source_kinds():
    assert source_utils.is_file_source("samples/camera.mp4")
    assert source_utils.is_file_source("file:///tmp/camera.mp4")
    assert not source_utils.is_file_source("rtsp://camera/live")
    assert not source_utils.is_file_source("rtsps://camera/live")
    assert source_utils.is_v4l2_source("/dev/video0")


def test_validates_source_and_name_arrays():
    assert source_utils.normalize_source_config(
        "",
        ["argus://0", "argus://1", "argus://2"],
        ["camera_0", "camera_1", "camera_2"],
    ) == (["argus://0", "argus://1", "argus://2"], ["camera_0", "camera_1", "camera_2"])

    with pytest.raises(ValueError):
        source_utils.normalize_source_config("", ["a", "b"], ["only-one-name"])
    with pytest.raises(ValueError):
        source_utils.normalize_source_config("", [], ["camera_0"])
    with pytest.raises(ValueError):
        source_utils.normalize_source_config("", ["argus://0"], ["camera-0"])
