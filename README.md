# deepstream-sahi

面向 Jetson 的 ROS 2 + NVIDIA DeepStream 三路视频分析包。项目支持文件、RTSP、V4L2 和 Argus CSI 输入，使用 YOLOv11s、SAHI 切片、NMM 合并、tracker、2x2 OSD 以及按相机发布的 ROS 2 检测消息。

英文说明见 [README.en.md](README.en.md)。

## 数据流

```text
文件 / RTSP / V4L2 / Argus x3
  -> nvstreammux
  -> nvsahipreprocess
  -> nvinfer
  -> nvsahipostprocess
  -> nvtracker
  -> ROS 2 metadata probe
  -> nvmultistreamtiler
  -> nvdsosd
  -> nv3dsink 或 fakesink
```

SAHI 使用固定的 native plugin。缺少 `nvsahipreprocess` 或 `nvsahipostprocess` 时，程序会明确失败，不会静默切换到旧的 Python 合并路径。

## 目录

- `deepstream/`：ROS 2 节点、source bin 和 DeepStream 管线。
- `config/sim_3cam_headless.yaml`：三路文件、无可见窗口和检测 topic profile。
- `config/sim_3cam.yaml`：三路文件和 2x2 OSD profile。
- `config/csi_3cam.yaml`：三路 Argus CSI profile。
- `config/v4l2_3cam.yaml`：当前三相机 V4L2 profile，使用稳定的 `/dev/v4l/by-path` 链接和 `UYVY 1920x1080@30`。
- `scripts/`：容器安装、运行时检查、相机输入 probe 和环境脚本。
- `docs/`：教程、操作指南、参考、架构说明和相机输入排障流程。

## 文档

从[文档入口](docs/README.md)按教程、操作指南、参考或解释选择页面。

## 依赖

- Jetson、DeepStream 7.1、CUDA 12.6 和匹配的 NVIDIA runtime。
- ROS 2 Humble、`vision_msgs` 和 NVIDIA `pyds` 1.2.0。
- 构建 native plugin 所需的 CUDA、GStreamer 和 TensorRT 开发文件。
- 文件输入需要 GPU、EGL/X11 和 `/dev` 映射；无窗口 V4L2 输入使用 surfaceless EGL 和 `/dev` 映射。
- Argus CSI 额外需要宿主机能枚举 camera module，并把 `/tmp/argus_socket` 映射到容器。
- 安装脚本需要可写的容器根文件系统和 root；只读容器不能通过提权变成可写。

## 安装和构建

在容器内从仓库根目录执行：

```bash
./scripts/capture_environment.sh
./scripts/install_inplace.sh
./scripts/build_sahi_plugins.sh
./scripts/fetch_yolov11s_onnx.sh
./scripts/build_yolov11s_parser.sh
source scripts/env.sh
python -m colcon --log-base .colcon/log build \
  --base-paths . \
  --build-base .colcon/build \
  --install-base .colcon/install \
  --symlink-install \
  --packages-select deepstream
source .colcon/install/setup.bash
```

详细安装和容器重建要求见[现有容器安装指南](docs/how-to/install-in-existing-container.md)。

## 运行

三路文件无显示运行：

```bash
./scripts/check_runtime.sh --require-gpu
./scripts/run_sim_3cam_headless.sh
```

三路文件 OSD：

```bash
./scripts/check_runtime.sh --require-display
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam.yaml \
  display:=true \
  publish_detections:=true
```

三路 Argus CSI：

```bash
./scripts/check_runtime.sh --require-argus
for sensor_id in 0 1 2; do
  ./scripts/probe_argus_camera.sh "$sensor_id" 120
done
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/csi_3cam.yaml \
  display:=false
```

启动完整 CSI profile 前，必须逐个验证 `sensor-id=0/1/2`，并确认每一路真实输出 120 个 buffer 且没有 Argus 错误。`gst-launch-1.0` 在 Argus 错误后可能仍返回 0，不能只看退出码。

三路 V4L2 相机：

```bash
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/v4l2_3cam.yaml \
  display:=false \
  publish_detections:=true
```

V4L2 profile 按 capture index 0、6、8 对应 `camera_0`、`camera_1`、`camera_2`，使用设备当前的 `UYVY 1920x1080@30`，转换为 `NV12/NVMM` 后进入 `nvstreammux`。稳定路径缺失时启动会明确失败，不会静默回退到 `/dev/videoN`。
无窗口 V4L2 运行使用 surfaceless EGL，不要求 `DISPLAY`、X11 socket 或 Argus socket；节点会在全 V4L2 且关闭显示和图像发布时自动选择该平台。

## 验证边界

本仓库当前的 CSI 前置验证只确认相机数据能进入 `deepstream.launch.py` 所需的输入边界，不把真实推理、检测内容、ROS topic 或 30 分钟稳定性当作相机前置条件。完整命令和预期输出见[三路管线验证手册](docs/how-to/verify-milestones.md)。

当前三相机验证先确认 `/dev/v4l/by-path` 的物理映射、设备格式和 V4L2 实际帧，再在容器中确认 GPU、设备节点和插件，最后进入完整 V4L2 launch 并检查三个 ROS detection topic。Argus 是独立的可选输入路径，不是 V4L2 三相机验收的前置条件。

常见问题包括容器缺少 `/tmp/argus_socket`、`No cameras available`、设备树缺少 NVIDIA camera enumerator、camera kernel node 打开失败，以及 V4L2 caps 协商失败。对应检查和处理方式见[CSI 相机输入验证](docs/how-to/verify-csi-camera-input.md)。

## ROS 2 输出

启用 `publish_detections:=true` 后，默认 topic 为：

- `/deepstream/status`。
- `/deepstream/camera_0/detections`。
- `/deepstream/camera_1/detections`。
- `/deepstream/camera_2/detections`。

消息字段和坐标约定见[ROS 2 话题参考](docs/reference/ros2-topics.md)。
