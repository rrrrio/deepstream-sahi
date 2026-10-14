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
- `config/v4l2_3cam.yaml`: a three-camera V4L2 fallback example for devices exposing `UYVY 1920x1080@30`; adjust it to the reported format.
- `scripts/`: container setup, runtime checks, camera probes, and environment helpers.
- `docs/`: tutorials, how-to guides, references, architecture, and camera-input troubleshooting.

## Documentation

Use the [documentation index](docs/README.md) to choose a tutorial, how-to guide, reference, or explanation.

## Requirements

- Jetson with DeepStream 7.1, CUDA 12.6, and a matching NVIDIA container runtime.
- ROS 2 Humble, `vision_msgs`, and NVIDIA `pyds` 1.2.0.
- CUDA, GStreamer, and TensorRT development packages for the native plugins and parser.
- GPU, EGL/X11 access, and `/dev` mapping for file or V4L2 input.
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
./scripts/check_runtime.sh --require-display
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

Three-camera V4L2 fallback:

```bash
for device in /dev/video0 /dev/video1 /dev/video2; do
  ./scripts/probe_v4l2_camera.sh "$device" 10
done
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/v4l2_3cam.yaml \
  display:=false \
  publish_detections:=false
```

The fallback example uses `UYVY 1920x1080@30` and converts it to `NV12/NVMM` before `nvstreammux`; adjust the profile to the format reported by the host camera.

## Validation boundary

The CSI preflight scope ends when camera data reaches the input boundary required by `deepstream.launch.py`. It does not accept inference results, detection content, ROS topics, or a 30-minute soak as camera prerequisites.

Run camera validation in this order: inspect host sensors, media topology, formats, and `nvargus-daemon`; check GPU, device nodes, Argus socket, and plugins in the container; validate each Argus sensor; then enter the full launch. If Argus is unavailable but V4L2 produces frames, use the V4L2 profile to validate the input boundary. The two paths have independent acceptance criteria.

Common failures include a missing `/tmp/argus_socket`, `No cameras available`, a missing NVIDIA camera enumerator in the device tree, camera kernel-node open failures, and V4L2 caps negotiation errors. See [CSI camera input validation](docs/how-to/verify-csi-camera-input.md) for the checks and recovery paths.

## ROS 2 output

With `publish_detections:=true`, the default topics are:

- `/deepstream/status`.
- `/deepstream/camera_0/detections`.
- `/deepstream/camera_1/detections`.
- `/deepstream/camera_2/detections`.

See the [ROS 2 topic reference](docs/reference/ros2-topics.md) for message fields and coordinate conventions.
