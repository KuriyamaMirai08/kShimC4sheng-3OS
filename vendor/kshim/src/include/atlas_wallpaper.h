#ifndef KSHIM_ATLAS_WALLPAPER_H
#define KSHIM_ATLAS_WALLPAPER_H

#include <stddef.h>
#include <lvgl.h>

int kshim_atlas_wallpaper_decode(void *Buffer, size_t Capacity, lv_image_dsc_t *OutImage);

#endif /* KSHIM_ATLAS_WALLPAPER_H */
