#!/usr/bin/env python3
"""Extract UniXNU FAT32 ramdisk files from boot_b and pack a compact FAT32 image.

Stdlib-only Python 3. Preserves exact file contents (verified by SHA-256) while
shrinking the FAT32 container (default 136 MiB, spc=4 / 2 KiB clusters so
cluster count stays >= 65,525 as required by FAT32 and EDK2 FatDxe).
"""

import argparse
import hashlib
import json
import math
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "packer"))
import bootimg  # noqa: E402

BPS = 512
SPC = 4  # 2048 B clusters: mandatory to keep cluster count >= 65525 for FAT32
CLUSTER_BYTES = BPS * SPC
RSVD_SECS = 32
NUM_FATS = 2
MIN_FAT32_CLUSTERS = 65525


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_fat32_tree(img: bytes):
    """Independently parse a FAT32 image and return {path: (bytes, lfn_ok)}."""
    bps, spc, rsvd, nfats = struct.unpack_from("<HBHB", img, 11)
    if bps != 512 or spc < 1:
        raise ValueError(f"Unexpected BPB geometry bps={bps} spc={spc}")
    tot32 = struct.unpack_from("<I", img, 32)[0]
    spf32 = struct.unpack_from("<I", img, 36)[0]
    root_clus = struct.unpack_from("<I", img, 44)[0]
    fs_type = img[82:90]
    if fs_type != b"FAT32   ":
        raise ValueError(f"Not FAT32: {fs_type!r}")
    if tot32 * bps > len(img):
        raise ValueError(f"Image truncated: tot32*bps={tot32*bps} > {len(img)}")

    fat_off = rsvd * bps
    data_off = (rsvd + nfats * spf32) * bps
    clus_sz = bps * spc
    avail_clus = (tot32 - rsvd - nfats * spf32) // spc

    def next_clus(c: int):
        v = struct.unpack_from("<I", img, fat_off + c * 4)[0] & 0x0FFFFFFF
        if v >= 0x0FFFFFF8:
            return None
        if v < 2 or v >= avail_clus + 2:
            raise ValueError(f"Invalid FAT entry {c} -> {v:#x}")
        return v

    def read_chain(c: int) -> bytes:
        out = bytearray()
        seen = set()
        while c is not None:
            if c in seen:
                raise ValueError(f"FAT loop at cluster {c}")
            seen.add(c)
            off = data_off + (c - 2) * clus_sz
            out += img[off : off + clus_sz]
            c = next_clus(c)
        return bytes(out)

    def lfn_checksum(short11: bytes) -> int:
        s = 0
        for b in short11:
            s = (((s & 1) << 7) + (s >> 1) + b) & 0xFF
        return s

    files = {}

    def walk(c: int, prefix: str):
        data = read_chain(c)
        lfn_parts = []
        for i in range(0, len(data), 32):
            ent = data[i : i + 32]
            if ent[0] == 0x00:
                break
            if ent[0] == 0xE5:
                lfn_parts = []
                continue
            attr = ent[11]
            if attr == 0x0F:
                seq = ent[0]
                chk = ent[13]
                raw_chars = ent[1:11] + ent[14:26] + ent[28:32]
                lfn_parts.append((seq, chk, raw_chars))
                continue
            if attr & 0x08:  # volume label
                lfn_parts = []
                continue

            short11 = ent[0:11]
            if lfn_parts:
                expected_chk = lfn_checksum(short11)
                ordered = sorted(lfn_parts, key=lambda x: x[0] & 0x1F)
                raw = bytearray()
                for idx, (seq, chk, chars) in enumerate(ordered, 1):
                    if (seq & 0x1F) != idx:
                        raise ValueError(f"LFN sequence mismatch in {prefix}")
                    if chk != expected_chk:
                        raise ValueError(f"LFN checksum mismatch in {prefix}: {chk} != {expected_chk}")
                    raw += chars
                name = raw.decode("utf-16le").split("\x00", 1)[0]
                lfn_parts = []
            else:
                n = ent[0:8].decode("ascii").rstrip()
                e = ent[8:11].decode("ascii").rstrip()
                name = f"{n}.{e}" if e else n

            ch_hi = struct.unpack_from("<H", ent, 20)[0]
            ch_lo = struct.unpack_from("<H", ent, 26)[0]
            fclus = (ch_hi << 16) | ch_lo
            fsize = struct.unpack_from("<I", ent, 28)[0]

            if name in (".", ".."):
                if name == ".." and prefix.count("/") == 1 and fclus != 0:
                    raise ValueError(f"Root-parent '..' in {prefix} has cluster {fclus} != 0")
                continue

            full = f"{prefix}/{name}"
            if attr & 0x10:
                walk(fclus, full)
            else:
                content = read_chain(fclus)[:fsize] if fsize > 0 else b""
                files[full] = content

    walk(root_clus, "")
    return {
        "bps": bps,
        "spc": spc,
        "rsvd": rsvd,
        "nfats": nfats,
        "tot32": tot32,
        "spf32": spf32,
        "avail_clus": avail_clus,
        "files": files,
    }


