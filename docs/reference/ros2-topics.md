# ROS 2 话题参考

## 目录

- [解说](#解说)
- [参考场景](#参考场景)
  - [发布空检测数组](#发布空检测数组)
  - [发布带 tracker 的检测](#发布带-tracker-的检测)
- [组件与参数说明](#组件与参数说明)
  - [组件说明](#组件说明)
  - [参数说明](#参数说明)

## 解说

节点在 metadata probe 中按 `frame_meta.source_id` 找到 source name，并向该 source 的检测 topic 发布一条 `vision_msgs/msg/Detection2DArray`。探针位于 tiler 前，所以 bbox 使用每路原始图像坐标；OSD 标签在 tracker 后写入，便于显示同一套置信度和 ID。

## 参考场景

### 发布空检测数组

```gherkin
Feature: 无目标帧发布
  Background:
    Given publish_detections 已启用且 vision_msgs 可用

  Scenario: 当前帧没有目标
    Given source 仍在持续产生帧
    When metadata probe 处理该帧
    Then 对应 topic 发布 Detection2DArray
    And detections 数组为空
```

### 发布带 tracker 的检测

```gherkin
Feature: 带 tracker 的检测发布
  Scenario: tracker 为目标提供 ID
    Given NMM 已在 tracker 前完成
    And 当前帧包含一个可跟踪目标
    When 节点转换 DeepStream metadata
    Then Detection2D.id 为字符串形式的 object_id
    And bbox 使用原图像素坐标
```

## 组件与参数说明

### 组件说明

| Topic | 类型 | 默认条件 |
| --- | --- | --- |
| `/deepstream/status` | `std_msgs/msg/String` | 始终创建。 |
| `/deepstream/<source_name>/detections` | `vision_msgs/msg/Detection2DArray` | `publish_detections=true` 且 `vision_msgs` 可用。 |
| `/deepstream/<source_name>/image/annotated/compressed` | `sensor_msgs/msg/CompressedImage` | `publish_annotated=true`。每路一个 topic，`format` 为 `jpeg`。 |

### 参数说明

```yaml
source_names: [camera_0, camera_1, camera_2]
publish_detections: true
publish_annotated: false
allowed_class_ids: []
```

| 字段 | 类型 / 值 | 说明 |
| --- | --- | --- |
| `header.frame_id` | string | source name，例如 `camera_0`。 |
| `header.stamp` | ROS time | 优先由 DeepStream `ntp_timestamp` 转换；无有效 NTP 时使用节点时钟。 |
| `CompressedImage.format` | string | annotated image 固定为 `jpeg`；JPEG 在 GPU pipeline 中编码。 |
| `detections` | `Detection2D[]` | 当前帧检测列表，无目标时为空。 |
| `Detection2D.id` | string | tracker object ID；未跟踪时为空字符串。 |
| `Detection2D.bbox.center` | 像素坐标 | bbox 中心点。 |
| `Detection2D.bbox.size_x`, `size_y` | float | bbox 宽和高，单位为像素。 |
| `results[].hypothesis.class_id` | string | 标签；若没有标签则使用 class ID。 |
| `results[].hypothesis.score` | float | 置信度，限制在 `[0, 1]`。 |

`allowed_class_ids: [0, 2]` 会在 SAHI NMM 和 tracker 之前移除其他类别，因此未允许的对象不会进入检测消息、OSD 或标注图像。

检测置信度可用时优先使用 detector confidence；否则使用 tracker confidence。完整启动方式见[启用 ROS 2 发布](../how-to/enable-ros2-publishing.md)。
