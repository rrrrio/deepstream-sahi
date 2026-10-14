#!/usr/bin/env python3
"""
File: config_validator.py
Description: Runtime validation helpers for DeepStream inference, SAHI, and tracker configs.
Author: Yuhao Ding
"""

import os
import pathlib
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


def resolve_runtime_config_path(path: str, package_share: str) -> str:
    """Resolve an explicit config path for source and installed-package launches.

    A path that exists relative to the caller's working directory wins. This
    preserves repository-root commands. Installed parameter profiles may carry
    ``config/<name>`` though, so a missing relative path falls back to the
    package's installed ``config`` directory.
    """
    value = path.strip()
    if not value or os.path.isabs(value) or os.path.isfile(value):
        return value

    installed = os.path.join(package_share, "config", os.path.basename(value))
    return installed if os.path.isfile(installed) else value


def resolve_runtime_asset_path(config_path: str, value: str) -> str:
    """Resolve an asset from its config, with a repository fallback.

    An installed ROS package can contain a config file without the optional
    model or parser library that was absent when the package was built. When
    the runtime environment points at this repository, retry the same
    relative asset against the repository's source config directory.
    """
    resolved = resolve_path(config_path, value)
    if os.path.isfile(resolved) or os.path.isabs(value):
        return resolved

    project_root = os.environ.get("DEEPSTREAM_SAHI_ROOT", "").strip()
    if project_root:
        repository_config = os.path.join(
            project_root,
            "config",
            os.path.basename(config_path),
        )
        repository_asset = resolve_path(repository_config, value)
        if os.path.isfile(repository_asset):
            return repository_asset
    return resolved


def validate_infer_config(infer_config: str, logger=None):
    validate_file_exists(infer_config, "Infer config")

    cfg = read_ini(infer_config)
    if "property" not in cfg:
        raise ValueError(f"{infer_config} missing [property] section")

    prop = cfg["property"]
    keys_to_require = ["onnx-file", "labelfile-path"]
    for key in keys_to_require:
        value = prop.get(key, fallback="").strip()
        if not value:
            raise ValueError(
                f"{infer_config} missing required key '{key}' in [property]"
            )
        resolved = resolve_runtime_asset_path(infer_config, value)
        validate_file_exists(resolved, f"{key} ({infer_config})")

    custom_lib = prop.get("custom-lib-path", fallback="").strip()
    if custom_lib:
        validate_file_exists(
            resolve_runtime_asset_path(infer_config, custom_lib),
            f"custom-lib-path ({infer_config})",
        )

    calib_file = prop.get("int8-calib-file", fallback="").strip()
    if prop.getint("network-mode", fallback=0) == 1 and calib_file:
        validate_file_exists(
            resolve_runtime_asset_path(infer_config, calib_file),
            f"int8-calib-file ({infer_config})",
        )

    engine_value = prop.get("model-engine-file", fallback="").strip()
    if engine_value:
        engine_path = resolve_path(infer_config, engine_value)
        engine_dir = os.path.dirname(engine_path)
        if engine_dir and not os.path.isdir(engine_dir):
            raise FileNotFoundError(
                f"Engine directory does not exist: {engine_dir}"
            )
        if not os.path.exists(engine_path):
            _log_warn(
                logger,
                f"Engine file does not exist yet (will be generated at runtime): {engine_path}",
            )


