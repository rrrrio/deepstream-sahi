# 首次运行三路文件

本教程带你用三个本地视频完成一次三路 SAHI 运行。完成后，你能从每路 ROS 2 topic 读取检测结果。OSD 保留为后续单独的显示验收步骤。

## 前置条件

- 已安装 ROS 2 Humble、DeepStream、`pyds` 和 `vision_msgs`。
- 已运行 `scripts/fetch_yolov11s_onnx.sh` 与 `scripts/build_yolov11s_parser.sh`。
- 已完成[现有容器安装指南](../how-to/install-in-existing-container.md)中的构建步骤。
- 三个视频文件可从当前环境读取。
- 当前环境是否具备 GPU、显示和 NVMM 运行资源，需要按[验证手册](../how-to/verify-milestones.md)单独确认。

## 1. 准备环境

```bash
cd /workspace/deepstream-sahi
source scripts/env.sh
source .colcon/install/setup.bash
```

检查命令可找到 ROS 包：

```bash
ros2 pkg prefix deepstream
```

输出应指向 `.colcon/install` 下的安装前缀。

## 2. 运行三路视频

将下面的路径替换为三个实际文件：

```bash
./scripts/run_sim_3cam_headless.sh
```

文件 source 使用 `nvurisrcbin`，`file_loop:=true` 使视频结束后继续播放。该脚本会先检查 GPU 和
NVIDIA X11/EGL 映射，再以 `display:=false` 与 `publish_detections:=true` 启动；headless
表示不创建可见 OSD 窗口，并不表示可以省略 EGL/X11。GPU/NVDEC 仍是必需条件。

## 3. 观察结果

另开一个终端，加载同一环境：

```bash
source /opt/ros/humble/setup.bash
source /workspace/deepstream-sahi/.colcon/install/setup.bash
ros2 topic list | sort | grep '/deepstream/.*/detections'
```

应能看到：

```text
/deepstream/camera_0/detections
/deepstream/camera_1/detections
/deepstream/camera_2/detections
```

读取第一路的一条消息：

```bash
ros2 topic echo --once /deepstream/camera_0/detections
```

需要 OSD 时，运行 `params_file:=config/sim_3cam.yaml display:=true publish_detections:=true`。预期布局为 2x2：前三格对应三个 source，第四格留空。框上的文字包含 source 名、类别、置信度和可用的 tracking ID。

要用 DeepStream 的交通样例验证非空车辆检测，可把 profile 换成
`config/sim_3cam_traffic.yaml`。它使用 1280x720 输入，每路 7 个 ROI。

## 4. 检查 SAHI 结果

在默认 1920x1080 输入、`slice_width:=640`、`slice_height:=640`、水平和垂直重叠均为 `0.2` 时，每路包含 9 个 ROI，其中一个是全图 ROI。三路输入因此为 27 个 YOLOv11s ROI；headless profile 使用 tensor batch 1 分段处理。实际 batch 和插件属性应从运行日志或 `gst-inspect-1.0` 检查，不能仅凭画面推断。

## 下一步

- 需要相机时，参见[从文件切换到 CSI](../how-to/switch-file-to-csi.md)。
- 需要无窗口运行时，参见[显示与无窗口运行](../how-to/run-display-and-headless.md)。
- 需要字段定义时，参见[ROS 2 话题参考](../reference/ros2-topics.md)。
