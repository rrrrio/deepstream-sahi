# 故障排查

先记录启动命令、`git status --short`、环境版本和第一条错误日志。下面的检查按常见现象组织；硬件相关结果必须在具备相应设备的环境中确认。

## 节点在 source 校验阶段退出

检查：

```bash
ros2 param dump /deepstream_node
```

常见原因是 `sources` 为空且 `source` 也为空，或 `source_names` 与 `sources` 长度不同。名称不能为空且必须唯一。单路兼容调用可以只传 `source:=file:///data/input.mp4`；多路调用应显式传等长数组。

## 找不到配置文件或 engine

检查 `infer_config`、`preprocess_config` 和 `tracker_config` 的实际路径：

```bash
test -f config/config_infer_primary_yolov11s_sahi.txt
test -f models/yolo11s.onnx
test -f .local/lib/libnvdsinfer_yolov11s.so
test -f config/deepstream_tracker_config.txt
```

配置中的 ONNX、label 和 custom library 路径相对于配置文件解析。首次使用某个模型时，TensorRT 可能构建 engine；构建是否成功取决于匹配的 GPU、CUDA 和 DeepStream 环境。

YOLOv11s 默认资产可通过 `./scripts/fetch_yolov11s_onnx.sh` 下载。下载失败时设置 `HTTP_PROXY` 与 `HTTPS_PROXY`，并保留脚本中的 SHA256 校验。`libnvdsinfer_yolov11s.so` 缺失时运行 `./scripts/build_yolov11s_parser.sh`；该步骤需要 `libnvinfer-dev` 和 `libnvonnxparsers-dev`。

如果错误显示 `.colcon/install/deepstream/share/deepstream/models/yolo11s.onnx`
不存在，先确认仓库中的 `models/yolo11s.onnx` 已通过下载脚本校验。运行时会在
安装包没有模型时回退到仓库资产；修改模型或依赖后重新执行 colcon 构建，并删除
`.colcon/build/deepstream/models` 中指向旧 `.download` 文件的断链。

## `gst-inspect-1.0` 找不到 SAHI 插件

```bash
source scripts/env.sh
echo "$GST_PLUGIN_PATH"
gst-inspect-1.0 nvsahipreprocess nvsahipostprocess
find .local/lib/gstreamer-1.0 -name 'libnvdsgst_sahi*.so' -print
```

如果库文件不存在，按[现有容器安装指南](install-in-existing-container.md)构建插件。如果库存在但加载失败，检查 CUDA、DeepStream 和 GStreamer 依赖。SAHI 模式要求原生插件，不提供 Python fallback。

## 文件播放一次后停止

文件 source 需要 `file_loop:=true`：

```bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam.yaml display:=false
```

多路文件应使用 `sources` 数组；单路兼容参数 `source` 适用于 `ros2 run deepstream deepstream_node` 直接启动。RTSP、V4L2 和 Argus 被视为 live source，不使用文件循环语义。

## CSI 报 Argus 或 sensor 错误

```bash
./scripts/check_runtime.sh --require-argus
```

确认容器使用 NVIDIA runtime，且 Argus socket、设备节点和共享内存已映射。逐路用 `argus://0`、`argus://1`、`argus://2` 检查 sensor ID，再运行三路。应用参数无法创建缺失的宿主机设备。

> [!CAUTION]
> 不要直接覆盖供应商的 camera `.dtbo`/`.dtb`，也不要未经完整回滚准备就在
> `/boot/extlinux/extlinux.conf` 中加入显式 `FDT` 来修复 Argus。Jetson 的实际启动
> 设备树可能由 QSPI、UEFI 和 L4TLauncher 共同选择；手工加入 `FDT` 可能绕过 board
> DT，造成黑屏、SSH 不可达并进入 Recovery。先区分容器缺少
> `/tmp/argus_socket`、宿主机 Argus 枚举失败和设备树问题；修改设备树前备份原文件、
> 记录 SHA256，并准备串口和回滚路径。完整恢复流程见[Jetson CSI 配置与启动恢复记录](jetson-csi-boot-recovery.md)。

## 三路 CSI 验证记录（2026-10-13）

本次验证在 Seeed reComputer Super 开发环境完成，live device tree 为
`nvidia,p3768-0000+p3767-0000-super`，L4T 为
`R36.4.3`，内核为 `5.15.148-tegra`。提权后的 `nvidia-smi` 可以识别
`Orin (nvgpu)`，GPU 和 DeepStream 资源检查通过。

要区分两个 namespace：提供硬件的宿主机确实有 `/tmp/argus_socket`、`/dev/video0`、
`/dev/video1`、`/dev/video2` 和 `/dev/media0`；最初运行检查的独立诊断容器没有
这些挂载，因此不能代表目标宿主机。实际运行中的 `ds7-sahi` 容器挂载了 `/dev`，
但 Docker inspect 显示没有挂载 `/tmp/argus_socket`，所以容器内的
`./scripts/check_runtime.sh --require-argus` 会因 socket 缺失失败。重建 CSI
容器时必须增加：

