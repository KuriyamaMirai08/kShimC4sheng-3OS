# Deepin 25 / 主线 Linux 开机黑屏与触摸修复 (`sheng-display-fix`)

## 现象与根因
1. **开机黑屏**：通过 `kShimC` 从 `boot_a` 直接引导 Deepin 25 / 主线 Linux 时，`msm_dpu` / `DSI-1` 从 bootloader/UEFI splash 接管可能残留显示状态，且 `ktz8866-backlight` 驱动缓存了软件状态 `led_on`（需要一次 `0 -> >0` 亮度写入才会重新通过 I2C 打开 `BL_EN_BIT`）。
2. **触摸失效陷阱**：执行 `xrandr --output DSI-1 --off` → `--mode 3048x2032 --rate 120` 会触发 `NT36532E` TDDI 复位引脚，硬件上能同时恢复屏幕与 `xiaomi-sheng-thp`（从 `waiting for a touch reference frame` 恢复到 `touch reference ready`）；但如果在 `graphical.target`（LightDM Greeter 和 Deepin `daemon/display` 启动**之后**）再关开 `DSI-1`，`daemon/display` 会把 `NVTCapacitiveTouchScreen` (`event3`) 的 XInput2 `Device Enabled` 置为 `0` 且不会自动恢复。

## 修复原理
- 将显示重置前移到 LightDM 的 `display-setup-script`（`/etc/lightdm/lightdm.conf.d/99-sheng-display-fix.conf`），在 Greeter 与 `daemon/display` 启动**之前**完成一次性的 `DSI-1` 重置、`xset dpms force on` 以及 `ktz8866-backlight` `0 -> SAVED` 循环。
- 保留原版 Deepin `/var/lib/lastore/scripts/gen_upgrade_check_config.sh` 调用。
- 重置完成后通过 `libX11.so.6` / `libXi.so.6` 检查并确保 `NVT` / `Touch` 设备的 `Device Enabled` 为 `1`。

## 安装方法（在 Skyward Recovery 或 Linux 终端中）
将 `linux` 分区（通常为 `/dev/block/sda31`）挂载为可写后安装：

```sh
mount -t ext4 -o rw /dev/block/sda31 /tmp/linux_rw

# 若曾安装过旧版 graphical.target 服务，先移除避免重复触发 xrandr --off
rm -f /tmp/linux_rw/etc/systemd/system/graphical.target.wants/sheng-display-fix.service \
      /tmp/linux_rw/etc/systemd/system/sheng-display-fix.service

install -m 755 sheng-display-fix.sh /tmp/linux_rw/usr/local/lib/dpms-guard/sheng-display-fix.sh
mkdir -p /tmp/linux_rw/etc/lightdm/lightdm.conf.d
install -m 644 99-sheng-display-fix.conf /tmp/linux_rw/etc/lightdm/lightdm.conf.d/99-sheng-display-fix.conf

sync
umount /tmp/linux_rw
```

开机日志位于 `/var/log/sheng-display-diag.log`。
