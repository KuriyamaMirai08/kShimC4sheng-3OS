/* SPDX-License-Identifier: GPL-3.0-only */
#include <recovery_linux.h>

#include <config.h>
#include <dt.h>
#include <dt_handoff.h>
#include <dt_simplefb.h>
#include <libfdt.h>
#include <platform.h>
#include <qcom_dt.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#define REC_ALIGN_2M UINT64_C(0x200000)
#define REC_SCAN_MIN UINT64_C(0x80000000)
#define REC_SCAN_LOW_MAX UINT64_C(0x100000000)
#define REC_SCAN_MAX UINT64_C(0x10000000000)
#define REC_PAYLOAD_VER_DEFLATE 2U

/* Freestanding, zero-malloc, byte-safe RFC 1951 raw DEFLATE decompressor (puff). */
#define PUFF_MAXBITS 15
#define PUFF_MAXLCODES 286
#define PUFF_MAXDCODES 30
#define PUFF_MAXCODES (PUFF_MAXLCODES + PUFF_MAXDCODES)
#define PUFF_FIXLCODES 288

struct puff_state {
  uint8_t *out;
  size_t outlen;
  size_t outcnt;
  const uint8_t *in;
  size_t inlen;
  size_t incnt;
  int bitbuf;
  int bitcnt;
};

struct puff_huffman {
  int16_t *count;
  int16_t *symbol;
};

static int puff_bits(struct puff_state *s, int need)
{
  int val = s->bitbuf;
  while (s->bitcnt < need) {
    if (s->incnt >= s->inlen) return -1;
    val |= (int)(s->in[s->incnt++]) << s->bitcnt;
    s->bitcnt += 8;
  }
  s->bitbuf = val >> need;
  s->bitcnt -= need;
  return val & ((1 << need) - 1);
}

static int puff_stored(struct puff_state *s)
{
  s->bitbuf = 0;
  s->bitcnt = 0;
  if (s->incnt + 4 > s->inlen) return 2;
  uint32_t len = (uint32_t)s->in[s->incnt] | ((uint32_t)s->in[s->incnt + 1] << 8);
  uint32_t nlen = (uint32_t)s->in[s->incnt + 2] | ((uint32_t)s->in[s->incnt + 3] << 8);
  s->incnt += 4;
  if ((len ^ 0xFFFFU) != nlen) return -2;
  if (s->incnt + len > s->inlen) return 2;
  if (s->outcnt + len > s->outlen) return 1;
  while (len--)
    s->out[s->outcnt++] = s->in[s->incnt++];
  return 0;
}

static int puff_decode(struct puff_state *s, const struct puff_huffman *h)
{
  int len = 1, code = 0, first = 0, index = 0;
  int bitbuf = s->bitbuf;
  int left = s->bitcnt;
  const int16_t *next = h->count + 1;

  for (;;) {
    while (left--) {
      code |= bitbuf & 1;
      bitbuf >>= 1;
      int count = *next++;
      if (code - count < first) {
        s->bitbuf = bitbuf;
        s->bitcnt = (s->bitcnt - len) & 7;
        return h->symbol[index + (code - first)];
      }
      index += count;
      first += count;
      first <<= 1;
      code <<= 1;
      len++;
    }
    left = (PUFF_MAXBITS + 1) - len;
    if (left == 0) break;
    if (s->incnt >= s->inlen) return -10;
    bitbuf = s->in[s->incnt++];
    if (left > 8) left = 8;
  }
  return -10;
}

static int puff_construct(struct puff_huffman *h, const int16_t *length, int n)
{
  int16_t offs[PUFF_MAXBITS + 1];
  for (int len = 0; len <= PUFF_MAXBITS; len++)
    h->count[len] = 0;
  for (int symbol = 0; symbol < n; symbol++)
    h->count[length[symbol]]++;
  if (h->count[0] == n) return 0;

  int left = 1;
  for (int len = 1; len <= PUFF_MAXBITS; len++) {
    left <<= 1;
    left -= h->count[len];
    if (left < 0) return left;
  }

  offs[1] = 0;
  for (int len = 1; len < PUFF_MAXBITS; len++)
    offs[len + 1] = (int16_t)(offs[len] + h->count[len]);

  for (int symbol = 0; symbol < n; symbol++) {
    if (length[symbol] != 0)
      h->symbol[offs[length[symbol]]++] = (int16_t)symbol;
  }
  return left;
}

