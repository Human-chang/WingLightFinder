# -*- coding: utf-8 -*-
"""介面素材：原始碼裡沒有 emoji、用到的線條圖示都有檔案、遮罩 PNG 讀得懂、塗色後是正常的 PNG。"""
import base64
import os
import re
import struct
import sys
import unittest
import zlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.config import DEFAULT_GROUPS  # noqa: E402
from ui.about import MARK_SIZES  # noqa: E402
from ui.winglight_tk import ICON_SIZES, Mask, read_mask, render, rotate_mask  # noqa: E402

# Emoji 與常被當成 emoji 顯示的符號：表情與圖形符號、技術符號（U+23xx 的沙漏、播放鍵）、雜項符號、裝飾符號、
# 星星等（U+2B50）、幾何圖形裡的播放鍵與小方塊、變體選擇符 U+FE0F。全形加號、｜、…、／ 這些微軟正黑體內建的不算。
EMOJI_RANGES = [(0x1F000, 0x1FAFF), (0x2300, 0x23FF), (0x2600, 0x27BF), (0x2B00, 0x2BFF),
                (0x25AA, 0x25AB), (0x25B6, 0x25B6), (0x25C0, 0x25C0), (0x25FB, 0x25FE), (0xFE0F, 0xFE0F)]
EMOJI = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in EMOJI_RANGES) + "]")
ICON_DIR = os.path.join(ROOT, "assets", "icons")


def source_files():
    for folder in ("core", "ui", "tools"):
        for name in sorted(os.listdir(os.path.join(ROOT, folder))):
            if name.endswith(".py"):
                yield os.path.join(folder, name)
    yield "main.py"
    yield "使用說明.txt"


class NoEmojiTest(unittest.TestCase):
    def test_source_has_no_emoji(self):
        """Tk 每遇到一個新 emoji 都要掃描系統字型（啟動慢好幾秒），帶 U+FE0F 的還會變方框。"""
        found = []
        for rel in source_files():
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    for ch in EMOJI.findall(line):
                        found.append(f"{rel}:{n} U+{ord(ch):04X}")
        self.assertEqual(found, [])


class IconFilesTest(unittest.TestCase):
    def used_icons(self):
        """原始碼裡用到的圖示名稱：icons.get("…")、icon="…"、"icon": "…"、選單 item("…", "…") 等。"""
        patterns = [r'icons\.get\(\s*"([a-z-]+)"', r'icon="([a-z-]+)"', r'"icon": "([a-z-]+)"',
                    r'item\("[^"]*", "([a-z-]+)"', r'_ICON = "([a-z-]+)"', r'\(\s*"([a-z-]+)", "(?:ok|warn|hi)"\)',
                    r'chevrons\.image\(\s*"([a-z-]+)"', r'_add_group\([^)]*"([a-z-]+)", (?:True|False)\)',
                    r'self\.mask\("([a-z-]+)"', r'icons\.mask\("([a-z-]+)"', r'"(sun|moon)"']
        names = set()
        for rel in source_files():
            if rel.endswith(".py"):
                with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                    text = f.read()
                for pattern in patterns:
                    names.update(re.findall(pattern, text))
        return names

    def test_every_used_icon_has_all_sizes(self):
        names = self.used_icons()
        # 確定 regex 有抓到東西：分類、狀態、樹的箭頭、深淺色切換
        self.assertTrue({g["icon"] for g in DEFAULT_GROUPS} | {"check", "warning", "chevron-down", "sun", "moon",
                                                               "folder", "close", "plus"} <= names, names)
        missing = [f"{n}_{s}.png" for n in sorted(names) for s in ICON_SIZES
                   if not os.path.isfile(os.path.join(ICON_DIR, f"{n}_{s}.png"))]
        self.assertEqual(missing, [])

    def test_every_icon_has_svg_source(self):
        """PNG 都是從 assets/icons_src 的規範 SVG 轉的（tools/make_icons.py）。"""
        pngs = {f.rsplit("_", 1)[0] for f in os.listdir(ICON_DIR)}
        svgs = {f[:-4] for f in os.listdir(os.path.join(ROOT, "assets", "icons_src"))}
        self.assertEqual(pngs, svgs)

    def test_about_mark_images(self):
        for size in MARK_SIZES:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "assets", "logos", f"winglight-mark_{size}.png")), size)


def decode_rgba(b64):
    """測試用：把 render() 的 PNG 解回 (寬, 高, RGBA bytes)。render 只寫濾波 0。"""
    data = base64.b64decode(b64)
    width, height, depth, color_type = struct.unpack(">IIBB", data[16:26])
    assert (depth, color_type) == (8, 6)
    pos, idat = 8, b""
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            idat += data[pos + 8:pos + 8 + length]
        pos += 12 + length
    raw = zlib.decompress(idat)
    stride = width * 4 + 1
    return width, height, b"".join(raw[y * stride + 1:(y + 1) * stride] for y in range(height))


class MaskTest(unittest.TestCase):
    def test_all_masks_decode(self):
        for name in os.listdir(ICON_DIR):
            size = int(name.rsplit("_", 1)[1][:-4])
            mask = read_mask(os.path.join(ICON_DIR, name))
            self.assertEqual((mask.width, mask.height, len(mask.rows)), (size, size, size), name)
            self.assertTrue(any(any(row) for row in mask.rows), f"{name} 是空白的")

    def test_rotate_quarter_turn(self):
        # 左上角一點：逆時針轉 90° 後到左下角
        mask = Mask(3, 2, [bytes([9, 0, 0]), bytes([0, 0, 0])])
        turned = rotate_mask(mask, 1)
        self.assertEqual((turned.width, turned.height), (2, 3))
        self.assertEqual(turned.rows, [bytes([0, 0]), bytes([0, 0]), bytes([9, 0])])
        self.assertEqual(rotate_mask(mask, 4).rows, mask.rows)

    def test_render_tints_and_composites(self):
        mask = Mask(2, 1, [bytes([255, 128])])
        w, h, px = decode_rgba(render(2, 1, [(mask, "#2B55D4", 1, (0, 0))]))
        self.assertEqual((w, h), (2, 1))
        self.assertEqual(px, bytes([0x2B, 0x55, 0xD4, 255, 0x2B, 0x55, 0xD4, 128]))
        # 兩層：底下白色不透明，上面一點黑色
        dot = Mask(1, 1, [bytes([255])])
        _, _, px = decode_rgba(render(2, 1, [(Mask(2, 1, [bytes([255, 255])]), "#FFFFFF", 1, (0, 0)),
                                             (dot, "#000000", 1, (1, 0))]))
        self.assertEqual(px, bytes([255, 255, 255, 255, 0, 0, 0, 255]))


if __name__ == "__main__":
    unittest.main()
