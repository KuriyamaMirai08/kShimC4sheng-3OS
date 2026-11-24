/* SPDX-License-Identifier: GPL-3.0-only */
#include "initrd.h"

#include <kshim_mmu.h>
#include <limits.h>
#include <string.h>

#include "src/libs/lz4/lz4.h"

#define INITRD_IMAGE_MAGIC   0x644d5241u          /* ARM64 "ARM\x64" */
#define INITRD_HEADER_BYTES  64u
#define INITRD_PAGE_4K       4096ull
#define INITRD_PAGE_16K      16384ull
#define INITRD_PAGE_64K      65536ull
#define INITRD_WINDOW_ALIGN  0x40000000ull        /* 1 GiB */
#define INITRD_WINDOW_MAX    0x800000000ull       /* 32 GiB */
#define INITRD_RESERVATION_MAX 128u

/* Unaligned-safe little-endian reads: the packed Image header is only
 * guaranteed byte-accessible. */
static uint32_t initrd_le32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t initrd_le64(const uint8_t *p)
{
    return (uint64_t)initrd_le32(p) | ((uint64_t)initrd_le32(p + 4) << 32);
}

/* A root child is a memory node when it carries a valid device_type = "memory"
 * or, only when device_type is absent, when its name is "memory" / "memory@". */
static bool initrd_memory_node(const void *fdt, int node)
{
    int len;
    const void *device_type = fdt_getprop(fdt, node, "device_type", &len);
    const char *name;

    if (device_type && len >= 0) {
        const char *s = dt_string(fdt, node, "device_type");
        return s && strcmp(s, "memory") == 0;
    }
    name = fdt_get_name(fdt, node, NULL);
    if (!name)
        return false;
    return strcmp(name, "memory") == 0 || strncmp(name, "memory@", 7) == 0;
}

/* Root #address-cells / #size-cells, only 1 or 2 supported; the DT defaults
 * are 2/1. -1 marks a malformed or unsupported value. */
static int initrd_root_cells(const void *fdt, const char *property, int fallback)
{
    int len;
    uint32_t value;
    const fdt32_t *p = fdt_getprop(fdt, 0, property, &len);

    if (!p)
        return len == -FDT_ERR_NOTFOUND ? fallback : -1;
    if (len != 4)
        return -1;
    value = fdt32_to_cpu(*p);
    if (value < 1 || value > 2)
        return -1;
    return (int)value;
}

/* True when [start, end) is fully covered by the union of the enabled root
 * memory nodes; adjacent or overlapping banks are accepted, holes are not.
 * The scan advances the covered end, so it is bounded by the bank ends. */
static bool initrd_ram_covers(const void *fdt, uint64_t start, uint64_t end)
{
    int address_cells = initrd_root_cells(fdt, "#address-cells", 2);
    int size_cells = initrd_root_cells(fdt, "#size-cells", 1);
    int tuple;
    uint64_t cursor = start;

    if (address_cells < 1 || size_cells < 1)
        return false;
    tuple = address_cells + size_cells;

    for (;;) {
        uint64_t best = cursor;
        bool found = false;
        int node;

        fdt_for_each_subnode(node, fdt, 0) {
            int reg_len;
            const fdt32_t *reg;
            unsigned banks, i;

            if (!initrd_memory_node(fdt, node) || !dt_available(fdt, node))
                continue;
            reg = fdt_getprop(fdt, node, "reg", &reg_len);
            if (!reg) {
                if (reg_len == -FDT_ERR_NOTFOUND)
                    continue;
                return false;
            }
            if (reg_len <= 0 || reg_len % (4 * tuple))
                return false;
            banks = (unsigned)reg_len / (unsigned)(4 * tuple);
            for (i = 0; i < banks; ++i) {
                struct dt_range range;
                uint64_t range_end;

                if (dt_reg(fdt, node, i, &range))
                    return false;
                range_end = range.base + range.size;
                if (range.base <= cursor && cursor < range_end &&
                    (!found || range_end > best)) {
                    best = range_end;
                    found = true;
                }
            }
        }
        if (!found)
            return false;
        if (best >= end)
            return true;
        cursor = best;
    }
}

