# kShimC4sheng-3OS

[简体中文](README.md) | **English**

A single-slot `boot_a` graphical **3-OS boot switcher** for the **Xiaomi Pad 6S Pro 12.4 (`sheng` / SM8550)**, derived from [`tc1911/kShimC4sheng`](https://github.com/tc1911/kShimC4sheng) (GPL-3.0):

- **Android** (stock HyperOS, default entry, 5s countdown, Bugdroid icon)
- **iPadOS 27** (UniXNU `d8` persistent, Apple SVG icon, `KSHMLZ4`-compressed FAT32 ramdisk)
- **Linux** (Deepin 25 / mainline SM8550 Linux, Tux icon, **0-reboot direct boot from `boot_a`**)

`recovery_a` (e.g. Skyward Recovery) and `boot_b` (Android rescue slot) remain completely untouched.

## Hardware-Verified Setup
Tested on Xiaomi Pad 6S Pro 12.4 (`sheng`, HyperOS `OS3.0.306.0.WNXCNXM`, `current-slot: a`):
- `#29 userdata` (`160.4 GiB`): Stock Android HyperOS
- `#30 darwin` (`26.0 GiB`): iPadOS 27 (`UniXNU d8`, `10 GiB` APFS Data seed, `~9.7 GiB` free)
- `#31 linux` (`46.0 GiB`): Deepin 25 arm64 with LightDM `display-setup-script` DSI-1 + backlight + XI2 touch fix (`linux-fixes/sheng-display-fix/`)

## Highlights
- **Fits within the 176 MiB ABL Recovery+Boot pool ceiling** (`166.46 MiB` packed) via streaming `KSHMLZ4` decompression (`vendor/kshim/src/dt/initrd.c`, `tools/compress_initrd_lz4.py`).
- **Custom 3-OS Atlas UI**: starry-night wallpaper, transparent indigo panels, Bugdroid / Apple / Tux icons (`tools/gen_custom_icons.py`), and subsetted Noto Sans CJK font.
- **Complete Open-Source Tooling (`tools/`)**:
  - `patch_seed_uuid.py`: patches all `APSB` volume UUIDs in custom-sized APFS Data seeds to `XNU_OWNER_DATA_VOLUME_UUID = 2A5E9027-D9E1-40F9-983C-41BD3F1994F5` and recomputes Fletcher-64 checksums.
  - `gen_walv2_init_any.cpp` & `make_walv2_sparse.py`: generates `WALv2` sparse init images for any `darwin` partition size.
  - `repack_fat32.py`, `make_ipados_boot.py`, `verify_dualboot_img.py`: repacks FAT32 initrd, builds 2-OS or 3-OS `boot_a.img` using `prebuilt/sheng-runtime.bin`, and verifies manifest/bounds.
  - `flash_raw_sparse_slices.py`: streams large raw Linux images in `256 MiB` Android sparse slices to avoid Windows `fastboot.exe` `std::bad_alloc`.

## Quick Start
1. Prepare your own device inputs (`stock_boot_a.img`, `unixnu_boot.img`, `ipados-fat32.kshmlz4`, and `linux_boot_kshim.img` — **proprietary vendor/Apple binaries are never distributed in this repo**).
2. Pack `boot_a`:
   ```sh
   python3 tools/make_ipados_boot.py \
     --stock-boot out/inputs/stock_boot_a.img \
     --unixnu-boot out/inputs/unixnu_boot.img \
     --fat32 out/stageB/ipados-fat32-136m-uuid.kshmlz4 \
     --deepin-boot out/inputs/linux_boot_kshim.img \
     --runtime prebuilt/sheng-runtime.bin \
     --default android --timeout-ms 5000 --entry-name "iPadOS 27" \
     --out out/boot_a-kshim-3os.img
   ```
3. Flash `darwin` (`WALv2`), `boot_a`, and `linux`, and apply [`linux-fixes/sheng-display-fix/`](linux-fixes/sheng-display-fix/README.md) if running Deepin 25.

See [README.md](README.md) for the full Chinese step-by-step guide, partition layout, troubleshooting, and complete upstream repository attributions.

## Upstream Credits & License
Licensed under **GPL-3.0** ([`LICENSE`](LICENSE)). Built upon:
- [`tc1911/kShimC4sheng`](https://github.com/tc1911/kShimC4sheng), [`sunflower2333/kShimC`](https://github.com/sunflower2333/kShimC), [`mydanyi/armadaos-freedom`](https://github.com/mydanyi/armadaos-freedom)
- [`Project-Aloha/DualBootKernelPatcher`](https://github.com/Project-Aloha/DualBootKernelPatcher), [`Project-Aloha/CrDK`](https://github.com/Project-Aloha/CrDK)
- [`UniXNU/UniXNU-sheng`](https://github.com/UniXNU/UniXNU-sheng), [`UniXNU/RamBlock`](https://github.com/UniXNU/RamBlock), [`KuriyamaMirai08/UniXNU-sheng`](https://github.com/KuriyamaMirai08/UniXNU-sheng), [`KuriyamaMirai08/UniXNU-sheng-DualBoot-Guide`](https://github.com/KuriyamaMirai08/UniXNU-sheng-DualBoot-Guide)
- [`ianchb/sm8550-mainline`](https://github.com/ianchb/sm8550-mainline), [`ianchb/debian-sheng`](https://github.com/ianchb/debian-sheng), [`ianchb/sheng-firmware`](https://github.com/ianchb/sheng-firmware)
- [`code002-2/Xiaomi-pad-6s-pro-Linux`](https://github.com/code002-2/Xiaomi-pad-6s-pro-Linux), [`map220v/android_device_xiaomi_sheng`](https://github.com/map220v/android_device_xiaomi_sheng), [`NoobMaster233/twrp-sheng-build`](https://github.com/NoobMaster233/twrp-sheng-build)
