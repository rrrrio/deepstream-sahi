# DeepStream SAHI 文档

这组文档说明如何运行三路 DeepStream SAHI 管线，以及如何读取 ROS 2 输出。GPU、显示、Argus 和 CSI 的通过状态必须以实际设备上的运行记录为准。

## 文档地图

### 教程

- [首次运行三路文件](tutorial/first-run-three-file.md)：用三个本地视频完成一次可观察的运行。

### 操作指南

- [在现有容器中安装](how-to/install-in-existing-container.md)：安装依赖、构建插件和准备运行容器。
- [三路管线验证手册](how-to/verify-milestones.md)：按依赖顺序执行软件、显示、ROS 2、CSI 和 V4L2 验收。
- [从文件切换到 CSI](how-to/switch-file-to-csi.md)：将文件 source 切换为 Jetson Argus 相机。
- [验证 CSI 相机输入](how-to/verify-csi-camera-input.md)：检查宿主机、容器、Argus 和 V4L2 输入边界。
- [运行显示与无窗口模式](how-to/run-display-and-headless.md)：选择 2x2 OSD 或无窗口 sink。
- [启用 ROS 2 检测发布](how-to/enable-ros2-publishing.md)：发布每路 `Detection2DArray`。
- [故障排查](how-to/troubleshooting.md)：按现象定位常见问题。
- [Jetson CSI 配置与启动恢复记录](how-to/jetson-csi-boot-recovery.md)：记录 Argus、设备树、UEFI Recovery 和启动恢复流程。

### 参考

- [配置参考](reference/configuration.md)：查询参数、默认值和约束。
- [ROS 2 话题参考](reference/ros2-topics.md)：查询话题、消息字段和坐标约定。
- [运行时要求](reference/runtime-requirements.md)：查询宿主机、容器、插件和设备条件。

### 解释

- [架构说明](explanation/architecture.md)：理解 source、SAHI、tracker、OSD 和 ROS 元数据的关系。

## 推荐阅读顺序

首次运行时，先完成[在现有容器中安装](how-to/install-in-existing-container.md)，再阅读[首次运行三路文件](tutorial/first-run-three-file.md)。需要相机时，按[验证 CSI 相机输入](how-to/verify-csi-camera-input.md)的顺序检查宿主机和容器，再执行[从文件切换到 CSI](how-to/switch-file-to-csi.md)。

CSI 前置验证只确认相机数据能到达 `deepstream.launch.py` 所需的输入边界，不把真实推理、检测内容、ROS 话题或长时间稳定性作为相机前置条件。Argus 尚未可用时，可用 `config/v4l2_3cam.yaml` 验证 V4L2 到 NVMM/nvstreammux 的输入边界；该结果不等价于 Argus CSI 通过。

## 运行入口

```bash
source scripts/env.sh
source .colcon/install/setup.bash
ros2 launch deepstream deepstream.launch.py \
  params_file:=config/sim_3cam_headless.yaml \
  display:=false \
  publish_detections:=true
```

文件 source 默认循环。三路配置文件和 CSI 配置文件分别见 [`config/sim_3cam.yaml`](../config/sim_3cam.yaml) 与 [`config/csi_3cam.yaml`](../config/csi_3cam.yaml)；如果当前 checkout 尚未包含它们，请按[配置参考](reference/configuration.md)中的参数传入等价值。