def prepare_runtime_infer_config(
    infer_config: str,
    engine_cache_dir: str,
    batch_size: int,
) -> str:
    """Write a runtime config with absolute assets and a writable engine cache."""
    cfg = read_ini(infer_config)
    if "property" not in cfg:
        raise ValueError(f"{infer_config} missing [property] section")

    prop = cfg["property"]
    for key in (
        "onnx-file",
        "labelfile-path",
        "int8-calib-file",
        "custom-lib-path",
    ):
        value = prop.get(key, fallback="").strip()
        if value:
            resolved = resolve_runtime_asset_path(infer_config, value)
            if key == "custom-lib-path" and not os.path.isfile(resolved):
                project_root = os.environ.get("DEEPSTREAM_SAHI_ROOT", "")
                fallback = os.path.join(
                    project_root,
                    ".local",
                    "lib",
                    os.path.basename(value),
                )
                if project_root and os.path.isfile(fallback):
                    resolved = fallback
            prop[key] = resolved

    cache_dir = pathlib.Path(engine_cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    onnx_name = pathlib.Path(prop.get("onnx-file", "model.onnx")).stem
    network_mode = prop.getint("network-mode", fallback=0)
    precision = {0: "fp32", 1: "int8", 2: "fp16"}.get(network_mode, "mode")
    prop["batch-size"] = str(batch_size)
    onnx_path = pathlib.Path(prop.get("onnx-file", "model.onnx"))
    sdk_engine = pathlib.Path(
        f"{onnx_path}_b{batch_size}_gpu0_{precision}.engine"
    )
    engine_path = (
        sdk_engine if sdk_engine.is_file() else cache_dir / sdk_engine.name
    )
    prop["model-engine-file"] = str(engine_path)

    runtime_path = (
        cache_dir / f"{onnx_name}_infer_b{batch_size}_{precision}.txt"
    )
    with runtime_path.open("w", encoding="utf-8") as stream:
        cfg.write(stream)
    return str(runtime_path)


def prepare_runtime_preprocess_config(
    preprocess_config: str,
    config_cache_dir: str,
    batch_size: int,
    total_roi_count: int = 0,
    use_cuda_memory: bool = False,
) -> str:
    """Write a native SAHI config whose tensor batch matches the active sources."""
    if batch_size <= 0:
        raise ValueError("SAHI tensor batch size must be positive")
    cfg = read_ini(preprocess_config)
    if "property" not in cfg:
        raise ValueError(f"{preprocess_config} missing [property] section")

    prop = cfg["property"]
    shape = [
        part.strip() for part in prop.get("network-input-shape", "").split(";")
    ]
    if len(shape) != 4:
        raise ValueError(f"Invalid network-input-shape in {preprocess_config}")
    shape[0] = str(batch_size)
    prop["network-input-shape"] = ";".join(shape)

    if total_roi_count > 0:
        required_batches = (total_roi_count + batch_size - 1) // batch_size
        for key in ("scaling-buf-pool-size", "tensor-buf-pool-size"):
            configured = prop.getint(key, fallback=0)
            prop[key] = str(max(configured, required_batches))

    if use_cuda_memory:
        prop["scaling-pool-memory-type"] = "2"
        prop["scaling-pool-compute-hw"] = "1"

    custom_lib = prop.get("custom-lib-path", fallback="").strip()
    if custom_lib:
        prop["custom-lib-path"] = resolve_runtime_asset_path(
            preprocess_config, custom_lib
        )

    cache_dir = pathlib.Path(config_cache_dir).expanduser().resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    config_name = pathlib.Path(preprocess_config).stem
    runtime_path = cache_dir / f"{config_name}_b{batch_size}.txt"
    with runtime_path.open("w", encoding="utf-8") as stream:
        cfg.write(stream)
    return str(runtime_path)


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
        raise ValueError(
            f"Invalid preprocess size in {preprocess_config}: {width}x{height}"
        )
    if not tensor_name:
        raise ValueError(f"Missing tensor-name in {preprocess_config}")

    custom_lib = prop.get("custom-lib-path", fallback="").strip()
    if custom_lib:
        validate_file_exists(
            resolve_runtime_asset_path(preprocess_config, custom_lib),
            f"custom-lib-path ({preprocess_config})",
        )


def validate_sahi_compat(
    preprocess_config: str, infer_config: str, logger=None
):
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

    onnx_path = resolve_runtime_asset_path(infer_config, onnx_file)
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

    _log_info(
        logger,
        f"SAHI compatibility check OK: preprocess={pre_w}x{pre_h}, onnx={onnx_w}x{onnx_h}",
    )


def validate_sahi_batch(
    preprocess_config: str,
    tensor_batch_size: int,
    total_roi_count: int,
):
    """Validate a tensor batch that divides all SAHI ROIs in one mux batch."""
    prop = read_ini(preprocess_config)["property"]
    shape = [
        part.strip() for part in prop.get("network-input-shape", "").split(";")
    ]
    if not shape or not shape[0].isdigit():
        raise ValueError(f"Invalid network-input-shape in {preprocess_config}")
    configured_batch = int(shape[0])
    if configured_batch <= 0:
        raise ValueError(
            f"SAHI preprocess batch must be positive: {configured_batch}"
        )
    if tensor_batch_size <= 0:
        raise ValueError(
            f"SAHI tensor batch must be positive: {tensor_batch_size}"
        )
    if total_roi_count <= 0:
        raise ValueError(
            f"SAHI total ROI count must be positive: {total_roi_count}"
        )
    if configured_batch != tensor_batch_size:
        raise ValueError(
            "SAHI batch mismatch: "
            f"preprocess={configured_batch}, nvinfer={tensor_batch_size}"
        )
    if total_roi_count % configured_batch != 0:
        raise ValueError(
            "SAHI tensor batch must divide the total ROI count: "
            f"tensor_batch={configured_batch}, total_rois={total_roi_count}"
        )
    required_batches = total_roi_count // configured_batch
    for key in ("scaling-buf-pool-size", "tensor-buf-pool-size"):
        configured = prop.getint(key, fallback=0)
        if configured < required_batches:
            raise ValueError(
                f"{key} must hold all SAHI tensor batches before input push: "
                f"configured={configured}, required={required_batches}"
            )


def validate_runtime_configs(
    mode: str,
    infer_config: str,
    preprocess_config: Optional[str],
    tracker_enabled: bool,
    tracker_config: Optional[str],
    tensor_batch_size: int = 0,
    total_roi_count: int = 0,
    logger=None,
):
    validate_infer_config(infer_config, logger=logger)

    if mode == "sahi":
        if not preprocess_config:
            raise ValueError(
                "preprocess_config is required in native SAHI mode"
            )
        validate_preprocess_config(preprocess_config)
        validate_sahi_compat(preprocess_config, infer_config, logger=logger)
        if tensor_batch_size > 0:
            validate_sahi_batch(
                preprocess_config,
                tensor_batch_size,
                total_roi_count,
            )

    if tracker_enabled:
        if not tracker_config:
            raise ValueError(
                "tracker_config is required when tracker_enabled=true"
            )
        validate_file_exists(tracker_config, "Tracker config")
