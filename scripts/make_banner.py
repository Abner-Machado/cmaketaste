#!/usr/bin/env python3
"""Draw the repository banner.

It shows a finding, because the finding is the product: a rule, its confidence,
a file and a line, and a sentence saying what breaks.

    python scripts/make_banner.py assets/banner.png

Needs Pillow. Nothing else here needs anything.
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

W, H = 1280, 640

BG = (12, 12, 14)
PANEL = (22, 22, 26)
EDGE = (42, 42, 48)
FG = (236, 236, 238)
DIM = (128, 130, 138)
ACCENT = (224, 153, 92)
CONF = (126, 186, 140)

WIN_FONTS = r"C:\Windows\Fonts"
CANDIDATES = {
    "mono": ["consola.ttf", "DejaVuSansMono.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"],
    "mono_bold": ["consolab.ttf", "DejaVuSansMono-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"],
    "sans": ["segoeui.ttf", "DejaVuSans.ttf",
             "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"],
    "sans_bold": ["segoeuib.ttf", "DejaVuSans-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"],
}


def load(kind, size):
    for name in CANDIDATES[kind]:
        for path in (name, os.path.join(WIN_FONTS, name)):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main(out="assets/banner.png"):
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    f_title = load("sans_bold", 60)
    f_tag = load("sans", 26)
    f_rule = load("mono_bold", 20)
    f_code = load("mono", 19)
    f_small = load("mono", 18)

    d.rectangle([0, 0, W, 3], fill=ACCENT)

    x = 74
    d.text((x, 84), "cmaketaste", font=f_title, fill=FG)
    d.text((x, 162), "audits the build, not the formatting", font=f_tag, fill=ACCENT)
    d.text((x, 204), "Twenty rules for a C++ project's CMake setup,", font=f_tag, fill=DIM)
    d.text((x, 236), "each one carrying how sure it is of itself.", font=f_tag, fill=DIM)

    px, py, pw, ph = 74, 312, W - 148, 226
    d.rounded_rectangle([px, py, px + pw, py + ph], radius=10, fill=PANEL, outline=EDGE)

    cx, cy = px + 26, py + 22
    d.text((cx, cy), "pinned-dependencies", font=f_rule, fill=FG)
    w = d.textlength("pinned-dependencies  ", font=f_rule)
    d.text((cx + w, cy), "[0.90]", font=f_rule, fill=CONF)
    w2 = d.textlength("pinned-dependencies  [0.90]  ", font=f_rule)
    d.text((cx + w2, cy), "Pin every fetched dependency", font=f_code, fill=DIM)

    d.text((cx + 16, cy + 42), "CMakeLists.txt:15", font=f_code, fill=ACCENT)
    for i, line in enumerate([
            "fmt is pinned to 'master', which is a moving branch.",
            "Two people configuring on different days get different source."]):
        d.text((cx + 40, cy + 74 + i * 26), line, font=f_code, fill=FG)

    d.text((cx + 16, cy + 140), "CMakeLists.txt:18", font=f_code, fill=ACCENT)
    d.text((cx + 40, cy + 170), "json is fetched with no GIT_TAG at all.",
           font=f_code, fill=FG)

    d.text((74, H - 56), "MIT  ·  python standard library only  ·  no CMake required",
           font=f_small, fill=DIM)

    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    img.save(out)
    print("wrote %s  %dx%d" % (out, W, H))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "assets/banner.png"))
