"""Pure helpers for DeepStream metadata conversion."""

from typing import Optional, Tuple


def select_confidence(detector_confidence: float, tracker_confidence: float) -> float:
    """Choose a usable detector/tracker confidence and clamp it to [0, 1]."""
    value = detector_confidence if detector_confidence >= 0.0 else tracker_confidence
    return max(0.0, min(1.0, float(value)))


def tracking_id_string(object_id: int, untracked_id: Optional[int] = None) -> str:
    """Return a ROS-friendly tracking ID, or an empty string when untracked."""
    value = int(object_id)
    if value < 0 or (untracked_id is not None and value == int(untracked_id)):
        return ""
    return str(value)


def ntp_timestamp_parts(timestamp_ns: int) -> Optional[Tuple[int, int]]:
    """Convert a DeepStream nanosecond epoch timestamp to ROS time fields."""
    value = int(timestamp_ns)
    if value <= 0:
        return None
    return divmod(value, 1_000_000_000)
