# 验证 CSI 相机输入

本指南从仓库根目录验证三相机输入。当前正式路径是 V4L2：它确认三路真实帧能到达 `deepstream.launch.py`、推理和 ROS topic。Argus 是独立的可选路径，不是 V4L2 验收前置条件。

## 验证路径

当前先验证稳定的 V4L2 by-path，再验证三路 DeepStream 和 ROS 输出。Argus 只在明确需要 `nvarguscamerasrc`、ISP 或 Argus 元数据时单独验证；V4L2 通过不代表 Argus 已通过，反之亦然。

```text
CSI camera driver
       |
       +--> /dev/v4l/by-path/index0/6/8 -> container /dev
       |                                    |
       |                               v4l2src
       |                                    |
       `--------------------------------> NV12/NVMM
                                            |
                                        nvstreammux
                                            |
                                    deepstream.launch.py

Optional Argus path:

camera module -> nvargus-daemon -> /tmp/argus_socket -> nvarguscamerasrc
```

## 1. 宿主机检查

在提供相机设备的运行环境执行：

```bash
v4l2-ctl --list-devices
v4l2-ctl --list-formats-ext -d /dev/video0
ls -l /dev/v4l/by-path/platform-tegra-capture-vi-video-index{0,6,8}
ls -l /dev/video* /dev/media*
```

继续使用 `media-ctl -p -d /dev/mediaN` 检查 sensor、CSI 链路和格式。设备节点编号不应直接当作稳定的物理相机身份；profile 使用 `/dev/v4l/by-path` 固定 capture index。

如果宿主机没有 `/dev/videoN`，先修复相机驱动、I2C、media topology 或供电问题。没有 V4L2 节点时，容器参数和 GStreamer 管线无法补出相机。

## 2. V4L2 容器资源检查

当前 V4L2 容器至少需要 NVIDIA runtime、相机设备、共享内存和 EGL 资源；显示模式再需要 X11。启动容器时保留以下关键参数：

```bash
--runtime nvidia \
--privileged \
--ipc=host \
--network=host \
-v /dev:/dev \
-v /tmp/.X11-unix:/tmp/.X11-unix:rw
```

进入容器后执行：

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

V4L2 路径不要求 `/tmp/argus_socket`。无窗口 V4L2 使用 surfaceless EGL；若出现 `Authorization required` 或 `No EGL Display`，先确认探针或 launch 使用了 `EGL_PLATFORM=surfaceless`，再判断相机输入。

## 3. 可选的 Argus 验证

只有产品明确需要 Argus 时，才执行以下检查。不要把它作为当前 V4L2 三相机的前置步骤：

```bash
for sensor_id in 0 1 2; do
  ./scripts/probe_argus_camera.sh "$sensor_id" 120
done
```

脚本使用 GStreamer buffer tracer 统计实际输出的 120 个 buffer。每一路都必须通过计数和错误文本检查，不能只依据 `Got EOS` 或退出码判断成功。`gst-launch-1.0` 可能在 Argus 错误后收到 EOS 并返回 0。

## 4. 常见 Argus 问题

| 现象 | 检查方向 | 处理方式 |
| --- | --- | --- |
| `Connecting to nvargus-daemon failed` | 容器内 `/tmp/argus_socket` | 在容器启动时映射宿主机 socket，并确认 socket 在启动前已经存在。 |
| `No cameras available` | 宿主机 sensor 注册、设备树、Argus module 描述 | 检查 `nvargus-daemon` 日志、`/proc/device-tree` 和供应商提供的 camera `.dtsi`/`.dtbo`；仅补 Docker 参数通常不能解决。 |
| `NVIDIA Camera enumerator not found in proc device-tree` | live device tree 是否包含 Argus camera platform/module | 使用与载板、sensor、JetPack 匹配的设备树和驱动配置；不要先修改应用层 caps。 |
| `Failure to open Camera Kernel Node` | camera kernel node、驱动绑定和权限 | 检查内核日志、media topology、sensor probe 和设备节点权限。 |
| 有 `/dev/videoN`，但 Argus 失败 | V4L2 路径与 Argus 路径的注册条件不同 | 先按稳定 by-path 的 V4L2 路径验证输入；要使用 Argus，仍需补齐 camera module、驱动和必要的 ISP tuning。 |
| 服务已 active，但仍无相机 | daemon 运行不等于 camera enumeration 成功 | 检查 daemon 日志和设备树；重启服务只能刷新状态，不能补充缺失的硬件描述。 |

如果只是需要确认问题是否位于容器，先使用临时容器额外挂载 `/tmp/argus_socket` 重复第 2、3 步。临时容器取流失败时，不要把失败归因于应用配置，继续看宿主机 Argus 错误。

## 5. 逐路验证 V4L2 输入

按 profile 的稳定路径执行：

```bash
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
```

探针验证的输入链路为：

```text
设备实际 raw 格式 -> v4l2src io-mode=2 -> nvvideoconvert
                   -> NV12/NVMM -> nvstreammux -> fakesink
```

仓库的 `config/v4l2_3cam.yaml` 使用 `UYVY 1920x1080@30`。如果设备报告其他格式，应先按 `v4l2-ctl --list-formats-ext` 的结果修改 profile 中的 `v4l2_pixel_format`、宽高和帧率，并同步修改探针 caps；不要把设备不支持的格式硬编码到管线。

稳定路径缺失时必须停止，不能回退到 `/dev/videoN`，否则 `camera_0/1/2` 可能对应错误物理相机。

如果出现 `Signal lost`、`No input source was detected` 或 `not-negotiated`，优先检查实际像素格式、分辨率、帧率、`io-mode` 和设备是否被其他进程占用。容器没有 `v4l2-ctl` 时，格式枚举在宿主机完成，GStreamer NVMM 检查仍可继续。

## 6. 进入 DeepStream 输入边界

V4L2 通过后，使用对应 profile 启动短时管线：

```bash
source /opt/ros/humble/setup.bash
source scripts/env.sh
timeout --signal=INT --kill-after=3s 12s \
  EGL_PLATFORM=surfaceless ros2 launch deepstream deepstream.launch.py \
  params_file:=config/v4l2_3cam.yaml \
  display:=false publish_detections:=true publish_annotated:=false
```

通过标准是：三个 source 初始化，出现 NVMM/source mux 相关初始化日志，没有 caps、input signal 或设备打开错误，并能看到 `/deepstream/camera_0/1/2/detections`。无窗口 V4L2 流程使用 surfaceless EGL，不要求 `DISPLAY` 或 X11 socket；主动停止产生的 `KeyboardInterrupt`、EOS 或 teardown 日志不代表输入失败。

Argus profile 只有在 sensor 0、1、2 独立取流通过后才执行：

```bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/csi_3cam.yaml \
  display:=false publish_detections:=false
```

## 7. 通过标准与回退

- V4L2 通过：三条稳定 by-path 能按实际格式进入 NV12/NVMM 和 `nvstreammux`，并能初始化 `deepstream.launch.py` 的 source，同时发布三个 ROS detection topic。
- Argus 通过：仅在产品选择 Argus 时，宿主机已注册 camera module，容器能访问 Argus socket，三个 sensor 都能真实输出帧，且没有 Argus 枚举或设备节点错误。
- 只通过资源检查：只能说明 Docker、GPU、插件或 socket 存在，不能说明相机输入正常。
- Argus 失败而 V4L2 通过：可以使用 V4L2 profile 继续验证应用输入边界；如果产品必须使用 `nvarguscamerasrc`，应转交设备树、驱动和 ISP 配置问题，不要用 V4L2 结果替代 Argus 验收。
