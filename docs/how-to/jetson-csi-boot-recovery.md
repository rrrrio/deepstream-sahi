# Jetson CSI 配置与启动恢复记录

本文记录 2026-10-13 至 2026-10-14 在 `ssh bdca2@192.168.10.158` 上排查三路 VEYE/Seeed CSI 相机、修改设备树后进入 Recovery，以及最终恢复正常启动的完整过程。

本文既是一次故障复盘，也是同类设备的恢复操作指南。目标设备为 Seeed reComputer Super，L4T `R36.4.3`，内核 `5.15.148-tegra`，系统根分区位于 NVMe APP 分区 `/dev/nvme0n1p1`。

## 结论

这次实际包含两个互相独立的问题：

1. 宿主机的相机节点和 Argus daemon 存在，但最初使用的诊断容器没有映射 `/tmp/argus_socket`；同时 live device tree 中 `tegra-camera-platform` 的层级不符合当前 Argus 枚举器的预期，因此 V4L2 可用不代表 Argus 可用。
2. 为修复设备树而直接替换 camera overlay、合并 DTB，并在 `extlinux.conf` 中加入显式 `FDT`，改变了 Jetson UEFI/L4TLauncher 的启动链路，导致黑屏并进入 Recovery。最终通过 UEFI 将 OS chain A 状态恢复为 `Normal` 后，系统重新进入正常启动。

最终确认：

- NVMe APP 分区可读写，`/boot/Image` 和 `/boot/initrd` 存在。
- `extlinux.conf` 的主启动项使用正确的 APP `PARTUUID`，且没有生效的 `FDT` 行。
- `nvbootctrl` 显示 bootloader A/B 均为 `normal`，当前 slot 为 A。
- `BoardRecoveryBoot` 变量值为 `0`，不是通过该变量强制 Recovery。
- UEFI 中将 `OS chain A status` 设为 `Normal` 后，设备正常进入系统。

## 系统链路

下面的链路区分了相机输入链路和启动链路。两条链路都必须分别通过，不能用其中一条的结果替代另一条。

```text
相机输入链路

[Sensor + 载板连接]
          |
          v
[设备树 overlay / camera module 描述]
          |
          +--> [内核 sensor probe] --> [/dev/videoN + /dev/mediaN]
          |
          +--> [tegra-camera-platform]
                         |
                         v
                 [nvargus-daemon]
                         |
                         v
                 [/tmp/argus_socket]
                         |
                         v
       [容器映射 /dev + socket + --ipc=host]
                         |
                         v
       [nvarguscamerasrc sensor-id=N]
                         |
                         v
                 [DeepStream pipeline]
```

```text
Jetson 启动链路

[QSPI / UEFI]
      |
      v
[ESP p10: EFI/BOOT/BOOTAA64.EFI]
      |
      v
[L4TLauncher]
      |
      +--> 正常 Direct Boot
      |       |
      |       v
      |   [APP p1: /boot/extlinux/extlinux.conf]
      |       |
      |       v
      |   [Image + initrd]
      |       |
      |       v
      |   [root=APP PARTUUID]
      |       |
      |       v
      |   [系统根分区挂载为 /]
      |
      +--> Recovery Boot
              |
              v
          [recovery initrd]
              |
              +--> root=/dev/initrd
              +--> 临时把 APP 挂载到 /mnt 检查 OTA
              +--> 检查失败后停在 initramfs shell
```

`/mnt` 是 initramfs 中的临时挂载点，不是系统根分区的持久挂载点。重启后它为空是正常的；正常启动时 APP 应切换为 `/`，而不是长期保持在 `/mnt/root`。

## Argus 故障定位

### 1. 先区分宿主机和容器

宿主机存在：

```text
/tmp/argus_socket
/dev/video0
/dev/video1
/dev/video2
/dev/media0
```

