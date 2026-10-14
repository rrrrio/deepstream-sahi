"""Pure helpers for classifying and validating pipeline sources."""

import os
import re
import stat
from typing import Iterable, List, Tuple
from urllib.parse import urlparse


SOURCE_NAME_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")
V4L2_DEVICE_PREFIXES = ("/dev/video", "/dev/v4l/by-path/")


def _is_v4l2_device_path(value: str) -> bool:
    return value.startswith(V4L2_DEVICE_PREFIXES)


def is_v4l2_source(source: str) -> bool:
    """Return whether *source* names a local V4L2 camera device."""
    value = source.strip()
    return _is_v4l2_device_path(value) or value.startswith("v4l2://")


def is_argus_source(source: str) -> bool:
    """Return whether *source* selects a Jetson CSI sensor through Argus."""
    return source.strip().startswith("argus://")


def argus_sensor_id(source: str) -> int:
    """Extract a non-negative sensor ID from an ``argus://N`` source."""
    value = source.strip()
    if not is_argus_source(value):
        raise ValueError(f"Unsupported Argus source: {source!r}")

    parsed = urlparse(value)
    raw_id = parsed.netloc or parsed.path.lstrip("/")
    if not raw_id.isdigit():
        raise ValueError(
            f"Argus source must use argus://<sensor-id>: {source!r}"
        )
    return int(raw_id)


def is_file_source(source: str) -> bool:
    """Return whether *source* is a local path or file URI."""
    value = source.strip()
    if value.startswith("file://"):
        return True
    return "://" not in value and not is_v4l2_source(value)


def is_live_source(source: str) -> bool:
    """Return whether a source should use live-source streammux behavior."""
    value = source.strip()
    return (
        value.startswith(("rtsp://", "rtsps://", "v4l2://"))
        or is_v4l2_source(value)
        or is_argus_source(value)
    )


def v4l2_device_path(source: str) -> str:
    """Normalize a V4L2 source value to an absolute device path."""
    value = source.strip()
    if _is_v4l2_device_path(value):
        return value
    if value.startswith("v4l2://"):
        parsed = urlparse(value)
        path = parsed.path
        if parsed.netloc and not path:
            path = "/" + parsed.netloc
        elif parsed.netloc and path:
            path = "/" + parsed.netloc + path
        if _is_v4l2_device_path(path):
            return path
    raise ValueError(f"Unsupported V4L2 source: {source!r}")


def validate_v4l2_device(source: str) -> str:
    """Return *source*'s device path when it is an accessible character device."""
    path = v4l2_device_path(source)
    try:
        mode = os.stat(path).st_mode
    except FileNotFoundError as exc:
        raise ValueError(f"V4L2 device does not exist: {path}") from exc
    except OSError as exc:
        raise ValueError(
            f"Unable to access V4L2 device {path}: {exc}"
        ) from exc
    if not stat.S_ISCHR(mode):
        raise ValueError(f"V4L2 path is not a character device: {path}")
    return path


def configure_headless_v4l2_egl(
    sources: Iterable[str], display: bool, publish_annotated: bool
) -> bool:
    """Select surfaceless EGL for headless all-V4L2 DeepStream pipelines."""
    resolved_sources = list(sources)
    if (
        resolved_sources
        and all(is_v4l2_source(source) for source in resolved_sources)
        and not display
        and not publish_annotated
    ):
        os.environ["EGL_PLATFORM"] = "surfaceless"
        return True
    return False


def normalize_source_config(
    source: str,
    sources: Iterable[str],
    source_names: Iterable[str],
) -> Tuple[List[str], List[str]]:
    """Resolve legacy and multi-source parameters into validated lists."""
    resolved_sources = [
        str(value).strip() for value in sources if str(value).strip()
    ]
    if not resolved_sources and source.strip():
        resolved_sources = [source.strip()]
    if not resolved_sources:
        raise ValueError("sources must contain at least one source")

    resolved_names = [str(value).strip() for value in source_names]
    if not resolved_names:
        resolved_names = [
            f"camera_{index}" for index in range(len(resolved_sources))
        ]
    if len(resolved_names) != len(resolved_sources):
        raise ValueError("source_names must have the same length as sources")
    if any(not name for name in resolved_names):
        raise ValueError("source_names must not contain empty names")
    if len(set(resolved_names)) != len(resolved_names):
        raise ValueError("source_names must be unique")
    invalid_names = [
        name
        for name in resolved_names
        if not SOURCE_NAME_PATTERN.fullmatch(name)
    ]
    if invalid_names:
        raise ValueError(
            "source_names must start with a letter and contain only letters, "
            f"digits, and underscores: {invalid_names}"
        )

    return resolved_sources, resolved_names