```bash
-v /tmp/argus_socket:/tmp/argus_socket \
--ipc=host
```

宿主机上的 `nvargus-daemon` 为 `active (running)`、`enabled`。三路设备均可通过
宿主机 V4L2 mmap 取 10 帧，媒体拓扑也已建立；`/dev/video0`、`/dev/video1`、
`/dev/video2` 分别对应 `veyecam 9-003b`、`10-003b`、`12-003b`，第四个 DT 声明
位置 `11-003b` 未绑定。V4L2 通过不等于 Argus 通过。

使用正确的容器挂载和 X11 授权后，三路 V4L2 probe 均通过 10 帧，caps 路径为
`UYVY 1920x1080@30 -> NV12/NVMM -> nvstreammux`。随后运行
`config/v4l2_3cam.yaml` 约 20 秒，三路 source、SAHI native postprocess、tracker
和 TensorRT engine 均初始化成功；受控停止时三个 source 都收到 EOS，没有出现
caps、signal 或 device-open 错误。这是当前无需重启宿主机即可使用的安全输入路径。

该次临时验证容器使用了 `/tmp/argus_socket`、`/dev`、`--ipc=host` 和 NVIDIA
runtime；目标 X server 最初只允许 `bdca2`，因此额外临时执行了
`xhost +SI:localuser:root`，容器退出后立即撤销。若日志出现
`Authorization required` 或 `No EGL Display`，先检查 X11 授权，不要把它误判为
V4L2 没有帧。

宿主机的 live device tree 包含四个 `veyecam` module（`cam0_veyecam` 到
`cam3_veyecam`），节点声明为 `1920x1080`、`UYVY`、双 lane、30 FPS。当前启动的
VEYE/Seeed camera overlay 为 `/boot/tegra234-p3767-camera-p3768-veyecam2m-quad-seeed.dtbo`；
反编译结果把 `tegra-camera-platform` 放在 `/bus@0/tegra-camera-platform`，而不是
Argus 期望的设备树根路径 `/tegra-camera-platform`。宿主机 `nvargus-daemon` 日志
明确报告：

```text
OFParserListModules: NVIDIA Camera enumerator not found in proc device-tree
NvCamHwHalOpen: Failure to open Camera Kernel Node
NvOdmImagerGetModuleList: Camera node not detected - No such file or directory
NvPclModuleListInitialize: device detection on this platform is not supported
```

这是 Argus 枚举阶段失败，不是容器单纯缺少 `/dev/videoN`。宿主机直接运行
`nvarguscamerasrc sensor-id=0/1/2`，以及只额外挂载 Argus socket 的临时 CSI
容器，均会输出 `No cameras available`；`gst-launch-1.0` 仍可能以 EOS 和退出码
0 结束，因此必须使用 `scripts/probe_argus_camera.sh` 的真实 buffer 计数。

内核启动日志同时显示 `9-003b`、`10-003b` 和 `12-003b` 已检测到 IMX327 并绑定
`veyecam`，只有 `11-003b` probe 失败。这说明 `serdes_link_freq err: -61`
不是三路全部失败的充分解释；VEYE 官方 BSP 的 `veyecam2m` 示例也没有声明该属性。
当前更强的根因证据是 `tegra-camera-platform` 的 DT 层级错误。前期使用目标机自带
`dtc` 将该 DTBO 移到根路径后，离线 `fdtoverlay` 合并验证通过；随后为验证启动链路
确实进行了 DTBO/DTB 和 `extlinux.conf` 的实验性修改，设备因此出现黑屏并进入
Recovery。完整经过和恢复证据见[Jetson CSI 配置与启动恢复记录](jetson-csi-boot-recovery.md)。

