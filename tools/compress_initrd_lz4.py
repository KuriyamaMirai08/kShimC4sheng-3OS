#!/usr/bin/env python3
"""Compress the 136 MiB iPadOS FAT32 ramdisk into KSHMLZ41 4 MiB-chunked LZ4-HC9."""

import argparse
import hashlib
import struct
import zlib
from pathlib import Path

import lz4.block

MAGIC = b"KSHMLZ41"
DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def extract_fat32_from_v2(v2_path: Path) -> bytes:
    data = v2_path.read_bytes()
    # From boot_a-kshim-3os-v2.img manifest: entry 3 initrd @ kernel_off 55771136 + 4096 = 55775232, size 142606336
    fat = data[4096 + 55771136 : 4096 + 55771136 + 142606336]
    assert fat[82:90] == b"FAT32   ", f"Unexpected FAT32 signature: {fat[82:90]!r}"
    return fat


def compress_kshmlz41(raw: bytes, block_size: int = DEFAULT_BLOCK_SIZE) -> bytes:
    raw_size = len(raw)
    raw_crc = zlib.crc32(raw) & 0xFFFFFFFF
    chunks = bytearray()
    block_count = 0
    for off in range(0, raw_size, block_size):
        piece = raw[off : off + block_size]
        comp = lz4.block.compress(piece, mode="high_compression", compression=9, store_size=False)
        chunks.extend(struct.pack("<I", len(comp)))
        chunks.extend(comp)
        block_count += 1

    total_size = 32 + len(chunks)
    pre_hdr = struct.pack("<8s5I", MAGIC, raw_size, block_size, block_count, raw_crc, total_size)
    hdr_crc = zlib.crc32(pre_hdr) & 0xFFFFFFFF
    out = pre_hdr + struct.pack("<I", hdr_crc) + bytes(chunks)
    assert len(out) == total_size
    return out


def verify_kshmlz41(blob: bytes, expected_raw: bytes) -> None:
    magic, raw_size, block_size, block_count, raw_crc, total_size, hdr_crc = struct.unpack_from("<8s6I", blob, 0)
    assert magic == MAGIC
    assert (zlib.crc32(blob[:28]) & 0xFFFFFFFF) == hdr_crc
    assert total_size == len(blob)
    assert raw_size == len(expected_raw)
    pos = 32
    rebuilt = bytearray()
    for _ in range(block_count):
        clen, = struct.unpack_from("<I", blob, pos)
        pos += 4
        want = min(block_size, raw_size - len(rebuilt))
        rebuilt.extend(lz4.block.decompress(blob[pos : pos + clen], uncompressed_size=want))
        pos += clen
    assert pos == total_size
    assert bytes(rebuilt) == expected_raw
    assert (zlib.crc32(rebuilt) & 0xFFFFFFFF) == raw_crc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-v2", type=Path, default=Path("/mnt/d/kShimC4sheng-iPadOS/out/boot_a-kshim-3os-v2.img"))
    ap.add_argument("--raw-fat32", type=Path, default=None)
    ap.add_argument("--out", type=Path, default=Path("/mnt/d/kShimC4sheng-iPadOS/out/work/ipados-fat32-136m.kshmlz4"))
    args = ap.parse_args()

    raw = args.raw_fat32.read_bytes() if args.raw_fat32 else extract_fat32_from_v2(args.from_v2)
    blob = compress_kshmlz41(raw)
    verify_kshmlz41(blob, raw)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(blob)
    print(f"PASS: {args.out} ({len(blob)} B = {len(blob)/1048576:.2f} MiB), raw={len(raw)} B sha256={sha256_bytes(raw)}")


if __name__ == "__main__":
    main()
