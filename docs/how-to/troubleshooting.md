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