参考：[VEYE 官方 BSP](https://github.com/veyeimaging/nvidia_jetson_veye_bsp) 中的
`veyecam.c` 和 `tegra234-camera-rbpcv3-veyecam2m.dtsi`；官方 overlay 将
`tegra-camera-platform` 放在设备树根路径。

不要把失败后的 rebind 当作修复手段；它可能留下 `reset_gpio (-16)` 等资源残留。
验证 DT 修复时应重启宿主机后重新采集启动日志，确认三路仍出现 `bound`/
`Detected VEYECAM sensor`，再运行 Argus buffer probe。

推荐恢复顺序：

1. 当前设备优先使用 `config/v4l2_3cam.yaml`。按[现有容器安装指南](install-in-existing-container.md)启动一次性 V4L2 容器，映射 `/dev`、共享内存和 NVIDIA runtime；先运行三条 `/dev/v4l/by-path` 的 `scripts/probe_v4l2_camera.sh`，再运行短时 ROS 2 管线。不要把 Argus 失败误判为 V4L2 失败。
2. 如果确认必须修复设备树，使用与 reComputer Super、sensor 和 L4T `R36.4.3` 匹配的
   供应商 BSP、`.dtsi`/`.dtbo` 和刷写流程。不要根据 `serdes_link_freq` 错误自行填入
   猜测值，也不要在运行中的系统上手工替换 DTB/DTBO 或追加 `FDT`。
3. 在可回滚、串口可用且已保存原始文件和 SHA256 后，再通过供应商流程应用修正版配置。
   启动后检查根路径
   `/proc/device-tree/tegra-camera-platform`、三路 `bound`/`Detected VEYECAM sensor`、
   `/tmp/argus_socket` 和 `nvargus-daemon`。
4. 如果产品必须使用 Argus，再创建 Argus 容器，映射 `/dev`、`/tmp/argus_socket`、
   `--ipc=host` 和 NVIDIA runtime，然后在容器内运行
   `./scripts/check_runtime.sh --require-argus`。
5. 重新执行三个 `./scripts/probe_argus_camera.sh 0 120`、`1 120`、`2 120`，
   三路都通过后才启动 `config/csi_3cam.yaml`。

本次过程的前期只通过 SSH 读取并离线验证了修正版 DTBO；后续实验性 DTBO/DTB 和
`extlinux.conf` 修改属于高影响操作，最终触发了启动链路故障。若暂时不能确认 Argus
设备树配置，三路 V4L2 可以作为应用输入回退路径继续验证，但不能替代 Argus 验收。

本次后续恢复表明，启动设备树实验确实可能影响 UEFI/L4TLauncher 链路。不要把
“文件已恢复到 `/boot`”当作“启动链路已恢复”的证据；应通过串口检查
`/proc/cmdline`、APP 根分区挂载状态、`nvbootctrl` slot 状态和 UEFI 的 OS chain
状态。详见[Jetson CSI 配置与启动恢复记录](jetson-csi-boot-recovery.md)。

## 显示启动失败

先确认 GPU 和 EGL/X11 资源，再用无可见窗口的 profile 分离 OSD sink：

```bash
./scripts/check_runtime.sh --require-display
ros2 launch deepstream deepstream.launch.py display:=false
```

无窗口模式仍需要 `DISPLAY`、`/tmp/.X11-unix`、EGL 权限和容器显示映射，因为
native SAHI allocator 使用 EGL-backed surface。显示模式预期为 2x2 tile；没有
图形会话时不要用 `display:=true` 作为验收命令。

## ROS 检测 topic 不存在

检查参数和依赖：

```bash
ros2 topic list | grep '/deepstream/'
ros2 interface show vision_msgs/msg/Detection2DArray
```

必须设置 `publish_detections:=true`，并能导入 `vision_msgs`。topic 名来自 `source_names`，例如 `camera_0` 对应 `/deepstream/camera_0/detections`。如果 topic 存在但数组为空，先确认视频确实有帧，再检查模型和置信度阈值。

## bbox 或 tracking ID 不符合预期

确认发布探针位于 tracker 后、tiler 前，并读取原图坐标。NMM 应位于 tracker 前；否则切片边界的重复框可能产生多个 tracker ID。未跟踪目标的 `Detection2D.id` 为空字符串；检测置信度不可用时会回退到 tracker confidence。

## 性能或 batch 异常

默认 1920x1080、640x640、0.2 overlap 产生每路 9 个 ROI。多路时总 ROI 为 `source_count * roi_count`；`pgie_batch_size` 必须整除总 ROI，允许插件将一帧拆成多个 tensor batch。检查运行日志、nvinfer batch 属性和 preprocess plugin 属性，不要仅根据 FPS 判断 batch 正确。

当 `pgie_batch_size=1` 时，三路默认 profile 会把一个 mux buffer 拆成 27 个
SAHI tensor batch。`scaling-buf-pool-size` 和 `tensor-buf-pool-size` 必须至少能
容纳这些 batch；默认 YOLOv11s 配置使用 32。池只有 6 时，native preprocessor
会在第 7 个 ROI 等待 buffer，ROS topic 会被创建但没有消息。

如果 TensorRT 日志显示 `Tactic Device request` 大于 `Available`，说明 GPU workspace 不足，优先使用 `config/sim_3cam_headless.yaml` 的 batch 1。清理其他 GPU 工作负载，并在 Jetson 宿主机检查电源模式与时钟；`nvpmodel` 和 `jetson_clocks` 通常不在容器内。不要在模型构建仍在进行时并发启动其他 DeepStream 管线。

更多逐项命令见[三路验证手册](verify-milestones.md)和[运行时要求](../reference/runtime-requirements.md)。

## YOLOv11s parser 调试

启动前设置 `DEEPSTREAM_SAHI_PARSER_DEBUG=1`，可以打印每次 parser 调用的输出尺寸、最高类别分数和接受的检测数量。该变量会为每个 tensor batch 输出一行，只应在排查模型输出时使用。
