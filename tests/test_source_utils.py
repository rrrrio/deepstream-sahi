import stat
from types import SimpleNamespace

import pytest

from deepstream.source_utils import (
    is_live_source,
    is_v4l2_source,
    v4l2_device_path,
)


source_utils = pytest.importorskip("deepstream.source_utils")


def test_detects_device_path_and_uri():
    assert is_v4l2_source("/dev/video0")
    assert is_v4l2_source("v4l2:///dev/video1")
    assert is_v4l2_source(
        "/dev/v4l/by-path/platform-tegra-capture-vi-video-index0"
    )
    assert is_v4l2_source(
        "v4l2:///dev/v4l/by-path/platform-tegra-capture-vi-video-index6"
    )


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
    stable_path = "/dev/v4l/by-path/platform-tegra-capture-vi-video-index8"
    assert v4l2_device_path(stable_path) == stable_path
    assert v4l2_device_path(f"v4l2://{stable_path}") == stable_path


def test_rejects_malformed_v4l2_uri():
    try:
        v4l2_device_path("v4l2://camera0")
    except ValueError:
        pass
    else:
        raise AssertionError("malformed V4L2 URI was accepted")


def test_validates_v4l2_character_devices(monkeypatch):
    stable_path = "/dev/v4l/by-path/platform-tegra-capture-vi-video-index0"
    monkeypatch.setattr(
        source_utils.os,
        "stat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFCHR),
    )

    assert source_utils.validate_v4l2_device(stable_path) == stable_path


def test_rejects_missing_or_non_character_v4l2_devices(monkeypatch):
    stable_path = "/dev/v4l/by-path/platform-tegra-capture-vi-video-index0"

    def missing(_path):
        raise FileNotFoundError

    monkeypatch.setattr(source_utils.os, "stat", missing)
    with pytest.raises(ValueError, match="does not exist"):
        source_utils.validate_v4l2_device(stable_path)

    monkeypatch.setattr(
        source_utils.os,
        "stat",
        lambda _path: SimpleNamespace(st_mode=stat.S_IFREG),
    )
    with pytest.raises(ValueError, match="not a character device"):
        source_utils.validate_v4l2_device(stable_path)


def test_headless_v4l2_selects_surfaceless_egl(monkeypatch):
    monkeypatch.setenv("EGL_PLATFORM", "x11")
    sources = [
        "/dev/v4l/by-path/platform-tegra-capture-vi-video-index0",
        "/dev/v4l/by-path/platform-tegra-capture-vi-video-index6",
        "/dev/v4l/by-path/platform-tegra-capture-vi-video-index8",
    ]

    assert source_utils.configure_headless_v4l2_egl(sources, False, False)
    assert source_utils.os.environ["EGL_PLATFORM"] == "surfaceless"


def test_display_v4l2_does_not_override_egl_platform(monkeypatch):
    monkeypatch.setenv("EGL_PLATFORM", "x11")

    assert not source_utils.configure_headless_v4l2_egl(
        ["/dev/video0"], True, False
    )
    assert source_utils.os.environ["EGL_PLATFORM"] == "x11"


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
    ) == (
        ["argus://0", "argus://1", "argus://2"],
        ["camera_0", "camera_1", "camera_2"],
    )

    with pytest.raises(ValueError):
        source_utils.normalize_source_config("", ["a", "b"], ["only-one-name"])
    with pytest.raises(ValueError):
        source_utils.normalize_source_config("", [], ["camera_0"])
    with pytest.raises(ValueError):
        source_utils.normalize_source_config("", ["argus://0"], ["camera-0"])
