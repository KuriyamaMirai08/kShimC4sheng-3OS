#include <algorithm>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <unordered_map>
#include <vector>

#include "PersistOverlay.h"

namespace {
constexpr uint32_t kSparseMagic = 0xED26FF3A;
constexpr uint16_t kChunkRaw = 0xCAC1;
constexpr uint16_t kChunkFill = 0xCAC2;
constexpr uint16_t kChunkDontCare = 0xCAC3;

struct DirtySimOps {
  uint64_t storeFirst = 0;
  uint64_t windowBlocks = 0;
  // Sparse map of blocks: blocks not present in `written` return 0xA5 (dirty stale bytes)!
  std::unordered_map<uint64_t, std::vector<uint8_t>> written;
  std::unordered_map<uint64_t, uint32_t> fillVal;

  bool read(uint64_t rel, uint32_t count, void *dst) {
    uint8_t *out = static_cast<uint8_t *>(dst);
    for (uint32_t i = 0; i < count; ++i) {
      uint64_t lba = storeFirst + rel + i;
      if (rel + i >= windowBlocks) return false;
      auto it = written.find(lba);
      if (it != written.end()) {
        std::memcpy(out + i * 4096ULL, it->second.data(), 4096);
      } else {
        auto fit = fillVal.find(lba);
        if (fit != fillVal.end()) {
          uint32_t v = fit->second;
          for (size_t off = 0; off < 4096; off += 4) {
            std::memcpy(out + i * 4096ULL + off, &v, 4);
          }
        } else {
          std::memset(out + i * 4096ULL, 0xA5, 4096);
        }
      }
    }
    return true;
  }

  bool write(uint64_t, uint32_t, const void *) { return false; }
  bool sync() { return true; }
};

bool parse_hex32(const char *hex, uint8_t out[32]) {
  if (std::strlen(hex) != 64) return false;
  for (int i = 0; i < 32; ++i) {
    unsigned int v = 0;
    if (std::sscanf(hex + 2 * i, "%02x", &v) != 1) return false;
    out[i] = static_cast<uint8_t>(v);
  }
  return true;
}
}  // namespace

int main(int argc, char **argv) {
  if (argc != 5) {
    std::fprintf(stderr, "Usage: %s <sparse.img> <kPages> <partition_blocks> <seedHash_64hex>\n", argv[0]);
    return 1;
  }
  FILE *f = std::fopen(argv[1], "rb");
  if (!f) {
    std::fprintf(stderr, "Failed to open %s\n", argv[1]);
    return 1;
  }
  uint64_t kPages = std::strtoull(argv[2], nullptr, 10);
  uint64_t partBlocks = std::strtoull(argv[3], nullptr, 10);
  uint8_t seed[32];
  if (!parse_hex32(argv[4], seed)) {
    std::fprintf(stderr, "Invalid seedHash\n");
    return 1;
  }

  uint8_t hdr[28];
  if (std::fread(hdr, 1, 28, f) != 28) return 2;
  uint32_t magic = 0, blksz = 0, totblks = 0, totchunks = 0;
  std::memcpy(&magic, hdr, 4);
  std::memcpy(&blksz, hdr + 12, 4);
  std::memcpy(&totblks, hdr + 16, 4);
  std::memcpy(&totchunks, hdr + 20, 4);
  if (magic != kSparseMagic || blksz != 4096 || totblks != partBlocks) {
    std::fprintf(stderr, "Sparse header mismatch\n");
    return 2;
  }

  constexpr uint64_t kWalBlocks = 16380;
  constexpr uint64_t kReserveBlocks = 262144;
  uint64_t window = 0, bitmapBlocks = 0;
  if (!persist::wal::windowBlocksFor(kPages, kWalBlocks, &window) ||
      !persist::bitmapBlocksFor(kPages, &bitmapBlocks)) {
    return 2;
  }
  uint64_t storeFirst = partBlocks - kReserveBlocks - window;

  DirtySimOps ops;
  ops.storeFirst = storeFirst;
  ops.windowBlocks = window;

  uint64_t pos = 0;
  for (uint32_t c = 0; c < totchunks; ++c) {
    uint8_t ch[12];
    if (std::fread(ch, 1, 12, f) != 12) return 3;
    uint16_t ctype = 0;
    uint32_t cblks = 0, csz = 0;
    std::memcpy(&ctype, ch, 2);
    std::memcpy(&cblks, ch + 4, 4);
    std::memcpy(&csz, ch + 8, 4);
    if (ctype == kChunkDontCare) {
      if (csz != 12) return 3;
    } else if (ctype == kChunkFill) {
      if (csz != 16) return 3;
      uint32_t fv = 0;
      if (std::fread(&fv, 4, 1, f) != 1) return 3;
      for (uint32_t i = 0; i < cblks; ++i) {
        if (pos + i >= storeFirst && pos + i < storeFirst + window) {
          ops.fillVal[pos + i] = fv;
        }
      }
    } else if (ctype == kChunkRaw) {
      if (csz != 12 + cblks * 4096U) return 3;
      for (uint32_t i = 0; i < cblks; ++i) {
        std::vector<uint8_t> blk(4096);
        if (std::fread(blk.data(), 1, 4096, f) != 4096) return 3;
        if (pos + i < 3846144ULL) {
          std::fprintf(stderr, "ERROR: sparse writes into SHNGROOTv2 at lba %llu!\n", (unsigned long long)(pos + i));
          return 4;
        }
        ops.written[pos + i] = std::move(blk);
      }
    } else {
      std::fprintf(stderr, "Unsupported chunk %x\n", ctype);
      return 3;
    }
    pos += cblks;
  }
  std::fclose(f);
  if (pos != partBlocks) {
    std::fprintf(stderr, "Total blocks %llu != %llu\n", (unsigned long long)pos, (unsigned long long)partBlocks);
    return 4;
  }

  uint32_t entryCap = static_cast<uint32_t>(
      (kWalBlocks / persist::wal::kTransactionBlocks) * persist::wal::kPagesPerTransaction * 2);
  std::vector<uint8_t> bits(bitmapBlocks * 4096ULL, 0);
  std::vector<persist::wal::Entry> entries(entryCap);
  for (bool ro : {true, false}) {
    std::fill(bits.begin(), bits.end(), 0);
    std::fill(entries.begin(), entries.end(), persist::wal::Entry{});
    persist::Overlay ov = {};
    bool ok = ov.resumeExistingV2(ops, kPages, seed, ro, bits.data(), entries.data(), entryCap, kWalBlocks, false);
    if (!ok || !ov.walV2 || !ov.walStore.checkpointCapable || ov.present != 0 ||
        !ov.walStore.stoppedAtUncommittedTail || ov.walStore.nextSlot != 0) {
      std::fprintf(stderr, "DIRTY-DISK resumeExistingV2(ro=%d) FAILED! ok=%d cp=%d present=%llu tail=%d slot=%llu\n",
                   (int)ro, (int)ok, (int)ov.walStore.checkpointCapable, (unsigned long long)ov.present,
                   (int)ov.walStore.stoppedAtUncommittedTail, (unsigned long long)ov.walStore.nextSlot);
      return 5;
    }
  }
  std::printf("DIRTY-DISK (0xA5) verify PASS for %s (ro=1 & ro=0, storeFirst=%llu, window=%llu)\n",
              argv[1], (unsigned long long)storeFirst, (unsigned long long)window);
  return 0;
}
