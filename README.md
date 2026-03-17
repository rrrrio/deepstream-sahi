# deepstream-sahi

ROS2 DeepStream package (base + SAHI) with optional native C/C++ GStreamer plugins.

Author: **Yuhao Ding**
License: **MIT**

## Architecture

### SAHI Pipeline (with C plugins)

```
source → streammux → nvsahipreprocess → nvinfer → nvsahipostprocess → nvtracker → nvdsosd → sink
```

- **nvsahipreprocess**: Computes ROIs dynamically from `slice-width`, `slice-height`, `overlap-h`, `overlap-v` — no static config file needed.
- **nvsahipostprocess**: Greedy NMM in C++ on `NvDsObjectMeta`, runs as `GstBaseTransform` in-place.

### SAHI Pipeline (Python fallback)

When the C plugins are not compiled/installed, the pipeline falls back to:

```
source → streammux → nvdspreprocess (auto-generated config) → nvinfer → [Python probe NMM] → nvtracker → nvdsosd → sink
```

The preprocess config is generated dynamically at startup from the slice parameters. The Python probe on `nvinfer src` performs NMM as before.

## Contents

- `deepstream_node`: Single runtime node, `mode:=base|sahi`
- `config/`: DeepStream config files
- `launch/deepstream.launch.py`: Launch file
- `plugins/`: Native C/C++ GStreamer plugins (optional)

## Requirements

- ROS2 Humble
- NVIDIA DeepStream + `pyds`
- (Optional) `vision_msgs`

### For C plugins (optional)

- DeepStream SDK sources (for headers)
- GStreamer dev libs (`libgstreamer1.0-dev`)
- CMake 3.14+

## Folder Layout

```
models/Balloon_V11N_Last.pt.onnx
models/model_b1_gpu0_fp32.engine    # optional, generated at runtime
models/labels.txt
plugins/libnvdsinfer_custom_impl_Yolo.so
```

## Build

### ROS2 Package

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select deepstream
source install/setup.bash
```

### C/C++ Plugins (optional)

```bash
cd plugins
make
sudo make install
```

Or with cmake directly:

```bash
cd plugins && mkdir build && cd build
cmake .. -DDEEPSTREAM_DIR=/opt/nvidia/deepstream/deepstream
make -j$(nproc)
sudo make install
```

## Run

### Base mode

```bash
ros2 run deepstream deepstream_node --ros-args \
  -p mode:=base \
  -p source:=./samples/input.mp4 \
  -p display:=false
```

### SAHI mode (dynamic config — no preprocess config file needed)

```bash
ros2 run deepstream deepstream_node --ros-args \
  -p mode:=sahi \
  -p source:=./samples/input.mp4 \
  -p slice_width:=1024 \
  -p slice_height:=1024 \
  -p overlap_h:=0.2 \
  -p overlap_v:=0.2 \
  -p display:=false
```

### SAHI mode (static preprocess config, backward compatible)

```bash
ros2 run deepstream deepstream_node --ros-args \
  -p mode:=sahi \
  -p source:=./samples/input.mp4 \
  -p preprocess_config:=config/config_preprocess_sahi.txt \
  -p display:=false
```

### Launch

```bash
ros2 launch deepstream deepstream.launch.py \
  mode:=sahi \
  source:=./samples/input.mp4 \
  slice_width:=1024 \
  slice_height:=1024
```

## Parameters

| Parameter | Default | Description |
|-----------|---------|-------------|
| `mode` | `base` | `base` or `sahi` |
| `source` | — | Video file path or RTSP URI |
| `slice_width` | `640` | SAHI slice width (pixels) |
| `slice_height` | `640` | SAHI slice height (pixels) |
| `overlap_h` | `0.2` | Horizontal overlap fraction [0, 1) |
| `overlap_v` | `0.2` | Vertical overlap fraction [0, 1) |
| `preprocess_config` | — | Static config (empty = auto-generate) |
| `merge_enabled` | `true` | Enable NMM merge in SAHI mode |
| `merge_metric` | `ios` | `ios` or `iou` |
| `merge_threshold` | `0.5` | Overlap threshold for merging |
| `tracker_enabled` | `false` | Enable nvtracker |
| `display` | `true` | Show video output |

## Topics

- `/deepstream/status` (`std_msgs/String`)
- `/deepstream/target_pixel` (`geometry_msgs/Point`)
- `/deepstream/image/annotated` (`sensor_msgs/Image`, optional)
- `/deepstream/detections` (`vision_msgs/Detection2DArray`, if `vision_msgs` is installed)
