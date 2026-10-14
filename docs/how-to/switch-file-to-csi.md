# 从文件切换到 CSI

本指南把已有的三路文件 source 替换为 Jetson Argus CSI source。它只适用于宿主机已经枚举 Argus 相机、容器已经映射 NVIDIA 设备和 `/tmp/argus_socket` 的环境。

## 完成标准

每个 `argus://N` source 能独立产出帧；三路同时运行时 source 名、检测 topic 和 OSD tile 仍一一对应。实际通过状态必须由相机硬件日志确认。

## 前置检查

先在宿主机执行：

```bash
cd ~/wp/deepstream-sahi
./scripts/host_preflight_simulation.sh "$HOME/wp"
```

再在容器中执行：

```bash
./scripts/check_runtime.sh --require-argus
```

缺少任一资源时，先按[运行时要求](../reference/runtime-requirements.md)检查容器启动参数。`--require-argus` 只确认 DeepStream、GPU、`nvarguscamerasrc` 插件和 socket 资源存在，不能证明 sensor 能取帧；应用内的 source 分类检查也不能替代宿主机相机检查。

## 逐路检查 sensor ID

在完整管线前直接检查每个 Argus sensor：

```bash
for sensor_id in 0 1 2; do
  ./scripts/probe_argus_camera.sh "$sensor_id" 120
done
```

脚本会检查每个 sensor 是否实际输出 120 个 buffer，并检查 Argus 错误。`gst-launch` 的退出码不能单独作为通过条件，因为 Argus 错误可能以 EOS 结束并返回 0。某个 sensor 单独失败时，不要继续三路启动。

## 切换到三路 CSI

使用 [`config/csi_3cam.yaml`](../../config/csi_3cam.yaml) 中的三路值，或直接传参：

```bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/csi_3cam.yaml \
  display:=false \
  publish_detections:=true
```

Argus source 使用 `nvarguscamerasrc`，按 `camera_fps` 请求帧率，并通过 NV12/NVMM 进入 mux。`sources` 的顺序决定 `source_id`、OSD tile 和 topic 名；不要在运行中交换 `source_names` 与 `sources` 的顺序。

## 验证与回退

```bash
ros2 topic list | sort | grep '/deepstream/.*/detections'
ros2 topic hz /deepstream/camera_0/detections
```

若三路启动失败，先回到单路 `argus://N`，再回到[三路验证手册](verify-milestones.md)的 CSI 检查。回退到文件只需把 `sources` 换成三个 `file://` URI，并保持相同的 `source_names`；SAHI、tracker 和 ROS topic 配置可以不变。

如果宿主机日志出现 `NVIDIA Camera enumerator not found in proc device-tree` 或 `Camera node not detected`，这不是容器参数问题，需要修复供应商设备树/Argus camera module 配置。按[CSI 相机输入验证](verify-csi-camera-input.md)中的错误表检查设备树、驱动和容器资源。