def make_short11(name: str, used_shorts: set) -> bytes:
    """Generate a unique valid uppercase 8.3 short name (11 bytes)."""
    upper = name.upper()
    if "." in upper:
        base, ext = upper.rsplit(".", 1)
    else:
        base, ext = upper, ""
    clean_base = "".join(c for c in base if c.isalnum() or c in "_-")
    clean_ext = "".join(c for c in ext if c.isalnum() or c in "_-")[:3]

    if len(clean_base) <= 8 and clean_base == base and len(ext) <= 3 and clean_ext == ext:
        cand = clean_base.ljust(8).encode("ascii") + clean_ext.ljust(3).encode("ascii")
        if cand not in used_shorts:
            used_shorts.add(cand)
            return cand

    for idx in range(1, 100):
        suffix = f"~{idx}"
        stem = (clean_base[: 8 - len(suffix)] or "FILE") + suffix
        cand = stem.ljust(8).encode("ascii") + clean_ext.ljust(3).encode("ascii")
        if cand not in used_shorts:
            used_shorts.add(cand)
            return cand
    raise RuntimeError(f"Exhausted short names for {name}")


def lfn_checksum(short11: bytes) -> int:
    s = 0
    for b in short11:
        s = (((s & 1) << 7) + (s >> 1) + b) & 0xFF
    return s


def encode_lfn_entries(name: str, short11: bytes) -> bytes:
    """Build FAT32 VFAT LFN directory entries for `name`."""
    chk = lfn_checksum(short11)
    u16 = name.encode("utf-16le")
    # Pad with 0x0000 then 0xffff to a multiple of 26 bytes (13 UTF-16LE code units)
    rem = len(u16) % 26
    if rem != 0:
        u16 += b"\x00\x00"
        pad_bytes = 26 - len(u16) % 26
        if pad_bytes != 26:
            u16 += b"\xff" * pad_bytes
    chunks = [u16[i : i + 26] for i in range(0, len(u16), 26)]
    n = len(chunks)
    out = bytearray()
    for rev_idx in range(n, 0, -1):
        chunk = chunks[rev_idx - 1]
        seq = rev_idx | (0x40 if rev_idx == n else 0)
        ent = bytearray(32)
        ent[0] = seq
        ent[1:11] = chunk[0:10]
        ent[11] = 0x0F
        ent[12] = 0
        ent[13] = chk
        ent[14:26] = chunk[10:22]
        ent[26:28] = b"\x00\x00"
        ent[28:32] = chunk[22:26]
        out += ent
    return bytes(out)


def encode_dir_entry(short11: bytes, attr: int, first_clus: int, size: int) -> bytes:
    ent = bytearray(32)
    ent[0:11] = short11
    ent[11] = attr
    # Fixed deterministic timestamps (2026-11-24 12:00:00)
    crt_time = (12 << 11) | (0 << 5) | 0
    crt_date = ((2026 - 1980) << 9) | (11 << 5) | 24
    struct.pack_into("<HHHH", ent, 14, crt_time, crt_date, crt_date, (first_clus >> 16) & 0xFFFF)
    struct.pack_into("<HHHI", ent, 22, crt_time, crt_date, first_clus & 0xFFFF, size)
    return bytes(ent)


