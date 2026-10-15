# 配置参考

## 目录

- [解说](#解说)
- [参考场景](#参考场景)
  - [多路 source 映射](#多路-source-映射)
  - [兼容单路 source](#兼容单路-source)
- [组件与参数说明](#组件与参数说明)
  - [组件说明](#组件说明)
  - [参数说明](#参数说明)

## 解说

节点通过 ROS 2 参数构造 source、streammux、SAHI、tracker、OSD 和输出 publisher。`sources` 是多路入口；`source_names` 与它按位置对应，并用于 OSD 标签和 ROS topic 名。为保持旧命令可用，`source` 在 `sources` 为空时提供单路入口。

SAHI 使用输入尺寸、切片尺寸、显式 ROI 列表和重叠比例生成 ROI。`rois` 非空时直接使用列表中的 `[left, top, width, height]`，三路 source 共用同一组配置；`rois: "[]"` 或空字符串时按 overlap 使用完整网格。默认 1920x1080、640x640、0.2/0.2 的网格模式会生成 9 个局部 ROI；三路 profile 使用 3 个显式中心横向切片，并由 `include_full_frame` 加 1 个全图 ROI。`pgie_batch_size` 为 0 时，节点使用全部 source ROI；显式 batch 必须整除总 ROI。

默认模型是动态 batch YOLOv11s COCO-80 ONNX。它的输入是 `images`，形状为 `batch×3×640×640`；输出 `output0` 的形状为 `batch×84×anchors`。前四个 channel 是中心点和宽高，后 80 个 channel 是类别置信度。`libnvdsinfer_yolov11s.so` 在 nvinfer 内解码该格式，nvinfer 的 NMS 再处理单个 tile 的重叠框，SAHI postprocess 处理 tile 间重复框。

## 参考场景

### 多路 source 映射

```gherkin
Feature: 多路 source 映射
  Background:
    Given 节点收到等长且名称唯一的 sources 和 source_names

  Scenario: 按位置建立相机输出
    Given sources 为三个文件或 Argus URI
    When 管线启动
    Then source 的顺序对应 source_id 和 OSD tile
    And 每个 source_name 对应一个检测 topic
```

### 兼容单路 source

```gherkin
Feature: 单路 source 兼容
  Scenario: 未提供 sources 时使用 source
    Given sources 为空且 source 包含一个可识别 URI
    When 节点解析运行配置
    Then source 被转换为一个元素的 sources
    And 未提供 source_names 时名称为 camera_0
```

## 组件与参数说明

### 组件说明

```mermaid
%%{init: {"theme": "neutral", "flowchart": {"curve": "basis", "htmlLabels": true, "padding": 10}}}%%
flowchart LR
    A[Sources] --> B[nvstreammux]
    B --> C[SAHI ROI]
    C --> D[nvinfer]
    D --> E[NMM]
    E --> F[nvtracker]
    F --> G[ROS metadata probe]
    G --> H[nvmultistreamtiler]
    H --> I[nvdsosd / sink]
    classDef source fill:#E8F3FF,stroke:#2563EB,color:#0F172A;
    classDef process fill:#F2ECFF,stroke:#7C3AED,color:#2E1065;
    classDef storage fill:#E7F8F1,stroke:#047857,color:#064E3B;
    class A source;
    class B,C,D,E,F,G,H,I process;
```

文件和 RTSP 使用 URI source；`argus://N` 使用 `nvarguscamerasrc`。NMM 在 tracker 前合并跨 ROI 的框，ROS probe 在 tracker 后读取 detector/tracker metadata。显示输出经过 tiler；发布的 bbox 保持原图坐标。

### 参数说明

示例：

```yaml
mode: sahi
sources: ["file:///data/cam0.mp4", "file:///data/cam1.mp4", "file:///data/cam2.mp4"]
source_names: [camera_0, camera_1, camera_2]
file_loop: true
camera_fps: 30
v4l2_pixel_format: UYVY
slice_width: 640
slice_height: 640
overlap_h: 0.2
overlap_v: 0.2
rois: "[[0,220,640,640], [640,220,640,640], [1280,220,640,640]]"
include_full_frame: true
tracker_enabled: true
display: false
publish_detections: true
```

| 参数 | 类型 / 默认 | 说明 |
| --- | --- | --- |
| `mode` | string / `base` | `base` 或 `sahi`。 |
| `source` | string / 空 | 单路兼容参数；仅在 `sources` 为空时使用。 |
| `sources` | string[] / 空 | 至少一个 source；支持文件路径、`file://`、RTSP、V4L2 和 `argus://N`。 |
| `source_names` | string[] / 自动 | 必须与 `sources` 等长且唯一；为空时生成 `camera_0` 等名称。 |
| `file_loop` | bool / `true` | 文件 source 播放结束后是否循环。 |
| `camera_fps` | int / `30` | Argus source 请求的帧率。 |
| `v4l2_pixel_format` | string / `UYVY` | V4L2 raw source 的输入像素格式；应与宿主机 `v4l2-ctl` 报告的格式一致。 |
| `width`, `height` | int / `1920`, `1080` | 输入帧尺寸和 SAHI ROI 计算基准。 |
| `slice_width`, `slice_height` | int / `640`, `640` | YOLOv11s SAHI 切片尺寸。 |
| `overlap_h`, `overlap_v` | float / `0.2` | 水平和垂直重叠比例，范围为 `[0, 1)`。 |
| `rois` | string / `""` | JSON 风格的 ROI 列表，每项为 `[left, top, width, height]`；例如 `"[[0,220,640,640], [640,220,640,640]]"`。空字符串或 `"[]"` 使用 overlap 计算默认网格。三路 source 共用这组 ROI。 |
| `include_full_frame` | bool / `true` | 是否额外加入 `[0,0,width,height]` 全图 ROI；显式列表已经包含全图时不会重复添加。 |
| `pgie_batch_size` | int / `0` | `<=0` 时使用总 ROI；显式值必须整除总 ROI。 |
| `det_interval` | int / `2` | nvinfer 检测间隔；负值表示不主动设置。 |
| `merge_enabled` | bool / `true` | SAHI 模式是否启用 NMM。 |
| `merge_metric` | string / `ios` | `ios` 或 `iou`。 |
| `merge_threshold` | float / `0.5` | NMM 合并阈值，必须大于 0。 |
| `tracker_enabled` | bool / `true` | 是否启用 nvtracker。 |
| `tracker_config` | string / 包内默认 | tracker 配置文件路径。 |
| `display` | bool / `true` | 是否创建显示 sink；关闭后使用 headless 路径。 |
| `publish_detections` | bool / `false` | 是否按 source 发布 `Detection2DArray`。 |
| `publish_annotated` | bool / `false` | 是否按 source 发布 GPU JPEG 标注图像。 |
| `allowed_class_ids` | int[] / `[]` | 允许发布和跟踪的数字类别 ID；空列表表示不过滤。 |
| `infer_config` | string / 按 mode | nvinfer 配置文件路径。 |
| `preprocess_config` | string / SAHI 包内默认 | 原生 SAHI preprocess 配置；SAHI 模式必须提供有效文件。 |
| `engine_cache_dir` | string / 项目缓存 | TensorRT runtime 配置和 engine 的可写缓存目录。 |
| `batched_push_timeout` | int / `40000` | streammux batched push timeout，单位为微秒。 |

`sources` 和 `source_names` 的校验失败会在管线启动前报错。三路示例见 [`config/sim_3cam.yaml`](../../config/sim_3cam.yaml)、[`config/csi_3cam.yaml`](../../config/csi_3cam.yaml) 和 [`config/v4l2_3cam.yaml`](../../config/v4l2_3cam.yaml)。

类别过滤示例：`allowed_class_ids: [0, 2]`。通过 launch 覆盖时使用逗号分隔格式，例如 `allowed_class_ids:=0,2`；空列表不安装过滤 probe。

`deepstream.launch.py` 当前默认选择 `sim_3cam_headless.yaml`，该 profile 自身设置
`display=false`、`publish_detections=true`，用于先验收三路文件到检测 topic。只有在
命令行显式传入对应 launch 参数时才会覆盖 YAML profile。直接使用
`ros2 run deepstream deepstream_node` 时仍采用表中的节点默认值。

[`config/sim_3cam_headless.yaml`](../../config/sim_3cam_headless.yaml) 不创建可见窗口，
但 native SAHI allocator 仍需要有效的 NVIDIA EGL 资源；无窗口运行可使用 surfaceless EGL。该 profile 选择 IOU
tracker 与 batch 1；需要 NvDCF 或更高 tensor batch 时使用 GPU workspace 足够的 profile。