static int puff_codes(struct puff_state *s,
                      const struct puff_huffman *lencode,
                      const struct puff_huffman *distcode)
{
  static const int16_t lens[29] = {
      3, 4, 5, 6, 7, 8, 9, 10, 11, 13, 15, 17, 19, 23, 27, 31,
      35, 43, 51, 59, 67, 83, 99, 115, 131, 163, 195, 227, 258};
  static const int16_t lext[29] = {
      0, 0, 0, 0, 0, 0, 0, 0, 1, 1, 1, 1, 2, 2, 2, 2,
      3, 3, 3, 3, 4, 4, 4, 4, 5, 5, 5, 5, 0};
  static const int16_t dists[30] = {
      1, 2, 3, 4, 5, 7, 9, 13, 17, 25, 33, 49, 65, 97, 129, 193,
      257, 385, 513, 769, 1025, 1537, 2049, 3073, 4097, 6145, 8193, 12289, 16385, 24577};
  static const int16_t dext[30] = {
      0, 0, 0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5, 5, 6, 6,
      7, 7, 8, 8, 9, 9, 10, 10, 11, 11, 12, 12, 13, 13};

  int symbol;
  do {
    symbol = puff_decode(s, lencode);
    if (symbol < 0) return symbol;
    if (symbol < 256) {
      if (s->outcnt >= s->outlen) return 1;
      s->out[s->outcnt++] = (uint8_t)symbol;
    } else if (symbol > 256) {
      symbol -= 257;
      if (symbol >= 29) return -10;
      int extra = puff_bits(s, lext[symbol]);
      if (extra < 0) return -1;
      size_t len = (size_t)lens[symbol] + (size_t)extra;

      symbol = puff_decode(s, distcode);
      if (symbol < 0) return symbol;
      if (symbol >= 30) return -10;
      int dextra = puff_bits(s, dext[symbol]);
      if (dextra < 0) return -1;
      size_t dist = (size_t)dists[symbol] + (size_t)dextra;
      if (dist > s->outcnt) return -11;
      if (s->outcnt + len > s->outlen) return 1;
      while (len--) {
        s->out[s->outcnt] = s->out[s->outcnt - dist];
        s->outcnt++;
      }
    }
  } while (symbol != 256);
  return 0;
}

static int puff_fixed(struct puff_state *s)
{
  static int virgin = 1;
  static int16_t lencnt[PUFF_MAXBITS + 1], lensym[PUFF_FIXLCODES];
  static int16_t distcnt[PUFF_MAXBITS + 1], distsym[PUFF_MAXDCODES];
  static struct puff_huffman lencode = {lencnt, lensym};
  static struct puff_huffman distcode = {distcnt, distsym};

  if (virgin) {
    int16_t lengths[PUFF_FIXLCODES];
    int symbol = 0;
    for (; symbol < 144; symbol++) lengths[symbol] = 8;
    for (; symbol < 256; symbol++) lengths[symbol] = 9;
    for (; symbol < 280; symbol++) lengths[symbol] = 7;
    for (; symbol < PUFF_FIXLCODES; symbol++) lengths[symbol] = 8;
    puff_construct(&lencode, lengths, PUFF_FIXLCODES);
    for (symbol = 0; symbol < PUFF_MAXDCODES; symbol++) lengths[symbol] = 5;
    puff_construct(&distcode, lengths, PUFF_MAXDCODES);
    virgin = 0;
  }
  return puff_codes(s, &lencode, &distcode);
}

