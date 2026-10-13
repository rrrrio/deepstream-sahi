"""Repository-level contracts for the installed three-camera profiles."""

import pathlib

import pytest
import yaml

from deepstream.config_validator import (
    resolve_runtime_config_path,
    resolve_runtime_asset_path,
    validate_preprocess_config,
    validate_sahi_batch,
)
from deepstream.pipeline_common import compute_sahi_rois
from deepstream.source_utils import (
    is_argus_source,
    is_file_source,
    is_v4l2_source,
    normalize_source_config,
)


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
CONFIG_DIR = REPO_ROOT / "config"


def _profile(name):
    with (CONFIG_DIR / name).open(encoding="utf-8") as stream:
        return yaml.safe_load(stream)["deepstream_node"]["ros__parameters"]


@pytest.mark.parametrize(
    "profile_name, expected_file_sources",
    [
        ("sim_3cam.yaml", True),
        ("sim_3cam_headless.yaml", True),
        ("sim_3cam_traffic.yaml", True),
        ("csi_3cam.yaml", False),
        ("v4l2_3cam.yaml", False),
    ],
)
def test_three_camera_profiles_keep_the_sahi_batch_contract(
    profile_name,
    expected_file_sources,
):
    profile = _profile(profile_name)
    sources, names = normalize_source_config(
        "",
        profile["sources"],
        profile["source_names"],
    )
    roi_count = len(
        compute_sahi_rois(
            profile["width"],
            profile["height"],
            profile["slice_width"],
            profile["slice_height"],
            profile["overlap_h"],
            profile["overlap_v"],
        )
    )

    assert profile["mode"] == "sahi"
    assert len(sources) == len(names) == 3
    expected_roi_count = 7 if profile_name == "sim_3cam_traffic.yaml" else 9
    assert roi_count == expected_roi_count
    assert (len(sources) * roi_count) % profile["pgie_batch_size"] == 0
    assert (
        all(is_file_source(source) for source in sources)
        is expected_file_sources
    )
    if profile_name == "csi_3cam.yaml":
        assert all(is_argus_source(source) for source in sources)
    elif profile_name == "v4l2_3cam.yaml":
        assert all(is_v4l2_source(source) for source in sources)
        assert profile["v4l2_pixel_format"] == "UYVY"
    else:
        assert (
            all(is_argus_source(source) for source in sources)
            is not expected_file_sources
        )


def test_yolov11s_preprocess_config_is_valid_and_matches_batch_one():
    preprocess = CONFIG_DIR / "config_preprocess_yolov11s_sahi.txt"
    validate_preprocess_config(str(preprocess))
    validate_sahi_batch(
        str(preprocess), tensor_batch_size=1, total_roi_count=27
    )


@pytest.mark.parametrize("batch_size,total_rois", [(0, 27), (1, 0), (2, 27)])
def test_sahi_batch_rejects_invalid_or_nondividing_values(
    batch_size, total_rois
):
    preprocess = CONFIG_DIR / "config_preprocess_yolov11s_sahi.txt"
    with pytest.raises(ValueError):
        validate_sahi_batch(str(preprocess), batch_size, total_rois)


def test_sahi_batch_rejects_pools_smaller_than_required_tensor_batches(
    tmp_path,
):
    preprocess = tmp_path / "preprocess.txt"
    preprocess.write_text(
        "[property]\nnetwork-input-shape=1;3;640;640\n"
        "scaling-buf-pool-size=6\ntensor-buf-pool-size=6\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="scaling-buf-pool-size"):
        validate_sahi_batch(
            str(preprocess), tensor_batch_size=1, total_roi_count=27
        )


def test_installed_profile_tracker_path_falls_back_to_package_share(
    tmp_path, monkeypatch
):
    package_share = tmp_path / "share" / "deepstream"
    installed_config = package_share / "config"
    installed_config.mkdir(parents=True)
    tracker = installed_config / "deepstream_tracker_iou_config.txt"
    tracker.write_text("[tracker]\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    resolved = resolve_runtime_config_path(
        "config/deepstream_tracker_iou_config.txt",
        str(package_share),
    )

    assert resolved == str(tracker)


def test_installed_infer_config_assets_fall_back_to_repository(
    tmp_path, monkeypatch
):
    package_share = tmp_path / "share" / "deepstream"
    installed_config = package_share / "config"
    installed_config.mkdir(parents=True)
    infer = installed_config / "config_infer_primary_yolov11s_sahi.txt"
    infer.write_text(
        "[property]\nonnx-file=../models/yolo11s.onnx\n", encoding="utf-8"
    )

    repository = tmp_path / "repository"
    repository_config = repository / "config"
    repository_models = repository / "models"
    repository_config.mkdir(parents=True)
    repository_models.mkdir()
    (repository_config / infer.name).write_text(
        infer.read_text(), encoding="utf-8"
    )
    model = repository_models / "yolo11s.onnx"
    model.write_bytes(b"model")
    monkeypatch.setenv("DEEPSTREAM_SAHI_ROOT", str(repository))

    resolved = resolve_runtime_asset_path(str(infer), "../models/yolo11s.onnx")

    assert resolved == str(model)
