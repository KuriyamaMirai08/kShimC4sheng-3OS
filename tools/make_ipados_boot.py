#!/usr/bin/env python3
"""Pack a single-slot boot_a dual-boot image (Android + iPadOS 27 / UniXNU d8).

Uses MultiBootKernelPatcher with:
  entry 0  stock Android kernel   (BaseImage, Align=21)
  entry 1  sheng-uefi             (raw ARM64 Image, Align=20, text_offset=0xa7100000)
  entry 2  sheng-abl.dtb          (DTB, Align=3, /chosen/kshim,initrd-entry = <3>)
  entry 3  compact FAT32 ramdisk  (Blob, Align=16 for 64 KiB initrd_resolve safety)
  entry 4  Attached Shim          (4 MiB kShimC runtime)
"""

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packer"))
import bootimg  # noqa: E402
import extract  # noqa: E402
import fdt  # noqa: E402
import manifest  # noqa: E402

PARTITION_SIZE = 201326592  # 192 MiB
RECOVERY_POOL_BOOT_LIMIT = 176 * 1024 * 1024  # 176 MiB ceiling for ABL Recovery + Boot
AVB_RESERVE = 64 * 1024
DTB_MAGIC = b"\xd0\x0d\xfe\xed"


def die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    raise SystemExit(1)


def read_bytes(path: str, label: str) -> bytes:
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as e:
        die(f"{label}: {e}")
    if not data:
        die(f"{label} is empty: {path}")
    return data


def patch_sheng_dtb(dtb_bytes: bytes, dst_path: str, initrd_index: int) -> bytes:
    """Set /chosen/kshim,initrd-entry while preserving original /chosen/bootargs."""
    tree = fdt.Fdt.parse(dtb_bytes)
    orig_bootargs = tree.get("/chosen", "bootargs")
    tree.set("/chosen", "kshim,initrd-entry", int(initrd_index).to_bytes(4, "big"))
    tree.delete("/chosen", "linux,initrd-start")
    tree.delete("/chosen", "linux,initrd-end")
    tree.write(dst_path)

    patched = read_bytes(dst_path, "patched DTB")
    check = fdt.Fdt.parse(patched)
    if check.get("/chosen", "bootargs") != orig_bootargs:
        die("patched DTB altered /chosen/bootargs")
    if check.get("/chosen", "kshim,initrd-entry") != int(initrd_index).to_bytes(4, "big"):
        die("patched DTB /chosen/kshim,initrd-entry mismatch")
    for prop in ("linux,initrd-start", "linux,initrd-end"):
        if check.get("/chosen", prop) is not None:
            die(f"patched DTB still contains {prop}")
    return patched


def patch_linux_dtb(dtb_bytes: bytes, dst_path: str) -> bytes:
    """Set /chosen/armada,root-medium = 'tf' (Tux icon) while preserving /chosen/bootargs."""
    tree = fdt.Fdt.parse(dtb_bytes)
    orig_bootargs = tree.get("/chosen", "bootargs")
    if not orig_bootargs or b"root=PARTLABEL=linux" not in orig_bootargs:
        die(f"unexpected Deepin DTB bootargs: {orig_bootargs!r}")
    tree.set("/chosen", "armada,root-medium", b"tf\x00")
    tree.write(dst_path)

    patched = read_bytes(dst_path, "patched linux DTB")
    check = fdt.Fdt.parse(patched)
    if check.get("/chosen", "bootargs") != orig_bootargs:
        die("patched linux DTB altered /chosen/bootargs")
    if check.get("/chosen", "armada,root-medium") != b"tf\x00":
        die("patched linux DTB /chosen/armada,root-medium mismatch")
    return patched