但最初使用的独立诊断容器没有映射 `/tmp/argus_socket`。因此，容器内执行检查失败不能证明宿主机缺少 Argus。CSI 容器至少需要：

```bash
-v /tmp/argus_socket:/tmp/argus_socket \
--ipc=host
```

同时需要 NVIDIA runtime、`/dev`、GPU/EGL 资源和相应显示资源。容器资源检查只能说明依赖被映射，不能证明 Argus 已经发现 sensor。

### 2. V4L2 通过不等于 Argus 通过

宿主机通过 V4L2 mmap 可以从三路节点取帧，媒体拓扑也已建立：

```text
/dev/video0 -> veyecam 9-003b
/dev/video1 -> veyecam 10-003b
/dev/video2 -> veyecam 12-003b
```

但 `nvarguscamerasrc sensor-id=0/1/2` 输出 `No cameras available`。这是因为 V4L2 设备节点由内核 sensor probe 提供，Argus 还需要从 live device tree 中找到 camera module 和 `tegra-camera-platform` 描述，并通过 `nvargus-daemon` 完成枚举。

`gst-launch-1.0` 在 Argus 出错后可能收到 EOS 并返回退出码 `0`，所以不能只看退出码。应使用真实 buffer 计数探针：

```bash
./scripts/probe_argus_camera.sh 0 120
./scripts/probe_argus_camera.sh 1 120
./scripts/probe_argus_camera.sh 2 120
```

### 3. Argus 日志给出的根因

宿主机 `nvargus-daemon` 报告：

```text
OFParserListModules: NVIDIA Camera enumerator not found in proc device-tree
NvCamHwHalOpen: Failure to open Camera Kernel Node
NvOdmImagerGetModuleList: Camera node not detected - No such file or directory
NvPclModuleListInitialize: device detection on this platform is not supported
```

live device tree 中存在四个 `veyecam` module，但 `tegra-camera-platform` 位于：

```text
/bus@0/tegra-camera-platform
```

而当前 Argus 枚举器期望的是设备树根路径下的：

```text
/tegra-camera-platform
```

因此，`serdes_link_freq err: -61` 不能单独作为“三路相机都失败”的结论。内核日志中三路 sensor 已 probe/bind，Argus 枚举阶段的 device-tree 层级错误是更强的证据。

## 高风险修改与启动故障

### 1. 实际做过的修改

为验证设备树修复，曾经进行过以下修改：

- 备份并替换 `/boot/tegra234-p3767-camera-p3768-veyecam2m-quad-seeed.dtbo`。
- 备份并替换 `/boot/kernel_tegra234-j401-p3768-0000+p3767-0000-recomputer-super.dtb`。
- 备份并替换 `/boot/dtb/kernel_tegra234-j401-p3768-0000+p3767-0000-recomputer-super.dtb`。
- 在 `/boot/extlinux/extlinux.conf` 的主启动项中加入显式 `FDT`，指向合并后的 fixed DTB。

原文件备份名使用了 `*.bak-codex-20261013` 后缀。首次只替换 DTBO 时，重启后的 live device tree 仍然没有变化，说明 UEFI 启动链路并没有采用该文件。随后加入显式 `FDT` 后，设备出现黑屏、SSH 不可达，并进入 Recovery/initramfs。

这里的关键风险不是 DTBO 文件是否能够被 `dtc` 或 `fdtoverlay` 离线处理，而是 Jetson 的实际启动链路可能由 QSPI/UEFI/L4TLauncher 注入或选择设备树。直接向 `extlinux.conf` 加 `FDT` 可能绕过供应商固件选择的 board DT，导致内核获得不匹配的 DTB。

### 2. 为什么会看到 `root=/dev/initrd`

Recovery 启动时，当前运行的 kernel 使用 recovery initrd 作为根：

```text
root=/dev/initrd
```

此时 shell 中的 `/` 是 initramfs，不是 NVMe APP 根分区，所以：

