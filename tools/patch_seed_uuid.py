#!/usr/bin/env python3
"""Patch APFS APSB volume UUID in a DBRDISK1 seed and recompute Fletcher-64 checksums."""

import argparse
import hashlib
import struct
import sys
import uuid
from pathlib import Path

BLOCK = 4096
TARGET_UUID = "2A5E9027-D9E1-40F9-983C-41BD3F1994F5"


def fletcher64(block: bytes) -> tuple[int, int]:
    low = high = 0
    for word, in struct.iter_unpack("<I", block[8:]):
        low += word
        high += low
    mod = 0xFFFFFFFF
    a = mod - (low + high) % mod
    b = mod - (low + a) % mod
    return a, b


def valid_object(block: bytes, kind: int) -> bool:
    if block is None or len(block) != BLOCK:
        return False
    if (struct.unpack_from("<I", block, 24)[0] & 0xFFFF) != kind:
        return False
    return fletcher64(block) == struct.unpack_from("<II", block, 0)


def packed_pages(blob: bytes):
    magic, version, block, logical, count = struct.unpack_from("<8sIIQQ", blob, 0)
    assert (magic, version, block) == (b"DBRDISK1", 1, BLOCK)
    cursor = 32 + count * 16
    pages = {}
    for off, size in struct.iter_unpack("<QQ", blob[32:cursor]):
        assert size > 0 and (off | size) % BLOCK == 0 and off + size <= logical
        for delta in range(0, size, BLOCK):
            pages[(off + delta) // BLOCK] = blob[cursor + delta : cursor + delta + BLOCK]
        cursor += size
    assert cursor == len(blob)
    return logical, pages


def verify_space_manager(logical: int, pages: dict):
    def nx_valid(blk):
        return valid_object(blk, 1) and blk[32:36] == b"NXSB" and struct.unpack_from("<IQ", blk, 36) == (BLOCK, logical // BLOCK)

    start = pages.get(0)
    assert nx_valid(start), "invalid NXSB"
    desc_count, data_count, desc_base, data_base = struct.unpack_from("<IIqq", start, 104)
    cands = [(struct.unpack_from("<Q", p, 16)[0], lba, p) for lba, p in pages.items() if desc_base <= lba < desc_base + desc_count and nx_valid(p)]
    assert cands, "no checkpoint superblock"
    xid, lba, cp = max(cands)
    assert cp[72:88] == start[72:88] and cp[104:128] == start[104:128]
    index, length, _, _, space_oid = struct.unpack_from("<IIIIQ", cp, 136)
    matches = []
    for delta in range(length - 1):
        mp = pages.get(desc_base + (index + delta) % desc_count)
        assert valid_object(mp, 12) and struct.unpack_from("<Q", mp, 16)[0] == xid
        flags, cnt = struct.unpack_from("<II", mp, 32)
        for dtype, _, size, _, fs, oid, addr in struct.iter_unpack("<IIIIQQQ", mp[40 : 40 + 40 * cnt]):
            if (dtype & 0xFFFF) == 5 and oid == space_oid and fs == 0:
                matches.append(pages[addr])
    assert len(matches) == 1 and valid_object(matches[0], 5)


def single_volume_uuid(pages: dict) -> str:
    ids = set()
    for p in pages.values():
        if p[32:36] == b"APSB":
            assert valid_object(p, 13), "APSB Fletcher-64 invalid!"
            ids.add(str(uuid.UUID(bytes=bytes(p[240:256]))).upper())
    assert len(ids) == 1, f"expected 1 APSB UUID, got {ids}"
    return ids.pop()


def compute_seed_hash(logical: int, pages: dict) -> str:
    h = hashlib.sha256()
    h.update(b"SHNGSEED2" + struct.pack("<Q", logical))
    for idx in sorted(pages):
        pg = pages[idx]
        if any(pg):
            h.update(struct.pack("<Q", idx))
            h.update(pg)
    return h.hexdigest()


def patch_seed(src: Path, dst: Path, target_uuid_str: str):
    target_bytes = uuid.UUID(target_uuid_str).bytes
    blob = bytearray(src.read_bytes())
    magic, ver, blk, logical, count = struct.unpack_from("<8sIIQQ", blob, 0)
    assert (magic, ver, blk) == (b"DBRDISK1", 1, BLOCK)

    cursor = 32 + count * 16
    patched = 0
    for off, size in struct.iter_unpack("<QQ", blob[32:cursor]):
        for delta in range(0, size, BLOCK):
            at = cursor + delta
            pg = bytearray(blob[at : at + BLOCK])
            if pg[32:36] == b"APSB" and (struct.unpack_from("<I", pg, 24)[0] & 0xFFFF) == 13:
                assert valid_object(bytes(pg), 13), f"Pre-patch APSB Fletcher-64 invalid at off={off+delta}"
                pg[240:256] = target_bytes
                a, b = fletcher64(bytes(pg))
                struct.pack_into("<II", pg, 0, a, b)
                assert valid_object(bytes(pg), 13), f"Post-patch APSB Fletcher-64 invalid at off={off+delta}"
                blob[at : at + BLOCK] = pg
                patched += 1
        cursor += size

    assert patched >= 1, "No APSB blocks found to patch"
    out_bytes = bytes(blob)
    out_logical, pages = packed_pages(out_bytes)
    assert out_logical == logical
    verify_space_manager(out_logical, pages)
    vol_uuid = single_volume_uuid(pages)
    assert vol_uuid == target_uuid_str.upper()
    seed_hash = compute_seed_hash(out_logical, pages)

    dst.write_bytes(out_bytes)
    print(
        f"Patched {src.name} -> {dst.name}: APSB_blocks={patched}, "
        f"logical={logical//(1<<30)} GiB, vol_uuid={vol_uuid}, "
        f"packed_sha256={hashlib.sha256(out_bytes).hexdigest()}, seedHash={seed_hash}"
    )
    return seed_hash


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--in", dest="src", type=Path, required=True)
    ap.add_argument("--out", dest="dst", type=Path, required=True)
    ap.add_argument("--uuid", default=TARGET_UUID)
    args = ap.parse_args()
    patch_seed(args.src, args.dst, args.uuid)


if __name__ == "__main__":
    sys.exit(main())
