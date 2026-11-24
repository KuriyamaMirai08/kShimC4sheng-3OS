#!/usr/bin/env python3
"""Generate 56x56 A8 anti-aliased Android Bugdroid and Apple icons for atlas_icons.h."""

import argparse
import math
import re
from pathlib import Path

SIZE = 56
SUPERSAMPLE = 8


def dist_segment(px, py, ax, ay, bx, by):
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    c1 = wx * vx + wy * vy
    if c1 <= 0.0:
        return math.hypot(px - ax, py - ay)
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        return math.hypot(px - bx, py - by)
    t = c1 / c2
    return math.hypot(px - (ax + t * vx), py - (ay + t * vy))


def dist_rounded_box(px, py, x0, y0, x1, y1, r_bottom):
    # Top corners sharp (r=0.4), bottom corners rounded (r_bottom)
    r = r_bottom if py > (y0 + y1) * 0.5 else 0.4
    cx = (x0 + x1) * 0.5
    cy = (y0 + y1) * 0.5
    hx = (x1 - x0) * 0.5 - r
    hy = (y1 - y0) * 0.5 - r
    dx = max(abs(px - cx) - hx, 0.0)
    dy = max(abs(py - cy) - hy, 0.0)
    return math.hypot(dx, dy) - r


def inside_android(x, y):
    # Head: semicircle centered at (28.0, 22.0), radius 11.5
    head = (y <= 22.0) and (math.hypot(x - 28.0, y - 22.0) <= 11.5)
    # Eye cutouts
    if head:
        if math.hypot(x - 23.0, y - 16.5) <= 1.65 or math.hypot(x - 33.0, y - 16.5) <= 1.65:
            head = False
    # Antennae
    ant_l = dist_segment(x, y, 22.6, 12.2, 18.8, 6.8) <= 1.05
    ant_r = dist_segment(x, y, 33.4, 12.2, 37.2, 6.8) <= 1.05
    # Torso: x in [16.5, 39.5], y in [24.0, 41.5], rounded bottom
    torso = dist_rounded_box(x, y, 16.5, 24.0, 39.5, 41.5, 3.0) <= 0.0
    # Arms
    arm_l = dist_segment(x, y, 12.2, 26.5, 12.2, 38.5) <= 2.55
    arm_r = dist_segment(x, y, 43.8, 26.5, 43.8, 38.5) <= 2.55
    # Legs
    leg_l = dist_segment(x, y, 22.5, 40.5, 22.5, 47.5) <= 2.65
    leg_r = dist_segment(x, y, 33.5, 40.5, 33.5, 47.5) <= 2.65
    return head or ant_l or ant_r or torso or arm_l or arm_r or leg_l or leg_r


def cubic_bezier(p0, p1, p2, p3, t):
    u = 1.0 - t
    return (
        u * u * u * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0] + t * t * t * p3[0],
        u * u * u * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1] + t * t * t * p3[1],
    )


def quad_bezier(p0, p1, p2, t):
    u = 1.0 - t
    return (
        u * u * p0[0] + 2 * u * t * p1[0] + t * t * p2[0],
        u * u * p0[1] + 2 * u * t * p1[1] + t * t * p2[1],
    )


