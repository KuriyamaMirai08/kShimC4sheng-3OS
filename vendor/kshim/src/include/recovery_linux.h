#pragma once

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>

#include <tb323fu_ramoops.h>

#define KSHIM_REC_MAGIC "KSHMLNX1"
#define KSHIM_REC_FOOTER_BYTES 56U

struct kshim_rec_linux_footer {
  uint8_t magic[8];
  uint32_t version;
  uint32_t payload_size;
  uint32_t skyward_size;
  uint32_t kernel_offset;
  uint32_t kernel_raw_size;
  uint32_t kernel_comp_size;
  uint32_t kernel_raw_crc32;
  uint32_t dtb_offset;
  uint32_t dtb_size;
  uint32_t dtb_crc32;
  uint32_t reserved;
  uint32_t footer_crc32;
};

struct kshim_rec_payload {
  bool found;
  uint64_t initrd_start;
  uint64_t initrd_end;
  uint64_t payload_start;
  uint64_t payload_end;
  int end_prop_len;
  struct kshim_rec_linux_footer footer;
};

void kshim_clean_poc(uint64_t address, size_t size);

bool kshim_recovery_payload_probe(const void *fdt, struct kshim_rec_payload *out);

void kshim_recovery_trim_for_skyward(void *fdt, const struct kshim_rec_payload *p);

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
    uint64_t *out_kernel_base);