/* Firmware header reservations and every reserved-memory range (no-map or
 * not) conflict with the initrd interval. */
static bool initrd_reservations_conflict(const void *fdt, uint64_t start,
                                         uint64_t end)
{
    struct dt_range ranges[INITRD_RESERVATION_MAX];
    bool no_map[INITRD_RESERVATION_MAX];
    size_t count = 0;
    size_t i;

    if (dt_reservations(fdt, ranges, no_map, INITRD_RESERVATION_MAX, &count))
        return true;
    for (i = 0; i < count; ++i) {
        uint64_t range_end = ranges[i].base + ranges[i].size;
        if (ranges[i].base < end && start < range_end)
            return true;
    }
    return false;
}

/* Validate an optional caller span: empty spans are skipped, overflowing
 * non-empty spans are malformed. */
static int initrd_span(const struct dt_range *range, struct dt_range *out)
{
    if (range->size == 0) {
        out->base = 0;
        out->size = 0;
        return 0;
    }
    if (range->base > UINT64_MAX - range->size)
        return -1;
    *out = *range;
    return 0;
}

static bool initrd_overlaps(uint64_t start, uint64_t end, uint64_t base,
                            uint64_t size)
{
    if (size == 0)
        return false;
    return base < end && start < base + size;
}

/* Resolve and fully validate the present /chosen/kshim,initrd-entry reference.
 * On success *out_start / *out_end hold the exact BLOB byte interval; the
 * page-rounded copy window is used only for the internal safety checks below.
 * Reads no BLOB bytes and mutates nothing. */
