# 三路管线验证手册

本手册按依赖顺序验证仓库的三路文件、显示、ROS 2、Argus CSI 和 V4L2 输入。验证边界由测试目标决定：CSI 部分只验证相机输入是否能进入 `deepstream.launch.py` 所需的 NV12/NVMM 输入，不验证模型推理结果和长时间稳定性。

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

检查输出中的 DeepStream、GPU、X11 和 Argus 资源。只缺少可选资源时可以继续软件测试；执行相机验证前必须通过 `--require-argus` 或替代 V4L2 条件。

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

应覆盖 source 分类、数组校验、ROI 计算、时间戳、tracking confidence、YOLOv11s parser 和三路 profile。当前本地结果为 `50 passed`，有一个 NumPy 弃用警告。

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

前置条件：GPU 设备、EGL/X11 资源、模型、parser 和 native SAHI plugin 均存在。

```bash
source scripts/env.sh
source .colcon/install/setup.bash
./scripts/run_sim_3cam_headless.sh
```

应看到三个 source 初始化，并持续运行超过单个输入文件时长。此步骤可继续检查 27 个 ROI、NMM、tracker 和 ROS topic；它不替代 CSI 检查。

## 6. 验证显示与无窗口模式

显示模式：

```bash
./scripts/check_runtime.sh --require-display
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam.yaml display:=true
```

无显示模式：

```bash
./scripts/check_runtime.sh --require-display
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam_headless.yaml display:=false
```

显示模式预期为 2x2 tile。无显示模式不创建可见窗口，但仍需要 native SAHI allocator 使用的 EGL/X11 资源。停止后确认没有残留 launch 或 node 进程。

## 7. 验证 ROS 2 topic

在启用 `publish_detections` 的管线运行时：

```bash
ros2 topic list | sort | grep '/deepstream/.*/detections'
ros2 topic echo --once /deepstream/camera_0/detections
ros2 topic hz /deepstream/camera_0/detections
```

应看到三个按 `source_names` 命名的检测 topic。空目标帧也应发布空数组；bbox 使用 tiler 前的原图坐标。

## 8. 宿主机 CSI 前置检查

在 Jetson 宿主机执行：

```bash
cd ~/wp/deepstream-sahi
./scripts/host_preflight_simulation.sh "$HOME/wp"
v4l2-ctl --list-devices
ls -l /dev/video* /dev/media*
systemctl is-active nvargus-daemon
test -S /tmp/argus_socket
```

`host_preflight_simulation.sh` 只验证 Docker、GPU 和工作区，不验证 Argus。必须继续检查 media topology、sensor format 和 Argus 日志。设备树/驱动问题不能通过容器参数修复。

## 9. 容器 CSI 资源检查

容器必须映射：

```text
/dev
/tmp/argus_socket
/tmp/.X11-unix
```

容器内执行：

```bash
source /opt/ros/humble/setup.bash
source scripts/env.sh
./scripts/check_runtime.sh --require-gpu
./scripts/check_runtime.sh --require-argus
gst-inspect-1.0 nvarguscamerasrc
test -S /tmp/argus_socket
```

`--require-argus` 通过只代表 socket、GPU 和插件存在，不代表 Argus 已发现相机。

## 10. 逐路验证 Argus

逐个执行：

```bash
for sensor_id in 0 1 2; do
  echo "=== sensor-id=$sensor_id ==="
  gst-launch-1.0 -e nvarguscamerasrc \
    sensor-id="$sensor_id" num-buffers=120 \
    ! 'video/x-raw(memory:NVMM),format=NV12,width=1920,height=1080,framerate=30/1' \
    ! fakesink sync=false 2>&1 | tee "/tmp/argus-$sensor_id.log"
  if grep -Eq 'No cameras available|Failure to open Camera Kernel Node|Camera node not detected' \
      "/tmp/argus-$sensor_id.log"; then
    echo "Argus sensor $sensor_id failed"
    exit 1
  fi
done
```

每一路都必须有真实取流输出，且不能出现 Argus 错误。不能只检查 `gst-launch` 退出码，因为错误可能通过 EOS 返回 0。

## 11. 逐路验证 V4L2 输入

如果 Argus 未通过，但 `/dev/videoN` 能取流，执行：

```bash
for device in /dev/video0 /dev/video1 /dev/video2; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
```

探针必须根据宿主机 `v4l2-ctl --list-formats-ext` 报告的实际格式协商。例如设备报告 `UYVY 1920x1080@30` 时，输入链路为：

```text
UYVY -> NV12/NVMM -> nvstreammux
```

脚本会使用 `v4l2src io-mode=2` 和显式 caps。容器没有 `v4l2-ctl` 时只跳过格式枚举；格式枚举建议在宿主机执行。

## 12. 进入 `deepstream.launch.py`

使用 V4L2 profile，只验证相机 source 初始化：

```bash
source /opt/ros/humble/setup.bash
source scripts/env.sh
timeout --signal=INT --kill-after=3s 12s \
  ros2 launch deepstream deepstream.launch.py \
  params_file:=config/v4l2_3cam.yaml \
  display:=false publish_detections:=false publish_annotated:=false
```

成功边界是日志出现三个 V4L2 source、native SAHI plugin、streammux/NvTracker 初始化，且没有 source caps、NVMM 或 input signal 错误。不要把 TensorRT 推理、检测结果和长时间稳定性纳入这一项。

Argus profile 的等价入口为：

```bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/csi_3cam.yaml \
  display:=false publish_detections:=false
```

只有三路 Argus 独立检查通过后才执行；如果宿主机 camera enumeration 尚未满足，不应继续启动该 profile。

## 13. 回滚

停止当前 launch 并确认进程退出。保留失败日志和环境快照。不要删除 DeepStream SDK、不要修改宿主机设备节点，也不要把容器内临时节点当作真实硬件映射。

各路线的检查命令、通过标准和常见错误见[CSI 相机输入验证](verify-csi-camera-input.md)。
