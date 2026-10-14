# deepstream-sahi

A ROS 2 and NVIDIA DeepStream package for three-source video analysis on Jetson. It supports file, RTSP, V4L2, and Argus CSI inputs, YOLOv11s, SAHI slicing, NMM merging, tracking, a 2x2 OSD, and per-camera ROS 2 detection topics.

The Chinese README is [README.md](README.md).

## Data flow

```text
file / RTSP / V4L2 / Argus x3
  -> nvstreammux
  -> nvsahipreprocess
  -> nvinfer
  -> nvsahipostprocess
  -> nvtracker
  -> ROS 2 metadata probe
  -> nvmultistreamtiler
  -> nvdsosd
  -> nv3dsink or fakesink
```

The SAHI profile requires the pinned native plugins. It fails clearly when `nvsahipreprocess` or `nvsahipostprocess` is unavailable; it does not silently fall back to the old Python merge path.

## Repository layout

- `deepstream/`: ROS 2 node, source bins, and DeepStream pipeline builders.
- `config/sim_3cam_headless.yaml`: three-file headless profile with detection topics.
- `config/sim_3cam.yaml`: three-file profile with a 2x2 OSD.
- `config/csi_3cam.yaml`: three Argus CSI sources.
- `config/v4l2_3cam.yaml`: the current three-camera V4L2 profile using stable `/dev/v4l/by-path` links and `UYVY 1920x1080@30`.
- `scripts/`: container setup, runtime checks, camera probes, and environment helpers.
- `docs/`: tutorials, how-to guides, references, architecture, and camera-input troubleshooting.

## Documentation

Use the [documentation index](docs/README.md) to choose a tutorial, how-to guide, reference, or explanation.

## Requirements

- Jetson with DeepStream 7.1, CUDA 12.6, and a matching NVIDIA container runtime.
- ROS 2 Humble, `vision_msgs`, and NVIDIA `pyds` 1.2.0.
- CUDA, GStreamer, and TensorRT development packages for the native plugins and parser.
- File input needs GPU, EGL/X11 access, and `/dev`; headless V4L2 input uses surfaceless EGL and `/dev`.
- For Argus CSI, the host must enumerate the camera modules and the container must mount `/tmp/argus_socket`.
- The installation scripts require a writable container root filesystem and root access.

## Install and build

Run from the repository root inside the container:

```bash
./scripts/capture_environment.sh
./scripts/install_inplace.sh
./scripts/build_sahi_plugins.sh
./scripts/fetch_yolov11s_onnx.sh
./scripts/build_yolov11s_parser.sh
source scripts/env.sh
python -m colcon --log-base .colcon/log build \
  --base-paths . \
  --build-base .colcon/build \
  --install-base .colcon/install \
  --symlink-install \
  --packages-select deepstream
source .colcon/install/setup.bash
```

See [Install in an existing container](docs/how-to/install-in-existing-container.md) for container recreation and rollback.

## Run

Three-file headless profile:

```bash
./scripts/check_runtime.sh --require-gpu
./scripts/run_sim_3cam_headless.sh
```

Three-file OSD profile:

```bash
./scripts/check_runtime.sh --require-display
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam.yaml \
  display:=true \
  publish_detections:=true
```

Three Argus CSI sources:

```bash
./scripts/check_runtime.sh --require-argus
for sensor_id in 0 1 2; do
  ./scripts/probe_argus_camera.sh "$sensor_id" 120
done
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/csi_3cam.yaml \
  display:=false
```

Before starting the CSI profile, test `sensor-id=0/1/2` independently. Each sensor must produce 120 real buffers without Argus errors. `gst-launch-1.0` can return 0 after an Argus error and EOS, so the exit status alone is not a valid success criterion.

Three V4L2 cameras:

```bash
for device in \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index0 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index6 \
  /dev/v4l/by-path/platform-tegra-capture-vi-video-index8; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/v4l2_3cam.yaml \
  display:=false \
  publish_detections:=true
```

The profile maps capture indexes 0, 6, and 8 to `camera_0`, `camera_1`, and `camera_2`. It uses `UYVY 1920x1080@30` and converts it to `NV12/NVMM` before `nvstreammux`. A missing stable path fails startup instead of silently falling back to `/dev/videoN`.
Headless V4L2 runs use surfaceless EGL and do not require `DISPLAY`, an X11 socket, or the Argus socket. The node selects this platform automatically when all sources are V4L2 and display/image publishing are disabled.

## Validation boundary

The CSI preflight scope ends when camera data reaches the input boundary required by `deepstream.launch.py`. It does not accept inference results, detection content, ROS topics, or a 30-minute soak as camera prerequisites.

For the current three-camera profile, first confirm the `/dev/v4l/by-path` physical mapping and real V4L2 frames, then check GPU, device nodes, and plugins in the container, and finally start the V4L2 profile with its three ROS detection topics. Argus is an independent optional input path, not a prerequisite for V4L2 acceptance.

Common failures include a missing `/tmp/argus_socket`, `No cameras available`, a missing NVIDIA camera enumerator in the device tree, camera kernel-node open failures, and V4L2 caps negotiation errors. See [CSI camera input validation](docs/how-to/verify-csi-camera-input.md) for the checks and recovery paths.

## ROS 2 output

With `publish_detections:=true`, the default topics are:

- `/deepstream/status`.
- `/deepstream/camera_0/detections`.
- `/deepstream/camera_1/detections`.
- `/deepstream/camera_2/detections`.

See the [ROS 2 topic reference](docs/reference/ros2-topics.md) for message fields and coordinate conventions.