- `/boot` 不存在或不是目标系统的 `/boot`。
- `/mnt` 可能为空。
- 手动挂载 `/dev/nvme0n1p1` 到 `/mnt/root` 后，才能看到真正的系统文件。
- 这个挂载不会跨重启保存。

因此，重启后 `/mnt` 为空不能证明 `/boot` 修改丢失，也不能证明 NVMe 文件系统损坏。应先看 `cat /proc/cmdline` 是否仍为 `root=/dev/initrd`，再判断是否还停留在 Recovery。

## 实际恢复流程

以下流程只在串口 initramfs shell 中执行过读操作和临时挂载。命令之间的空格必须保留；串口显示可能把长行换行，但那不等于文件内容真的换行。

### 1. 确认当前处于 initramfs

```bash
cat /proc/cmdline
mount
findmnt /
```

出现 `root=/dev/initrd` 且 `/` 的 source 为 `rootfs` 时，说明还没有切换到 APP 系统。

### 2. 确认 NVMe 分区

```bash
lsblk -f
blkid
```

本次关键分区为：

```text
/dev/nvme0n1p1  ext4  APP  PARTUUID=03951286-992a-430b-85ef-557478ed04db
/dev/nvme0n1p10 vfat  esp  PARTUUID=33bec042-73d1-414b-bf35-c4684880e144
```

### 3. 临时挂载 APP 和 ESP

```bash
mkdir -p /mnt/root
mount -t ext4 /dev/nvme0n1p1 /mnt/root

mkdir -p /mnt/esp
mount -t vfat /dev/nvme0n1p10 /mnt/esp

mount | grep nvme
```

如果看到 `/dev/nvme0n1p1 on /mnt/root` 和 `/dev/nvme0n1p10 on /mnt/esp`，说明分区和文件系统可访问。

### 4. 检查实际使用的 extlinux 配置

```bash
ls -la /mnt/root/boot/extlinux
sed -n '1,20l' /mnt/root/boot/extlinux/extlinux.conf
```

正常的主启动项应满足：

- `DEFAULT primary`。
- `LINUX /boot/Image`。
- `INITRD /boot/initrd`。
- `APPEND` 中包含 APP 的 `PARTUUID`。
- 没有生效的 `FDT` 行。
- `APPEND` 是一条完整的物理行；使用 `sed -n 'l'` 可区分终端折行和文件真实换行。

本次恢复后使用的关键配置为：

```text
root=PARTUUID=03951286-992a-430b-85ef-557478ed04db
```

### 5. 挂载 EFI 变量并检查启动状态

初始情况下 `/sys/firmware/efi/efivars` 为空，不能直接据此判断 EFI 变量丢失。先临时以只读方式挂载：

```bash
mkdir -p /sys/firmware/efi/efivars
mount -t efivarfs -o ro efivarfs /sys/firmware/efi/efivars
```

然后检查 bootloader slot：

```bash
/mnt/root/usr/sbin/nvbootctrl get-number-slots
/mnt/root/usr/sbin/nvbootctrl get-current-slot
/mnt/root/usr/sbin/nvbootctrl -t bootloader dump-slots-info
/mnt/root/usr/sbin/nvbootctrl -t rootfs dump-slots-info
```

本次结果为：

```text
当前 slot: 0
Current bootloader slot: A
Active bootloader slot: A
slot 0: normal
slot 1: normal
RootFS A/B is not enabled
```

`RootFS A/B is not enabled` 在该设备配置下是正常信息，不等于 NVMe 根分区损坏。

`BoardRecoveryBoot` 变量的文件内容为 EFI 属性 `07 00 00 00` 加数据字节 `00`，说明该变量没有被置为强制 Recovery。不要因为变量名包含 `Recovery` 就直接删除或改写它。

### 6. 在 UEFI 中恢复 OS chain A

重启时按 `ESC` 进入 Setup，进入：

```text
Device Manager
  -> NVIDIA Configuration
     -> L4T Configuration
        -> OS chain A status
```

