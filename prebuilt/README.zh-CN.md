# 预构建 Runtime 与 Windows 打包器

- `sheng-runtime.bin`：4 MiB `kShimC` runtime，基于 `vendor/kshim` 与 `config/sheng_ipados_defconfig` 编译（三系统 Atlas UI：Android + iPadOS 27 / `KSHMLZ4` + Linux，Noto Sans CJK 精简字库与星空壁纸）。
  - SHA-256：`51bc05f498e70a3b8c425507d9ec0a02a3ae78385b58e2e6860fea5baa6fa1ff`
- `windows/MultiBootKernelPatcher.exe`（及配套 DLL）：由 `vendor/packer` 在 Windows x64 下编译。

如需从源码重新编译 `sheng-runtime.bin`（Linux / WSL2）：
```sh
scripts/fetch_toolchain.sh
scripts/fetch_deps.sh
scripts/build_runtime.sh config/sheng_ipados_defconfig out/runtime
```
