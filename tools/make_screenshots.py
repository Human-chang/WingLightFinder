# -*- coding: utf-8 -*-
"""README 用的介面截圖：docs/screenshot-light.png、docs/screenshot-dark.png（視窗內容 1280 × 780，100% 縮放時）。

範例資料夾跟 tools/make_demo_folder.py 一樣，產生在專案底下的 demo_project（虛構的「範例道路改善工程」），
不含真實案名、地籍或公文字號。設定放暫存資料夾，不碰自己的 WingLightFinder.json。
用 Win32 PrintWindow 抓視窗本身，不抓螢幕範圍，不會抓到蓋在上面的其他視窗。只用 Python 標準庫。

    python tools\\make_screenshots.py
"""

import ctypes
import os
import struct
import sys
import tempfile
import tkinter as tk
import zlib
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from core.config import Config  # noqa: E402
from core.demo import make_demo_project  # noqa: E402

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
user32.GetParent.restype = wintypes.HWND
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, ctypes.c_void_p,
                            ctypes.c_void_p, wintypes.UINT]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]

PW_CLIENTONLY = 1
PW_RENDERFULLCONTENT = 2


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


def write_png(path, width, height, rgb):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    stride = width * 3
    raw = b"".join(b"\0" + bytes(rgb[y * stride:(y + 1) * stride]) for y in range(height))
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def capture(window, path):
    """抓 Tk 視窗的內容（不含標題列與邊框）存成 PNG，回傳 (寬, 高)。"""
    window.update()
    hwnd = user32.GetParent(window.winfo_id())
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    width, height = rect.right, rect.bottom
    screen = user32.GetDC(hwnd)
    memory = gdi32.CreateCompatibleDC(screen)
    bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
    old = gdi32.SelectObject(memory, bitmap)
    user32.PrintWindow(hwnd, memory, PW_CLIENTONLY | PW_RENDERFULLCONTENT)
    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth, info.biHeight, info.biPlanes, info.biBitCount = width, -height, 1, 32
    buffer = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(memory, bitmap, 0, height, buffer, ctypes.byref(info), 0)
    gdi32.SelectObject(memory, old)
    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(memory)
    user32.ReleaseDC(hwnd, screen)
    bgra = buffer.raw
    rgb = bytearray(width * height * 3)
    rgb[0::3], rgb[1::3], rgb[2::3] = bgra[2::4], bgra[1::4], bgra[0::4]
    write_png(path, width, height, rgb)
    return width, height


def main():
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
    project = make_demo_project(os.path.join(ROOT, "demo_project"))
    sub = os.path.join(project, "02_設計")
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)

    from ui.app import FinderApp
    with tempfile.TemporaryDirectory() as tmp:
        config = Config(base_dir=tmp)
        config.favorites = [project]
        config.tabs = [project, sub]
        config.active_tab = 0
        config.group_states = {"其他": True}
        config.geometry = "1280x780+40+40"
        config.theme = "light"
        config.save()

        root = tk.Tk()
        app = FinderApp(root, Config(base_dir=tmp))
        for _ in range(400):
            root.update()
            if project in app.cache and app.column_keys:
                break
            root.after(25)
        # 展開範例資料夾，看得到子資料夾和樹的箭頭
        node = app.tree.get_children(app.fav_node)[0]
        app.tree.focus(node)
        app.tree.event_generate("<<TreeviewOpen>>")
        app.tree.item(node, open=True)
        for name in ("light", "dark"):
            if app.theme.name != name:
                app.theme.set(name)
            root.update()
            root.after(300)
            root.update()
            path = os.path.join(docs, f"screenshot-{name}.png")
            width, height = capture(root, path)
            print(f"{os.path.relpath(path, ROOT)}  {width} × {height}")
        root.destroy()


if __name__ == "__main__":
    main()
