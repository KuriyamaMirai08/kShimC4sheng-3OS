#include <atlas_wallpaper.h>

#include <stdint.h>
#include <string.h>

#include "src/libs/tjpgd/tjpgd.h"

extern const unsigned char kshim_atlas_wallpaper_blob[];
extern const unsigned char kshim_atlas_wallpaper_blob_end[];

typedef struct {
  const uint8_t *Data;
  size_t Size;
  size_t Offset;
  uint32_t *Pixels;
  uint16_t Width;
  uint16_t Height;
} kshim_wallpaper_ctx_t;

static uint32_t mTjpgdPool[1024] __attribute__((aligned(8)));

static size_t WallpaperInput(JDEC *Jd, uint8_t *Buff, size_t Ndata)
{
  kshim_wallpaper_ctx_t *Ctx = (kshim_wallpaper_ctx_t *)Jd->device;
  size_t Rem;
  if (Ctx == NULL || Ctx->Offset >= Ctx->Size)
    return 0U;
  Rem = Ctx->Size - Ctx->Offset;
  if (Ndata > Rem)
    Ndata = Rem;
  if (Buff != NULL && Ndata != 0U)
    memcpy(Buff, Ctx->Data + Ctx->Offset, Ndata);
  Ctx->Offset += Ndata;
  return Ndata;
}

static int WallpaperOutput(JDEC *Jd, void *Bitmap, JRECT *Rect)
{
  kshim_wallpaper_ctx_t *Ctx = (kshim_wallpaper_ctx_t *)Jd->device;
  const uint8_t *Src = (const uint8_t *)Bitmap;
  uint32_t RectW = (uint32_t)(Rect->right - Rect->left + 1U);
  for (uint16_t y = Rect->top; y <= Rect->bottom; ++y) {
    if (y < Ctx->Height) {
      uint32_t *DstRow = Ctx->Pixels + (size_t)y * Ctx->Width;
      for (uint16_t x = Rect->left; x <= Rect->right; ++x) {
        size_t Off = ((size_t)(y - Rect->top) * RectW + (size_t)(x - Rect->left)) * 3U;
        if (x < Ctx->Width) {
          DstRow[x] = 0xff000000U |
              ((uint32_t)Src[Off + 2U] << 16) |
              ((uint32_t)Src[Off + 1U] << 8) |
              (uint32_t)Src[Off + 0U];
        }
      }
    }
  }
  return 1;
}

int kshim_atlas_wallpaper_decode(void *Buffer, size_t Capacity, lv_image_dsc_t *OutImage)
{
  size_t BlobSize;
  kshim_wallpaper_ctx_t Ctx;
  JDEC Jd;
  JRESULT Res;
  size_t Need;

  if (Buffer == NULL || OutImage == NULL || ((uintptr_t)Buffer & 3U) != 0U)
    return -1;
  BlobSize = (size_t)(kshim_atlas_wallpaper_blob_end - kshim_atlas_wallpaper_blob);
  if (BlobSize < 4U)
    return -2;

  Ctx = (kshim_wallpaper_ctx_t){
    .Data = kshim_atlas_wallpaper_blob,
    .Size = BlobSize,
    .Offset = 0U,
    .Pixels = (uint32_t *)Buffer,
    .Width = 0U,
    .Height = 0U,
  };

  Res = jd_prepare(&Jd, WallpaperInput, mTjpgdPool, sizeof(mTjpgdPool), &Ctx);
  if (Res != JDR_OK || Jd.width == 0U || Jd.height == 0U)
    return -3;

  Need = (size_t)Jd.width * (size_t)Jd.height * sizeof(uint32_t);
  if (Capacity < Need)
    return -4;

  Ctx.Width = Jd.width;
  Ctx.Height = Jd.height;
  Res = jd_decomp(&Jd, WallpaperOutput, 0);
  if (Res != JDR_OK)
    return -5;

  *OutImage = (lv_image_dsc_t){
    .header = {
      .magic = LV_IMAGE_HEADER_MAGIC,
      .cf = LV_COLOR_FORMAT_ARGB8888,
      .flags = 0,
      .w = Jd.width,
      .h = Jd.height,
      .stride = (uint16_t)(Jd.width * sizeof(uint32_t)),
    },
    .data_size = (uint32_t)Need,
    .data = (const uint8_t *)Buffer,
  };
  return 0;
}
