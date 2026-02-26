#!/usr/bin/env python3
"""
File: msg_adapter.py
Description: Converts DeepStream metadata into ROS2 message objects.
Author: Yuhao Ding
"""

from typing import Dict, List, Optional

from geometry_msgs.msg import Point
from std_msgs.msg import Header

import pyds

try:
    from vision_msgs.msg import BoundingBox2D, Detection2D, Detection2DArray, ObjectHypothesisWithPose

    HAS_VISION_MSGS = True
except Exception:  # pragma: no cover - runtime dependency varies by machine
    BoundingBox2D = Detection2D = Detection2DArray = ObjectHypothesisWithPose = None
    HAS_VISION_MSGS = False


def extract_detections(frame_meta) -> List[Dict]:
    detections = []
    l_obj = frame_meta.obj_meta_list
    while l_obj is not None:
        try:
            obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
        except StopIteration:
            break

        left = float(obj_meta.rect_params.left)
        top = float(obj_meta.rect_params.top)
        width = float(obj_meta.rect_params.width)
        height = float(obj_meta.rect_params.height)
        label = ""
        if hasattr(obj_meta, "obj_label") and obj_meta.obj_label:
            label = str(obj_meta.obj_label)

        detections.append(
            {
                "class_id": int(obj_meta.class_id),
                "confidence": float(obj_meta.confidence),
                "label": label,
                "bbox": [left, top, left + width, top + height],
            }
        )

        try:
            l_obj = l_obj.next
        except StopIteration:
            break

    return detections


def _fill_bbox(bbox_msg: BoundingBox2D, det: Dict):
    x1, y1, x2, y2 = det["bbox"]
    cx = (x1 + x2) / 2.0
    cy = (y1 + y2) / 2.0
    width = max(0.0, x2 - x1)
    height = max(0.0, y2 - y1)

    if hasattr(bbox_msg, "center"):
        center = bbox_msg.center
        if hasattr(center, "x"):
            center.x = float(cx)
            center.y = float(cy)
            if hasattr(center, "theta"):
                center.theta = 0.0
        elif hasattr(center, "position") and hasattr(center.position, "x"):
            center.position.x = float(cx)
            center.position.y = float(cy)

    if hasattr(bbox_msg, "size_x"):
        bbox_msg.size_x = float(width)
    elif hasattr(bbox_msg, "width"):
        bbox_msg.width = float(width)

    if hasattr(bbox_msg, "size_y"):
        bbox_msg.size_y = float(height)
    elif hasattr(bbox_msg, "height"):
        bbox_msg.height = float(height)


def _fill_hypothesis(hyp_msg: ObjectHypothesisWithPose, det: Dict):
    class_id = str(det["class_id"])
    score = float(det["confidence"])

    if hasattr(hyp_msg, "hypothesis"):
        if hasattr(hyp_msg.hypothesis, "class_id"):
            hyp_msg.hypothesis.class_id = class_id
        elif hasattr(hyp_msg.hypothesis, "id"):
            hyp_msg.hypothesis.id = int(det["class_id"])

        if hasattr(hyp_msg.hypothesis, "score"):
            hyp_msg.hypothesis.score = score
    else:
        if hasattr(hyp_msg, "id"):
            hyp_msg.id = int(det["class_id"])
        if hasattr(hyp_msg, "score"):
            hyp_msg.score = score


def build_detection_array_msg(
    detections: List[Dict],
    stamp,
    frame_id: str,
) -> Optional[Detection2DArray]:
    if not HAS_VISION_MSGS:
        return None

    msg = Detection2DArray()
    msg.header = Header(stamp=stamp, frame_id=frame_id)

    for det in detections:
        det_msg = Detection2D()
        if hasattr(det_msg, "header"):
            det_msg.header = Header(stamp=stamp, frame_id=frame_id)
        if hasattr(det_msg, "id"):
            det_msg.id = det.get("label", "") or str(det["class_id"])

        _fill_bbox(det_msg.bbox, det)

        hyp_msg = ObjectHypothesisWithPose()
        _fill_hypothesis(hyp_msg, det)
        det_msg.results.append(hyp_msg)

        msg.detections.append(det_msg)

    return msg


def build_best_target_msg(detections: List[Dict]) -> Optional[Point]:
    if not detections:
        return None

    best = max(detections, key=lambda det: det["confidence"])
    x1, y1, x2, y2 = best["bbox"]
    point = Point()
    point.x = float((x1 + x2) / 2.0)
    point.y = float((y1 + y2) / 2.0)
    point.z = 0.0
    return point
