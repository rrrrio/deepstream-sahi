# 三路管线验证手册

本手册按依赖顺序验证仓库的三路文件、显示、ROS 2 和 V4L2 输入。当前三相机正式路径使用稳定 by-path；Argus CSI 作为独立可选路径。验证边界包括真实输入、DeepStream source 和 ROS detection topic，不包含长时间稳定性。

所有容器内命令从 `/workspace/deepstream-sahi` 执行。硬件命令必须在对应的 Jetson 宿主机或正确映射的 Docker 容器内执行。

## 1. 记录环境

前置条件：Bash、可写工作区和仓库脚本。

```bash
git status --short
uname -a
python3 --version
gst-launch-1.0 --version
ros2 -h | sed -n '1,3p'
./scripts/check_runtime.sh
```

检查输出中的 DeepStream、GPU 和 X11 资源。执行当前三相机验证前必须通过 GPU、设备节点和 V4L2 条件，不要求 Argus socket。

## 2. 检查 Python 与 native plugin

```bash
source scripts/env.sh
python3 -c 'import pyds; print("pyds import: ok")'
gst-inspect-1.0 nvsahipreprocess
gst-inspect-1.0 nvsahipostprocess
```

应看到两个 plugin factory 及其属性。缺少插件时检查 `GST_PLUGIN_PATH`、`.local/lib/gstreamer-1.0` 和动态库依赖，然后重新构建。SAHI profile 不使用 Python fallback。

## 3. 运行无 GPU 单元测试

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q tests
```

应覆盖 source 分类、数组校验、ROI 计算、时间戳、tracking confidence、YOLOv11s parser、三路 profile 和原生插件归属检查；以当前测试命令的实际通过数量为准。

## 4. 构建 ROS 2 包

```bash
source scripts/env.sh
python -m colcon --log-base .colcon/log build \
  --base-paths . \
  --build-base .colcon/build \
  --install-base .colcon/install \
  --symlink-install \
  --packages-select deepstream
source .colcon/install/setup.bash
ros2 pkg prefix deepstream
```

包前缀应位于 `.colcon/install`。构建失败时先查看 `.colcon/log/latest_build`，确认 ROS、Python 和 DeepStream 环境在同一 shell 中加载。

## 5. 验证三路文件输入

前置条件：GPU 设备、EGL 资源、模型、parser 和 native SAHI plugin 均存在；显示模式另需 X11。

```bash
source scripts/env.sh
source .colcon/install/setup.bash
./scripts/run_sim_3cam_headless.sh
```

应看到三个 source 初始化，并持续运行超过单个输入文件时长。此步骤可继续检查 12 个显式 ROI、NMM、tracker 和 ROS topic；它不替代 CSI 检查。

## 6. 验证显示与无窗口模式

显示模式：

```bash
./scripts/check_runtime.sh --require-display
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam.yaml display:=true
```

无显示模式：

```bash
./scripts/check_runtime.sh --require-gpu
EGL_PLATFORM=surfaceless ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam_headless.yaml display:=false
```

显示模式预期为 2x2 tile。无显示模式不创建可见窗口，使用 surfaceless EGL 提供 native SAHI allocator 所需的 EGL 资源。停止后确认没有残留 launch 或 node 进程。

## 7. 验证 ROS 2 topic

在启用 `publish_detections` 的管线运行时：

```bash
ros2 topic list | sort | grep '/deepstream/.*/detections'
ros2 topic echo --once /deepstream/camera_0/detections
ros2 topic hz /deepstream/camera_0/detections
```

应看到三个按 `source_names` 命名的检测 topic。空目标帧也应发布空数组；bbox 使用 tiler 前的原图坐标。

## 8. 宿主机 V4L2 前置检查

在提供相机设备的运行环境执行：

```bash
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video0
ls -l /dev/v4l/by-path/platform-tegra-capture-vi-video-index{0,6,8}
ls -l /dev/video* /dev/media*
```

继续检查 media topology、sensor format 和三条稳定 by-path 的目标。设备节点编号不作为 `camera_0/1/2` 的身份。

## 9. 容器 V4L2 资源检查

容器必须映射：

```text
/dev
/tmp/.X11-unix
```

容器内执行：

```bash
source /opt/ros/humble/setup.bash
source scripts/env.sh
./scripts/check_runtime.sh --require-gpu
gst-inspect-1.0 v4l2src
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  test -c "$device"
done
```

V4L2 路径不要求 `/tmp/argus_socket`。无窗口运行使用 surfaceless EGL；如果出现 `Authorization required` 或 `No EGL Display`，先确认命令设置了 `EGL_PLATFORM=surfaceless`。

## 10. 逐路验证 V4L2 输入

按稳定 capture index 逐个执行：

```bash
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
```

脚本会按显式 `UYVY 1920x1080@30` caps 检查真实帧，并确认输入能转换到 NV12/NVMM。稳定路径缺失时不要改用 `/dev/videoN`，先修复设备映射。

## 11. 进入 `deepstream.launch.py`

使用当前三相机 V4L2 profile：

```bash
source /opt/ros/humble/setup.bash
source scripts/env.sh
timeout --signal=INT --kill-after=3s 20s \
  EGL_PLATFORM=surfaceless ros2 launch deepstream deepstream.launch.py \
  params_file:=config/v4l2_3cam.yaml \
  display:=false publish_detections:=true publish_annotated:=false
