# 验证 CSI 相机输入

本指南用于在 Jetson 宿主机和 Docker `ds7-sahi` 容器中验证相机前置条件。它只确认相机数据能到达 `deepstream.launch.py` 所需的输入边界，不验证模型推理、检测结果、ROS 话题内容或长时间稳定性。

## 验证路径

优先验证 Argus；如果相机已经注册为 V4L2 但 Argus 尚未可用，再验证 V4L2 回退路径。两条路径的通过结论独立，V4L2 通过不代表 `nvarguscamerasrc` 已通过。

```text
宿主机相机驱动
       |
       +--> Argus camera module -> nvargus-daemon
       |                              |
       |                         /tmp/argus_socket
       |                              |
       |                    Docker nvarguscamerasrc
       |
       `--> /dev/videoN -> Docker v4l2src -> NV12/NVMM
                                      |
                                  nvstreammux
                                      |
                             deepstream.launch.py
```

## 1. 宿主机检查

在 Jetson 宿主机执行：

```bash
cd ~/wp/deepstream-sahi
./scripts/host_preflight_simulation.sh "$HOME/wp"
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video0
ls -l /dev/video* /dev/media*
systemctl is-active nvargus-daemon
test -S /tmp/argus_socket
```

`host_preflight_simulation.sh` 只检查 Docker、GPU 和工作区，不证明 CSI 相机可用。继续使用 `media-ctl -p -d /dev/mediaN` 检查 sensor、CSI 链路和格式；设备节点编号不应直接当作稳定的物理相机身份，部署时优先使用 `/dev/v4l/by-path` 或现场确认映射。

如果宿主机没有 `/dev/videoN`，先修复相机驱动、I2C、media topology 或供电问题。没有 V4L2 节点时，容器参数和 GStreamer 管线无法补出相机。

## 2. 容器资源检查

CSI 容器至少需要 NVIDIA runtime、相机设备、Argus socket 和显示/EGL 资源。启动容器时保留以下关键参数：

```bash
--runtime nvidia \
--privileged \
--ipc=host \
--network=host \
-v /dev:/dev \
-v /tmp/argus_socket:/tmp/argus_socket \
-v /tmp/.X11-unix:/tmp/.X11-unix:rw
```

进入容器后执行：

```bash
cd /workspace/deepstream-sahi
source /opt/ros/humble/setup.bash
source scripts/env.sh
./scripts/check_runtime.sh --require-gpu
./scripts/check_runtime.sh --require-argus
gst-inspect-1.0 nvarguscamerasrc
test -S /tmp/argus_socket
```

`--require-argus` 只证明 GPU、插件和 socket 资源存在，不证明 Argus 已发现相机。若检查报告 socket 缺失，先修正容器启动参数；若 socket 存在但仍无法取流，继续检查宿主机 Argus 日志和设备树。

## 3. 逐路验证 Argus

不要直接启动三路完整管线。先逐个测试 sensor ID，并同时检查 GStreamer 退出状态和错误文本：

```bash
set -o pipefail
for sensor_id in 0 1 2; do
  echo "=== sensor-id=$sensor_id ==="
  output=$(mktemp)
  if ! gst-launch-1.0 -e nvarguscamerasrc \
      sensor-id="$sensor_id" num-buffers=120 \
      ! 'video/x-raw(memory:NVMM),format=NV12,width=1920,height=1080,framerate=30/1' \
      ! fakesink sync=false 2>&1 | tee "$output"; then
    echo "Argus sensor $sensor_id failed: gst-launch returned non-zero"
    rm -f "$output"
    exit 1
  fi
  if grep -Eq 'No cameras available|Failure to open Camera Kernel Node|Camera node not detected' "$output"; then
    echo "Argus sensor $sensor_id failed: camera enumeration error"
    rm -f "$output"
    exit 1
  fi
  rm -f "$output"