static int puff_dynamic(struct puff_state *s)
{
  static const int16_t order[19] = {
      16, 17, 18, 0, 8, 7, 9, 6, 10, 5, 11, 4, 12, 3, 13, 2, 14, 1, 15};
  int16_t lengths[PUFF_MAXCODES];
  int16_t lencnt[PUFF_MAXBITS + 1], lensym[PUFF_MAXLCODES];
  int16_t distcnt[PUFF_MAXBITS + 1], distsym[PUFF_MAXDCODES];
  struct puff_huffman lencode = {lencnt, lensym};
  struct puff_huffman distcode = {distcnt, distsym};

  int nlen = puff_bits(s, 5);
  int ndist = puff_bits(s, 5);
  int ncode = puff_bits(s, 4);
  if (nlen < 0 || ndist < 0 || ncode < 0) return -1;
  nlen += 257;
  ndist += 1;
  ncode += 4;
  if (nlen > PUFF_MAXLCODES || ndist > PUFF_MAXDCODES) return -3;

  int index = 0;
  for (; index < ncode; index++) {
    int b = puff_bits(s, 3);
    if (b < 0) return -1;
    lengths[order[index]] = (int16_t)b;
  }
  for (; index < 19; index++)
    lengths[order[index]] = 0;

  if (puff_construct(&lencode, lengths, 19) != 0) return -4;

  index = 0;
  while (index < nlen + ndist) {
    int symbol = puff_decode(s, &lencode);
    if (symbol < 0) return symbol;
    if (symbol < 16) {
      lengths[index++] = (int16_t)symbol;
    } else {
      int len = 0;
      int rep;
      if (symbol == 16) {
        if (index == 0) return -5;
        len = lengths[index - 1];
        rep = puff_bits(s, 2);
        if (rep < 0) return -1;
        rep += 3;
      } else if (symbol == 17) {
        rep = puff_bits(s, 3);
        if (rep < 0) return -1;
        rep += 3;
      } else {
        rep = puff_bits(s, 7);
        if (rep < 0) return -1;
        rep += 11;
      }
      if (index + rep > nlen + ndist) return -6;
      while (rep--)
        lengths[index++] = (int16_t)len;
    }
  }
  if (lengths[256] == 0) return -9;
  int err = puff_construct(&lencode, lengths, nlen);
  if (err && (err < 0 || nlen != lencode.count[0] + lencode.count[1]))
    return -7;
  err = puff_construct(&distcode, lengths + nlen, ndist);
  if (err && (err < 0 || ndist != distcode.count[0] + distcode.count[1]))
    return -8;
  return puff_codes(s, &lencode, &distcode);
}

int kshim_puff_inflate(uint8_t *dest, size_t *destlen, const uint8_t *source, size_t srclen)
{
  if (!dest || !destlen || !source) return -1;
  struct puff_state s;
  s.out = dest;
  s.outlen = *destlen;
  s.outcnt = 0;
  s.in = source;
  s.inlen = srclen;
  s.incnt = 0;
  s.bitbuf = 0;
  s.bitcnt = 0;

  int last, err;
  do {
    last = puff_bits(&s, 1);
    int type = puff_bits(&s, 2);
    if (last < 0 || type < 0) return -1;
    err = type == 0 ? puff_stored(&s) :
          type == 1 ? puff_fixed(&s) :
          type == 2 ? puff_dynamic(&s) : -1;
    if (err != 0) return err;
  } while (!last);

  *destlen = s.outcnt;
  return 0;
}