部分固件版本显示为 `NVIDIA Resource Configuration`。将 `OS chain A status` 设置为 `Normal`，保存并退出，再从 NVMe 的 `UEFI FORESEE ...` 项启动。

本次第一次按 `F10` 保存时出现：

```text
Submit Fail For Form: Grace Configuration.
Press D(d) to discard changes for this form, Press G(g) to go to this form
```

如果没有修改 Grace 参数，按 `d` 丢弃当前表单，返回后重新进入 L4T Configuration。不要为了消除弹窗去修改 Grace Configuration 的其他字段，也不要手写 EFI 变量。重新进入后成功将 OS chain A 设为 `Normal`，设备恢复正常启动。

## 启动恢复后的验收

进入正常系统后，先确认已经离开 Recovery：

```bash
cat /proc/cmdline
findmnt /
mount | grep nvme
```

正常结果应表现为：

- `/proc/cmdline` 不再只有 `root=/dev/initrd`，而是包含 APP 的 root 参数。
- `/` 来自 NVMe APP 分区，而不是 initramfs `rootfs`。
- `/boot/extlinux/extlinux.conf` 中没有未经验证的显式 `FDT`。

然后重新按相机链路验收：

```bash
ls -l /tmp/argus_socket /dev/video0 /dev/video1 /dev/video2
systemctl status nvargus-daemon --no-pager
./scripts/check_runtime.sh --require-argus
./scripts/probe_argus_camera.sh 0 120
./scripts/probe_argus_camera.sh 1 120
./scripts/probe_argus_camera.sh 2 120
```

只有三路都能真实输出 buffer，才启动 `config/csi_3cam.yaml`。如果 Argus 仍失败而 V4L2 通过，应继续修复设备树、驱动和 ISP 配置，不能用 V4L2 结果替代 Argus 验收。

## 相机配置避雷清单

以下做法明确不作为生产修复方式：

1. 不要直接覆盖供应商提供的 `/boot/*.dtbo`、`/boot/*.dtb`，也不要只凭离线 `dtc`/`fdtoverlay` 成功就认为目标设备一定会采用该设备树。
2. 不要为了修正 Argus 路径，未经完整回滚计划就在 `extlinux.conf` 中加入显式 `FDT`。在 Jetson UEFI 启动链路下，这可能绕过 QSPI/UEFI 选择的 board DT，造成黑屏、SSH 不可达和 Recovery 启动。
3. 修改设备树前必须保存原始 DTBO、DTB 和 `extlinux.conf`，记录 SHA256，并准备串口、显示器、UEFI Setup 和可用的恢复介质。
4. 一次只改一个启动边界。先确认容器 socket 映射，再确认宿主机 Argus，再确认 live device tree，最后才考虑固件或设备树变更。
5. 验证重启后实际生效的内容，使用 `/proc/device-tree`、`/proc/cmdline` 和启动日志，不要只检查 `/boot` 上的文件。
6. 如果产品必须使用 Argus，应优先使用与载板、sensor、JetPack/L4T 版本匹配的供应商 BSP、camera `.dtsi`/`.dtbo` 和刷写流程。不要在运行中的设备上手工拼装替代 DTB 作为长期方案。
7. 如果当前目标只是验证 DeepStream 输入边界，可以先用 V4L2 profile 验证应用；这能降低对 Argus 和设备树的改动压力，但不能宣称 Argus 已修复。

## 相关文档

- [验证 CSI 相机输入](verify-csi-camera-input.md)。
- [从文件切换到 CSI](switch-file-to-csi.md)。
- [故障排查](troubleshooting.md)。
- [运行时要求](../reference/runtime-requirements.md)。
- [NVIDIA Jetson Recovery Boot 讨论](https://forums.developer.nvidia.com/t/boot-failure-on-jetson-orin-nano-uefi-variables-corrupted-os-chain-a-unbootable/362474)。
