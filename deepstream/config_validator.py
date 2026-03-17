#!/usr/bin/env python3
"""
File: config_validator.py
Description: Runtime validation helpers for DeepStream infer/preprocess/tracker configs.
             Supports both static preprocess config and dynamic generation (config=None).
Author: Yuhao Ding
"""

import os
from typing import Optional

from .pipeline_common import read_ini, resolve_path


def _log_info(logger, text: str):
    if logger is not None:
        logger.info(text)


def _log_warn(logger, text: str):
    if logger is not None:
        logger.warning(text)


def validate_file_exists(path: str, label: str):
    if not path:
        raise ValueError(f"{label} path is empty")
    if not os.path.isfile(path):
        raise FileNotFoundError(f"{label} not found: {path}")


def validate_infer_config(infer_config: str, logger=None):
    validate_file_exists(infer_config, "Infer config")

    cfg = read_ini(infer_config)
    if "property" not in cfg:
        raise ValueError(f"{infer_config} missing [property] section")

    prop = cfg["property"]
    keys_to_require = ["onnx-file", "labelfile-path", "custom-lib-path"]
    for key in keys_to_require:
        value = prop.get(key, fallback="").strip()
        if not value:
            raise ValueError(f"{infer_config} missing required key '{key}' in [property]")
        resolved = resolve_path(infer_config, value)
        validate_file_exists(resolved, f"{key} ({infer_config})")

    engine_value = prop.get("model-engine-file", fallback="").strip()
    if engine_value:
        engine_path = resolve_path(infer_config, engine_value)
        engine_dir = os.path.dirname(engine_path)
        if engine_dir and not os.path.isdir(engine_dir):
            raise FileNotFoundError(f"Engine directory does not exist: {engine_dir}")
        if not os.path.exists(engine_path):
            _log_warn(logger, f"Engine file does not exist yet (will be generated at runtime): {engine_path}")


def validate_preprocess_config(preprocess_config: str):
    validate_file_exists(preprocess_config, "Preprocess config")
    cfg = read_ini(preprocess_config)
    if "property" not in cfg:
        raise ValueError(f"{preprocess_config} missing [property] section")

    prop = cfg["property"]
    width = prop.getint("processing-width", fallback=0)
    height = prop.getint("processing-height", fallback=0)
    tensor_name = prop.get("tensor-name", fallback="").strip()

    if width <= 0 or height <= 0:
        raise ValueError(f"Invalid preprocess size in {preprocess_config}: {width}x{height}")
    if not tensor_name:
        raise ValueError(f"Missing tensor-name in {preprocess_config}")


def validate_sahi_compat(preprocess_config: str, infer_config: str, logger=None):
    pre_cfg = read_ini(preprocess_config)
    inf_cfg = read_ini(infer_config)

    pre_prop = pre_cfg["property"]
    inf_prop = inf_cfg["property"]

    pre_w = pre_prop.getint("processing-width")
    pre_h = pre_prop.getint("processing-height")
    pre_tensor_name = pre_prop.get("tensor-name", fallback="").strip()

    onnx_file = inf_prop.get("onnx-file", fallback="").strip()
    if not onnx_file:
        raise ValueError(f"onnx-file is required in {infer_config}")

    onnx_path = resolve_path(infer_config, onnx_file)
    validate_file_exists(onnx_path, "ONNX file")

    try:
        import onnx  # pylint: disable=import-outside-toplevel
    except Exception:
        _log_warn(
            logger,
            "python 'onnx' module not available, skipping SAHI shape/tensor-name validation.",
        )
        return

    model = onnx.load(onnx_path)
    if not model.graph.input:
        raise RuntimeError(f"ONNX model has no input tensor: {onnx_path}")

    input_tensor = model.graph.input[0]
    onnx_name = input_tensor.name
    dims = [dim.dim_value for dim in input_tensor.type.tensor_type.shape.dim]
    if len(dims) < 4:
        raise RuntimeError(f"ONNX input rank < 4: {onnx_path}")

    onnx_h = dims[2]
    onnx_w = dims[3]

    if pre_tensor_name and pre_tensor_name != onnx_name:
        raise RuntimeError(
            "tensor-name mismatch between preprocess and ONNX: "
            f"preprocess='{pre_tensor_name}' onnx='{onnx_name}'"
        )

    if onnx_w > 0 and onnx_h > 0 and (onnx_w != pre_w or onnx_h != pre_h):
        raise RuntimeError(
            "Shape mismatch between preprocess and ONNX: "
            f"preprocess={pre_w}x{pre_h}, onnx={onnx_w}x{onnx_h}"
        )

    _log_info(logger, f"SAHI compatibility check OK: preprocess={pre_w}x{pre_h}, onnx={onnx_w}x{onnx_h}")


def validate_runtime_configs(
    mode: str,
    infer_config: str,
    preprocess_config: Optional[str],
    tracker_enabled: bool,
    tracker_config: Optional[str],
    logger=None,
):
    validate_infer_config(infer_config, logger=logger)

    if mode == "sahi":
        if preprocess_config:
            # Static config provided — validate it
            validate_preprocess_config(preprocess_config)
            validate_sahi_compat(preprocess_config, infer_config, logger=logger)
        else:
            _log_info(
                logger,
                "No static preprocess config — ROIs will be generated dynamically from slice parameters.",
            )

    if tracker_enabled:
        if not tracker_config:
            raise ValueError("tracker_config is required when tracker_enabled=true")
        validate_file_exists(tracker_config, "Tracker config")
