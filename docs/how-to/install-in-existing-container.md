# 在现有容器中安装

本指南在 Jetson 上的 DeepStream 7.1 容器中安装仓库依赖，并把项目产物保存在仓库目录。它不把仓库插件安装到 `/opt`，也不修改宿主机内核或设备树。

## 推荐：构建并启动环境镜像

仓库提供的镜像只包含 DeepStream、CUDA/TensorRT 开发文件、ROS 2、GStreamer 和
`pyds` 等系统环境，不复制项目源码，也不把模型、插件或 ROS 2 构建产物写入镜像。
源码通过启动脚本挂载到 `/workspace/deepstream-sahi`，因此容器退出后项目产物仍在当前
checkout 中。

从 checkout 根目录构建镜像：

```bash
docker build --network=host \
  -f docker/Dockerfile \
  -t deepstream-sahi:7.1-env \
  docker
```

构建上下文是 `docker/`，其中包含与 DeepStream 7.1、Python 3.10 和 Jetson
aarch64 匹配的 `pyds` wheel。镜像构建应在目标 Jetson 或等价的 aarch64 构建环境中
执行。`--network=host` 用于绕过部分 Jetson 主机上 Docker bridge 的 iptables raw
表不可用问题；它不会修改宿主机的防火墙或启动配置。

无窗口三路 V4L2 验证使用 surfaceless EGL：

```bash
EGL_PLATFORM=surfaceless ./docker/run_docker.sh
```

需要 OSD 窗口时，使用实际的本机 X display，并显式选择 X11 EGL：

```bash
DISPLAY_VALUE=:0 EGL_PLATFORM=x11 ./docker/run_docker.sh
```

将 `:0` 替换为本机实际的 display。启动脚本默认映射 NVIDIA runtime、`/dev`、共享
内存、host network 和 checkout；V4L2 profile 不需要 `/tmp/argus_socket`。只有验证
Argus CSI 时才增加：

```bash
MOUNT_ARGUS_SOCKET=1 EGL_PLATFORM=surfaceless ./docker/run_docker.sh
```

进入容器后，先执行本指南第 3 节的插件构建和第 4 节的 ROS 2 构建，再执行：

```bash
./scripts/check_runtime.sh --require-gpu
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
```

如果只需要复用已经创建的容器，可跳过本节，继续使用下面的手工安装流程。

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

## 6. 在已有容器中确认运行时映射

如果没有使用仓库启动脚本，而是从已有容器继续验证，先保存当前容器，保留回滚点：

```bash
docker commit ds7-sahi ds7-sahi:environment-snapshot
```

当前三相机 V4L2 无窗口容器只需要映射 `/dev`、GPU 和共享内存；运行时使用 surfaceless EGL。只有显示模式才需要 EGL/X11 映射，只有选择 Argus profile 时才额外挂载 `/tmp/argus_socket`。从 checkout 根目录启动一次性 V4L2 容器：

```bash
docker run --rm --name deepstream-sahi-v4l2 \
  --runtime nvidia \
  --privileged \
  --ipc=host \
  --network=host \
  -v /dev:/dev \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -e DISPLAY="$DISPLAY" \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -v "$PWD:/workspace/deepstream-sahi" \
  -w /workspace/deepstream-sahi \
  ds7-sahi:environment-snapshot bash
```

进入容器后执行 V4L2 资源检查：

```bash
cd /workspace/deepstream-sahi
source /opt/ros/humble/setup.bash
source scripts/env.sh
./scripts/check_runtime.sh --require-gpu
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  test -c "$device"
done
```

该容器可直接运行文件和 V4L2 profile。若验证 Argus，另行增加 `-v /tmp/argus_socket:/tmp/argus_socket` 和 `--ipc=host`，并按[CSI 相机输入验证](verify-csi-camera-input.md)的可选 Argus 流程执行。

显示模式还需要有效的 X11 授权。宿主机可按实际用户执行：

```bash
DISPLAY="$DISPLAY" xhost +SI:localuser:root
```

若容器使用 `XAUTHORITY`，同时挂载：

```bash
-e XAUTHORITY=/tmp/.Xauthority \
-v "$HOME/.Xauthority:/tmp/.Xauthority:ro"
```

`display:=false` 关闭可见窗口。无窗口全 V4L2 profile 使用 surfaceless EGL，不需要 `DISPLAY` 或 `/tmp/.X11-unix`；节点会自动设置 `EGL_PLATFORM=surfaceless`。手工运行 GStreamer V4L2 探针时，脚本也会使用该平台。

## 7. 容器内验证顺序

按以下顺序执行，不要跳过硬件边界：

```bash
./scripts/check_runtime.sh --require-gpu
gst-inspect-1.0 v4l2src
```

V4L2 设备路径另行检查：

```bash
v4l2-ctl --list-devices
ls -l /dev/v4l/by-path/platform-tegra-capture-vi-video-index{0,6,8}
```

逐个验证 V4L2：

```bash
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
```

稳定路径缺失时停止并修复设备映射，不回退到 `/dev/videoN`。

只有需要 Argus 时才额外执行：

```bash
./scripts/check_runtime.sh --require-argus
for sensor_id in 0 1 2; do
  ./scripts/probe_argus_camera.sh "$sensor_id" 120
done
```

## 8. 回滚

保留镜像快照，直到文件、GPU、OSD、ROS 2 和 V4L2 检查完成。使用 `--rm` 创建的临时容器会在退出后自动清理：

```bash
docker image inspect ds7-sahi:environment-snapshot
```

不要删除 DeepStream SDK，不要修改宿主机设备节点，不要用容器内临时节点替代真实设备映射。
