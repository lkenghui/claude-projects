#!/usr/bin/env python3
"""Generate Notes AI Assistant.icns icon."""
import os, subprocess, shutil, math
from PIL import Image, ImageDraw, ImageFont

SIZE = 1024

def make_icon():
    img = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # Rounded rect background (blue)
    radius = 220
    bg_color = (0, 122, 255, 255)
    draw.rounded_rectangle([0, 0, SIZE, SIZE], radius=radius, fill=bg_color)

    # White clipboard body
    cx, cy = SIZE // 2, SIZE // 2 + 30
    cw, ch = 440, 520
    cr = 40
    x0, y0 = cx - cw // 2, cy - ch // 2
    x1, y1 = cx + cw // 2, cy + ch // 2
    draw.rounded_rectangle([x0, y0, x1, y1], radius=cr, fill=(255, 255, 255, 255))

    # Clipboard clip (top centre)
    clip_w, clip_h = 160, 70
    clip_r = 20
    clipx0 = cx - clip_w // 2
    clipy0 = y0 - clip_h // 2
    draw.rounded_rectangle([clipx0, clipy0, clipx0 + clip_w, clipy0 + clip_h],
                            radius=clip_r, fill=(255, 255, 255, 255))
    # Inner clip hole
    hole_w, hole_h = 90, 40
    draw.rounded_rectangle([cx - hole_w//2, clipy0 + 15, cx + hole_w//2, clipy0 + 15 + hole_h],
                            radius=10, fill=bg_color)

    # Lines on clipboard
    line_color = (0, 122, 255, 200)
    line_x0, line_x1 = x0 + 60, x1 - 60
    for i, ly in enumerate([y0 + 130, y0 + 200, y0 + 270, y0 + 340]):
        w = line_x1 - line_x0 if i < 3 else (line_x1 - line_x0) * 0.6
        draw.rounded_rectangle([line_x0, ly, line_x0 + w, ly + 28], radius=14, fill=line_color)

    # Small star/sparkle (AI indicator) bottom right of clipboard
    star_cx, star_cy = x1 - 80, y1 - 80
    star_r = 45
    star_color = (0, 122, 255, 255)
    draw.ellipse([star_cx - star_r, star_cy - star_r, star_cx + star_r, star_cy + star_r],
                 fill=(255, 255, 255, 255))
    # Draw a small lightning bolt / star
    pts = []
    for i in range(8):
        angle = math.pi * i / 4 - math.pi / 8
        r = star_r * 0.6 if i % 2 == 0 else star_r * 0.28
        pts.append((star_cx + r * math.cos(angle), star_cy + r * math.sin(angle)))
    draw.polygon(pts, fill=star_color)

    return img


def build_icns(img, out_path):
    iconset = out_path.replace('.icns', '.iconset')
    os.makedirs(iconset, exist_ok=True)
    sizes = [16, 32, 64, 128, 256, 512, 1024]
    for s in sizes:
        resized = img.resize((s, s), Image.LANCZOS)
        resized.save(os.path.join(iconset, f'icon_{s}x{s}.png'))
        if s <= 512:
            resized2 = img.resize((s * 2, s * 2), Image.LANCZOS)
            resized2.save(os.path.join(iconset, f'icon_{s}x{s}@2x.png'))
    subprocess.run(['iconutil', '-c', 'icns', iconset, '-o', out_path], check=True)
    shutil.rmtree(iconset)
    print(f"Icon created: {out_path}")


if __name__ == '__main__':
    here = os.path.dirname(os.path.abspath(__file__))
    out = os.path.join(here, 'icon.icns')
    img = make_icon()
    build_icns(img, out)
