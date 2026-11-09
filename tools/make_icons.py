# -*- coding: utf-8 -*-
"""把介面用到的 emoji 轉成各尺寸的彩色 PNG（開發時執行一次，需要 Pillow）。

    python tools/make_icons.py

圖案取自 Windows 內建的 Segoe UI Emoji，也就是 Microsoft Fluent Emoji（MIT 授權）。
"""

import os
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from ui.icons import EMOJI, SIZES, icon_file  # noqa: E402

FONT = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts", "seguiemj.ttf")


def render(ch, size):
    font = ImageFont.truetype(FONT, 109)
    big = Image.new("RGBA", (160, 160), (0, 0, 0, 0))
    ImageDraw.Draw(big).text((10, 10), ch, font=font, embedded_color=True)
    box = big.getbbox()
    if box is None:
        raise ValueError(f"字型裡沒有 {ch!r}")
    glyph = big.crop(box)
    inner = size - max(1, size // 16)  # 留一點邊
    glyph.thumbnail((inner, inner), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(glyph, ((size - glyph.width) // 2, (size - glyph.height) // 2), glyph)
    return out


def main():
    out_dir = os.path.join(ROOT, "assets", "icons")
    os.makedirs(out_dir, exist_ok=True)
    for ch in EMOJI:
        for size in SIZES:
            render(ch, size).save(os.path.join(out_dir, icon_file(ch, size)), optimize=True)
    print(f"{len(EMOJI)} 個圖示 × {len(SIZES)} 種尺寸 → {out_dir}")


if __name__ == "__main__":
    main()
