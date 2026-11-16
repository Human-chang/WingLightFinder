# -*- coding: utf-8 -*-
"""「關於」視窗：產品名與版本、作者、可以點的 GitHub 連結（用預設瀏覽器開啟）。
左邊放 WingLight 系列標誌（規範：標誌用在系列介紹、關於視窗、作者頁面），圖是 tools/make_logo_images.py 先畫好的。
"""

import os
import tkinter as tk
import webbrowser

from core.version import APP_NAME, AUTHOR, AUTHOR_URL, SUBTITLE, VERSION
from ui.winglight_tk import FONTS, button, dialog, show_modal

MARK_PX = 64
MARK_SIZES = (64, 80, 96, 128)


def mark_file(assets_dir, scale):
    """依螢幕縮放挑系列標誌的大小：不小於 64px × 縮放倍率的最小那張。"""
    want = MARK_PX * scale
    size = next((s for s in MARK_SIZES if s >= want - 0.5), MARK_SIZES[-1])
    return os.path.join(assets_dir, "logos", f"winglight-mark_{size}.png")


class AboutDialog:
    def __init__(self, parent, theme, assets_dir):
        t, px = theme, theme.px
        self.theme = theme
        self.parent = parent
        self.win = win = dialog(parent, t, f"關於 {APP_NAME}")

        body = t.paint(tk.Frame(win, padx=px(24), pady=px(24)), bg="panel")
        body.pack(fill=tk.BOTH, expand=True)
        try:
            self.mark = tk.PhotoImage(master=win, file=mark_file(assets_dir, t.scale))
        except tk.TclError:
            self.mark = None
        if self.mark is not None:
            t.paint(tk.Label(body, image=self.mark, borderwidth=0), bg="panel").pack(side=tk.LEFT, anchor="n",
                                                                                   padx=(0, px(16)))
        info = t.paint(tk.Frame(body), bg="panel")
        info.pack(side=tk.LEFT, anchor="n")

        def line(text, font="body", fg="text", pady=0):
            label = t.paint(tk.Label(info, text=text, font=FONTS[font], borderwidth=0, padx=0, anchor="w"),
                            bg="panel", fg=fg)
            label.pack(anchor="w", pady=pady)
            return label

        line(APP_NAME, "page-title")
        line(SUBTITLE, fg="muted", pady=(px(2), 0))
        self.version_label = line(f"版本 {VERSION}", fg="muted")
        line(f"作者：{AUTHOR}", pady=(px(12), 0))
        self.link = line(AUTHOR_URL, fg="accent")
        self.link.configure(cursor="hand2")
        self.link.bind("<Button-1>", lambda e: webbrowser.open(AUTHOR_URL))
        self.link.bind("<Enter>", lambda e: self.link.configure(font=FONTS["body"] + ("underline",)))
        self.link.bind("<Leave>", lambda e: self.link.configure(font=FONTS["body"]))

        actions = t.paint(tk.Frame(win, padx=px(24)), bg="panel")
        actions.pack(fill=tk.X, pady=(0, px(16)))
        self.close_button = button(actions, t, "關閉", win.destroy, kind="primary")
        self.close_button.pack(side=tk.RIGHT)
        win.bind("<Return>", lambda e: win.destroy())

    def show(self):
        # 焦點放在視窗本身：Windows 會在有焦點的 tk.Button 裡畫一圈虛線框；Enter、Esc 都能關閉
        show_modal(self.win, self.parent, self.theme)
