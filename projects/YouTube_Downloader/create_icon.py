#!/usr/bin/env python3
"""Generate YouTube Downloader.icns icon. Requires: pip install pillow"""
import os
import shutil
import subprocess

from PIL import Image, ImageDraw

SIZE = 1024
ACCENT = (131, 88, 255, 255)      # #8358FF
WHITE = (255, 255, 255, 255)
BADGE_RED = (255, 82, 82, 255)


def make_icon():
    img = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    radius = 220
    draw.rounded_rectangle([0, 0, SIZE, SIZE], radius=radius, fill=ACCENT)

    # Download glyph: thick shaft + chevron head, centered
    cx, cy = SIZE // 2, SIZE // 2 - 90
    shaft_w = 90
    shaft_top = cy - 190
    shaft_bottom = cy + 40
    draw.rounded_rectangle(
        [cx - shaft_w // 2, shaft_top, cx + shaft_w // 2, shaft_bottom],
        radius=shaft_w // 2, fill=WHITE,
    )
    head_r = 180
    draw.polygon([
        (cx - head_r, cy - 40),
        (cx + head_r, cy - 40),
        (cx, cy + head_r - 40),
    ], fill=WHITE)

    # Tray/base line under the arrow
    tray_w = 420
    tray_y = cy + 220
    draw.rounded_rectangle(
        [cx - tray_w // 2, tray_y, cx + tray_w // 2, tray_y + 55],
        radius=27, fill=WHITE,
    )

    # Video badge, bottom-right corner
    badge_r = 165
    badge_cx, badge_cy = SIZE - 230, SIZE - 230
    draw.ellipse(
        [badge_cx - badge_r, badge_cy - badge_r, badge_cx + badge_r, badge_cy + badge_r],
        fill=BADGE_RED, outline=ACCENT, width=20,
    )
    tri = 90
    draw.polygon([
        (badge_cx - tri * 0.5, badge_cy - tri * 0.62),
        (badge_cx - tri * 0.5, badge_cy + tri * 0.62),
        (badge_cx + tri * 0.75, badge_cy),
    ], fill=WHITE)

    return img


def build_icns(img, out_path):
    iconset = out_path.replace(".icns", ".iconset")
    os.makedirs(iconset, exist_ok=True)
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for s in sizes:
        img.resize((s, s), Image.LANCZOS).save(os.path.join(iconset, f"icon_{s}x{s}.png"))
        if s <= 512:
            img.resize((s * 2, s * 2), Image.LANCZOS).save(
                os.path.join(iconset, f"icon_{s}x{s}@2x.png"))
    subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out_path], check=True)
    shutil.rmtree(iconset)
    print(f"Icon created: {out_path}")


if __name__ == "__main__":
    here = os.path.dirname(os.path.abspath(__file__))
    build_icns(make_icon(), os.path.join(here, "icon.icns"))
