#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <vector>

#include "PersistOverlay.h"

struct MemStoreOps {
  uint64_t windowBlocks = 0;
  std::map<uint64_t, std::vector<uint8_t>> blocks;
  std::vector<uint64_t> reads;

  bool read(uint64_t rel, uint32_t count, void *dst) {
    uint8_t *out = static_cast<uint8_t *>(dst);
    for (uint32_t i = 0; i < count; ++i) {
      uint64_t idx = rel + i;
      if (idx >= windowBlocks) return false;
      reads.push_back(idx);
      auto it = blocks.find(idx);
      if (it == blocks.end()) {
        std::memset(out + i * 4096ULL, 0, 4096);
      } else {
        std::memcpy(out + i * 4096ULL, it->second.data(), 4096);
      }
    }
    return true;
  }

  bool write(uint64_t rel, uint32_t count, const void *src) {
    const uint8_t *in = static_cast<const uint8_t *>(src);
    for (uint32_t i = 0; i < count; ++i) {
      uint64_t idx = rel + i;
      if (idx >= windowBlocks) return false;
      std::vector<uint8_t> blk(in + i * 4096ULL, in + (i + 1) * 4096ULL);
      bool all_zero = true;
      for (uint8_t b : blk) {
        if (b) {
          all_zero = false;
          break;
        }
      }
      if (all_zero) {
        blocks.erase(idx);
      } else {
        blocks[idx] = std::move(blk);
      }
    }
    return true;
  }

  bool sync() { return true; }
};

static bool parse_hex32(const char *hex, uint8_t out[32]) {
  if (std::strlen(hex) != 64) return false;
  for (int i = 0; i < 32; ++i) {
    unsigned int v = 0;
    if (std::sscanf(hex + 2 * i, "%02x", &v) != 1) return false;
    out[i] = static_cast<uint8_t>(v);
  }
  return true;
}

int main(int argc, char **argv) {
  if (argc != 4) {
    std::fprintf(stderr, "Usage: %s <kPages> <seedHash_64hex> <out_blocks_bin>\n", argv[0]);
    return 1;
  }
  char *endp = nullptr;
  uint64_t kPages = std::strtoull(argv[1], &endp, 10);
  if (!kPages || !endp || *endp != '\0') {
    std::fprintf(stderr, "Invalid kPages: %s\n", argv[1]);
    return 1;
  }
  uint8_t seed[32];
  if (!parse_hex32(argv[2], seed)) {
    std::fprintf(stderr, "Invalid seedHash hex\n");
    return 1;
  }
  constexpr uint64_t kWalBlocks =
      (64ULL * 1024ULL * 1024ULL / 4096ULL / persist::wal::kTransactionBlocks) *
      persist::wal::kTransactionBlocks;
  static_assert(kWalBlocks == 16380, "kWalBlocks must be 16380");

  persist::wal::Layout layout;
  uint64_t bitmapBlocks = 0;
  if (!persist::wal::layoutFor(kPages, kWalBlocks, &layout) ||
      !persist::bitmapBlocksFor(kPages, &bitmapBlocks)) {
    std::fprintf(stderr, "layoutFor/bitmapBlocksFor failed\n");
    return 1;
  }
  uint64_t window = layout.windowBlocks;
  std::printf("kPages=%llu window=%llu snapshotBlocks=%llu baseStart=%llu walStart=%llu walBlocks=%llu bitmapBlocks=%llu\n",
              (unsigned long long)kPages,
              (unsigned long long)window,
              (unsigned long long)layout.snapshotBlocks,
              (unsigned long long)layout.baseStart,
              (unsigned long long)layout.walStart,
              (unsigned long long)layout.walBlocks,
              (unsigned long long)bitmapBlocks);

  MemStoreOps ops;
  ops.windowBlocks = window;

  uint32_t entryCap = static_cast<uint32_t>(
      (kWalBlocks / persist::wal::kTransactionBlocks) * persist::wal::kPagesPerTransaction * 2);
  std::vector<uint8_t> bits(bitmapBlocks * 4096ULL, 0);
  std::vector<persist::wal::Entry> entries(entryCap);

  persist::Overlay ov = {};
  auto res_init = ov.open(ops, kPages, seed, false, bits.data(), true, true, entries.data(), entryCap, kWalBlocks);
  if (res_init != persist::Open::Fresh || !ov.walV2 || !ov.walStore.checkpointCapable || ov.present != 0) {
    std::fprintf(stderr, "init open failed: res=%d walV2=%d cp=%d present=%llu\n",
                 (int)res_init, (int)ov.walV2, (int)ov.walStore.checkpointCapable, (unsigned long long)ov.present);
    return 2;
  }

  for (bool ro : {true, false}) {
    std::fill(bits.begin(), bits.end(), 0);
    std::fill(entries.begin(), entries.end(), persist::wal::Entry{});
    persist::Overlay ov2 = {};
    ops.reads.clear();
    bool ok = ov2.resumeExistingV2(ops, kPages, seed, ro, bits.data(), entries.data(), entryCap, kWalBlocks, false);
    if (!ok || !ov2.walV2 || !ov2.walStore.checkpointCapable || ov2.present != 0 ||
        !ov2.walStore.stoppedAtUncommittedTail || ov2.walStore.nextSlot != 0) {
      std::fprintf(stderr, "resumeExistingV2(ro=%d) failed: ok=%d walV2=%d cp=%d present=%llu tail=%d slot=%llu\n",
                   (int)ro, (int)ok, (int)ov2.walV2, (int)ov2.walStore.checkpointCapable,
                   (unsigned long long)ov2.present, (int)ov2.walStore.stoppedAtUncommittedTail,
                   (unsigned long long)ov2.walStore.nextSlot);
      return 3;
    }
    std::printf("resumeExistingV2(ro=%d): OK checkpointCapable=1 present=0 reads=%zu\n",
                (int)ro, ops.reads.size());
  }

  FILE *f = std::fopen(argv[3], "wb");
  if (!f) {
    std::fprintf(stderr, "Failed to open output file\n");
    return 4;
  }
  uint64_t n = ops.blocks.size();
  std::fwrite(&window, sizeof(window), 1, f);
  std::fwrite(&n, sizeof(n), 1, f);
  for (const auto &kv : ops.blocks) {
    uint64_t idx = kv.first;
    std::fwrite(&idx, sizeof(idx), 1, f);
    std::fwrite(kv.second.data(), 4096, 1, f);
  }
  std::fclose(f);
  std::printf("Wrote %llu non-zero blocks to %s\n", (unsigned long long)n, argv[3]);
  return 0;
}