done
```

每一路都必须真实产生 120 帧，不能只依据 `Got EOS` 或退出码判断成功。`gst-launch-1.0` 可能在 Argus 错误后收到 EOS 并返回 0。

## 4. 常见 Argus 问题

| 现象 | 检查方向 | 处理方式 |
| --- | --- | --- |
| `Connecting to nvargus-daemon failed` | 容器内 `/tmp/argus_socket` | 在容器启动时映射宿主机 socket，并确认 socket 在启动前已经存在。 |
| `No cameras available` | 宿主机 sensor 注册、设备树、Argus module 描述 | 检查 `nvargus-daemon` 日志、`/proc/device-tree` 和供应商提供的 camera `.dtsi`/`.dtbo`；仅补 Docker 参数通常不能解决。 |
| `NVIDIA Camera enumerator not found in proc device-tree` | live device tree 是否包含 Argus camera platform/module | 使用与载板、sensor、JetPack 匹配的设备树和驱动配置；不要先修改应用层 caps。 |
| `Failure to open Camera Kernel Node` | camera kernel node、驱动绑定和权限 | 检查内核日志、media topology、sensor probe 和设备节点权限。 |
| 有 `/dev/videoN`，但 Argus 失败 | V4L2 路径与 Argus 路径的注册条件不同 | 先按 V4L2 回退路径验证输入；要使用 Argus，仍需补齐 camera module、驱动和必要的 ISP tuning。 |
| 服务已 active，但仍无相机 | daemon 运行不等于 camera enumeration 成功 | 检查 daemon 日志和设备树；重启服务只能刷新状态，不能补充缺失的硬件描述。 |

如果只是需要确认问题是否位于容器，先使用临时容器额外挂载 `/tmp/argus_socket` 重复第 2、3 步。临时容器取流失败时，不要把失败归因于应用配置，继续看宿主机 Argus 错误。

## 5. V4L2 回退路径

当宿主机能列出 `/dev/videoN` 且 `v4l2-ctl` 能报告格式，但 Argus 尚未通过时，执行：

```bash
for device in /dev/video0 /dev/video1 /dev/video2; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
```

探针验证的输入链路为：

```text
设备实际 raw 格式 -> v4l2src io-mode=2 -> nvvideoconvert
                   -> NV12/NVMM -> nvstreammux -> fakesink
```

仓库的 `config/v4l2_3cam.yaml` 示例使用 `UYVY 1920x1080@30`。如果设备报告其他格式，应先按 `v4l2-ctl --list-formats-ext` 的结果修改 profile 中的 `v4l2_pixel_format`、宽高和帧率，并同步修改探针 caps；不要把设备不支持的格式硬编码到管线。

如果出现 `Signal lost`、`No input source was detected` 或 `not-negotiated`，优先检查实际像素格式、分辨率、帧率、`io-mode` 和设备是否被其他进程占用。容器没有 `v4l2-ctl` 时，格式枚举在宿主机完成，GStreamer NVMM 检查仍可继续。

## 6. 进入 DeepStream 输入边界

V4L2 通过后，使用对应 profile 启动短时管线：

```bash
source /opt/ros/humble/setup.bash
source scripts/env.sh
timeout --signal=INT --kill-after=3s 12s \
  ros2 launch deepstream deepstream.launch.py \
  params_file:=config/v4l2_3cam.yaml \
  display:=false publish_detections:=false publish_annotated:=false
```

通过标准是：三个 source 初始化，出现 NVMM/source mux 相关初始化日志，且没有 caps、input signal 或设备打开错误。主动停止产生的 `KeyboardInterrupt`、EOS 或 teardown 日志不代表输入失败。

Argus profile 只有在 sensor 0、1、2 独立取流通过后才执行：

```bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/csi_3cam.yaml \
  display:=false publish_detections:=false
```

## 7. 通过标准与回退

- Argus 通过：宿主机已注册 camera module，容器能访问 Argus socket，三个 sensor 都能真实输出帧，且没有 Argus 枚举或设备节点错误。
- V4L2 通过：三个设备节点能按实际格式进入 NV12/NVMM 和 `nvstreammux`，并能初始化 `deepstream.launch.py` 的 source。
- 只通过资源检查：只能说明 Docker、GPU、插件或 socket 存在，不能说明相机输入正常。
- Argus 失败而 V4L2 通过：可以使用 V4L2 profile 继续验证应用输入边界；如果产品必须使用 `nvarguscamerasrc`，应转交设备树、驱动和 ISP 配置问题，不要用 V4L2 结果替代 Argus 验收。