static uint32_t rec_le32(const uint8_t *p)
{
  return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
         ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t rec_le64(const uint8_t *p)
{
  return (uint64_t)rec_le32(p) | ((uint64_t)rec_le32(p + 4) << 32);
}

static uint64_t rec_be_cells(const uint8_t *p, int len)
{
  uint64_t v = 0;
  int n = len >= 8 ? 8 : 4;
  for (int i = 0; i < n; ++i)
    v = (v << 8) | p[i];
  return v;
}

static uint32_t rec_crc32(const uint8_t *bytes, size_t len)
{
  uint32_t crc = UINT32_MAX;
  while (len-- != 0U) {
    crc ^= *bytes++;
    for (unsigned b = 0; b < 8U; ++b)
      crc = (crc >> 1) ^ (UINT32_C(0xedb88320) & (0U - (crc & 1U)));
  }
  return ~crc;
}

void kshim_clean_poc(uint64_t address, size_t size)
{
#if defined(__aarch64__)
  uint64_t ctr = 0;
  __asm__ volatile("mrs %0, ctr_el0" : "=r"(ctr));
  uint64_t dline = UINT64_C(4) << ((ctr >> 16) & 0xfU);
  uint64_t end = address + size;
  for (uint64_t cur = address & ~(dline - 1U); cur < end; cur += dline)
    __asm__ volatile("dc cvac, %0" : : "r"(cur) : "memory");
  __asm__ volatile("dsb sy\nic iallu\ndsb sy\nisb" : : : "memory");
#else
  (void)address;
  (void)size;
#endif
}

bool kshim_recovery_payload_probe(const void *fdt, struct kshim_rec_payload *out)
{
  if (!out) return false;
  memset(out, 0, sizeof(*out));
  if (!fdt) return false;

  int chosen = fdt_path_offset(fdt, "/chosen");
  if (chosen < 0) return false;

  int slen = 0, elen = 0;
  const uint8_t *sprop = fdt_getprop(fdt, chosen, "linux,initrd-start", &slen);
  const uint8_t *eprop = fdt_getprop(fdt, chosen, "linux,initrd-end", &elen);
  if (!sprop || !eprop || (slen != 4 && slen != 8) || (elen != 4 && elen != 8))
    return false;

  uint64_t start = rec_be_cells(sprop, slen);
  uint64_t end = rec_be_cells(eprop, elen);
  if (start == 0U || end <= start + KSHIM_REC_FOOTER_BYTES || end > REC_SCAN_MAX)
    return false;

  uint64_t min_pos = end - start > 131072U ? end - 131072U : start;
  for (uint64_t pos = (end - KSHIM_REC_FOOTER_BYTES) & ~UINT64_C(3);
       pos >= min_pos; pos -= 4U) {
    const uint8_t *raw = (const uint8_t *)(uintptr_t)pos;
    if (memcmp(raw, KSHIM_REC_MAGIC, 8) != 0) {
      if (pos < min_pos + 4U) break;
      continue;
    }
    struct kshim_rec_linux_footer f;
    memcpy(&f.magic, raw, 8);
    f.version = rec_le32(raw + 8);
    f.payload_size = rec_le32(raw + 12);
    f.skyward_size = rec_le32(raw + 16);
    f.kernel_offset = rec_le32(raw + 20);
    f.kernel_raw_size = rec_le32(raw + 24);
    f.kernel_comp_size = rec_le32(raw + 28);
    f.kernel_raw_crc32 = rec_le32(raw + 32);
    f.dtb_offset = rec_le32(raw + 36);
    f.dtb_size = rec_le32(raw + 40);
    f.dtb_crc32 = rec_le32(raw + 44);
    f.reserved = rec_le32(raw + 48);
    f.footer_crc32 = rec_le32(raw + 52);

    if (f.version != REC_PAYLOAD_VER_DEFLATE || rec_crc32(raw, 52U) != f.footer_crc32)
      goto next;
    if (f.payload_size < KSHIM_REC_FOOTER_BYTES)
      goto next;
    uint64_t pend = pos + KSHIM_REC_FOOTER_BYTES;
    if (f.payload_size > pend - start)
      goto next;
    uint64_t pstart = pend - f.payload_size;
    if (pstart - start < f.skyward_size)
      goto next;
    uint64_t body_limit = f.payload_size - KSHIM_REC_FOOTER_BYTES;
    if (f.kernel_raw_size < 64U || f.kernel_comp_size == 0U ||
        (uint64_t)f.kernel_offset + f.kernel_comp_size > body_limit ||
        f.dtb_size < 64U || (uint64_t)f.dtb_offset + f.dtb_size > body_limit)
      goto next;

    out->found = true;
    out->initrd_start = start;
    out->initrd_end = end;
    out->payload_start = pstart;
    out->payload_end = pend;
    out->end_prop_len = elen;
    out->footer = f;
    return true;
next:
    if (pos < min_pos + 4U) break;
  }
  return false;
}

void kshim_recovery_trim_for_skyward(void *fdt, const struct kshim_rec_payload *p)
{
  if (!fdt || !p || !p->found) return;
  uint64_t bc_len = p->initrd_end - p->payload_end;
  if (bc_len > 0U && bc_len <= 131072U) {
    memmove((void *)(uintptr_t)p->payload_start,
            (const void *)(uintptr_t)p->payload_end, (size_t)bc_len);
    kshim_clean_poc(p->payload_start, (size_t)bc_len);
  } else {
    bc_len = 0U;
  }
  uint64_t new_end = p->payload_start + bc_len;
  int chosen = fdt_path_offset(fdt, "/chosen");
  if (chosen >= 0) {
    if (p->end_prop_len == 8) {
      fdt64_t v = cpu_to_fdt64(new_end);
      (void)fdt_setprop_inplace(fdt, chosen, "linux,initrd-end", &v, sizeof(v));
    } else if (p->end_prop_len == 4) {
      fdt32_t v = cpu_to_fdt32((uint32_t)new_end);
      (void)fdt_setprop_inplace(fdt, chosen, "linux,initrd-end", &v, sizeof(v));
    }
    kshim_clean_poc((uint64_t)(uintptr_t)fdt, (size_t)fdt_totalsize(fdt));
  }
}

static bool spans_overlap(uint64_t a0, uint64_t a1, uint64_t b0, uint64_t b1)
{
  return a0 < b1 && b0 < a1;
}

static uint64_t find_safe_kernel_window(
    const void *fdt,
    uint64_t alloc_size,
    const struct kshim_rec_payload *p,
    uint64_t linux_base,
    uint64_t manifest_end,
    uint64_t image_start,
    uint64_t image_end)
{
  struct dt_range rsv[128];
  bool no_map[128];
  size_t rsv_count = 0;
  if (dt_reservations(fdt, rsv, no_map, 128, &rsv_count) != 0)
    return 0U;

  uint64_t fdt_start = (uint64_t)(uintptr_t)fdt;
  uint64_t fdt_end = fdt_start + (uint32_t)fdt_totalsize(fdt);

  for (int pass = 0; pass < 2; ++pass) {
    uint64_t pass_cap = (pass == 0) ? REC_SCAN_LOW_MAX : REC_SCAN_MAX;
    int node;
    fdt_for_each_subnode(node, fdt, 0) {
      const char *dtype = dt_string(fdt, node, "device_type");
      if (!dtype || strcmp(dtype, "memory") != 0 || !dt_available(fdt, node))
        continue;
      int rlen = 0;
      const fdt32_t *reg = fdt_getprop(fdt, node, "reg", &rlen);
      if (!reg || rlen <= 0 || (rlen % 16) != 0)
        continue;
      int banks = rlen / 16;
      for (int b = 0; b < banks; ++b) {
        uint64_t b0 = ((uint64_t)fdt32_to_cpu(reg[b * 4]) << 32) | fdt32_to_cpu(reg[b * 4 + 1]);
        uint64_t bsz = ((uint64_t)fdt32_to_cpu(reg[b * 4 + 2]) << 32) | fdt32_to_cpu(reg[b * 4 + 3]);
        if (bsz == 0U || b0 >= pass_cap) continue;
        uint64_t b1 = b0 + bsz;
        if (b1 > pass_cap) b1 = pass_cap;
        if (b0 < REC_SCAN_MIN) b0 = REC_SCAN_MIN;
        uint64_t cand = (b0 + REC_ALIGN_2M - 1U) & ~(REC_ALIGN_2M - 1U);
        while (cand + alloc_size <= b1) {
          uint64_t cend = cand + alloc_size;
          bool bad = false;
          if (spans_overlap(cand, cend, linux_base, manifest_end) ||
              spans_overlap(cand, cend, image_start, image_end) ||
              spans_overlap(cand, cend, fdt_start, fdt_end) ||
              spans_overlap(cand, cend, p->initrd_start, p->initrd_end)) {
            bad = true;
          }
          for (size_t r = 0; !bad && r < rsv_count; ++r) {
            if (spans_overlap(cand, cend, rsv[r].base, rsv[r].base + rsv[r].size))
              bad = true;
          }
          if (!bad) return cand;
          cand += REC_ALIGN_2M;
        }
      }
    }
  }
  return 0U;
}

int kshim_recovery_prepare_linux(
    const void *runtime_fdt,
    const struct kshim_rec_payload *p,
    uint64_t linux_base,
    uint64_t manifest_end,
    uint64_t image_start,
    uint64_t image_end,
    const void *mem_snapshot,
    size_t mem_snapshot_size,
    void *handoff_dtb,
    size_t handoff_dtb_cap,
    void *mem_overlay,
    size_t mem_overlay_cap,
    int ramoops_status,
    const tb323fu_ramoops_layout *ramoops_layout,
    uint64_t *out_kernel_base)
{
  if (!runtime_fdt || !p || !p->found || !handoff_dtb || !out_kernel_base)
    return -1;

  const uint8_t *comp = (const uint8_t *)(uintptr_t)(p->payload_start + p->footer.kernel_offset);
  const uint8_t *dtb = (const uint8_t *)(uintptr_t)(p->payload_start + p->footer.dtb_offset);

  if (rec_crc32(dtb, p->footer.dtb_size) != p->footer.dtb_crc32 ||
      dt_validate(dtb, p->footer.dtb_size) != 0)
    return -2;

  uint64_t alloc_size = ((uint64_t)p->footer.kernel_raw_size + 0x400000U + REC_ALIGN_2M - 1U) &
                        ~(REC_ALIGN_2M - 1U);
  if (alloc_size < UINT64_C(0x3000000))
    alloc_size = UINT64_C(0x3000000);

  uint64_t dest = find_safe_kernel_window(
      runtime_fdt, alloc_size, p, linux_base, manifest_end, image_start, image_end);
  if (dest == 0U)
    return -3;

  size_t out_len = (size_t)p->footer.kernel_raw_size;
  int decomp = kshim_puff_inflate(
      (uint8_t *)(uintptr_t)dest, &out_len,
      comp, (size_t)p->footer.kernel_comp_size);
  if (decomp != 0 || out_len != (size_t)p->footer.kernel_raw_size)
    return -4;

  const uint8_t *img = (const uint8_t *)(uintptr_t)dest;
  if (rec_crc32(img, p->footer.kernel_raw_size) != p->footer.kernel_raw_crc32)
    return -5;
  if (rec_le32(img + 0x38U) != UINT32_C(0x644d5241))
    return -6;
  uint64_t arm64_img_sz = rec_le64(img + 0x10U);
  if (arm64_img_sz == 0U || arm64_img_sz > alloc_size)
    return -7;

  if (dt_memory_apply(mem_snapshot, mem_snapshot_size,
                      dtb, p->footer.dtb_size,
                      handoff_dtb, handoff_dtb_cap,
                      mem_overlay, mem_overlay_cap) != 0)
    return -8;

  if (ramoops_status == TB323FU_RAMOOPS_OK && ramoops_layout != NULL) {
    if (tb323fu_ramoops_bind(handoff_dtb, handoff_dtb_cap, ramoops_layout) != TB323FU_RAMOOPS_OK)
      return -9;
  }

  int chosen = fdt_path_offset(handoff_dtb, "/chosen");
  if (chosen >= 0) {
    (void)fdt_delprop(handoff_dtb, chosen, "linux,initrd-start");
    (void)fdt_delprop(handoff_dtb, chosen, "linux,initrd-end");
  }

  struct kshim_resources *r = kshim_resources_get();
  if (dt_simplefb_apply(handoff_dtb, handoff_dtb_cap,
                        (r && r->splash_status == 0) ? &r->framebuffer : NULL) != 0)
    return -10;

  if (fdt_pack(handoff_dtb) != 0 || dt_validate(handoff_dtb, handoff_dtb_cap) != 0)
    return -11;

  kshim_clean_poc(dest, (size_t)p->footer.kernel_raw_size);
  kshim_clean_poc((uint64_t)(uintptr_t)handoff_dtb, (size_t)fdt_totalsize(handoff_dtb));
  *out_kernel_base = dest;
  return 0;
}
