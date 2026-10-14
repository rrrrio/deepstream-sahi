"""Helpers for DeepStream metadata conversion and filtering."""

from numbers import Integral
from typing import Iterable, Optional, Tuple


def normalize_allowed_class_ids(values: Iterable[int]) -> list[int]:
    """Validate and normalize the configured detector class IDs."""
    normalized = []
    for value in values or []:
        if isinstance(value, bool) or not isinstance(value, Integral):
            raise ValueError(
                "allowed_class_ids must contain only non-negative integers"
            )
        value = int(value)
        if value < 0:
            raise ValueError(
                "allowed_class_ids must contain only non-negative integers"
            )
        if value not in normalized:
            normalized.append(value)
    return normalized


def filter_object_metadata(batch_meta, allowed_class_ids, pyds_module):
    """Remove objects outside the allow-list from a DeepStream batch.

    The caller supplies the imported ``pyds`` module so this helper remains
    testable without importing the platform-specific bindings.
    """
    allowed = set(allowed_class_ids)
    if not allowed:
        return

    acquire_lock = getattr(pyds_module, "nvds_acquire_meta_lock", None)
    remove_object = getattr(
        pyds_module, "nvds_remove_obj_meta_from_frame", None
    )
    if acquire_lock is None or remove_object is None:
        raise RuntimeError(
            "DeepStream pyds bindings must provide metadata locking and "
            "nvds_remove_obj_meta_from_frame for class filtering"
        )

    with acquire_lock(batch_meta):
        l_frame = batch_meta.frame_meta_list
        while l_frame is not None:
            try:
                frame_meta = pyds_module.NvDsFrameMeta.cast(l_frame.data)
            except StopIteration:
                break

            l_obj = frame_meta.obj_meta_list
            while l_obj is not None:
                try:
                    next_l_obj = l_obj.next
                except StopIteration:
                    next_l_obj = None
                try:
                    obj_meta = pyds_module.NvDsObjectMeta.cast(l_obj.data)
                except StopIteration:
                    break

                if int(obj_meta.class_id) not in allowed:
                    remove_object(frame_meta, obj_meta)
                l_obj = next_l_obj

            try:
                l_frame = l_frame.next
            except StopIteration:
                break


def select_confidence(
    detector_confidence: float, tracker_confidence: float
) -> float:
    """Choose a usable detector/tracker confidence and clamp it to [0, 1]."""
    value = (
        detector_confidence
        if detector_confidence >= 0.0
        else tracker_confidence
    )
    return max(0.0, min(1.0, float(value)))


def tracking_id_string(
    object_id: int, untracked_id: Optional[int] = None
) -> str:
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