def svg_arc_to_points(x0, y0, rx, ry, phi_deg, large_arc, sweep, x1, y1, steps=24):
    if (x0 == x1 and y0 == y1) or rx == 0.0 or ry == 0.0:
        return [(x1, y1)]
    rx, ry = abs(rx), abs(ry)
    phi = math.radians(phi_deg % 360.0)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx2 = (x0 - x1) * 0.5
    dy2 = (y0 - y1) * 0.5
    x1p = cos_p * dx2 + sin_p * dy2
    y1p = -sin_p * dx2 + cos_p * dy2
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    rx2, ry2 = rx * rx, ry * ry
    x1p2, y1p2 = x1p * x1p, y1p * y1p
    num = max(0.0, rx2 * ry2 - rx2 * y1p2 - ry2 * x1p2)
    den = rx2 * y1p2 + ry2 * x1p2
    factor = math.sqrt(num / den) if den > 1e-12 else 0.0
    if large_arc == sweep:
        factor = -factor
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)
    cx = cos_p * cxp - sin_p * cyp + (x0 + x1) * 0.5
    cy = sin_p * cxp + cos_p * cyp + (y0 + y1) * 0.5

    def vec_angle(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        det = ux * vy - uy * vx
        return math.atan2(det, dot)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = vec_angle(1.0, 0.0, ux, uy)
    dtheta = vec_angle(ux, uy, vx, vy)
    if not sweep and dtheta > 0:
        dtheta -= 2.0 * math.pi
    elif sweep and dtheta < 0:
        dtheta += 2.0 * math.pi

    pts = []
    for k in range(1, steps + 1):
        ang = theta1 + dtheta * (k / steps)
        ca, sa = math.cos(ang), math.sin(ang)
        px = cos_p * (rx * ca) - sin_p * (ry * sa) + cx
        py = sin_p * (rx * ca) + cos_p * (ry * sa) + cy
        pts.append((px, py))
    return pts


def sample_svg_subpaths(d, steps=24):
    """Parse an SVG path string (supporting multi-subpaths, M/L/H/V/C/Q/T/A/Z and compact arc flags)."""
    num_re = re.compile(r"\s*,?\s*([-+]?(?:\d*\.\d+|\d+)(?:[eE][-+]?\d+)?)")
    flag_re = re.compile(r"\s*,?\s*([01])")
    n = len(d)
    pos = 0

    def skip_ws():
        nonlocal pos
        while pos < n and d[pos] in " \t\r\n,":
            pos += 1

    def read_num():
        nonlocal pos
        m = num_re.match(d, pos)
        if not m:
            raise ValueError(f"Expected number at {pos}: {d[pos:pos+24]!r}")
        pos = m.end()
        return float(m.group(1))

    def read_flag():
        nonlocal pos
        m = flag_re.match(d, pos)
        if not m:
            raise ValueError(f"Expected arc flag at {pos}: {d[pos:pos+24]!r}")
        pos = m.end()
        return int(m.group(1))

    def has_more_nums():
        skip_ws()
        return pos < n and not d[pos].isalpha()

    subpaths = []
    pts = []
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0
    last_q_ctrl = None
    cmd = None

    while True:
        skip_ws()
        if pos >= n:
            break
        if d[pos].isalpha():
            cmd = d[pos]
            pos += 1
        elif cmd is None:
            raise ValueError(f"Missing SVG command at {pos}")

        if cmd in "Zz":
            if pts:
                if pts[-1] != (sx, sy):
                    pts.append((sx, sy))
                subpaths.append(pts)
                pts = []
            cx, cy = sx, sy
            last_q_ctrl = None
            cmd = None
            continue

        if cmd in "Mm":
            x, y = read_num(), read_num()
            if cmd == "m":
                x, y = cx + x, cy + y
            if pts:
                subpaths.append(pts)
            pts = [(x, y)]
            cx, cy = x, y
            sx, sy = x, y
            cmd = "L" if cmd == "M" else "l"
            last_q_ctrl = None
        elif cmd in "Ll":
            while has_more_nums():
                x, y = read_num(), read_num()
                if cmd == "l":
                    x, y = cx + x, cy + y
                pts.append((x, y))
                cx, cy = x, y
            last_q_ctrl = None
        elif cmd in "Hh":
            while has_more_nums():
                x = read_num()
                if cmd == "h":
                    x = cx + x
                pts.append((x, cy))
                cx = x
            last_q_ctrl = None
        elif cmd in "Vv":
            while has_more_nums():
                y = read_num()
                if cmd == "v":
                    y = cy + y
                pts.append((cx, y))
                cy = y
            last_q_ctrl = None
        elif cmd in "Cc":
            while has_more_nums():
                x1, y1, x2, y2, x, y = [read_num() for _ in range(6)]
                if cmd == "c":
                    x1, y1 = cx + x1, cy + y1
                    x2, y2 = cx + x2, cy + y2
                    x, y = cx + x, cy + y
                p0, p1, p2, p3 = (cx, cy), (x1, y1), (x2, y2), (x, y)
                for s in range(1, steps + 1):
                    pts.append(cubic_bezier(p0, p1, p2, p3, s / steps))
                cx, cy = x, y
            last_q_ctrl = None
        elif cmd in "Qq":
            while has_more_nums():
                x1, y1, x, y = [read_num() for _ in range(4)]
                if cmd == "q":
                    x1, y1 = cx + x1, cy + y1
                    x, y = cx + x, cy + y
                p0, p1, p2 = (cx, cy), (x1, y1), (x, y)
                for s in range(1, steps + 1):
                    pts.append(quad_bezier(p0, p1, p2, s / steps))
                last_q_ctrl = (x1, y1)
                cx, cy = x, y
        elif cmd in "Tt":
            while has_more_nums():
                x, y = read_num(), read_num()
                if cmd == "t":
                    x, y = cx + x, cy + y
                if last_q_ctrl is not None:
                    x1, y1 = 2 * cx - last_q_ctrl[0], 2 * cy - last_q_ctrl[1]
                else:
                    x1, y1 = cx, cy
                p0, p1, p2 = (cx, cy), (x1, y1), (x, y)
                for s in range(1, steps + 1):
                    pts.append(quad_bezier(p0, p1, p2, s / steps))
                last_q_ctrl = (x1, y1)
                cx, cy = x, y
        elif cmd in "Aa":
            while has_more_nums():
                rx, ry, rot = read_num(), read_num(), read_num()
                fa, fs = read_flag(), read_flag()
                x, y = read_num(), read_num()
                if cmd == "a":
                    x, y = cx + x, cy + y
                pts.extend(svg_arc_to_points(cx, cy, rx, ry, rot, fa, fs, x, y, steps=steps))
                cx, cy = x, y
            last_q_ctrl = None
        else:
            raise ValueError(f"Unsupported SVG cmd: {cmd} at {pos}")

    if pts:
        subpaths.append(pts)
    return subpaths


def point_in_poly(x, y, poly):
    inside = False
    n = len(poly)
    for idx in range(n):
        x1, y1 = poly[idx]
        x2, y2 = poly[(idx + 1) % n]
        if (y1 > y) != (y2 > y):
            x_int = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < x_int:
                inside = not inside
    return inside


def normalize_polys(raw_polys):
    all_pts = [p for poly in raw_polys for p in poly]
    min_x = min(p[0] for p in all_pts)
    max_x = max(p[0] for p in all_pts)
    min_y = min(p[1] for p in all_pts)
    max_y = max(p[1] for p in all_pts)
    w = max_x - min_x
    h = max_y - min_y
    target = SIZE - 10.0  # ~5 px margin on all sides -> 46x46
    scale = target / max(w, h)
    cx = (min_x + max_x) * 0.5
    cy = (min_y + max_y) * 0.5
    scaled = [
        [((px - cx) * scale + SIZE * 0.5, (py - cy) * scale + SIZE * 0.5) for px, py in poly]
        for poly in raw_polys
    ]
    bboxes = [
        (
            min(p[0] for p in poly),
            min(p[1] for p in poly),
            max(p[0] for p in poly),
            max(p[1] for p in poly),
        )
        for poly in scaled
    ]
    return scaled, bboxes


def build_apple_polys():
    body_d = (
        "M318.7 268.7c-.2-36.7 16.4-64.4 50-84.8-18.8-26.9-47.2-41.7-84.7-44.6"
        "-35.5-2.8-74.3 20.7-88.5 20.7-15 0-49.4-19.7-76.4-19.7C63.3 141.2 4 184.8 4 273.5"
        "q0 39.3 14.4 81.2c12.8 36.7 59 126.7 107.2 125.2 25.2-.6 43-17.9 75.8-17.9"
        " 31.8 0 48.3 17.9 76.4 17.9 48.6-.7 90.4-82.5 102.6-119.3"
        "-65.2-30.7-61.7-90-61.7-91.9z"
    )
    leaf_d = (
        "M256.3 116.5c27.3-32.4 24.8-61.9 24-72.5-24.1 1.4-52 16.4-67.9 34.9"
        "-17.5 19.8-27.8 44.3-25.6 71.9 26.1 2 49.9-11.4 69.5-34.3z"
    )
    return normalize_polys(sample_svg_subpaths(body_d, steps=40) + sample_svg_subpaths(leaf_d, steps=40))


def build_tux_polys():
    # Official Simple Icons Linux Tux (viewBox 0 0 24 24)
    tux_d = (
        "M12.504 0c-.155 0-.315.008-.48.021-4.226.333-3.105 4.807-3.17 6.298-.076 1.092-.3 1.953-1.05 3.02"
        "-.885 1.051-2.127 2.75-2.716 4.521-.278.832-.41 1.684-.287 2.489a.424.424 0 00-.11.135"
        "c-.26.268-.45.6-.663.839-.199.199-.485.267-.797.4-.313.136-.658.269-.864.68"
        "-.09.189-.136.394-.132.602 0 .199.027.4.055.536.058.399.116.728.04.97-.249.68-.28 1.145-.106 1.484"
        ".174.334.535.47.94.601.81.2 1.91.135 2.774.6.926.466 1.866.67 2.616.47.526-.116.97-.464 1.208-.946"
        ".587-.003 1.23-.269 2.26-.334.699-.058 1.574.267 2.577.2.025.134.063.198.114.333l.003.003"
        "c.391.778 1.113 1.132 1.884 1.071.771-.06 1.592-.536 2.257-1.306.631-.765 1.683-1.084 2.378-1.503"
        ".348-.199.629-.469.649-.853.023-.4-.2-.811-.714-1.376v-.097l-.003-.003c-.17-.2-.25-.535-.338-.926"
        "-.085-.401-.182-.786-.492-1.046h-.003c-.059-.054-.123-.067-.188-.135a.357.357 0 00-.19-.064"
        "c.431-1.278.264-2.55-.173-3.694-.533-1.41-1.465-2.638-2.175-3.483-.796-1.005-1.576-1.957-1.56-3.368"
        ".026-2.152.236-6.133-3.544-6.139zm.529 3.405h.013c.213 0 .396.062.584.198.19.135.33.332.438.533"
        ".105.259.158.459.166.724 0-.02.006-.04.006-.06v.105a.086.086 0 01-.004-.021l-.004-.024"
        "a1.807 1.807 0 01-.15.706.953.953 0 01-.213.335.71.71 0 00-.088-.042c-.104-.045-.198-.064-.284-.133"
        "a1.312 1.312 0 00-.22-.066c.05-.06.146-.133.183-.198.053-.128.082-.264.088-.402v-.02"
        "a1.21 1.21 0 00-.061-.4c-.045-.134-.101-.2-.183-.333-.084-.066-.167-.132-.267-.132h-.016"
        "c-.093 0-.176.03-.262.132a.8.8 0 00-.205.334 1.18 1.18 0 00-.09.4v.019c.002.089.008.179.02.267"
        "-.193-.067-.438-.135-.607-.202a1.635 1.635 0 01-.018-.2v-.02a1.772 1.772 0 01.15-.768"
        "c.082-.22.232-.406.43-.533a.985.985 0 01.594-.2zm-2.962.059h.036c.142 0 .27.048.399.135"
        ".146.129.264.288.344.465.09.199.14.4.153.667v.004c.007.134.006.2-.002.266v.08"
        "c-.03.007-.056.018-.083.024-.152.055-.274.135-.393.2.012-.09.013-.18.003-.267v-.015"
        "c-.012-.133-.04-.2-.082-.333a.613.613 0 00-.166-.267.248.248 0 00-.183-.064h-.021"
        "c-.071.006-.13.04-.186.132a.552.552 0 00-.12.27.944.944 0 00-.023.33v.015"
        "c.012.135.037.2.08.334.046.134.098.2.166.268.01.009.02.018.034.024-.07.057-.117.07-.176.136"
        "a.304.304 0 01-.131.068 2.62 2.62 0 01-.275-.402 1.772 1.772 0 01-.155-.667 1.759 1.759 0 01.08-.668"
        " 1.43 1.43 0 01.283-.535c.128-.133.26-.2.418-.2zm1.37 1.706c.332 0 .733.065 1.216.399"
        ".293.2.523.269 1.052.468h.003c.255.136.405.266.478.399v-.131a.571.571 0 01.016.47"
        "c-.123.31-.516.643-1.063.842v.002c-.268.135-.501.333-.775.465-.276.135-.588.292-1.012.267"
        "a1.139 1.139 0 01-.448-.067 3.566 3.566 0 01-.322-.198c-.195-.135-.363-.332-.612-.465v-.005h-.005"
        "c-.4-.246-.616-.512-.686-.71-.07-.268-.005-.47.193-.6.224-.135.38-.271.483-.336"
        ".104-.074.143-.102.176-.131h.002v-.003c.169-.202.436-.47.839-.601.139-.036.294-.065.466-.065z"
        "m2.8 2.142c.358 1.417 1.196 3.475 1.735 4.473.286.534.855 1.659 1.102 3.024.156-.005.33.018.513.064"
        ".646-1.671-.546-3.467-1.089-3.966-.22-.2-.232-.335-.123-.335.59.534 1.365 1.572 1.646 2.757"
        ".13.535.16 1.104.021 1.67.067.028.135.06.205.067 1.032.534 1.413.938 1.23 1.537v-.043"
        "c-.06-.003-.12 0-.18 0h-.016c.151-.467-.182-.825-1.065-1.224-.915-.4-1.646-.336-1.77.465"
        "-.008.043-.013.066-.018.135-.068.023-.139.053-.209.064-.43.268-.662.669-.793 1.187"
        "-.13.533-.17 1.156-.205 1.869v.003c-.02.334-.17.838-.319 1.35-1.5 1.072-3.58 1.538-5.348.334"
        "a2.645 2.645 0 00-.402-.533 1.45 1.45 0 00-.275-.333c.182 0 .338-.03.465-.067a.615.615 0 00.314-.334"
        "c.108-.267 0-.697-.345-1.163-.345-.467-.931-.995-1.788-1.521-.63-.4-.986-.87-1.15-1.396"
        "-.165-.534-.143-1.085-.015-1.645.245-1.07.873-2.11 1.274-2.763.107-.065.037.135-.408.974"
        "-.396.751-1.14 2.497-.122 3.854a8.123 8.123 0 01.647-2.876c.564-1.278 1.743-3.504 1.836-5.268"
        ".048.036.217.135.289.202.218.133.38.333.59.465.21.201.477.335.876.335.039.003.075.006.11.006"
        ".412 0 .73-.134.997-.268.29-.134.52-.334.74-.4h.005c.467-.135.835-.402 1.044-.7z"
        "m2.185 8.958c.037.6.343 1.245.882 1.377.588.134 1.434-.333 1.791-.765l.211-.01"
        "c.315-.007.577.01.847.268l.003.003c.208.199.305.53.391.876.085.4.154.78.409 1.066"
        ".486.527.645.906.636 1.14l.003-.007v.018l-.003-.012c-.015.262-.185.396-.498.595"
        "-.63.401-1.746.712-2.457 1.57-.618.737-1.37 1.14-2.036 1.191-.664.053-1.237-.2-1.574-.898"
        "l-.005-.003c-.21-.4-.12-1.025.056-1.69.176-.668.428-1.344.463-1.897.037-.714.076-1.335.195-1.814"
        ".12-.465.308-.797.641-.984l.045-.022zm-10.814.049h.01c.053 0 .105.005.157.014"
        ".376.055.706.333 1.023.752l.91 1.664.003.003c.243.533.754 1.064 1.189 1.637"
        ".434.598.77 1.131.729 1.57v.006c-.057.744-.48 1.148-1.125 1.294-.645.135-1.52.002-2.395-.464"
        "-.968-.536-2.118-.469-2.857-.602-.369-.066-.61-.2-.723-.4-.11-.2-.113-.602.123-1.23v-.004l.002-.003"
        "c.117-.334.03-.752-.027-1.118-.055-.401-.083-.71.043-.94.16-.334.396-.4.69-.533"
        ".294-.135.64-.202.915-.47h.002v-.002c.256-.268.445-.601.668-.838.19-.201.38-.336.663-.336z"
        "m7.159-9.074c-.435.201-.945.535-1.488.535-.542 0-.97-.267-1.28-.466-.154-.134-.28-.268-.373-.335"
        "-.164-.134-.144-.333-.074-.333.109.016.129.134.199.2.096.066.215.2.36.333.292.2.68.467 1.167.467"
        ".485 0 1.053-.267 1.398-.466.195-.135.445-.334.648-.467.156-.136.149-.267.279-.267"
        ".128.016.034.134-.147.332a8.097 8.097 0 01-.69.468zm-1.082-1.583V5.64"
        "c-.006-.02.013-.042.029-.05.074-.043.18-.027.26.004.063 0 .16.067.15.135"
        "-.006.049-.085.066-.135.066-.055 0-.092-.043-.141-.068-.052-.018-.146-.008-.163-.065z"
        "m-.551 0c-.02.058-.113.049-.166.066-.047.025-.086.068-.14.068-.05 0-.13-.02-.136-.068"
        "-.01-.066.088-.133.15-.133.08-.031.184-.047.259-.005.019.009.036.03.03.05v.02h.003z"
    )
    return normalize_polys(sample_svg_subpaths(tux_d, steps=20))


APPLE_POLYS, APPLE_BBOXES = build_apple_polys()
TUX_POLYS, TUX_BBOXES = build_tux_polys()


def inside_apple(x, y):
    for poly, (x0, y0, x1, y1) in zip(APPLE_POLYS, APPLE_BBOXES):
        if x0 <= x <= x1 and y0 <= y <= y1 and point_in_poly(x, y, poly):
            return True
    return False


def inside_tux(x, y):
    inside = False
    for poly, (x0, y0, x1, y1) in zip(TUX_POLYS, TUX_BBOXES):
        if x0 <= x <= x1 and y0 <= y <= y1 and point_in_poly(x, y, poly):
            inside = not inside
    return inside


def rasterize(fn):
    grid = []
    step = 1.0 / SUPERSAMPLE
    offset = step * 0.5
    total = SUPERSAMPLE * SUPERSAMPLE
    for py in range(SIZE):
        for px in range(SIZE):
            hits = 0
            for sy in range(SUPERSAMPLE):
                y = py + offset + sy * step
                for sx in range(SUPERSAMPLE):
                    x = px + offset + sx * step
                    if fn(x, y):
                        hits += 1
            grid.append(round(255 * hits / total))
    return grid


def format_c_array(arr):
    lines = []
    for i in range(0, len(arr), 16):
        chunk = ", ".join(str(v) for v in arr[i : i + 16])
        lines.append(f"  {chunk},")
    return "\n".join(lines)


def ascii_preview(arr, title):
    print(f"=== {title} (28x28) ===")
    for y in range(0, SIZE, 2):
        row = []
        for x in range(0, SIZE, 2):
            v = (arr[y * SIZE + x] + arr[y * SIZE + x + 1] + arr[(y + 1) * SIZE + x] + arr[(y + 1) * SIZE + x + 1]) // 4
            row.append("#" if v >= 160 else "+" if v >= 64 else ".")
        print("".join(row))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--header", type=Path, help="Path to atlas_icons.h to update in place")
    ap.add_argument("--preview", action="store_true")
    ap.add_argument("--preview-png", type=Path, help="Save 224x224 preview PNG of Tux")
    args = ap.parse_args()

    android = rasterize(inside_android)
    apple = rasterize(inside_apple)
    tux = rasterize(inside_tux)

    if args.preview or not args.header:
        ascii_preview(android, "Android Bugdroid")
        ascii_preview(apple, "Apple Logo")
        ascii_preview(tux, "Linux Tux")

    if args.preview_png:
        from PIL import Image
        im = Image.new("RGBA", (SIZE, SIZE), (13, 21, 54, 255))
        px = im.load()
        for py in range(SIZE):
            for pxx in range(SIZE):
                a = tux[py * SIZE + pxx]
                r = round(13 + (245 - 13) * a / 255)
                g = round(21 + (248 - 21) * a / 255)
                b = round(54 + (255 - 54) * a / 255)
                px[pxx, py] = (r, g, b, 255)
        args.preview_png.parent.mkdir(parents=True, exist_ok=True)
        im.resize((224, 224), Image.Resampling.NEAREST).save(args.preview_png)

    if args.header:
        text = args.header.read_text(encoding="utf-8")
        text, n1 = re.subn(
            r"(static const uint8_t AtlasIconAndroidMap\[56 \* 56\] = \{\n)(.*?)(\n\};)",
            lambda m: m.group(1) + format_c_array(android) + m.group(3),
            text,
            count=1,
            flags=re.S,
        )
        text, n2 = re.subn(
            r"(static const uint8_t AtlasIconLinuxMap\[56 \* 56\] = \{\n)(.*?)(\n\};)",
            lambda m: m.group(1) + format_c_array(apple) + m.group(3),
            text,
            count=1,
            flags=re.S,
        )
        if n1 != 1 or n2 != 1:
            raise SystemExit(f"Failed to patch atlas_icons.h: n1={n1}, n2={n2}")

        tux_block = (
            "static const uint8_t AtlasIconTuxMap[56 * 56] = {\n"
            + format_c_array(tux)
            + "\n};\n"
            "static const lv_image_dsc_t AtlasIconTux = {\n"
            "  .header = {.magic = LV_IMAGE_HEADER_MAGIC, .cf = LV_COLOR_FORMAT_A8, .w = 56, .h = 56, .stride = 56},\n"
            "  .data_size = sizeof(AtlasIconTuxMap), .data = AtlasIconTuxMap,\n"
            "};\n"
        )
        if "AtlasIconTuxMap" in text:
            text, nt = re.subn(
                r"static const uint8_t AtlasIconTuxMap\[56 \* 56\] = \{\n.*?\n\};\nstatic const lv_image_dsc_t AtlasIconTux = \{\n.*?\n\};\n",
                lambda m: tux_block,
                text,
                count=1,
                flags=re.S,
            )
            assert nt == 1
        else:
            text = text.replace(
                "static const lv_image_dsc_t *AtlasIconAsset",
                tux_block + "\nstatic const lv_image_dsc_t *AtlasIconAsset",
                1,
            )

        text = text.replace(
            "case KSHIM_MENU_ROLE_INTERNAL: case KSHIM_MENU_ROLE_TF: return &AtlasIconLinux;",
            "case KSHIM_MENU_ROLE_INTERNAL: return &AtlasIconLinux;\n  case KSHIM_MENU_ROLE_TF: return &AtlasIconTux;",
        )
        args.header.write_text(text, encoding="utf-8")
        print(f"Updated {args.header} (Android + Apple + Tux 56x56 A8 icons)")


if __name__ == "__main__":
    main()