static int initrd_resolve(const void *fdt, size_t capacity,
                          const struct kshim_initrd_context *ctx,
                          const fdt32_t *reference, uint64_t *out_start,
                          uint64_t *out_end)
{
    const MANIFEST_ENTRY *blob_entry;
    const uint8_t *header;
    uint64_t payload, blob_start, blob_end, page;
    uint64_t kernel_start, kernel_end, kernel_size;
    uint64_t source_start, checked_start, checked_end;
    struct dt_range span;
    uint32_t index;

    if (!ctx || !ctx->entries || !ctx->entry || !ctx->header)
        return -FDT_ERR_BADVALUE;
    payload = ctx->packed_payload_size;
    if (ctx->packed_base > UINT64_MAX - payload)
        return -FDT_ERR_BADVALUE;

    index = fdt32_to_cpu(*reference);
    if (index >= ctx->entry_count)
        return -FDT_ERR_BADVALUE;
    blob_entry = ctx->entries[index];
    if (!blob_entry ||
        blob_entry->type != MF_ENTRY_TYPE_BLOB ||
        blob_entry->flags != 0 || blob_entry->size == 0 ||
        blob_entry->entry_offset != 0 ||
        blob_entry->device_tree_blob_entry != 0 ||
        blob_entry->load_address != 0 || blob_entry->copy_size_max != 0)
        return -FDT_ERR_BADVALUE;
    if (blob_entry->offset > payload ||
        blob_entry->size > payload - blob_entry->offset)
        return -FDT_ERR_BADVALUE;
    blob_start = ctx->packed_base + blob_entry->offset;
    blob_end = blob_start + blob_entry->size;

    /* Selected Linux Image: the selected entry must be a Linux entry with a
     * 64-byte header fully inside the selected size and the packed source
     * inside the payload before the manifest. */
    if (ctx->entry->type != MF_ENTRY_TYPE_LINUX ||
        ctx->entry->size < INITRD_HEADER_BYTES ||
        ctx->entry->offset > payload ||
        ctx->entry->size > payload - ctx->entry->offset)
        return -FDT_ERR_BADVALUE;

    header = (const uint8_t *)ctx->header;
    kernel_size = initrd_le64(header + 16); /* effective image_size */
    if (initrd_le32(header + 56) != INITRD_IMAGE_MAGIC || kernel_size == 0)
        return -FDT_ERR_BADVALUE;
    if (kernel_size < ctx->entry->size)
        kernel_size = ctx->entry->size;
    if (ctx->entry->flags & MF_ENTRY_FLAG_COPY)
        kernel_start = ctx->entry->load_address;
    else
        kernel_start = ctx->packed_base + ctx->entry->offset;
    if (kernel_start > UINT64_MAX - kernel_size)
        return -FDT_ERR_BADVALUE;
    kernel_end = kernel_start + kernel_size;
    source_start = ctx->packed_base + ctx->entry->offset;

    switch ((initrd_le64(header + 24) >> 1) & 0x3) {
    case 1:
        page = INITRD_PAGE_4K;
        break;
    case 2:
        page = INITRD_PAGE_16K;
        break;
    default: /* 0 is unknown -> conservative 64 KiB, as is 3 */
        page = INITRD_PAGE_64K;
        break;
    }
    /* Page-rounded copy window, used only for the safety checks below; the
     * advertised interval stays byte-exact. */
    checked_start = blob_start & ~(page - 1);
    if (blob_end > UINT64_MAX - (page - 1))
        return -FDT_ERR_BADVALUE;
    checked_end = (blob_end + (page - 1)) & ~(page - 1);

    /* ARM64 copy window: the rounded window together with the physical kernel
     * range (the final COPY kernel address already computed above) must fit in
     * one 1 GiB-aligned INITRD_WINDOW_MAX span. */
    {
        uint64_t window_base, window_top;

        window_base = (kernel_start < checked_start ? kernel_start
                                                    : checked_start) &
                      ~(INITRD_WINDOW_ALIGN - 1);
        window_top = kernel_end > checked_end ? kernel_end : checked_end;
        if (window_top - window_base > INITRD_WINDOW_MAX)
            return -FDT_ERR_BADVALUE;
    }

    /* Usable RAM coverage and every conflicting reservation. */
    if (!initrd_ram_covers(fdt, checked_start, checked_end))
        return -FDT_ERR_BADVALUE;
    if (initrd_reservations_conflict(fdt, checked_start, checked_end))
        return -FDT_ERR_BADVALUE;

    /* The kernel runtime, packed source, runtime/linker, both DT sources and
     * the handoff buffer itself are protected. */
    if (initrd_overlaps(checked_start, checked_end, kernel_start, kernel_size) ||
        initrd_overlaps(checked_start, checked_end, source_start,
                        ctx->entry->size))
        return -FDT_ERR_BADVALUE;
    if (initrd_span(&ctx->runtime, &span) ||
        initrd_overlaps(checked_start, checked_end, span.base, span.size))
        return -FDT_ERR_BADVALUE;
    if (initrd_span(&ctx->runtime_fdt, &span) ||
        initrd_overlaps(checked_start, checked_end, span.base, span.size))
        return -FDT_ERR_BADVALUE;
    if (initrd_span(&ctx->payload_fdt, &span) ||
        initrd_overlaps(checked_start, checked_end, span.base, span.size))
        return -FDT_ERR_BADVALUE;
    {
        struct dt_range handoff = {
            .base = (uint64_t)(uintptr_t)fdt,
            .size = (uint64_t)capacity,
        };
        if (initrd_span(&handoff, &span) ||
            initrd_overlaps(checked_start, checked_end, span.base, span.size))
            return -FDT_ERR_BADVALUE;
    }

    /* Every semantic check passed: expose the exact BLOB byte interval. */
    *out_start = blob_start;
    *out_end = blob_end;
    return 0;
}

/* No-reference path: keep the builtin-initramfs profile by dropping any
 * inherited initrd bounds in place. No fdt_open_into() is needed: deletion
 * only shrinks the tree, a missing /chosen is not an error, and any other
 * error is propagated. */