```

成功边界是日志出现三个 V4L2 source、native SAHI plugin、streammux/NvTracker 初始化，且没有 source caps、NVMM、input signal 或设备打开错误。该无窗口 V4L2 命令使用 surfaceless EGL，不要求 X11 socket。

## 12. 验证 ROS 2 输出

```bash
ros2 topic list | sort | grep '/deepstream/.*/detections'
ros2 topic echo --once /deepstream/camera_0/detections
ros2 topic hz /deepstream/camera_0/detections
```

应看到三个按 `source_names` 命名的 topic。使用已知目标或遮挡测试确认 topic 与 capture index 没有串路。

## 13. 可选的 Argus profile

```bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/csi_3cam.yaml \
  display:=false publish_detections:=false
```

只有产品明确选择 Argus 且三路 Argus 独立检查通过后才执行；它不影响 V4L2 profile 的验收。

## 14. 运行 30 分钟稳定性验收

三路真实相机的最终验收使用无窗口路径，避免显示 sink 抢占检测所需的 GPU surface。脚本默认运行 1800 秒，并同时检查：

- `camera_0`、`camera_1`、`camera_2` 三个 detection topic 持续有消息。
- 跳过启动后的第一个 10 秒预热窗口后，每个窗口的频率不低于 8 Hz。
- 三路同一窗口的频率差不超过 1 Hz。
- 每路整个测试期间的相对抖动不超过 25%。
- DeepStream 进程未提前退出，日志没有 `NVDSINFER_TENSORRT_ERROR`、`Failed in mem copy`、CUDA illegal memory access、`Signal lost` 或设备 busy 错误。

执行：

```bash
./scripts/verify_v4l2_stability.sh
```

脚本默认使用 `config/v4l2_3cam.yaml`，但强制 `display=false`、`publish_detections=true` 和 surfaceless EGL。首个窗口只用于预热，不参与速率门槛判断。该 profile 还将缩放池固定为 CUDA device memory，并在支持时让 V4L2 相机的 180° `nvvideoconvert` 使用 GPU compute，避免 Jetson 默认 surface-array/VIC 拷贝路径的长期 `Failed in mem copy`。当前 profile 包含每路 3 个切片加 1 个全图 ROI，batch 1 在目标 Orin 上的实际处理频率约为 12.6 Hz；`camera_fps: 30` 表示相机输入能力，不是检测 topic 的承诺频率。若产品需要不同吞吐门槛，可显式覆盖，例如：

```bash
./scripts/verify_v4l2_stability.sh \
  --duration 1800 --min-rate 9 --max-relative-jitter 0.2
```

通过后保留 `.state/stability/<UTC时间>/launch.log` 和 `topic-rates.log` 作为验收证据。测试中途退出、话题停止发布或日志出现运行期错误都判定失败。

## 15. 回滚

停止当前 launch 并确认进程退出。保留失败日志和环境快照。不要删除 DeepStream SDK、不要修改宿主机设备节点，也不要把容器内临时节点当作真实硬件映射。

各路线的检查命令、通过标准和常见错误见[CSI 相机输入验证](verify-csi-camera-input.md)。