def build_fat32(files_map: dict, size_mib: int) -> tuple[bytes, dict]:
    """Pack `files_map` ({'/EFI/...': bytes}) into a `size_mib` MiB FAT32 image."""
    total_bytes = size_mib * 1024 * 1024
    tot_sec = total_bytes // BPS
    est_clus = (tot_sec - RSVD_SECS) // SPC
    spf32 = math.ceil((est_clus + 2) * 4 / BPS)
    data_sec = tot_sec - RSVD_SECS - NUM_FATS * spf32
    avail_clus = data_sec // SPC
    if avail_clus < MIN_FAT32_CLUSTERS:
        raise ValueError(f"avail_clus={avail_clus} < {MIN_FAT32_CLUSTERS}; increase --size-mib")

    # Build directory tree
    tree = {}
    for path, data in sorted(files_map.items()):
        parts = [p for p in path.split("/") if p]
        cur = tree
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = data

    # Allocate clusters sequentially starting from root=2
    next_clus = 2
    fat_entries = {0: 0x0FFFFFF8, 1: 0x0FFFFFFF}
    img = bytearray(total_bytes)
    fat_off = RSVD_SECS * BPS
    data_off = (RSVD_SECS + NUM_FATS * spf32) * BPS

    def alloc_chain(n_clusters: int) -> int:
        nonlocal next_clus
        if n_clusters == 0:
            return 0
        start = next_clus
        if start - 2 + n_clusters > avail_clus:
            raise ValueError(
                f"FAT32 out of space: need {start - 2 + n_clusters} clusters, avail {avail_clus}"
            )
        for i in range(n_clusters - 1):
            fat_entries[start + i] = start + i + 1
        fat_entries[start + n_clusters - 1] = 0x0FFFFFFF
        next_clus += n_clusters
        return start

    def write_clusters(start_clus: int, payload: bytes):
        if start_clus == 0 or not payload:
            return
        off = data_off + (start_clus - 2) * CLUSTER_BYTES
        img[off : off + len(payload)] = payload

    def build_dir(node: dict, parent_clus: int, is_root: bool) -> int:
        # Reserve 1 cluster first (all dirs here have < 20 entries)
        dir_clus = alloc_chain(1)
        dir_bytes = bytearray()
        if not is_root:
            dot11 = b".          "
            dotdot11 = b"..         "
            dir_bytes += encode_dir_entry(dot11, 0x10, dir_clus, 0)
            # FAT32 rule: '..' pointing to root directory MUST have cluster 0
            dir_bytes += encode_dir_entry(dotdot11, 0x10, 0 if parent_clus == 2 else parent_clus, 0)

        used_shorts = set()
        for name in sorted(node.keys()):
            val = node[name]
            short11 = make_short11(name, used_shorts)
            dir_bytes += encode_lfn_entries(name, short11)
            if isinstance(val, dict):
                sub_clus = build_dir(val, dir_clus, False)
                dir_bytes += encode_dir_entry(short11, 0x10, sub_clus, 0)
            else:
                nclus = math.ceil(len(val) / CLUSTER_BYTES) if val else 0
                fclus = alloc_chain(nclus)
                write_clusters(fclus, val)
                dir_bytes += encode_dir_entry(short11, 0x20, fclus, len(val))

        if len(dir_bytes) > CLUSTER_BYTES:
            raise ValueError("Directory exceeded 1 cluster; multi-cluster dir needed")
        write_clusters(dir_clus, bytes(dir_bytes))
        return dir_clus

    root_clus = build_dir(tree, 0, True)
    assert root_clus == 2
    used_clus = next_clus - 2
    free_clus = avail_clus - used_clus

    # Boot sector (sector 0 and backup at sector 6)
    boot = bytearray(BPS)
    boot[0:3] = b"\xeb\x58\x90"
    boot[3:11] = b"MTOO4050"
    struct.pack_into(
        "<HBHBHHBHHHII",
        boot,
        11,
        BPS,
        SPC,
        RSVD_SECS,
        NUM_FATS,
        0,
        0,
        0xF8,
        0,
        32,
        64,
        0,
        tot_sec,
    )
    struct.pack_into("<IHHIHH", boot, 36, spf32, 0, 0, root_clus, 1, 6)
    struct.pack_into("<BBB", boot, 64, 0x80, 0, 0x29)
    struct.pack_into("<I", boot, 67, 0x554E4958)
    boot[71:82] = b"NO NAME    "
    boot[82:90] = b"FAT32   "
    boot[510:512] = b"\x55\xaa"

    # FSInfo (sector 1 and backup at sector 7)
    fsinfo = bytearray(BPS)
    struct.pack_into("<I", fsinfo, 0, 0x41615252)
    struct.pack_into("<I", fsinfo, 484, 0x61417272)
    struct.pack_into("<II", fsinfo, 488, free_clus, next_clus)
    struct.pack_into("<I", fsinfo, 508, 0xAA550000)

    img[0:BPS] = boot
    img[6 * BPS : 7 * BPS] = boot
    img[1 * BPS : 2 * BPS] = fsinfo
    img[7 * BPS : 8 * BPS] = fsinfo

    # Write FAT0 and FAT1
    fat_buf = bytearray(spf32 * BPS)
    for clus, val in fat_entries.items():
        struct.pack_into("<I", fat_buf, clus * 4, val)
    for fidx in range(NUM_FATS):
        off = fat_off + fidx * spf32 * BPS
        img[off : off + len(fat_buf)] = fat_buf

    meta = {
        "size_bytes": total_bytes,
        "size_mib": size_mib,
        "bps": BPS,
        "spc": SPC,
        "rsvd_secs": RSVD_SECS,
        "num_fats": NUM_FATS,
        "spf32": spf32,
        "tot_sec": tot_sec,
        "avail_clus": avail_clus,
        "used_clus": used_clus,
        "free_clus": free_clus,
        "free_mib": round(free_clus * CLUSTER_BYTES / (1024 * 1024), 3),
    }
    return bytes(img), meta


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-boot", required=True, help="Verified UniXNU boot_b image")
    ap.add_argument("--size-mib", type=int, default=136, help="Output FAT32 size in MiB (default: 136)")
    ap.add_argument("--replace-file", action="append", default=[], help="Replace a file in FAT32: DST_PATH=LOCAL_FILE")
    ap.add_argument("--out", required=True, help="Output FAT32 image path")
    ap.add_argument("--manifest-json", help="Optional JSON report path")
    args = ap.parse_args()

    with open(args.source_boot, "rb") as f:
        src_boot = bootimg.parse(f.read())
    if not src_boot["ramdisk_size"]:
        raise SystemExit("error: source boot image has no ramdisk")

    src_parsed = parse_fat32_tree(src_boot["ramdisk"])
    src_files = src_parsed["files"]
    if not src_files:
        raise SystemExit("error: no files found in source FAT32")

    for spec in args.replace_file:
        if "=" not in spec:
            raise SystemExit(f"error: invalid --replace-file '{spec}', expected DST_PATH=LOCAL_FILE")
        dst_path, local_path = spec.split("=", 1)
        if dst_path not in src_files:
            raise SystemExit(f"error: --replace-file target '{dst_path}' not found in source FAT32")
        with open(local_path, "rb") as rf:
            src_files[dst_path] = rf.read()
        print(f"Replaced {dst_path} with {local_path} ({len(src_files[dst_path])} B, sha256={sha256_hex(src_files[dst_path])})")

    out_bytes, meta = build_fat32(src_files, args.size_mib)

    # Independent read-back verification
    dst_parsed = parse_fat32_tree(out_bytes)
    if set(dst_parsed["files"].keys()) != set(src_files.keys()):
        raise SystemExit("error: file list mismatch after FAT32 repack")
    if dst_parsed["avail_clus"] < MIN_FAT32_CLUSTERS:
        raise SystemExit(f"error: avail_clus {dst_parsed['avail_clus']} < {MIN_FAT32_CLUSTERS}")
    if out_bytes[0:BPS] != out_bytes[6 * BPS : 7 * BPS]:
        raise SystemExit("error: backup boot sector mismatch")
    if out_bytes[BPS : 2 * BPS] != out_bytes[7 * BPS : 8 * BPS]:
        raise SystemExit("error: backup FSInfo sector mismatch")
    fat_sz = meta["spf32"] * BPS
    fat0_off = RSVD_SECS * BPS
    if out_bytes[fat0_off : fat0_off + fat_sz] != out_bytes[fat0_off + fat_sz : fat0_off + 2 * fat_sz]:
        raise SystemExit("error: FAT0 != FAT1")

    file_records = []
    for path in sorted(src_files.keys()):
        s_hash = sha256_hex(src_files[path])
        d_hash = sha256_hex(dst_parsed["files"][path])
        if s_hash != d_hash or len(src_files[path]) != len(dst_parsed["files"][path]):
            raise SystemExit(f"error: content mismatch for {path}")
        file_records.append({"path": path, "size": len(src_files[path]), "sha256": d_hash})

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "wb") as f:
        f.write(out_bytes)

    report = {
        "source_boot": os.path.abspath(args.source_boot),
        "output": os.path.abspath(args.out),
        "output_sha256": sha256_hex(out_bytes),
        "fat32": meta,
        "files": file_records,
    }
    if args.manifest_json:
        os.makedirs(os.path.dirname(os.path.abspath(args.manifest_json)), exist_ok=True)
        with open(args.manifest_json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

    print(
        f"FAT32 repacked OK: {meta['size_bytes']} B ({meta['size_mib']} MiB), "
        f"avail_clus={meta['avail_clus']}, used_clus={meta['used_clus']}, "
        f"free={meta['free_mib']} MiB, files={len(file_records)}, sha256={report['output_sha256']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
