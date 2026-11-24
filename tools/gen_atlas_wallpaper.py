#!/usr/bin/env python3
"""Generate 1536x1024 crisp unblurred starry-night baseline JPEG wallpaper."""

import argparse
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw


OUT_W, OUT_H = 2400, 1600
LOG_W, LOG_H = 3048, 2032


def atlas_panel_rects():
    margin = LOG_W * 21 // 1000
    header_h = LOG_H * 74 // 1000
    footer_h = LOG_H * 46 // 1000
    side_w = LOG_W * 250 // 1000
    content_x = margin + side_w + LOG_W * 30 // 1000
    content_w = LOG_W - content_x - margin
    panel_h = LOG_H - header_h - footer_h - 20
    radius = 24
    sidebar = (margin, header_h, margin + side_w, header_h + panel_h)
    content = (content_x, header_h, content_x + content_w, header_h + panel_h)
    return sidebar, content, radius


def add_vignettes(rgb_arr):
    h, _, _ = rgb_arr.shape
    y = np.arange(h, dtype=np.float32)[:, None]
    alpha = np.zeros((h, 1), dtype=np.float32)

    top_h = 105.0
    top_mask = y < top_h
    alpha[top_mask] = 0.38 * ((1.0 - y[top_mask] / top_h) ** 1.6)

    bot_start = h - 85.0
    bot_mask = y >= bot_start
    alpha[bot_mask] = 0.42 * (((y[bot_mask] - bot_start) / 85.0) ** 1.6)

    out = rgb_arr * (1.0 - alpha[..., None])
    return np.clip(out, 0.0, 255.0)


def generate(src_path: Path, out_jpg: Path, preview_png: Path | None, max_bytes: int):
    src = Image.open(src_path).convert("RGB")
    sw, sh = src.size
    target_w = round(sh * 3 / 2)
    if sw > target_w:
        x0 = (sw - target_w) // 2
        src = src.crop((x0, 0, x0 + target_w, sh))
    base = src.resize((OUT_W, OUT_H), Image.Resampling.LANCZOS)

    base_arr = add_vignettes(np.asarray(base, dtype=np.float32))
    final_img = Image.fromarray(base_arr.astype(np.uint8), mode="RGB")

    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    for q in (88, 86, 84, 80):
        final_img.save(
            out_jpg,
            format="JPEG",
            quality=q,
            progressive=False,
            optimize=True,
            subsampling="4:2:0",
        )
        sz = out_jpg.stat().st_size
        if sz <= max_bytes:
            print(f"Saved {out_jpg}: {OUT_W}x{OUT_H} baseline JPEG q={q}, {sz} B")
            break
    else:
        print(f"WARNING: {out_jpg} is {out_jpg.stat().st_size} B (> {max_bytes} B)")

    if preview_png is not None:
        sidebar, content, radius = atlas_panel_rects()
        prev = final_img.convert("RGBA")
        over = Image.new("RGBA", (OUT_W, OUT_H), (0, 0, 0, 0))
        d = ImageDraw.Draw(over)
        sx, sy = OUT_W / LOG_W, OUT_H / LOG_H
        for rect in (sidebar, content):
            r_scaled = (rect[0] * sx, rect[1] * sy, rect[2] * sx, rect[3] * sy)
            d.rounded_rectangle(
                r_scaled,
                radius=radius * sx,
                fill=(9, 15, 38, 76),
                outline=(157, 184, 255, 76),
                width=1,
            )
        prev = Image.alpha_composite(prev, over).convert("RGB")
        preview_png.parent.mkdir(parents=True, exist_ok=True)
        prev.save(preview_png, format="PNG")
        print(f"Saved preview {preview_png}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--preview", type=Path)
    ap.add_argument("--max-bytes", type=int, default=310 * 1024)
    args = ap.parse_args()
    generate(args.src, args.out, args.preview, args.max_bytes)


if __name__ == "__main__":
    main()