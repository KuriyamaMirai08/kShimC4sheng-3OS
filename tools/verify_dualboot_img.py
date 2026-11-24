#!/usr/bin/env python3
"""9-gate static verifier for single-slot boot_a dual-boot images (Android + iPadOS 27).

Simulates kShimC's manifest validation, dt_handoff_reservations(), and initrd_resolve()
against real stock boot_a, sheng-abl.dtb, and vendor_boot_b DTBs without touching
any device.
"""

import argparse
import hashlib
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "packer"))

import bootimg  # noqa: E402
import extract  # noqa: E402
import fdt  # noqa: E402
import manifest  # noqa: E402
import repack_fat32  # noqa: E402

PARTITION_SIZE = 201326592  # 192 MiB
AVB_RESERVE = 64 * 1024
LIMIT = PARTITION_SIZE - AVB_RESERVE
INITRD_WINDOW_ALIGN = 0x40000000  # 1 GiB
INITRD_WINDOW_MAX = 0x800000000   # 32 GiB
LINUX_BASE_SIM = 0xA8000000


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def align_up(v: int, a: int) -> int:
    return (v + a - 1) & ~(a - 1)


def find_node(tree: fdt.Fdt, path: str):
    cur = tree.root
    if path == "/":
        return cur
    for part in [p for p in path.split("/") if p]:
        nxt = None
        for ch in cur.children:
            if ch.name == part:
                nxt = ch
                break
        if nxt is None:
            return None
        cur = nxt
    return cur


def get_prop(node, name: str, default=None):
    for k, v in node.props:
        if k == name:
            return v
    return default


def has_prop(node, name: str) -> bool:
    return any(k == name for k, _ in node.props)


def u32_prop(node, name: str, default=None):
    raw = get_prop(node, name)
    if raw is None:
        return default
    if len(raw) != 4:
        raise ValueError(f"{node.name}/{name} len={len(raw)} != 4")
    return struct.unpack(">I", raw)[0]


def collect_reservations(blob: bytes):
    """Mirror handoff_reservations.c:collect()."""
    tree = fdt.Fdt.parse(blob)
    items = []
    # 1. FDT mem_rsvmap
    off_rsv = struct.unpack_from(">I", blob, 16)[0]
    pos = off_rsv
    while True:
        addr, sz = struct.unpack_from(">QQ", blob, pos)
        if addr == 0 and sz == 0:
            break
        if sz == 0 or addr + sz < addr:
            raise ValueError("bad mem_rsvmap entry")
        items.append({"base": addr, "size": sz, "nomap": False, "reusable": False, "name": "mem_rsv"})
        pos += 16

    rm = find_node(tree, "/reserved-memory")
    if rm is None:
        return items
    st = get_prop(rm, "status")
    if st is not None and st.rstrip(b"\x00") not in (b"okay", b"ok"):
        return items

    ac = u32_prop(tree.root, "#address-cells", 2)
    sc = u32_prop(tree.root, "#size-cells", 1)
    rac = u32_prop(rm, "#address-cells")
    rsc = u32_prop(rm, "#size-cells")
    if rac != ac or rsc != sc or rac not in (1, 2) or rsc not in (1, 2):
        raise ValueError(f"reserved-memory cells mismatch root({ac},{sc}) vs rm({rac},{rsc})")
    if get_prop(rm, "ranges") != b"":
        raise ValueError("reserved-memory ranges is not empty")

    cell_bytes = 4 * (rac + rsc)
    for ch in rm.children:
        cst = get_prop(ch, "status")
        if cst is not None and cst.rstrip(b"\x00") not in (b"okay", b"ok"):
            continue
        reg = get_prop(ch, "reg")
        if reg is None:
            continue
        if len(reg) == 0 or len(reg) % cell_bytes != 0:
            raise ValueError(f"bad reg len in /reserved-memory/{ch.name}")
        nomap = has_prop(ch, "no-map")
        reusable = has_prop(ch, "reusable")
        if nomap and reusable:
            raise ValueError(f"both no-map and reusable in {ch.name}")
        for off in range(0, len(reg), cell_bytes):
            b = int.from_bytes(reg[off : off + 4 * rac], "big")
            s = int.from_bytes(reg[off + 4 * rac : off + cell_bytes], "big")
            if s == 0:
                continue
            if b + s < b:
                raise ValueError(f"overflow in {ch.name}")
            items.append({"base": b, "size": s, "nomap": nomap, "reusable": reusable, "name": ch.name})
    return items


