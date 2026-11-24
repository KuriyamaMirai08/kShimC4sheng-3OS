# kShimC4sheng-3OS

**简体中文** | [English](README.en.md)

基于 [`tc1911/kShimC4sheng`](https://github.com/tc1911/kShimC4sheng)（GPL-3.0）二改的小米平板 6S Pro 12.4（`sheng` / SM8550）**单槽 `boot_a` 图形化三系统启动器**：

- **Android**（原厂 HyperOS，默认项，5 秒倒计时，Bugdroid 图标）
- **iPadOS 27**（UniXNU `d8` 持久化，Apple SVG 图标，`KSHMLZ4` 流式解压 FAT32 ramdisk）
- **Linux**（Deepin 25 / 主线 SM8550 Linux，Tux 图标，**从 `boot_a` 零重启直接启动**）

同时保持 `recovery_a`（如 Skyward Recovery）和 `boot_b`（Android 救砖槽位）**完全不动**。

---

## 真机实测状态

已在小米平板 6S Pro 12.4（HyperOS `OS3.0.306.0.WNXCNXM`，`current-slot: a`）实机完整验证：

| 系统 | 分区与容量 | 状态 |
| --- | --- | --- |
| Android（HyperOS） | `#29 userdata`（`160.4 GiB`，未动） | 正常启动 |
| iPadOS 27（UniXNU `d8`） | `#30 darwin`（`26.0 GiB`，10 GiB Data 种子可用约 `9.7 GiB`） | 正常启动、持久化与 IPA 安装正常 |
| Linux（Deepin 25 arm64） | `#31 linux`（`46.0 GiB`，首次启动自动扩满） | 正常直启、加载画面/桌面显示正常、触控正常 |
| Recovery（Skyward v0.3.1） | `recovery_a`（原版未改） | 按键进入与 ADB 正常 |

---

## 核心特性

1. **单槽 `boot_a` 直启三系统（176 MiB 限制内）**
   - 小米 ABL 恢复/启动池上限为 `176 MiB`。本项目在 `vendor/kshim` 中实现了 `KSHMLZ4`（`vendor/kshim/src/dt/initrd.c`）流式解压，将 `136 MiB` UniXNU FAT32 ramdisk 压缩至约 `70 MiB`（`tools/compress_initrd_lz4.py`），使 **Android 内核 + UniXNU UEFI/DTB/LZ4 Ramdisk + Linux 内核/DTB + 4 MiB kShimC Runtime** 整体打包后仅 `166.46 MiB`（`174,551,040 B`），无需二次重启或切槽。
2. **定制 Atlas 三系统图形界面**
   - 星空背景壁纸、透明暗蓝面板、Bugdroid / Apple / Tux 矢量图标（`tools/gen_custom_icons.py`、`tools/gen_atlas_wallpaper.py`）与 Noto Sans CJK 精简中文字库（`vendor/kshim/src/ui/fonts/noto_sans_cjk_menu.ksf`）。
   - 菜单快捷键与设置项通过 QCOM SDAM NVM 持久保存（语言、开机延迟、上次启动项）。
3. **完整开源配套工具链（`tools/`）**
   - `tools/patch_seed_uuid.py`：修补自定义容量 APFS Data 种子中全部 `APSB` 卷 UUID 为 `kernel.macho` 硬编码的 `2A5E9027-D9E1-40F9-983C-41BD3F1994F5` 并重算 Fletcher-64 校验和（解决自定义分区大小时的 `offline scratch Data mount failed: 2`）。
   - `tools/gen_walv2_init_any.cpp` & `tools/make_walv2_sparse.py`：为任意容量 `darwin` 分区生成 `WALv2` 初始化稀疏镜像。
   - `tools/repack_fat32.py` & `tools/compress_initrd_lz4.py`：更新 FAT32 ramdisk 内的 `scratch-data.img` 并压缩为 `KSHMLZ4`。
   - `tools/make_ipados_boot.py` & `tools/verify_dualboot_img.py`：打包并静态校验双系统/三系统 `boot_a.img`（自动校验 `176 MiB` 上限、manifest 对齐与 DTB `/chosen` 属性）。
   - `tools/flash_raw_sparse_slices.py`：将超大 `linux.img` 按 `256 MiB` 分片预生成 Android sparse 镜像流式刷入，避免 Windows 版 `fastboot.exe` 内存溢出（`std::bad_alloc`）。
4. **Deepin 25 / 主线 Linux 显示与触控修复（`linux-fixes/sheng-display-fix/`）**
   - 将 `DSI-1` 重置与 `ktz8866-backlight` `0 -> SAVED` 背光唤醒前移至 LightDM `display-setup-script`，并通过 `libXi` 检查确保 `NVTCapacitiveTouchScreen` 保持启用。

---

## 仓库不包含什么（重要声明）

依据上游 `kShimC4sheng` 与 GPL-3.0 合规要求，本仓库与 Release **不包含任何设备厂商或苹果专有二进制**，请勿公开分发包含原厂内核或 iPadOS 镜像的成品 `boot_a.img`：
- 不含小米原厂 `stock_boot_a.img` / Android 内核
- 不含 UniXNU `boot.img`、加密/解密 `scratch-data.img`、APFS/iPadOS rootfs
- 不含 Deepin 25 / Linux rootfs 大镜像

您需要从自己的设备或各自上游渠道准备上述输入文件，再使用本仓库的 `prebuilt/sheng-runtime.bin` 和 `tools/` 在本地几分钟内完成打包。

---

## 推荐分区方案（256 GB UFS `sda`，4096 B 扇区）

> ⚠️ **警告**：修改 GPT 分区表极度危险，操作前务必在 Recovery 下完整备份当前 GPT（`sgdisk --backup=...`）以及原厂 `boot_a.img`。

实测推荐布局（保留原厂 `#29 userdata` 不动，仅在尾部划分 `darwin` 与 `linux`）：

| 分区号 | 分区名 | 容量 | 用途 |
| ---: | --- | --- | --- |
| `#29` | `userdata` | `~160.4 GiB` | Android（保持原样） |
| `#30` | `darwin` | `26.0 GiB` | iPadOS 27（`10 GiB` APFS Data 种子，可用约 `9.7 GiB`） |
| `#31` | `linux` | `46.0 GiB` | Deepin 25 / 主线 Linux（首次启动自动扩满） |

---

## 使用方法

### 第 1 步：准备输入文件

将以下文件放到 `out/inputs/`（或通过命令行参数指定路径）：
1. `stock_boot_a.img`：与你当前系统版本完全一致的小米原厂 `boot_a.img`（来自官方线刷包 `images/boot.img` 或已 root 设备 `dd` 备份）。
2. `unixnu_boot.img`：UniXNU `sheng` 构建产物（含 `sheng-uefi` 与 appended DTB，参考 [`KuriyamaMirai08/UniXNU-sheng`](https://github.com/KuriyamaMirai08/UniXNU-sheng)）。
3. `ipados-fat32.kshmlz4`：UniXNU FAT32 ramdisk（若你调整了 `darwin` / Data 种子大小，见第 2 步生成；否则直接压缩现有 FAT32 镜像）。
4. `linux_boot_kshim.img`：主线 Linux / Deepin 25 的 `kShimC` boot 镜像（内含 `Linux` 内核与 `sheng-dtb`，且 `bootargs` 包含 `root=PARTLABEL=linux`）。

### 第 2 步：（可选）自定义 `darwin` 分区容量与 `WALv2` 初始化镜像

如果你将 `darwin` 分区调整为自定义大小（例如 `26 GiB` = `6815744` 个 4096 B 扇区），并准备了对应的 `scratch-data.img`（例如 `10 GiB` APFS Data 种子）：

1. **修补 APFS Data 种子 UUID**：
   ```sh
   python3 tools/patch_seed_uuid.py \
     --in out/stageB/scratch-data-10g.img \
     --out out/stageB/scratch-data-10g-uuid.img
   ```
2. **写回 FAT32 ramdisk 并使用 `KSHMLZ4` 压缩**：
   ```sh
   python3 tools/repack_fat32.py \
     --in out/inputs/orig-fat32-136m.img \
     --out out/stageB/ipados-fat32-136m-uuid.img \
     --replace /EFI/UniXNU/Payloads/scratch-data.img=out/stageB/scratch-data-10g-uuid.img

   python3 tools/compress_initrd_lz4.py \
     --in out/stageB/ipados-fat32-136m-uuid.img \
     --out out/stageB/ipados-fat32-136m-uuid.kshmlz4
   ```
3. **编译并生成匹配的 `WALv2` 稀疏初始化镜像**：
   ```sh
   g++ -O2 -std=c++17 tools/gen_walv2_init_any.cpp -o out/gen_walv2_init_any
   ./out/gen_walv2_init_any 6815744 out/stageB/scratch-data-10g-uuid.img out/stageB/walv2-blocks.bin
   python3 tools/make_walv2_sparse.py \
     --blocks out/stageB/walv2-blocks.bin \
     --total-sectors 6815744 \
     --out out/stageB/darwin-walv2-init.sparse.img
   ```

### 第 3 步：打包三系统 `boot_a.img`（免交叉编译）

仓库已内置编译好的 3-OS `prebuilt/sheng-runtime.bin`（`4 MiB`）与 Windows 版 `prebuilt/windows/MultiBootKernelPatcher.exe`：

```sh
python3 tools/make_ipados_boot.py \
  --stock-boot out/inputs/stock_boot_a.img \
  --unixnu-boot out/inputs/unixnu_boot.img \
  --fat32 out/stageB/ipados-fat32-136m-uuid.kshmlz4 \
  --deepin-boot out/inputs/linux_boot_kshim.img \
  --runtime prebuilt/sheng-runtime.bin \
  --default android \
  --timeout-ms 5000 \
  --entry-name "iPadOS 27" \
  --out out/boot_a-kshim-3os.img
```

打包后务必执行静态校验（检查大小 `<= 176 MiB`、manifest 与 `SHNGSEED2` 一致性）：

```sh
python3 tools/verify_dualboot_img.py \
  --image out/boot_a-kshim-3os.img \
  --seed out/stageB/scratch-data-10g-uuid.img \
  --walv2-sparse out/stageB/darwin-walv2-init.sparse.img
```

> 若仅需 **Android + iPadOS 27** 双系统，去掉 `--deepin-boot` 参数即可。

### 第 4 步：刷入设备（`slot a`）

刷入前先确认设备型号与当前槽位：

```sh
fastboot getvar product       # 必须为 sheng
fastboot getvar current-slot  # 必须为 a
```

依次刷入 `darwin`、`boot_a` 与 `linux`：

```sh
# 1. 刷入 iPadOS WALv2 初始化镜像
fastboot flash darwin out/stageB/darwin-walv2-init.sparse.img

# 2. 刷入三系统 boot_a 启动器
fastboot flash boot_a out/boot_a-kshim-3os.img

# 3. 刷入 Linux 系统镜像（Windows 下若 fastboot 直接刷 16 GiB 报 std::bad_alloc，请使用分片工具）：
python tools/flash_raw_sparse_slices.py --image path/to/linux.img --partition linux
```

### 第 5 步：安装 Deepin 25 显示与触控修复

进入 Skyward Recovery（或任意 Linux 环境）挂载 `linux` 分区并安装 [`linux-fixes/sheng-display-fix/`](linux-fixes/sheng-display-fix/README.md) 中的 `sheng-display-fix.sh` 与 `99-sheng-display-fix.conf`，完成后 `sync && umount` 再重启。

### 菜单操作说明

- **音量 `+` / `-`**：上下移动选择系统（Android / iPadOS 27 / Linux / 高级启动 / 设置等）
- **电源键**：确认启动 / 进入子菜单
- **回滚原厂 Android**：`fastboot flash boot_a stock_boot_a.img && fastboot set_active a && fastboot reboot`

---

## 从源码编译 `sheng-runtime.bin`（可选）

仅在修改 `vendor/kshim/` 菜单、图标、壁纸或引导逻辑时需要（需 Linux / WSL2）：

```sh
scripts/fetch_toolchain.sh
scripts/fetch_deps.sh
scripts/build_runtime.sh config/sheng_ipados_defconfig out/runtime
```

产物固定为 `4 MiB`（`out/runtime.bin`）。

---

## 参考仓库与致谢

本项目站在多位开源作者与社区维护者的工作之上，特此完整标出所有参考仓库与上游项目：

| 项目 / 仓库 | 作用与贡献 | 许可证 |
| --- | --- | --- |
| [`tc1911/kShimC4sheng`](https://github.com/tc1911/kShimC4sheng) | 本仓库直接基于其二改，`sheng` 平台适配、打包器与 Atlas 六页中文菜单 | GPL-3.0 |
| [`sunflower2333/kShimC`](https://github.com/sunflower2333/kShimC) | kShimC 内核级多系统引导原作者（B站「单翼」） | GPL-3.0 |
| [`mydanyi/armadaos-freedom`](https://github.com/mydanyi/armadaos-freedom) | `kshim-success-source` 快照与 Atlas / `STAR_CHART` UI 设计来源 | GPL-2.0-or-later / GPL-3.0 |
| [`Project-Aloha/DualBootKernelPatcher`](https://github.com/Project-Aloha/DualBootKernelPatcher) | `MultiBootKernelPatcher` 多系统镜像打包器 | GPL-3.0-or-later |
| [`Project-Aloha/CrDK`](https://github.com/Project-Aloha/CrDK) | 裸机驱动与运行环境库 | MIT |
| [`UniXNU/UniXNU-sheng`](https://github.com/UniXNU/UniXNU-sheng) & [`UniXNU/RamBlock`](https://github.com/UniXNU/RamBlock) | 小米平板 6S Pro (`sheng`) XNU/iPadOS 引导与 `WALv2` 持久化框架 | — |
| [`KuriyamaMirai08/UniXNU-sheng`](https://github.com/KuriyamaMirai08/UniXNU-sheng) & [`KuriyamaMirai08/UniXNU-sheng-DualBoot-Guide`](https://github.com/KuriyamaMirai08/UniXNU-sheng-DualBoot-Guide) | UniXNU `d8` 持久化补丁、APFS 种子构建工作流与双系统指南 | — |
| [`ianchb/sm8550-mainline`](https://github.com/ianchb/sm8550-mainline) | `sheng` 主线 Linux 内核、`ktz8866` 背光、THP 触控与 MiPPS 适配 | GPL-2.0 |
| [`ianchb/debian-sheng`](https://github.com/ianchb/debian-sheng) & [`ianchb/sheng-firmware`](https://github.com/ianchb/sheng-firmware) | `sheng` Linux rootfs 构建工作流与固件提取 | — |
| [`code002-2/Xiaomi-pad-6s-pro-Linux`](https://github.com/code002-2/Xiaomi-pad-6s-pro-Linux) | `sheng` 分区布局与双系统刷机参考 | — |
| [`map220v/android_device_xiaomi_sheng`](https://github.com/map220v/android_device_xiaomi_sheng) & [`NoobMaster233/twrp-sheng-build`](https://github.com/NoobMaster233/twrp-sheng-build) | `sheng` 主线内核早期适配与 Recovery 设备树 | — |
| [`notofonts/noto-cjk`](https://github.com/notofonts/noto-cjk) | `NotoSansCJKsc-Regular.otf`（精简子集 `noto_sans_cjk_menu.ksf`） | SIL OFL 1.1 |
| [`lvgl/lvgl`](https://github.com/lvgl/lvgl) / [`picolibc/picolibc`](https://github.com/picolibc/picolibc) | 图形界面库 / 裸机 C 标准库 | MIT / BSD-3-Clause |
| Deepin 25 arm64 for `sheng` 社区镜像 & Skyward Recovery | Deepin 25 体验镜像整合（破孩de小窝）与 `sheng` Recovery | — |

---

## 许可证

本项目继承自 `kShimC` / `kShimC4sheng`，按 **GPL-3.0** 开源发布，详见 [`LICENSE`](LICENSE) 与 [`LICENSES.zh-CN.md`](LICENSES.zh-CN.md)。
