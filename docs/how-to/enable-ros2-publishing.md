# 启用 ROS 2 检测发布

本指南为每个 source 启用 `vision_msgs/msg/Detection2DArray` 发布，并检查 topic 是否使用了正确的相机名称。

## 前置条件

节点必须能导入 `vision_msgs`：

```bash
ros2 interface show vision_msgs/msg/Detection2DArray
```

如果接口不存在，安装与 ROS 2 发行版匹配的 `vision_msgs`，然后重新构建工作区。

## 启动发布

`publish_detections` 是节点参数。三路运行示例：

```bash
source scripts/env.sh
source .colcon/install/setup.bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam_headless.yaml
```

节点为每个 `source_names` 创建一个 publisher：

```text
/deepstream/camera_0/detections
/deepstream/camera_1/detections
/deepstream/camera_2/detections
```

## 检查消息

```bash
ros2 topic info /deepstream/camera_0/detections
ros2 topic echo --once /deepstream/camera_0/detections
ros2 topic hz /deepstream/camera_0/detections
```

即使当前帧没有目标，也应发布空的 `detections` 数组。检测 topic 的 header `frame_id` 使用对应的 source name；时间戳优先使用 DeepStream frame metadata 的 NTP 时间。bbox 坐标是 tiler 前的原始图像坐标，中心和尺寸以像素表示。

## 可选输出

- `publish_annotated:=true` 按 source 发布 `/deepstream/<source_name>/image/annotated/compressed`，消息类型为 `sensor_msgs/msg/CompressedImage`。
- `allowed_class_ids:=0,2`（或在 YAML 中使用 `[0, 2]`）只保留指定数字类别；空列表表示保留全部类别。
- `/deepstream/status` 始终用于报告节点状态，如 `initialized` 和 `running`。

字段、置信度回退和 tracker ID 的完整定义见[ROS 2 话题参考](../reference/ros2-topics.md)。
