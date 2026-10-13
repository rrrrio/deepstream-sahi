"""Compile and run the CPU-only DeepStream YOLOv11s parser harness."""

import pathlib
import shutil
import subprocess

import pytest


REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PARSER_DIR = REPO_ROOT / "plugins" / "yolov11s"
DEEPSTREAM_DIR = pathlib.Path("/opt/nvidia/deepstream/deepstream")
PARSER_HEADER = (
    DEEPSTREAM_DIR / "sources" / "includes" / "nvdsinfer_custom_impl.h"
)


@pytest.mark.skipif(
    not PARSER_HEADER.is_file() or shutil.which("g++") is None,
    reason="DeepStream parser headers and g++ are required",
)
def test_yolov11s_parser_harness():
    result = subprocess.run(
        [
            "make",
            "-C",
            str(PARSER_DIR),
            "test",
            f"DEEPSTREAM_DIR={DEEPSTREAM_DIR}",
            "CUDA_DIR=/usr/local/cuda",
            "TENSORRT_INCLUDE_DIR=/usr/include/aarch64-linux-gnu",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "NvDsInferParseYolo11 harness passed" in result.stdout
