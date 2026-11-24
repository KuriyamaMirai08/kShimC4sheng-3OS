#!/usr/bin/env python3
"""Build an Android sparse image for WALv2 initialization on a resized darwin partition.

Safety & stale-data protection:
  - 0 .. storeFirst - 1041: DONT_CARE (preserves SHNGROOTv2 read-only OS untouched)
  - storeFirst - 1040 .. storeFirst - 1: FILL 0 (16-block Ledger + 1024-block guard)
  - storeFirst + 0 .. storeFirst + 1 + 2*snapshotBlocks: RAW for non-zero blocks, FILL 0 for the rest of Root/SnapshotA/SnapshotB
  - storeFirst + baseStart .. storeFirst + walStart - 1: DONT_CARE (base blocks; bitmap is 0 and verifyBase=false)
  - storeFirst + walStart .. storeFirst + window - 1: FILL 0 (entire 16,380-block WAL ring cleared of any old data)
  - storeFirst + window .. partitionBlocks - 1: DONT_CARE (1024 MiB log-reserve)
"""

import argparse
import hashlib
import math
import os
import struct
import sys

BLOCK = 4096
SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW = 0xCAC1
CHUNK_FILL = 0xCAC2
CHUNK_DONT_CARE = 0xCAC3
SHNGROOT_END_BLOCKS = 3846144  # 14.671875 GiB (15,753,805,824 B)
LEDGER_AND_GUARD_BLOCKS = 1024 + 16
LOG_RESERVE_BLOCKS = 262144  # 1024 MiB default log-reserve
WAL_BLOCKS = 16380


def snapshot_blocks_for(pages: int) -> int:
    bitmap = (pages + 8 * BLOCK - 1) // (8 * BLOCK)
    checksums = (pages + 1023) // 1024
    return 1 + bitmap + checksums


def write_chunk(f, ctype: int, blocks: int, data: bytes = b""):
    if blocks <= 0:
        return 0
    if ctype == CHUNK_DONT_CARE:
        f.write(struct.pack("<HHII", ctype, 0, blocks, 12))
        return 1
    if ctype == CHUNK_FILL:
        f.write(struct.pack("<HHII", ctype, 0, blocks, 16) + b"\x00\x00\x00\x00")
        return 1
    if ctype == CHUNK_RAW:
        assert len(data) == blocks * BLOCK
        f.write(struct.pack("<HHII", ctype, 0, blocks, 12 + len(data)) + data)
        return 1
    raise ValueError(f"Unknown chunk type {ctype:#x}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--blocks-bin", required=True, help="Output from gen_walv2_init_any")
    ap.add_argument("--kpages", type=int, required=True, help="APFS Data page count (4 KiB pages)")
    ap.add_argument("--partition-blocks", type=int, required=True, help="Target darwin partition 4 KiB block count")
    ap.add_argument("--out", required=True, help="Output Android sparse image path")
    args = ap.parse_args()

    with open(args.blocks_bin, "rb") as f:
        window, n = struct.unpack("<QQ", f.read(16))
        blocks = {}
        for _ in range(n):
            idx, = struct.unpack("<Q", f.read(8))
            blocks[idx] = f.read(BLOCK)

    snap_blocks = snapshot_blocks_for(args.kpages)
    base_start = 2 + 2 * snap_blocks
    wal_start = base_start + args.kpages
    expected_window = wal_start + WAL_BLOCKS
    if window != expected_window:
        raise SystemExit(f"error: window {window} != expected {expected_window}")

    store_first = args.partition_blocks - LOG_RESERVE_BLOCKS - window
    min_allowed = SHNGROOT_END_BLOCKS + LEDGER_AND_GUARD_BLOCKS
    if store_first < min_allowed:
        raise SystemExit(
            f"error: store_first={store_first} < min_allowed={min_allowed} "
            f"(overlaps SHNGROOTv2 + guard + ledger!)"
        )

    ledger_first = store_first - LEDGER_AND_GUARD_BLOCKS
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)

    chunks = 0
    pos = 0
    with open(args.out, "wb") as out:
        out.write(b"\x00" * 28)

        # 1. Preserve SHNGROOTv2 + unused gap before ledger
        chunks += write_chunk(out, CHUNK_DONT_CARE, ledger_first - pos)
        pos = ledger_first

        # 2. Zero 16-block ledger + 1024-block guard
        chunks += write_chunk(out, CHUNK_FILL, LEDGER_AND_GUARD_BLOCKS)
        pos += LEDGER_AND_GUARD_BLOCKS
        assert pos == store_first

        # 3. Root slots (0, 1) + SnapshotA + SnapshotB: RAW for non-zero, FILL 0 for zero
        meta_end = base_start
        cur = 0
        while cur < meta_end:
            if cur in blocks:
                run_end = cur + 1
                raw_buf = bytearray(blocks[cur])
                while run_end < meta_end and run_end in blocks and (run_end - cur) < 4096:
                    raw_buf += blocks[run_end]
                    run_end += 1
                chunks += write_chunk(out, CHUNK_RAW, run_end - cur, bytes(raw_buf))
                cur = run_end
            else:
                run_end = cur + 1
                while run_end < meta_end and run_end not in blocks:
                    run_end += 1
                chunks += write_chunk(out, CHUNK_FILL, run_end - cur)
                cur = run_end
        pos += meta_end

        # Verify all emitted non-zero blocks were inside [0, meta_end)
        for idx in blocks:
            if idx >= meta_end:
                raise SystemExit(f"error: unexpected non-zero block at idx={idx} >= meta_end={meta_end}")

        # 4. Base overlay pages (unused when present == 0 and verifyBase=false)
        chunks += write_chunk(out, CHUNK_DONT_CARE, args.kpages)
        pos += args.kpages

        # 5. Clear entire WAL ring (16,380 blocks) to zero so no stale data survives
        chunks += write_chunk(out, CHUNK_FILL, WAL_BLOCKS)
        pos += WAL_BLOCKS
        assert pos == store_first + window

        # 6. Log-reserve at tail of darwin partition
        chunks += write_chunk(out, CHUNK_DONT_CARE, args.partition_blocks - pos)
        pos = args.partition_blocks

        out.seek(0)
        out.write(struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12, BLOCK, args.partition_blocks, chunks, 0))

    with open(args.out, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    print(
        f"WALv2 sparse OK: {args.out} | partition_blks={args.partition_blocks} "
        f"store_first={store_first} (margin={store_first - min_allowed} blks = {(store_first - min_allowed)*4/1024:.2f} MiB) "
        f"window={window} chunks={chunks} sha256={sha}"
    )


if __name__ == "__main__":
    main()
