# 在现有容器中安装

本指南在 Jetson 上的 DeepStream 7.1 容器中安装仓库依赖，并把项目产物保存在仓库目录。它不把仓库插件安装到 `/opt`，也不修改宿主机内核或设备树。

## 1. 记录初始状态

在容器内从仓库根目录执行：

```bash
cd /workspace/deepstream-sahi
./scripts/capture_environment.sh
```

脚本把带 UTC 时间戳的报告和 `.state/environment-latest.txt` 写入仓库。只读容器也可以执行这一步。

## 2. 检查容器写权限

安装脚本使用 apt，因此需要 root、可写的仓库和可写的容器根文件系统：

```bash
id -u
test -w .
test -w /
```

如果 `test -w /` 失败，说明容器根文件系统是只读挂载。root 权限不能改变只读挂载；请重建可写容器，或使用已经包含依赖的镜像。不要在运行中的只读容器里使用 `nsenter`、`mknod` 或临时创建 NVIDIA 设备节点。

安装前检查 apt 候选版本：

```bash
apt-cache policy ros-humble-ros-base cuda-cudart-dev-12-6 cuda-crt-12-6 \
  libnvinfer-dev libnvonnxparsers-dev
```

ROS 2、CUDA、TensorRT 和 DeepStream 必须来自与 JetPack 匹配的发行版。不要混用 x86、独立 GPU 或其他 JetPack 版本的软件源。

安装依赖：

```bash
./scripts/install_inplace.sh
```

脚本会安装构建工具、CUDA 12.6 开发包、GStreamer 开发包、TensorRT 开发包、Python/ROS 2 依赖，创建使用系统 site-packages 的 `.venv`，并安装 NVIDIA Jetson `pyds` 1.2.0 wheel。

如果 wheel 不在默认下载位置：

```bash
PYDS_WHEEL=/path/to/pyds-1.2.0-*.whl ./scripts/install_inplace.sh
```

wheel 必须匹配 Python、Jetson、CUDA 和 DeepStream 版本。

## 3. 构建 SAHI 插件

插件源码已经纳入当前仓库，直接执行：

```bash
./scripts/build_sahi_plugins.sh
```

构建结果位于 `.local/lib/gstreamer-1.0`。加载环境并检查：

```bash
source scripts/env.sh
gst-inspect-1.0 nvsahipreprocess nvsahipostprocess
```

SAHI profile 要求这两个 native plugin；插件缺失时，程序应明确失败，不会自动使用旧的 Python 合并路径。

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

检查 Python binding 和插件：

```bash
python3 -c 'import pyds; print("pyds import: ok")'
gst-inspect-1.0 nvsahipreprocess nvsahipostprocess
```

如果没有 NVIDIA 设备映射，`pyds` 可能输出 EGL 警告。把 Python import 检查和 `check_runtime.sh` 资源检查分开判断，不能仅凭 import 结果接受管线。

## 5. 准备 YOLOv11s 资源

默认 SAHI profile 使用动态 batch、640x640 的 YOLOv11s COCO-80 ONNX：

```bash
HTTP_PROXY=http://your_proxy:port \
HTTPS_PROXY=http://your_proxy:port \
./scripts/fetch_yolov11s_onnx.sh
./scripts/build_yolov11s_parser.sh
```

下载脚本会校验 SHA256。解析器构建需要 `libnvinfer-dev` 和 `libnvonnxparsers-dev`。如果模型或 parser 不存在，先修复资源，再运行相机输入验证。

## 6. 创建正确的 Docker CSI 运行环境

先保存当前容器，保留回滚点：

```bash
docker commit ds7-sahi ds7-sahi:environment-snapshot
```

CSI 容器必须额外挂载 Argus socket。最小示例：

```bash
docker run --name ds7-sahi-csi \
  --runtime nvidia \
  --privileged \
  --ipc=host \
  --network=host \
  -v /dev:/dev \
  -v /tmp/argus_socket:/tmp/argus_socket \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -e DISPLAY="$DISPLAY" \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -v "$HOME/wp:/workspace" \
  -w /workspace \
  ds7-sahi:environment-snapshot bash
```

不要把 `/tmp/argus_socket` 误写成普通目录；容器启动时宿主机 socket 必须已经存在。进入容器后执行：

```bash
cd /workspace/deepstream-sahi
source /opt/ros/humble/setup.bash
source scripts/env.sh
./scripts/check_runtime.sh --require-argus
```

如果现有启动脚本只映射了 `/dev` 和 X11，却没有映射 `/tmp/argus_socket`，它可以运行文件 profile，却不能满足 CSI 前置条件。修改启动器或使用上面的显式命令后再做 Argus 验证。相机输入的检查顺序和错误处理见[CSI 相机输入验证](verify-csi-camera-input.md)。

显示模式还需要有效的 X11 授权。宿主机可按实际用户执行：

```bash
DISPLAY="$DISPLAY" xhost +SI:localuser:root
```

若容器使用 `XAUTHORITY`，同时挂载：

```bash
-e XAUTHORITY=/tmp/.Xauthority \
-v "$HOME/.Xauthority:/tmp/.Xauthority:ro"
```

`display:=false` 只关闭可见窗口；当前 native SAHI allocator 仍需要 NVIDIA EGL/X11 资源。

## 7. 容器内验证顺序

按以下顺序执行，不要跳过硬件边界：

```bash
./scripts/check_runtime.sh --require-gpu
./scripts/check_runtime.sh --require-display
./scripts/check_runtime.sh --require-argus
gst-inspect-1.0 nvarguscamerasrc
```

宿主机另行检查：

```bash
v4l2-ctl --list-devices
systemctl is-active nvargus-daemon
```

然后逐个验证 Argus：

```bash
for sensor_id in 0 1 2; do
  ./scripts/probe_argus_camera.sh "$sensor_id" 120
done
```

脚本会检查日志错误并统计实际输出的 120 个 buffer。由于 Argus 错误可能通过 EOS 返回退出码 0，不能只检查 shell 返回值。

如果 Argus 尚未修复，但宿主机 V4L2 能出帧，可执行替代路径：

```bash
for device in /dev/video0 /dev/video1 /dev/video2; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
```

## 8. 回滚

保留旧容器和快照，直到文件、GPU、OSD、ROS 2 和 CSI 检查完成。只删除本次创建的替代容器：

```bash
docker stop ds7-sahi-csi
docker rm ds7-sahi-csi
docker image inspect ds7-sahi:environment-snapshot
```

不要删除 DeepStream SDK，不要修改宿主机设备节点，不要用容器内临时节点替代真实设备映射。