static int initrd_remove(void *handoff)
{
    int chosen;

    chosen = fdt_path_offset(handoff, "/chosen");
    if (chosen == -FDT_ERR_NOTFOUND)
        return 0;
    if (chosen < 0)
        return chosen;
    {
        int rc = fdt_delprop(handoff, chosen, "linux,initrd-start");
        if (rc != 0 && rc != -FDT_ERR_NOTFOUND)
            return rc;
    }
    chosen = fdt_path_offset(handoff, "/chosen");
    if (chosen == -FDT_ERR_NOTFOUND)
        return 0;
    if (chosen < 0)
        return chosen;
    {
        int rc = fdt_delprop(handoff, chosen, "linux,initrd-end");
        if (rc != 0 && rc != -FDT_ERR_NOTFOUND)
            return rc;
    }
    return 0;
}

static uint32_t initrd_crc32(const uint8_t *bytes, size_t len)
{
    static uint32_t table[256];
    static int inited;
    if (!inited) {
        for (uint32_t i = 0; i < 256U; ++i) {
            uint32_t c = i;
            for (unsigned b = 0; b < 8U; ++b)
                c = (c & 1U) ? (UINT32_C(0xedb88320) ^ (c >> 1)) : (c >> 1);
            table[i] = c;
        }
        inited = 1;
    }
    uint32_t crc = UINT32_MAX;
    while (len-- != 0U)
        crc = table[(crc ^ *bytes++) & 0xffU] ^ (crc >> 8);
    return ~crc;
}

static void initrd_clean_poc(uint64_t address, size_t size)
{
#if defined(__aarch64__)
    uint64_t ctr = 0;
    __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
    uint64_t dline = UINT64_C(4) << ((ctr >> 16) & 0xfU);
    uint64_t end = address + size;
    for (uint64_t cur = address & ~(dline - 1U); cur < end; cur += dline)
        __asm__ volatile("dc cvac, %0" : : "r"(cur) : "memory");
    __asm__ volatile("dsb sy" : : : "memory");
#else
    (void)address;
    (void)size;
#endif
}

static bool initrd_window_safe(const void *handoff, size_t capacity,
                               const struct kshim_initrd_context *ctx,
                               uint64_t cand, uint64_t cend,
                               bool allow_packed_payload,
                               uint64_t excl_base, uint64_t excl_size)
{
    uint64_t kernel_size = initrd_le64((const uint8_t *)ctx->header + 16);
    if (kernel_size < ctx->entry->size)
        kernel_size = ctx->entry->size;
    uint64_t kernel_start = (ctx->entry->flags & MF_ENTRY_FLAG_COPY)
        ? ctx->entry->load_address
        : (ctx->packed_base + ctx->entry->offset);
    uint64_t kernel_end = kernel_start + kernel_size;

    uint64_t wbase = (kernel_start < cand ? kernel_start : cand) & ~(INITRD_WINDOW_ALIGN - 1ULL);
    uint64_t wtop = kernel_end > cend ? kernel_end : cend;
    if (wtop - wbase > INITRD_WINDOW_MAX)
        return false;

    struct dt_range span_rt = {0}, span_rfdt = {0}, span_pfdt = {0};
    if (initrd_span(&ctx->runtime, &span_rt) ||
        initrd_span(&ctx->runtime_fdt, &span_rfdt) ||
        initrd_span(&ctx->payload_fdt, &span_pfdt))
        return false;

    if (!allow_packed_payload &&
        initrd_overlaps(cand, cend, ctx->packed_base, ctx->packed_payload_size))
        return false;
    if (initrd_overlaps(cand, cend, kernel_start, kernel_size) ||
        initrd_overlaps(cand, cend, span_rt.base, span_rt.size) ||
        initrd_overlaps(cand, cend, span_rfdt.base, span_rfdt.size) ||
        initrd_overlaps(cand, cend, span_pfdt.base, span_pfdt.size) ||
        initrd_overlaps(cand, cend, UINT64_C(0xA7000000), UINT64_C(0x01000000)) ||
        initrd_overlaps(cand, cend, excl_base, excl_size) ||
        initrd_overlaps(cand, cend, (uint64_t)(uintptr_t)handoff, (uint64_t)capacity))
        return false;
    if (!initrd_ram_covers(handoff, cand, cend))
        return false;
    if (initrd_reservations_conflict(handoff, cand, cend))
        return false;
    return true;
}