def simulate_merge_reservations(runtime_blob: bytes, payload_blob: bytes):
    """Mirror handoff_reservations.c:merge_reservations()."""
    src = collect_reservations(runtime_blob)
    dst = collect_reservations(payload_blob)
    for want_nomap in (False, True):
        for s in src:
            if s["nomap"] != want_nomap:
                continue
            cursor = s["base"]
            end = s["base"] + s["size"]
            while cursor < end:
                cover_end = cursor
                next_start = end
                for d in dst:
                    db, de = d["base"], d["base"] + d["size"]
                    if de <= cursor or db >= end:
                        continue
                    if d["reusable"] or (s["nomap"] and not d["nomap"]):
                        raise ValueError(
                            f"merge_reservations conflict: src={s['name']}({s['base']:#x}+{s['size']:#x}, nomap={s['nomap']}) "
                            f"vs dst={d['name']}({db:#x}+{d['size']:#x}, nomap={d['nomap']}, reusable={d['reusable']})"
                        )
                    if db <= cursor:
                        cover_end = max(cover_end, de)
                    elif db < next_start:
                        next_start = db
                if cover_end > cursor:
                    cursor = min(cover_end, end)
                    continue
                if len(dst) >= 256:
                    raise ValueError("DST_CAP (256) exceeded")
                dst.append({"base": cursor, "size": next_start - cursor, "nomap": s["nomap"], "reusable": False, "name": "kshim-gap"})
                cursor = next_start
    return dst


