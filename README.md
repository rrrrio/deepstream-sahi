# deepstream-sahi

ROS2 DeepStream package (base + SAHI).

Author: **Yuhao Ding**
License: **MIT**

## Contents

- `deepstream_node`: Single runtime node, `mode:=base|sahi`
- `config/`: DeepStream config files
- `launch/deepstream.launch.py`: Launch file

## Requirements

- ROS2 Humble
- NVIDIA DeepStream + `pyds`
- (Optional) `vision_msgs`

## Folder Layout (Before Running)

Place model and plugin files in this project structure:

- `models/Balloon_V11N_Last.pt.onnx`
- `models/model_b1_gpu0_fp32.engine` (optional; can be generated at runtime)
- `models/labels.txt`
- `plugins/libnvdsinfer_custom_impl_Yolo.so`

Note: Paths inside `config/config_infer_primary_yolo11*.txt` are configured as **relative**.

## Build

```bash
cd ~/ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select deepstream
source install/setup.bash
```

## Run

Base:

```bash
ros2 run deepstream deepstream_node --ros-args -p mode:=base -p source:=./samples/input.mp4 -p display:=false
```

SAHI:

```bash
ros2 run deepstream deepstream_node --ros-args -p mode:=sahi -p source:=./samples/input.mp4 -p display:=false
```

Launch:

```bash
ros2 launch deepstream deepstream.launch.py mode:=sahi source:=./samples/input.mp4
```

## Topics

- `/deepstream/status` (`std_msgs/String`)
- `/deepstream/target_pixel` (`geometry_msgs/Point`)
- `/deepstream/image/annotated` (`sensor_msgs/Image`, optional)
- `/deepstream/detections` (`vision_msgs/Detection2DArray`, if `vision_msgs` is installed)