def write_pack_ini(path: str, args, staged: dict):
    lines = [
        "[Pack]",
        f"Shim = {args.runtime}",
        f"Output = {staged['packed']}",
        f"Default = {args.default_section}",
        f"Timeout = {args.timeout_ms}",
        "",
        "[Image-00]",
        "Name = Android",
        f"Path = {staged['android']}",
        "Type = Linux",
        "BaseImage = true",
        "Align = 21",
        "",
        "[Image-01]",
        f"Name = {args.entry_name}",
        f"Path = {staged['uefi']}",
        "Type = Linux",
        "BaseImage = false",
        "Align = 20",
        "DeviceTreeBlob = Image-02",
        "",
        "[Image-02]",
        "Name = sheng-dtb",
        f"Path = {staged['dtb']}",
        "Type = DTB",
        "BaseImage = false",
        "Align = 3",
        "",
        "[Image-03]",
        "Name = initrd",
        f"Path = {args.fat32}",
        "Type = Blob",
        "BaseImage = false",
        "Align = 16",
        "",
    ]
    if args.deepin_boot:
        lines.extend([
            "[Image-04]",
            "Name = Linux",
            f"Path = {staged['linux_img']}",
            "Type = Linux",
            "BaseImage = false",
            "Align = 21",
            "DeviceTreeBlob = Image-05",
            "",
            "[Image-05]",
            "Name = linux-dtb",
            f"Path = {staged['linux_dtb']}",
            "Type = DTB",
            "BaseImage = false",
            "Align = 3",
            "",
        ])
    lines.extend([
        "[Manifest]",
        "Type = Manifest",
        "",
    ])
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def verify_packed(packed: bytes, stock: dict, staged: dict, args) -> dict:
    m = manifest.parse(packed)
    if m["image_size"] != len(packed):
        die(f"manifest image_size {m['image_size']} != packed size {len(packed)}")

    base = m["base_image"]
    for field, off in (
        ("instructions", 0x00),
        ("image_size", 0x10),
        ("manifest_offset", 0x20),
        ("manifest_size", 0x28),
        ("shim_offset", 0x30),
    ):
        want = bootimg.u64(stock["kernel"], off)
        if base[field] != want:
            die(f"base image backup {field} 0x{base[field]:x} != stock header 0x{want:x}")

    if args.deepin_boot:
        expected_names = ["Android", args.entry_name, "sheng-dtb", "initrd", "Linux", "linux-dtb", "Attached Shim"]
        names = [e["name"] for e in m["entries"]]
        if names != expected_names:
            die(f"manifest entries {names!r} != expected {expected_names!r}")
        e0, e1, e2, e3, e4, e5, e6 = m["entries"]
        if e0["type_name"] != "Linux" or "BASE_IMAGE" not in e0["flag_names"]:
            die("entry 0 invalid")
        if e1["type_name"] != "Linux" or "HAS_DTB" not in e1["flag_names"] or e1["dtb_entry"] != 2:
            die("entry 1 invalid")
        if e2["type_name"] != "DTB" or e2["size"] != os.path.getsize(staged["dtb"]):
            die("entry 2 invalid")
        if e3["type_name"] != "Blob" or e3["size"] != os.path.getsize(args.fat32):
            die("entry 3 invalid")
        if e4["type_name"] != "Linux" or "HAS_DTB" not in e4["flag_names"] or e4["dtb_entry"] != 5 or e4["size"] != os.path.getsize(staged["linux_img"]):
            die("entry 4 (Linux) invalid")
        if e5["type_name"] != "DTB" or e5["size"] != os.path.getsize(staged["linux_dtb"]):
            die("entry 5 (linux-dtb) invalid")
        if e6["type_name"] != "Shim" or e6["offset"] != m["shim_offset"]:
            die("entry 6 (Shim) invalid")
    else:
        expected_names = ["Android", args.entry_name, "sheng-dtb", "initrd", "Attached Shim"]
        names = [e["name"] for e in m["entries"]]
        if names != expected_names:
            die(f"manifest entries {names!r} != expected {expected_names!r}")

        e0, e1, e2, e3, e4 = m["entries"]
        if e0["type_name"] != "Linux" or "BASE_IMAGE" not in e0["flag_names"]:
            die("entry 0 invalid")
        if e1["type_name"] != "Linux" or "HAS_DTB" not in e1["flag_names"] or e1["dtb_entry"] != 2:
            die("entry 1 invalid")
        if e2["type_name"] != "DTB" or e2["size"] != os.path.getsize(staged["dtb"]):
            die("entry 2 invalid")
        if e3["type_name"] != "Blob" or e3["size"] != os.path.getsize(args.fat32):
            die("entry 3 invalid")
        if e4["type_name"] != "Shim" or e4["offset"] != m["shim_offset"]:
            die("entry 4 invalid")
    for e in m["entries"]:
        if e["offset"] + e["size"] > m["manifest_offset"]:
            die(f"entry {e['name']} overlaps manifest")
    return m


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stock-boot", default=os.path.join(ROOT, "out", "inputs", "stock_boot_a.img"))
    ap.add_argument("--unixnu-boot", default=os.path.join(ROOT, "out", "inputs", "unixnu_boot.img"))
    ap.add_argument("--fat32", default=os.path.join(ROOT, "out", "work", "ipados-fat32-136m.img"))
    ap.add_argument("--runtime", default=os.path.join(ROOT, "prebuilt", "sheng-runtime.bin"))
    ap.add_argument(
        "--packer",
        default=os.path.join(ROOT, "prebuilt", "windows", "MultiBootKernelPatcher.exe"),
    )
    ap.add_argument("--out", default=os.path.join(ROOT, "out", "boot_a-kshim-p1.img"))
    ap.add_argument("--default", choices=("android", "ipados"), default="android")
    ap.add_argument("--timeout-ms", type=int, default=8000)
    ap.add_argument("--entry-name", default="iPadOS 27")
    ap.add_argument("--deepin-boot", default=None, help="Optional Deepin boot_a_kShimC.img to embed Linux directly")
    ap.add_argument("--work", default=os.path.join(ROOT, "out", "work", "pack_p1"))
    ap.add_argument("--keep-work", action="store_true")
    args = ap.parse_args()

    for attr in ("stock_boot", "unixnu_boot", "fat32", "runtime", "packer", "out", "work"):
        setattr(args, attr, os.path.abspath(getattr(args, attr)))
    if args.deepin_boot:
        args.deepin_boot = os.path.abspath(args.deepin_boot)

    args.default_section = "Image-00" if args.default == "android" else "Image-01"

    stock = bootimg.parse(read_bytes(args.stock_boot, "stock boot"))
    bootimg.assert_raw_arm64_image(stock["kernel"], "stock kernel")
    if stock["version"] not in (3, 4):
        die(f"expected stock v3/v4, got v{stock['version']}")
    for off in (0x20, 0x28, 0x30):
        if bootimg.u64(stock["kernel"], off) != 0:
            die(f"stock kernel header offset 0x{off:x} is non-zero")

    unixnu = bootimg.parse(read_bytes(args.unixnu_boot, "unixnu boot_b"))
    kblob = unixnu["kernel"]
    if kblob[:2] != b"\x1f\x8b":
        die("unixnu kernel is not gzipped")
    uefi_raw, trailing = extract.gunzip(kblob)
    bootimg.assert_raw_arm64_image(uefi_raw, "sheng-uefi")
    uefi_img_sz = struct.unpack_from("<Q", uefi_raw, 0x10)[0]
    if uefi_img_sz != 0x3000C0:
        die(f"unexpected sheng-uefi image_size 0x{uefi_img_sz:x} != 0x3000c0")

    fdt_hit = extract.find_fdt(trailing, at_start=True)
    if not fdt_hit:
        die("appended DTB not found after gzipped sheng-uefi")
    dtb_raw = trailing[fdt_hit[0] : fdt_hit[0] + fdt_hit[1]]
    if dtb_raw[:4] != DTB_MAGIC:
        die("bad DTB magic")

    runtime = read_bytes(args.runtime, "runtime")
    if len(runtime) != 4 * 1024 * 1024:
        die(f"runtime must be 4 MiB, got {len(runtime)}")
    if not os.path.isfile(args.fat32):
        die(f"missing fat32 image: {args.fat32}")
    if not os.path.isfile(args.packer):
        die(f"missing packer: {args.packer}")

    os.makedirs(args.work, exist_ok=True)
    staged = {
        "android": os.path.join(args.work, "stock-kernel.img"),
        "uefi": os.path.join(args.work, "sheng-uefi.bin"),
        "dtb": os.path.join(args.work, "sheng-abl.patched.dtb"),
        "packed": os.path.join(args.work, "packed-kernel.bin"),
    }
    with open(staged["android"], "wb") as f:
        f.write(stock["kernel"])
    with open(staged["uefi"], "wb") as f:
        f.write(uefi_raw)
    patch_sheng_dtb(dtb_raw, staged["dtb"], initrd_index=3)

    if args.deepin_boot:
        deepin = bootimg.parse(read_bytes(args.deepin_boot, "deepin boot"))
        dm = manifest.parse(deepin["kernel"])
        le = next((e for e in dm["entries"] if e["name"] == "Linux"), None)
        de = next((e for e in dm["entries"] if e["name"] == "sheng-dtb"), None)
        if not le or not de:
            die("missing Linux or sheng-dtb in deepin boot image")
        linux_raw = deepin["kernel"][le["offset"] : le["offset"] + le["size"]]
        bootimg.assert_raw_arm64_image(linux_raw, "deepin Linux")
        linux_dtb_raw = deepin["kernel"][de["offset"] : de["offset"] + de["size"]]
        staged["linux_img"] = os.path.join(args.work, "deepin-linux.img")
        staged["linux_dtb"] = os.path.join(args.work, "deepin-sheng.patched.dtb")
        with open(staged["linux_img"], "wb") as f:
            f.write(linux_raw)
        patch_linux_dtb(linux_dtb_raw, staged["linux_dtb"])

    ini_path = os.path.join(args.work, "pack.ini")
    write_pack_ini(ini_path, args, staged)

    proc = subprocess.run([args.packer, ini_path], capture_output=True, text=True)
    sys.stderr.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        die(f"MultiBootKernelPatcher failed ({proc.returncode})")

    packed = read_bytes(staged["packed"], "packed kernel")
    m = verify_packed(packed, stock, staged, args)

    total = stock["page_size"] + len(packed)
    limit = RECOVERY_POOL_BOOT_LIMIT if args.deepin_boot else (PARTITION_SIZE - AVB_RESERVE)
    if total > limit:
        die(f"boot image {total} B exceeds limit {limit} B")

    out_img = bootimg.build(stock, packed)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(out_img)

    report = {
        "output": args.out,
        "size": len(out_img),
        "partition_size": PARTITION_SIZE,
        "avb_reserve": AVB_RESERVE,
        "headroom_vs_limit": limit - len(out_img),
        "header_version": stock["version"],
        "page_size": stock["page_size"],
        "packed_kernel_size": len(packed),
        "sha256": hashlib.sha256(out_img).hexdigest(),
        "default": args.default,
        "timeout_ms": args.timeout_ms,
        "entry_name": args.entry_name,
        "manifest": m,
    }
    with open(args.out + ".json", "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    if not args.keep_work:
        shutil.rmtree(args.work, ignore_errors=True)

    print(
        json.dumps(
            {
                k: report[k]
                for k in ("output", "size", "headroom_vs_limit", "header_version", "sha256")
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