def extract_all_fdts(blob: bytes):
    """Extract all valid FDTs from a vendor_boot image."""
    fdts = []
    pos = 0
    while True:
        idx = blob.find(b"\xd0\x0d\xfe\xed", pos)
        if idx < 0:
            break
        if idx + 8 <= len(blob):
            tot = struct.unpack_from(">I", blob, idx + 4)[0]
            if 0x40 <= tot <= len(blob) - idx:
                try:
                    fdt.Fdt.parse(blob[idx : idx + tot])
                    fdts.append((idx, blob[idx : idx + tot]))
                    pos = idx + tot
                    continue
                except Exception:
                    pass
        pos = idx + 4
    return fdts


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--boot", required=True)
    ap.add_argument("--stock-boot", required=True)
    ap.add_argument("--unixnu-boot", required=True)
    ap.add_argument("--vendor-boot", required=True)
    ap.add_argument("--runtime", required=True)
    ap.add_argument("--fat32", default=os.path.join(ROOT, "out", "work", "ipados-fat32-136m.img"))
    args = ap.parse_args()

    results = []

    def gate(num: int, name: str, fn):
        try:
            detail = fn()
            results.append((num, name, "PASS", detail))
            print(f"[PASS] Gate {num}: {name} -- {detail}")
        except Exception as e:
            results.append((num, name, "FAIL", str(e)))
            print(f"[FAIL] Gate {num}: {name} -- {e}")

    with open(args.boot, "rb") as f:
        boot_raw = f.read()
    with open(args.stock_boot, "rb") as f:
        stock_raw = f.read()
    with open(args.unixnu_boot, "rb") as f:
        unixnu_raw = f.read()
    with open(args.vendor_boot, "rb") as f:
        vboot_raw = f.read()
    with open(args.runtime, "rb") as f:
        runtime_raw = f.read()

    boot = bootimg.parse(boot_raw)
    stock = bootimg.parse(stock_raw)
    unixnu = bootimg.parse(unixnu_raw)
    packed = boot["kernel"]
    m = manifest.parse(packed)

    # Gate 1
    def g1():
        assert boot["version"] == 4, f"version {boot['version']} != 4"
        assert boot["page_size"] == 4096, f"page_size {boot['page_size']}"
        assert boot["header_size"] == 1584, f"header_size {boot['header_size']}"
        assert boot["ramdisk_size"] == 0, f"ramdisk_size {boot['ramdisk_size']}"
        assert boot["signature_size"] == 0, f"signature_size {boot['signature_size']}"
        assert len(boot_raw) % 4096 == 0, "not 4K aligned"
        assert len(boot_raw) <= LIMIT, f"size {len(boot_raw)} > limit {LIMIT}"
        return f"v4, size={len(boot_raw):,} B ({len(boot_raw)/1048576:.3f} MiB), headroom={LIMIT-len(boot_raw):,} B"

    gate(1, "Boot v4 Header & Partition Budget", g1)

    # Gate 2
    def g2():
        base = m["base_image"]
        for field, off in (
            ("instructions", 0x00),
            ("image_size", 0x10),
            ("manifest_offset", 0x20),
            ("manifest_size", 0x28),
            ("shim_offset", 0x30),
        ):
            want = bootimg.u64(stock["kernel"], off)
            assert base[field] == want, f"{field}: {base[field]:#x} != {want:#x}"
        ins0, ins1 = struct.unpack_from("<II", packed, 0)
        assert (ins0 & 0xFC000000) == 0x94000000, f"ins0 {ins0:#x} not BL"
        bl_off = (ins0 & 0x03FFFFFF) << 2
        assert bl_off == m["shim_offset"], f"BL target {bl_off:#x} != shim_offset {m['shim_offset']:#x}"
        stock_ins0, stock_ins1 = struct.unpack_from("<II", stock["kernel"], 0)
        first_is_b = (stock_ins0 & 0xFC000000) == 0x14000000
        second_is_b = (stock_ins1 & 0xFC000000) == 0x14000000
        if first_is_b and not second_is_b:
            want_ins1 = 0x14000000 | ((stock_ins0 - 1) & 0x03FFFFFF)
        else:
            assert second_is_b, "stock kernel has no supported entry branch"
            want_ins1 = stock_ins1
        assert ins1 == want_ins1, f"ins1 {ins1:#x} != {want_ins1:#x}"
        return f"BaseImage backup exact; BL @0x0 -> +{bl_off:#x}"

    gate(2, "Stock BaseImage Backup & Entry BL", g2)

    # Gate 3
    def g3():
        assert m["image_size"] == len(packed)
        assert len(m["entries"]) == 5
        e0, e1, e2, e3, e4 = m["entries"]
        assert e0["type_name"] == "Linux" and "BASE_IMAGE" in e0["flag_names"]
        assert e1["type_name"] == "Linux" and "HAS_DTB" in e1["flag_names"] and e1["dtb_entry"] == 2
        assert e2["type_name"] == "DTB"
        assert e3["type_name"] == "Blob" and e3["flags"] == 0
        assert e4["type_name"] == "Shim" and e4["offset"] == m["shim_offset"]
        assert e4["size"] == len(runtime_raw) == 4 * 1024 * 1024
        assert packed[e4["offset"] : e4["offset"] + e4["size"]] == runtime_raw, "Shim bytes != runtime"
        spans = sorted((e["offset"], e["offset"] + e["size"], e["name"]) for e in m["entries"])
        for i in range(len(spans) - 1):
            assert spans[i][1] <= spans[i + 1][0], f"overlap {spans[i][2]} & {spans[i+1][2]}"
            assert spans[i][1] <= m["manifest_offset"], f"{spans[i][2]} overlaps manifest"
        assert spans[-1][1] <= m["manifest_offset"]
        return f"5 entries non-overlapping; Attached Shim SHA-256={sha256_hex(runtime_raw)[:16]}..."

    gate(3, "Manifest Geometry & Non-Overlap", g3)

    # Gate 4
    uefi_expected, trailing = extract.gunzip(unixnu["kernel"])
    fdt_hit = extract.find_fdt(trailing, at_start=True)
    orig_dtb = trailing[fdt_hit[0] : fdt_hit[0] + fdt_hit[1]]

    def g4():
        e1 = m["entries"][1]
        got = packed[e1["offset"] : e1["offset"] + e1["size"]]
        assert got == uefi_expected, "Image-01 bytes != sheng-uefi"
        assert got[0x38:0x3C] == b"ARM\x64"
        img_sz = struct.unpack_from("<Q", got, 0x10)[0]
        assert img_sz == 0x3000C0
        return f"sheng-uefi exact match ({len(got):,} B, sha256={sha256_hex(got)[:16]}...)"

    gate(4, "sheng-uefi Integrity", g4)

    # Gate 5
    e2 = m["entries"][2]
    dtb_blob = packed[e2["offset"] : e2["offset"] + e2["size"]]
    vb_fdts = extract_all_fdts(vboot_raw)

    def g5():
        tree = fdt.Fdt.parse(dtb_blob)
        assert u32_prop(tree.root, "#address-cells") == 2
        assert u32_prop(tree.root, "#size-cells") == 2
        assert tree.get("/chosen", "kshim,initrd-entry") == (3).to_bytes(4, "big")
        assert tree.get("/chosen", "linux,initrd-start") is None
        assert tree.get("/chosen", "linux,initrd-end") is None
        chosen = find_node(tree, "/chosen")
        for ch in chosen.children:
            comp = get_prop(ch, "compatible", b"")
            assert b"simple-framebuffer" not in comp, "unexpected simple-framebuffer node"
        assert len(vb_fdts) >= 1, "no FDTs found in vendor_boot_b"
        max_rsv = 0
        for idx, (off, vblob) in enumerate(vb_fdts):
            merged = simulate_merge_reservations(vblob, dtb_blob)
            max_rsv = max(max_rsv, len(merged))
            assert len(merged) < 128, f"merged reservations {len(merged)} >= 128"
        return f"DTB valid, initrd-entry=3, {len(vb_fdts)} vendor_boot DTBs merged cleanly (max {max_rsv} rsv)"

    gate(5, "sheng-dtb & dt_handoff_reservations Simulation", g5)

    # Gate 6
    def g6():
        e1, e2_m, e3, e4 = m["entries"][1], m["entries"][2], m["entries"][3], m["entries"][4]
        u_hdr = packed[e1["offset"] : e1["offset"] + 64]
        assert u_hdr[56:60] == b"ARM\x64"
        u_img_sz = max(struct.unpack_from("<Q", u_hdr, 16)[0], e1["size"])
        u_flags = struct.unpack_from("<Q", u_hdr, 24)[0]
        page_shift = (u_flags >> 1) & 0x3
        page = {1: 4096, 2: 16384}.get(page_shift, 65536)
        assert page == 65536

        k_start = LINUX_BASE_SIM + e1["offset"]
        k_end = k_start + u_img_sz
        b_start = LINUX_BASE_SIM + e3["offset"]
        b_end = b_start + e3["size"]
        chk_start = b_start & ~(page - 1)
        chk_end = align_up(b_end, page)
        assert chk_start == b_start, f"blob_start {b_start:#x} not 64K aligned"

        win_base = min(k_start, chk_start) & ~(INITRD_WINDOW_ALIGN - 1)
        win_top = max(k_end, chk_end)
        assert win_top - win_base <= INITRD_WINDOW_MAX

        protected = [
            ("Image-01", k_start, k_end),
            ("Image-02(DTB)", LINUX_BASE_SIM + e2_m["offset"], LINUX_BASE_SIM + e2_m["offset"] + e2_m["size"]),
            ("Shim+HandoffDtb", LINUX_BASE_SIM + e4["offset"], LINUX_BASE_SIM + e4["offset"] + e4["size"]),
            ("UEFI-SelfCopy", 0xA7100000, 0xA7400000),
        ]
        for label, s, e in protected:
            assert not (s < chk_end and chk_start < e), f"initrd [{chk_start:#x},{chk_end:#x}) overlaps {label} [{s:#x},{e:#x})"

        # Check against all reserved-memory ranges in sheng-abl.dtb & vendor_boot DTBs
        for rsv in collect_reservations(dtb_blob):
            rs, re = rsv["base"], rsv["base"] + rsv["size"]
            assert not (rs < chk_end and chk_start < re), f"initrd overlaps DTB rsv {rsv['name']} [{rs:#x},{re:#x})"
        for _, vblob in vb_fdts:
            for rsv in collect_reservations(vblob):
                rs, re = rsv["base"], rsv["base"] + rsv["size"]
                assert not (rs < chk_end and chk_start < re), f"initrd overlaps vendor_boot rsv {rsv['name']} [{rs:#x},{re:#x})"

        return f"chk=[{chk_start:#x},{chk_end:#x}), win={(win_top-win_base)/1048576:.1f} MiB <= 32 GiB, 0 conflicts"

    gate(6, "initrd_resolve() Exact Simulation", g6)

    # Gate 7
    def g7():
        e3 = m["entries"][3]
        fat_blob = packed[e3["offset"] : e3["offset"] + e3["size"]]
        dst = repack_fat32.parse_fat32_tree(fat_blob)
        src = repack_fat32.parse_fat32_tree(unixnu["ramdisk"])
        assert dst["bps"] == 512 and dst["spc"] == 4
        assert dst["avail_clus"] >= 65525
        assert set(dst["files"].keys()) == set(src["files"].keys())
        for p in sorted(src["files"].keys()):
            assert sha256_hex(dst["files"][p]) == sha256_hex(src["files"][p]), f"SHA-256 mismatch: {p}"
        return f"FAT32 valid (avail_clus={dst['avail_clus']}), all {len(src['files'])} files SHA-256 exact"

    gate(7, "Embedded FAT32 & 7-File SHA-256", g7)

    # Gate 8 (optional WSL fsck.fat)
    def g8():
        if not os.path.isfile(args.fat32) or not shutil.which("wsl"):
            return "SKIPPED (wsl or standalone fat32 missing)"
        wsl_path = args.fat32.replace("\\", "/")
        if len(wsl_path) >= 2 and wsl_path[1] == ":":
            wsl_path = f"/mnt/{wsl_path[0].lower()}{wsl_path[2:]}"
        chk = subprocess.run(["wsl", "which", "fsck.fat"], capture_output=True, encoding="utf-8", errors="replace")
        if chk.returncode != 0:
            return "SKIPPED (fsck.fat not installed in WSL)"
        proc = subprocess.run(["wsl", "fsck.fat", "-n", "-v", wsl_path], capture_output=True, encoding="utf-8", errors="replace")
        if proc.returncode != 0:
            raise RuntimeError(f"fsck.fat exited {proc.returncode}: {proc.stdout}\n{proc.stderr}")
        return "fsck.fat -n -v clean"

    gate(8, "Host fsck.fat Cross-Check (Optional)", g8)

    # Gate 9
    def g9():
        b_sz = os.path.getsize(args.unixnu_boot)
        v_sz = os.path.getsize(args.vendor_boot)
        assert b_sz == 185733120, f"boot_b size {b_sz} != 185733120"
        assert v_sz == 3891200, f"vendor_boot_b size {v_sz} != 3891200"
        return f"boot_b={b_sz:,} B, vendor_boot_b={v_sz:,} B intact"

    gate(9, "Untouched Baseline Verification", g9)

    failed = [r for r in results if r[2] == "FAIL"]
    if failed:
        print(f"\nSUMMARY: {len(failed)} gate(s) FAILED.")
        return 1
    print(f"\nSUMMARY: All {len(results)} gates PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