static uint64_t initrd_find_decomp_window(const void *handoff, size_t capacity,
                                          const struct kshim_initrd_context *ctx,
                                          uint64_t alloc_sz,
                                          uint64_t excl_base, uint64_t excl_size)
{
    for (int pass = 0; pass < 2; ++pass) {
        uint64_t start_addr = (pass == 0) ? UINT64_C(0xBA000000) : UINT64_C(0x80000000);
        uint64_t end_addr = (pass == 0) ? UINT64_C(0x100000000) : UINT64_C(0xBA000000);
        for (uint64_t cand = start_addr;
             cand + alloc_sz <= end_addr;
             cand += UINT64_C(0x200000)) {
            if (initrd_window_safe(handoff, capacity, ctx, cand, cand + alloc_sz,
                                   false, excl_base, excl_size))
                return cand;
        }
    }
    return 0U;
}

static int initrd_maybe_decompress_kshmlz41(const void *handoff, size_t capacity,
                                            const struct kshim_initrd_context *ctx,
                                            uint64_t *io_start, uint64_t *io_end)
{
    uint64_t src_start = *io_start;
    uint64_t src_end = *io_end;
    if (src_end <= src_start || src_end - src_start < 32ULL)
        return 0;

    const uint8_t *hdr = (const uint8_t *)(uintptr_t)src_start;
    if (memcmp(hdr, "KSHMLZ41", 8) != 0)
        return 0;

    uint32_t raw_size = initrd_le32(hdr + 8);
    uint32_t block_size = initrd_le32(hdr + 12);
    uint32_t block_count = initrd_le32(hdr + 16);
    uint32_t raw_crc32 = initrd_le32(hdr + 20);
    uint32_t total_size = initrd_le32(hdr + 24);
    uint32_t hdr_crc32 = initrd_le32(hdr + 28);

    if (initrd_crc32(hdr, 28U) != hdr_crc32)
        return -FDT_ERR_BADVALUE;
    if (raw_size == 0U || raw_size > UINT32_C(256) * 1024U * 1024U ||
        block_size == 0U || block_size > UINT32_C(8) * 1024U * 1024U ||
        total_size < 32U || (uint64_t)total_size > (src_end - src_start))
        return -FDT_ERR_BADVALUE;
    uint32_t expect_blocks = (raw_size + block_size - 1U) / block_size;
    if (block_count != expect_blocks)
        return -FDT_ERR_BADVALUE;

    int mmu_started = 0;
    int rc = -FDT_ERR_BADVALUE;
    if (!kshim_mmu_is_enabled() && ctx->runtime_fdt.base != 0U) {
        kshim_mmu_early_config_t ecfg = {
            .fdt = (const void *)(uintptr_t)ctx->runtime_fdt.base,
            .payload_base = ctx->packed_base,
            .payload_end = ctx->packed_base + ctx->packed_payload_size,
        };
        if (kshim_mmu_configure_early(&ecfg) == 0)
            mmu_started = 1;
    }

    uint64_t alloc_sz = ((uint64_t)raw_size + UINT64_C(0xFFFF)) & ~UINT64_C(0xFFFF);
    uint64_t tmp_sz = ((uint64_t)total_size + UINT64_C(0xFFFF)) & ~UINT64_C(0xFFFF);

    /* Prefer EfiReservedMemoryType inside [0xA8000000, 0xB8000000) so UEFI DXE heap
     * allocations never overwrite the decompressed FAT32 ramdisk. */
    uint64_t dest = (ctx->packed_base + UINT64_C(0x03600000) + UINT64_C(0xFFFF)) & ~UINT64_C(0xFFFF);
    if (!initrd_window_safe(handoff, capacity, ctx, dest, dest + alloc_sz, true, 0U, 0U))
        dest = initrd_find_decomp_window(handoff, capacity, ctx, alloc_sz, 0U, 0U);
    if (dest == 0U)
        goto out;

    uint64_t tmp = initrd_find_decomp_window(handoff, capacity, ctx, tmp_sz, dest, alloc_sz);
    if (tmp == 0U)
        goto out;
    memcpy((void *)(uintptr_t)tmp, hdr, (size_t)total_size);
    const uint8_t *src = (const uint8_t *)(uintptr_t)tmp;

    uint64_t src_off = 32ULL;
    uint64_t out_off = 0ULL;
    for (uint32_t i = 0; i < block_count; ++i) {
        if (src_off + 4ULL > total_size)
            goto out;
        uint32_t comp_len = initrd_le32(src + src_off);
        src_off += 4ULL;
        if (comp_len == 0U || src_off + comp_len > total_size)
            goto out;
        uint32_t rem = raw_size - (uint32_t)out_off;
        uint32_t want = rem > block_size ? block_size : rem;
        int dec = LZ4_decompress_safe((const char *)(src + src_off),
                                      (char *)(uintptr_t)(dest + out_off),
                                      (int)comp_len, (int)want);
        if (dec != (int)want)
            goto out;
        src_off += comp_len;
        out_off += want;
    }
    if (out_off != raw_size || src_off != total_size)
        goto out;
    if (initrd_crc32((const uint8_t *)(uintptr_t)dest, (size_t)raw_size) != raw_crc32)
        goto out;

    initrd_clean_poc(dest, (size_t)raw_size);
    *io_start = dest;
    *io_end = dest + raw_size;
    rc = 0;

out:
    if (mmu_started)
        (void)kshim_mmu_prepare_handoff();
    return rc;
}

