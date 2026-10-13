import configparser
from pathlib import Path

import onnx
import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def _dims(value_info):
    return [
        dimension.dim_param or dimension.dim_value
        for dimension in value_info.type.tensor_type.shape.dim
    ]


def test_yolov11s_onnx_has_dynamic_batch_and_raw_detection_output():
    model_path = REPO_ROOT / "models" / "yolo11s.onnx"
    if not model_path.is_file():
        pytest.skip("run scripts/fetch_yolov11s_onnx.sh to install the model asset")

    model = onnx.load(model_path)
    assert model.graph.input[0].name == "images"
    assert _dims(model.graph.input[0]) == ["batch", 3, 640, 640]
    assert model.graph.output[0].name == "output0"
    assert _dims(model.graph.output[0]) == ["batch", 84, "anchors"]


@pytest.mark.parametrize(
    "config_name",
    [
        "config_infer_primary_yolov11s.txt",
        "config_infer_primary_yolov11s_sahi.txt",
    ],
)
def test_yolov11s_configs_match_assets_and_custom_parser(config_name):
    config_path = REPO_ROOT / "config" / config_name
    parser = configparser.ConfigParser()
    assert parser.read(config_path)
    prop = parser["property"]

    assert prop["onnx-file"] == "../models/yolo11s.onnx"
    assert prop["labelfile-path"] == "../models/coco80.txt"
    assert prop["parse-bbox-func-name"] == "NvDsInferParseYolo11"
    assert prop["output-blob-names"] == "output0"
    assert prop["custom-lib-path"] == "../lib/libnvdsinfer_yolov11s.so"

    labels = (REPO_ROOT / "models" / "coco80.txt").read_text(encoding="utf-8").splitlines()
    assert len(labels) == 80
    assert labels[0] == "person"
    assert labels[-1] == "toothbrush"
