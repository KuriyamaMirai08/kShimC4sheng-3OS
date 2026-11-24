# Prebuilt Runtime and Windows Packer

- `sheng-runtime.bin`: 4 MiB `kShimC` runtime built from `vendor/kshim` using `config/sheng_ipados_defconfig` (3-OS Atlas UI: Android + iPadOS 27 / `KSHMLZ4` + Linux, Noto Sans CJK subset font, 4K starry-night wallpaper).
  - SHA-256: `51bc05f498e70a3b8c425507d9ec0a02a3ae78385b58e2e6860fea5baa6fa1ff`
- `windows/MultiBootKernelPatcher.exe` (and companion DLLs): built from `vendor/packer` for Windows x64 hosts.

Rebuild `sheng-runtime.bin` from source (Linux / WSL2 with `aarch64` cross-toolchain):
```sh
scripts/fetch_toolchain.sh
scripts/fetch_deps.sh
scripts/build_runtime.sh config/sheng_ipados_defconfig out/runtime
```