int kshim_initrd_apply(void *handoff, size_t capacity,
                       const struct kshim_initrd_context *ctx)
{
    int chosen, len;
    const fdt32_t *reference;
    uint64_t start, end;

    if (!handoff || capacity > (size_t)INT_MAX)
        return -FDT_ERR_BADVALUE;

    chosen = fdt_path_offset(handoff, "/chosen");
    if (chosen == -FDT_ERR_NOTFOUND)
        return initrd_remove(handoff);
    if (chosen < 0)
        return chosen;

    reference = fdt_getprop(handoff, chosen, "kshim,initrd-entry", &len);
    if (!reference) {
        if (len == -FDT_ERR_NOTFOUND)
            return initrd_remove(handoff);
        return len;
    }

    if (len != 4)
        return -FDT_ERR_BADVALUE;
    {
        int rc = initrd_resolve(handoff, capacity, ctx, reference, &start, &end);
        if (rc != 0)
            return rc;
        rc = initrd_maybe_decompress_kshmlz41(handoff, capacity, ctx, &start, &end);
        if (rc != 0)
            return rc;
    }

    /* All semantic checks passed: only now mutate the tree. The caller
     * discards the output on any write failure. */
    {
        int rc = fdt_open_into(handoff, handoff, (int)capacity);
        if (rc != 0)
            return rc;
    }
    chosen = fdt_path_offset(handoff, "/chosen");
    if (chosen < 0)
        return chosen;
    {
        int rc = fdt_setprop_u64(handoff, chosen, "linux,initrd-start", start);
        if (rc != 0)
            return rc;
    }
    chosen = fdt_path_offset(handoff, "/chosen");
    if (chosen < 0)
        return chosen;
    {
        int rc = fdt_setprop_u64(handoff, chosen, "linux,initrd-end", end);
        if (rc != 0)
            return rc;
    }
    return 0;
}
