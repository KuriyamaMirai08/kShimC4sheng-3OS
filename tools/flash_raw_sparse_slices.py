#!/usr/bin/env python3
"""Stream-flash a large raw partition image via fastboot using 256 MiB Android sparse slices.

Avoids Windows fastboot.exe std::bad_alloc on 16+ GiB raw images and skips zero blocks via FILL 0.
"""

import argparse
import os
import struct
import subprocess
import sys
import time

BLOCK = 4096
ZERO_BLOCK = b"\x00" * BLOCK
SPARSE_MAGIC = 0xED26FF3A
CHUNK_RAW = 0xCAC1
CHUNK_FILL = 0xCAC2
CHUNK_DONT_CARE = 0xCAC3
MAX_RAW_BLOCKS = 4096  # 16 MiB per RAW chunk


def build_sparse_slice(raw_f, start_blk: int, slice_blks: int, total_blks: int, out_path: str):
    data_bytes = slice_blks * BLOCK
    raw_f.seek(start_blk * BLOCK)
    buf = raw_f.read(data_bytes)
    assert len(buf) == data_bytes, f"short read at blk {start_blk}"

    chunks = 0
    raw_blks_written = 0
    fill_blks_written = 0
    pos = 0

    with open(out_path, "wb") as out:
        out.write(b"\x00" * 28)
        if start_blk > 0:
            out.write(struct.pack("<HHII", CHUNK_DONT_CARE, 0, start_blk, 12))
            chunks += 1
            pos += start_blk

        # Walk 4 KiB blocks and coalesce consecutive zero / non-zero runs
        i = 0
        mv = memoryview(buf)
        while i < slice_blks:
            off = i * BLOCK
            if mv[off : off + BLOCK] == ZERO_BLOCK:
                j = i + 1
                while j < slice_blks and mv[j * BLOCK : (j + 1) * BLOCK] == ZERO_BLOCK:
                    j += 1
                cnt = j - i
                out.write(struct.pack("<HHII", CHUNK_FILL, 0, cnt, 16) + b"\x00\x00\x00\x00")
                chunks += 1
                fill_blks_written += cnt
                pos += cnt
                i = j
            else:
                j = i + 1
                while j < slice_blks and (j - i) < MAX_RAW_BLOCKS and mv[j * BLOCK : (j + 1) * BLOCK] != ZERO_BLOCK:
                    j += 1
                cnt = j - i
                out.write(struct.pack("<HHII", CHUNK_RAW, 0, cnt, 12 + cnt * BLOCK))
                out.write(mv[off : j * BLOCK])
                chunks += 1
                raw_blks_written += cnt
                pos += cnt
                i = j

        tail_blks = total_blks - (start_blk + slice_blks)
        if tail_blks > 0:
            out.write(struct.pack("<HHII", CHUNK_DONT_CARE, 0, tail_blks, 12))
            chunks += 1
            pos += tail_blks

        assert pos == total_blks, f"pos {pos} != {total_blks}"
        out.seek(0)
        out.write(struct.pack("<IHHHHIIII", SPARSE_MAGIC, 1, 0, 28, 12, BLOCK, total_blks, chunks, 0))

    return raw_blks_written, fill_blks_written, chunks


def verify_sparse_slice(sparse_path: str, raw_f, start_blk: int, slice_blks: int, total_blks: int):
    with open(sparse_path, "rb") as sf:
        magic, maj, minv, fhs, chs, blksz, tot, nchunks, _ = struct.unpack("<IHHHHIIII", sf.read(28))
        assert magic == SPARSE_MAGIC and blksz == BLOCK and tot == total_blks
        pos = 0
        raw_f.seek(start_blk * BLOCK)
        expected = raw_f.read(slice_blks * BLOCK)
        rebuilt = bytearray(slice_blks * BLOCK)
        for _ in range(nchunks):
            ctype, _, cblks, csz = struct.unpack("<HHII", sf.read(12))
            if ctype == CHUNK_DONT_CARE:
                assert csz == 12
            elif ctype == CHUNK_FILL:
                assert csz == 16
                fv = sf.read(4)
                assert fv == b"\x00\x00\x00\x00"
                assert start_blk <= pos and pos + cblks <= start_blk + slice_blks
            elif ctype == CHUNK_RAW:
                assert csz == 12 + cblks * BLOCK
                assert start_blk <= pos and pos + cblks <= start_blk + slice_blks
                data = sf.read(cblks * BLOCK)
                rel = (pos - start_blk) * BLOCK
                rebuilt[rel : rel + cblks * BLOCK] = data
            else:
                raise AssertionError(f"bad chunk {ctype:#x}")
            pos += cblks
        assert pos == total_blks
        assert bytes(rebuilt) == expected, "slice verification mismatch!"


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--raw", required=True)
    ap.add_argument("--partition", required=True)
    ap.add_argument("--serial", required=True)
    ap.add_argument("--fastboot", required=True)
    ap.add_argument("--slice-mib", type=int, default=256)
    ap.add_argument("--tmp", required=True)
    ap.add_argument("--dry-run-first", action="store_true")
    args = ap.parse_args()

    raw_size = os.path.getsize(args.raw)
    assert raw_size % BLOCK == 0, f"raw size {raw_size} not 4K-aligned"
    total_blks = raw_size // BLOCK
    slice_blks = (args.slice_mib * 1024 * 1024) // BLOCK
    num_slices = (total_blks + slice_blks - 1) // slice_blks

    total_raw_mib = 0.0
    total_fill_mib = 0.0
    t_all = time.time()

    with open(args.raw, "rb") as rf:
        if args.dry_run_first:
            build_sparse_slice(rf, 0, min(slice_blks, total_blks), total_blks, args.tmp)
            verify_sparse_slice(args.tmp, rf, 0, min(slice_blks, total_blks), total_blks)
            print(f"DRY-RUN SLICE 0 PASS: {args.tmp} matches {args.raw} byte-for-byte!")
            return 0

        for idx in range(num_slices):
            start_blk = idx * slice_blks
            cur_blks = min(slice_blks, total_blks - start_blk)
            t0 = time.time()
            r_blks, f_blks, chunks = build_sparse_slice(rf, start_blk, cur_blks, total_blks, args.tmp)
            if idx == 0:
                verify_sparse_slice(args.tmp, rf, start_blk, cur_blks, total_blks)

            r_mib = r_blks * BLOCK / 1048576.0
            f_mib = f_blks * BLOCK / 1048576.0
            total_raw_mib += r_mib
            total_fill_mib += f_mib

            proc = subprocess.run(
                [args.fastboot, "-s", args.serial, "flash", args.partition, args.tmp],
                capture_output=True,
                text=True,
            )
            if proc.returncode != 0:
                sys.stderr.write(proc.stdout)
                sys.stderr.write(proc.stderr)
                raise SystemExit(f"fastboot flash failed on slice {idx+1}/{num_slices} (code {proc.returncode})")
            dt = time.time() - t0
            print(
                f"[slice {idx+1:02d}/{num_slices}] raw={r_mib:6.1f} MiB fill0={f_mib:6.1f} MiB chunks={chunks} OK ({dt:.2f}s)",
                flush=True,
            )

    print(
        f"ALL {num_slices} SLICES FLASHED OK in {time.time()-t_all:.1f}s: "
        f"raw={total_raw_mib:.1f} MiB, fill0={total_fill_mib:.1f} MiB"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
