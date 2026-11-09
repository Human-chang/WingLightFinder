# -*- coding: utf-8 -*-
"""介面圖示：預先轉好的 PNG，取代直接寫在文字裡的 emoji。

Tk 每遇到一個新的 emoji 都要掃描系統字型找字（第一次約 1.1 秒，之後每個約 80 毫秒），
介面上二十幾個 emoji 會讓啟動多花 2 秒。改用 tools/make_icons.py 預先轉好的 PNG，
圖案來自 Microsoft Fluent Emoji（MIT 授權），而且是彩色的。
"""

import os
import sys
import tkinter as tk

from core.config import app_dir

SIZES = (16, 20, 24, 32)
EMOJI = "📁📂⭐❌🔄🔍🕒📄📐📊📈📝📷📍🎬📦📌💻🌐🕘📅📋📑⚡⚠⏳▶"


def icon_dir():
    return os.path.join(getattr(sys, "_MEIPASS", app_dir()), "assets", "icons")


def icon_file(emoji, size):
    return f"{ord(emoji[0]):x}_{size}.png"


class Icons:
    def __init__(self, root, scale=1.0):
        self.root = root
        want = 16 * scale
        self.size = next((s for s in SIZES if s >= want - 0.5), SIZES[-1])
        self.cache = {}

    def get(self, emoji):
        if not emoji:
            return None
        if emoji not in self.cache:
            path = os.path.join(icon_dir(), icon_file(emoji, self.size))
            try:
                self.cache[emoji] = tk.PhotoImage(master=self.root, file=path)
            except tk.TclError:
                self.cache[emoji] = None
        return self.cache[emoji]
