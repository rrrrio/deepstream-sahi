# 运行显示与无窗口模式

本指南说明如何在同一条三路管线上选择显示输出或无窗口输出。两种模式都保留 metadata probe 和 ROS 2 发布；区别在于是否创建可见视频 sink。

## 显示模式

容器需要有效的 `DISPLAY`、X11 socket 和 NVIDIA/EGL 运行资源。启动三路文件：

```bash
source scripts/env.sh
source .colcon/install/setup.bash
./scripts/check_runtime.sh --require-display
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam.yaml \
  display:=true
```

预期窗口为 2x2 tiler：前三个 tile 按 source 顺序显示，第四个 tile 为空。OSD 位于 tiler 后，因此显示的是合成布局；ROS bbox 仍使用 tiler 前的原图坐标。

## 无窗口模式

不需要可见窗口时，关闭显示。文件和全 V4L2 profile 都可以使用 surfaceless EGL：

```bash
./scripts/check_runtime.sh --require-gpu
EGL_PLATFORM=surfaceless ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam_headless.yaml
```

无窗口模式仍应持续处理 source，并可用 `ros2 topic echo` 或 `ros2 topic hz` 检查输出。不要用没有窗口作为检测或 CSI 已通过的证据。

当前 native SAHI allocator 在 Jetson 上仍使用 EGL-backed surface；surfaceless EGL
提供该资源而不创建 OSD 窗口。`scripts/run_sim_3cam_headless.sh` 会在启动前检查
GPU 资源，不需要 X11 socket。

`sim_3cam_headless.yaml` 使用 DeepStream IOU tracker 和 tensor batch 1，以降低
NvDCF 视觉 surface pool 的显示资源压力并适配受限 GPU workspace；这不会移除
native SAHI allocator 对 EGL 的要求。生产 CSI/OSD profile 继续使用 NvDCF；
GPU workspace 足够时可提高 `pgie_batch_size`。

## 常见切换

- EGL 报错：先检查容器的 GPU 映射，并对无窗口 profile 设置
  `EGL_PLATFORM=surfaceless`；`display:=false` 只关闭可见窗口，不能移除
  native SAHI 对 EGL 的依赖。
- 无窗口但需要图像消息：使用 `publish_annotated:=true`，并检查 `/deepstream/image/annotated`；该 topic 默认关闭。
- 画面 tile 数量不符：确认 `sources` 数量和 `source_names` 数量相同，并检查 `nvmultistreamtiler` 配置。

显示和 CSI 所需的宿主机资源见[运行时要求](../reference/runtime-requirements.md)。
