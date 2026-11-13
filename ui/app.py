# -*- coding: utf-8 -*-
"""WingLightFinder 主視窗：左邊資料夾樹，右邊依軟體類型分欄、由新到舊列出檔案。"""

import os
import sys
import time
import queue
import string
import ctypes
import winreg
import threading
import subprocess
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, ttk, messagebox
from datetime import datetime, timedelta

from core.clipboard import copy_file_to_clipboard
from core.config import Config, app_dir
from core.scanner import scan_folder, scan_depth_for, classify
from core.tabs import TabList
from ui.icons import Icons

APP_TITLE = "WingLightFinder — 專案檔案速查"
FONT = "Microsoft JhengHei"
PAGE_SIZE = 100          # 每欄先列出的筆數，其餘按「顯示全部」再展開
OTHERS_PAGE_SIZE = 25
OTHERS_KEY = "__others__"
OTHERS_NAME = "其他"       # 不在任何分類裡的檔案；預設不顯示，在「顯示分類」勾選
OTHERS_ICON = "📎"
REFRESH_MIN_AGE = 3        # 秒：剛掃過的資料夾不重掃
AUTO_REFRESH_MS = 30_000   # 視窗開著時，每 30 秒在背景重掃目前的資料夾

C = {
    "bg_main": "#1e1e1e",
    "bg_panel": "#252526",
    "bg_element": "#333333",
    "bg_hover": "#2a2d2e",
    "fg_text": "#cccccc",
    "fg_dim": "#808080",
    "fg_highlight": "#ffffff",
    "accent": "#0e639c",
    "accent_hover": "#1177bb",
    "info": "#56b6c2",
    "warn": "#e5c07b",
    "link": "#4fc1ff",
    "year_bg": "#2c313a",
    "year_fg": "#e5c07b",
}

TIME_FILTERS = [("全部", None), ("今天", "today"), ("7 天", 7), ("30 天", 30), ("今年", "year")]


def find_winglightpdf():
    """WingLightPDF 按過「設為預設PDF」後會在登錄檔留下執行檔路徑。"""
    for key in (r"Software\Classes\Applications\WingLightPDF.exe\shell\open\command",
                r"Software\Classes\WingLightPDF.Document\shell\open\command"):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                cmd, _ = winreg.QueryValueEx(k, "")
        except OSError:
            continue
        exe = cmd.split('"')[1] if cmd.startswith('"') else cmd.split(" ")[0]
        if os.path.isfile(exe):
            return exe
    return None


def format_size(n):
    if n >= 1024 * 1024:
        return f"{n / 1024 / 1024:.2f} MB"
    return f"{n / 1024:.1f} KB"


def _change_note(old, new):
    """比對兩次掃描結果：有變動時回傳狀態列的說明，沒有變動回傳空字串。"""
    before = {f.path: (f.mtime, f.size) for f in old.files}
    after = {f.path: (f.mtime, f.size) for f in new.files}
    added = len(after.keys() - before.keys())
    removed = len(before.keys() - after.keys())
    modified = sum(1 for p in after.keys() & before.keys() if after[p] != before[p])
    if not (added or removed or modified) and old.unreadable == new.unreadable:
        return ""
    parts = [f"{label} {n} 個" for label, n in (("新增", added), ("修改", modified), ("移除", removed)) if n]
    return "已自動更新" + ("：" + "、".join(parts) if parts else "")


class HoverTip:
    """滑鼠停在檔案上時，顯示被截掉的完整檔名與子資料夾。"""

    def __init__(self, root):
        self.root = root
        self.win = None
        self.after_id = None
        self.key = None

    def schedule(self, key, text, x, y):
        if key == self.key:
            return
        self.hide()
        self.key = key
        self.after_id = self.root.after(450, lambda: self._show(text, x, y))

    def _show(self, text, x, y):
        self.after_id = None
        self.win = tk.Toplevel(self.root)
        self.win.wm_overrideredirect(True)
        self.win.wm_geometry(f"+{x + 16}+{y + 14}")
        tk.Label(self.win, text=text, bg="#3c3c3c", fg=C["fg_highlight"], font=(FONT, 9),
                 padx=8, pady=4, justify=tk.LEFT, relief=tk.SOLID, borderwidth=1).pack()

    def hide(self):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.win:
            self.win.destroy()
            self.win = None
        self.key = None


class FinderApp:
    def __init__(self, root, config=None):
        self.root = root
        self.config = config or Config()
        self.groups = self.config.groups()
        self.group_vars = {}
        self.nodes = {}             # 資料夾樹節點 → 路徑
        self.fav_node = None
        self.recent_node = None
        remember = self.config.remember_session
        self.tabset = TabList(self.config.tabs if remember else [], self.config.active_tab if remember else 0,
                              pinned=self.config.pinned_tabs)
        self.cache = {}             # 資料夾 → ScanResult，所有分頁共用；先顯示上次的結果，再在背景重掃
        self.scanned_at = {}        # 資料夾 → 上次掃完的時間（time.monotonic）
        self.scanning = set()       # 正在背景掃描的資料夾
        self.polling = False
        self.app_active = True      # 切到別的程式時變成 False，切回來就重掃目前的資料夾
        self.drag = None            # 拖曳分頁中的狀態
        self.tab_cells = []
        self.wanted = self.tabset.folders()  # 還有分頁在用的資料夾；沒人用的掃描會中途停止
        self.results = queue.Queue()
        self.columns = {}           # 欄 key → (frame, title label, tree, title)
        self.column_keys = None
        self.item_files = {}        # Treeview item id → FileEntry
        self.more_items = {}        # 「顯示全部」那一列 → 欄 key
        self.item_seq = 0
        self.search_after = None
        self.switching = False      # 切換分頁時還原搜尋字，不要當成使用者在打字
        self.wlpdf = find_winglightpdf()

        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        self.icons = Icons(root, self.scale)
        root.title(APP_TITLE)
        root.geometry(self.config.geometry or f"{int(1280 * self.scale)}x{int(780 * self.scale)}")
        root.minsize(int(900 * self.scale), int(560 * self.scale))
        root.configure(bg=C["bg_main"])
        # 打包後圖示在 _internal/assets（sys._MEIPASS），從原始碼執行時在專案的 assets
        icon = os.path.join(getattr(sys, "_MEIPASS", app_dir()), "assets", "app_icon.ico")
        if os.path.exists(icon):
            try:
                root.iconbitmap(default=icon)
            except tk.TclError:
                pass

        self.tip = HoverTip(root)
        self.others_var = tk.BooleanVar(value=self.config.group_states.get(OTHERS_NAME, False))
        self.remember_var = tk.BooleanVar(value=remember)
        self._setup_styles()
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Control-f>", lambda e: self._focus_search())
        root.bind("<F5>", lambda e: self.rescan())
        root.bind_all("<Control-t>", lambda e: (self.new_tab(), "break")[1])
        root.bind_all("<Control-w>", lambda e: (self.close_tab(self.tabset.active), "break")[1])
        root.bind_all("<Control-Tab>", lambda e: (self._step_tab(1), "break")[1])
        root.bind_all("<Control-Shift-Tab>", lambda e: (self._step_tab(-1), "break")[1])
        # Windows 上的 Tk 不會送 <Activate>，用焦點進出判斷使用者是不是切到別的程式又切回來
        root.bind("<FocusOut>", lambda e: root.after_idle(self._check_app_inactive))
        root.bind("<FocusIn>", self._on_focus_in)
        root.after(AUTO_REFRESH_MS, self._auto_refresh)
        if self.tab.folder:
            root.after(50, lambda: self.open_folder(self.tab.folder))

    @property
    def tab(self):
        return self.tabset.current

    @property
    def current_folder(self):
        return self.tab.folder

    # ==========================================
    # 樣式與版面
    # ==========================================
    def _setup_styles(self):
        line = tkfont.Font(family=FONT, size=9).metrics("linespace")
        rowheight = max(line, self.icons.size) + int(8 * self.scale)
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=C["bg_main"], foreground=C["fg_text"], fieldbackground=C["bg_main"],
                        rowheight=rowheight, borderwidth=0, font=(FONT, 9))
        style.map("Treeview", background=[("selected", C["accent"])])
        style.configure("Treeview.Heading", background=C["bg_panel"], foreground=C["fg_highlight"], relief="flat",
                        font=(FONT, 9, "bold"))
        style.map("Treeview.Heading", background=[("active", C["bg_element"])])
        # 檔案清單沒有階層，拿掉展開箭頭佔的空間
        style.configure("Files.Treeview", indent=0)
        style.layout("Files.Treeview.Item", [
            ("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": ""}),
                ("Treeitem.focus", {"side": "left", "sticky": "", "children": [
                    ("Treeitem.text", {"side": "left", "sticky": ""}),
                ]}),
            ]}),
        ])
        style.configure("Vertical.TScrollbar", background=C["bg_element"], troughcolor=C["bg_main"],
                        bordercolor=C["bg_main"], arrowcolor=C["fg_text"])
        style.configure("TSeparator", background=C["bg_element"])

    def _button(self, parent, text, command, accent=False, icon=None):
        bg = C["accent"] if accent else C["bg_element"]
        fg = C["fg_highlight"] if accent else C["fg_text"]
        image = self.icons.get(icon)
        return tk.Button(parent, text=f" {text}" if image else text, image=image or "", compound="left",
                         command=command, bg=bg, fg=fg, activebackground=C["bg_hover"],
                         activeforeground=C["fg_highlight"], relief=tk.FLAT, borderwidth=0, padx=8, pady=4,
                         cursor="hand2", font=(FONT, 9))

    def _label(self, parent, text, icon=None, **kw):
        image = self.icons.get(icon)
        return tk.Label(parent, text=f" {text}" if image else text, image=image or "", compound="left",
                        bg=C["bg_panel"], fg=C["fg_text"], font=(FONT, 9), **kw)

    def _entry(self, parent, **kw):
        return tk.Entry(parent, bg=C["bg_main"], fg=C["fg_highlight"], insertbackground=C["fg_highlight"],
                        relief=tk.FLAT, font=(FONT, 10), **kw)

    def _checkbox(self, parent, text, variable, command, icon=None):
        image = self.icons.get(icon)
        return tk.Checkbutton(parent, text=f" {text}" if image else text, image=image or "", compound="left",
                              variable=variable, command=command,
                              bg=C["bg_panel"], fg=C["fg_text"], selectcolor=C["bg_element"],
                              activebackground=C["bg_panel"], activeforeground=C["fg_highlight"],
                              font=(FONT, 9))

    def _build_ui(self):
        top = tk.Frame(self.root, bg=C["bg_panel"], padx=10, pady=8)
        top.pack(side=tk.TOP, fill=tk.X)

        # 第一列：資料夾、搜尋、時間
        row1 = tk.Frame(top, bg=C["bg_panel"])
        row1.pack(fill=tk.X)
        self._button(row1, "瀏覽目錄", self.select_directory, accent=True, icon="📁").pack(side=tk.LEFT, padx=2)
        self._button(row1, "加入最愛", self.add_favorite, icon="⭐").pack(side=tk.LEFT, padx=2)
        self._button(row1, "移除最愛", self.remove_favorite, icon="❌").pack(side=tk.LEFT, padx=2)
        self._button(row1, "重新掃描", self.rescan, icon="🔄").pack(side=tk.LEFT, padx=2)
        ttk.Separator(row1, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=12)

        self._label(row1, "搜尋", icon="🔍").pack(side=tk.LEFT)
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._on_search_changed)
        self.search_entry = self._entry(row1, textvariable=self.search_var, width=26)
        self.search_entry.pack(side=tk.LEFT, ipady=4, padx=(6, 2))
        self.search_entry.bind("<Escape>", lambda e: self.search_var.set(""))
        self._button(row1, "✕", lambda: self.search_var.set("")).pack(side=tk.LEFT)
        ttk.Separator(row1, orient="vertical").pack(side=tk.LEFT, fill=tk.Y, padx=12)

        self._label(row1, "修改時間", icon="🕒").pack(side=tk.LEFT, padx=(0, 6))
        self.time_buttons = {}
        for label, value in TIME_FILTERS:
            btn = self._button(row1, label, lambda v=value: self.set_time_filter(v))
            btn.pack(side=tk.LEFT, padx=1)
            self.time_buttons[value] = btn
        self._refresh_time_buttons()

        remember = self._checkbox(row1, "記住上次畫面", self.remember_var, self._on_remember_toggled)
        remember.pack(side=tk.RIGHT, padx=3)
        tip = "勾選：下次開啟還原這次開著的分頁和加入的副檔名\n不勾：下次開啟只打開釘選的分頁"
        remember.bind("<Enter>", lambda e: self.tip.schedule("remember", tip, e.x_root, e.y_root))
        remember.bind("<Leave>", lambda e: self.tip.hide())

        # 第二列：分類
        row2 = tk.Frame(top, bg=C["bg_panel"])
        row2.pack(fill=tk.X, pady=(8, 0))
        self._label(row2, "顯示分類").pack(side=tk.LEFT, padx=(2, 6))
        self.filter_frame = tk.Frame(row2, bg=C["bg_panel"])
        self.filter_frame.pack(side=tk.LEFT)
        self._refresh_filters()
        self.entry_ext = self._entry(row2, width=8)
        self.entry_ext.pack(side=tk.LEFT, padx=(12, 4), ipady=3)
        self.entry_ext.bind("<Return>", lambda e: self.add_extension())
        self._button(row2, "+ 加入其他副檔名", self.add_extension).pack(side=tk.LEFT)

        # 狀態列（先 pack 才不會被主畫面擠掉）
        self.status = tk.Label(self.root, text="", anchor="w", compound="left", bg=C["bg_panel"], fg=C["info"],
                               font=(FONT, 9), padx=10, pady=4)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status.bind("<Button-1>", lambda e: self._show_unreadable())

        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left = tk.Frame(paned, bg=C["bg_main"])
        paned.add(left, weight=1)
        scroll = ttk.Scrollbar(left, orient="vertical")
        self.tree = ttk.Treeview(left, yscrollcommand=scroll.set)
        scroll.config(command=self.tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.heading("#0", text="專案與磁碟目錄", anchor="w")
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-2>", self._on_tree_middle_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)

        self.right = tk.Frame(paned, bg=C["bg_main"])
        paned.add(self.right, weight=4)
        self.tabbar = tk.Frame(self.right, bg=C["bg_main"])
        self.tabbar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 2))
        self.grid_frame = None
        self._show_placeholder("從左邊選一個資料夾，或按「瀏覽目錄」")

        self._init_tree()
        self._refresh_tab_bar()

    def _show_placeholder(self, text, icon=None):
        self._new_grid_frame()
        self.column_keys = None
        self.columns = {}
        image = self.icons.get(icon)
        tk.Label(self.grid_frame, text=f" {text}" if image else text, image=image or "", compound="left",
                 bg=C["bg_main"], fg=C["fg_dim"], font=(FONT, 12)).pack(expand=True)

    def _new_grid_frame(self):
        if self.grid_frame is not None:
            self.grid_frame.destroy()
        self.grid_frame = tk.Frame(self.right, bg=C["bg_main"])
        self.grid_frame.pack(fill=tk.BOTH, expand=True)

    def set_status(self, text, warn=False, icon=None):
        image = self.icons.get("⚠" if warn and icon is None else icon)
        self.status.config(text=f" {text}" if image else text, image=image or "",
                           fg=C["warn"] if warn else C["info"])

    def _focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, tk.END)

    # ==========================================
    # 資料夾樹與我的最愛
    # ==========================================
    def _init_tree(self):
        self.fav_node = self.tree.insert("", "end", text=" 我的最愛", image=self.icons.get("⭐") or "", open=True)
        self._refresh_favorites()

        home = os.path.expanduser("~")
        quick = self.tree.insert("", "end", text=" 快速存取", image=self.icons.get("📌") or "", open=False)
        for label, sub in (("桌面", "Desktop"), ("文件", "Documents"), ("下載", "Downloads")):
            self._add_node(quick, label, os.path.join(home, sub))

        local = self.tree.insert("", "end", text=" 本機與外接裝置", image=self.icons.get("💻") or "", open=False)
        net = self.tree.insert("", "end", text=" 網路磁碟", image=self.icons.get("🌐") or "", open=False)
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive = f"{letter}:\\"
                kind = ctypes.windll.kernel32.GetDriveTypeW(drive)
                if kind in (2, 3):      # 2=USB, 3=硬碟
                    self._add_node(local, f"磁碟 {letter}:", drive, icon="💻")
                elif kind == 4:         # 4=網路磁碟
                    self._add_node(net, f"網路槽 {letter}:", drive, icon="🌐")
            bitmask >>= 1

    def _add_node(self, parent, text, path, check=True, icon="📁"):
        if check and not os.path.exists(path):
            return None
        node = self.tree.insert(parent, "end", text=f" {text}", image=self.icons.get(icon) or "")
        self.nodes[node] = path
        self.tree.insert(node, "end", text="dummy")
        return node

    def _refresh_favorites(self):
        for child in self.tree.get_children(self.fav_node):
            self.nodes.pop(child, None)
            self.tree.delete(child)
        for path in self.config.favorites:
            name = os.path.basename(path.rstrip("\\/")) or path
            # 網路磁碟沒連上時也要列出來，點下去會在狀態列說明讀不到
            self._add_node(self.fav_node, name, path, check=False, icon="📂")

    def add_favorite(self):
        if not self.current_folder:
            self.set_status("先在左邊選一個資料夾，再加入最愛", warn=True)
            return
        self._add_favorite_path(self.current_folder)

    def _add_favorite_path(self, path):
        if path not in self.config.favorites:
            self.config.favorites.append(path)
            self._save_config()
            self._refresh_favorites()
            self.set_status(f"已加入最愛：{path}", icon="⭐")

    def remove_favorite(self):
        sel = self.tree.selection()
        if not sel or self.tree.parent(sel[0]) != self.fav_node:
            self.set_status("請先在「我的最愛」底下選一個資料夾", warn=True)
            return
        self._remove_favorite_path(self.nodes.get(sel[0]))

    def _remove_favorite_path(self, path):
        if path in self.config.favorites:
            self.config.favorites.remove(path)
            self._save_config()
            self._refresh_favorites()
            self.set_status(f"已移除最愛：{path}", icon="❌")

    def select_directory(self):
        path = filedialog.askdirectory()
        if not path:
            return
        path = os.path.normpath(path)
        if self.recent_node is None:
            self.recent_node = self.tree.insert("", 1, text=" 最近瀏覽", image=self.icons.get("🕘") or "", open=True)
        node = next((n for n in self.tree.get_children(self.recent_node) if self.nodes.get(n) == path), None)
        if node is None:
            node = self._add_node(self.recent_node, os.path.basename(path) or path, path, check=False)
        self.tree.see(node)
        self.tree.selection_set(node)

    def _on_tree_open(self, event):
        node = self.tree.focus()
        children = self.tree.get_children(node)
        if len(children) == 1 and self.tree.item(children[0], "text") == "dummy":
            self.tree.delete(children[0])
            if node in self.nodes:
                self._load_subfolders(node, self.nodes[node])

    def _load_subfolders(self, parent, path):
        try:
            names = sorted(os.listdir(path), key=str.upper)
        except OSError:
            self.set_status(f"無法讀取：{path}", warn=True)
            return
        for name in names:
            full = os.path.join(path, name)
            if os.path.isdir(full):
                self._add_node(parent, name, full, check=False)

    def _on_tree_select(self, event):
        sel = self.tree.selection()
        if sel and sel[0] in self.nodes:
            self.open_from_tree(self.nodes[sel[0]])

    def open_from_tree(self, path):
        """從左邊開資料夾；目前是釘選的分頁時開在新分頁，不要換掉釘選的路徑。"""
        if self.tab.pinned and path != self.tab.folder:
            self.new_tab(path)
        else:
            self.open_folder(path)

    def _on_tree_middle_click(self, event):
        node = self.tree.identify_row(event.y)
        if node in self.nodes:
            self.new_tab(self.nodes[node])

    def _on_tree_right_click(self, event):
        node = self.tree.identify_row(event.y)
        path = self.nodes.get(node)
        if not path:
            return
        menu = tk.Menu(self.root, tearoff=0, bg=C["bg_panel"], fg=C["fg_highlight"],
                       activebackground=C["accent"], activeforeground=C["fg_highlight"], borderwidth=0,
                       font=(FONT, 9))
        menu.add_command(label=" 在新分頁開啟", image=self.icons.get("📂") or "", compound="left",
                         command=lambda: self.new_tab(path))
        if not self.tab.pinned:
            menu.add_command(label=" 在目前分頁開啟", image=self.icons.get("📁") or "", compound="left",
                             command=lambda: self.open_folder(path))
        menu.add_separator()
        if path in self.config.favorites:
            menu.add_command(label=" 移除最愛", image=self.icons.get("❌") or "", compound="left",
                             command=lambda: self._remove_favorite_path(path))
        else:
            menu.add_command(label=" 加入最愛", image=self.icons.get("⭐") or "", compound="left",
                             command=lambda: self._add_favorite_path(path))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ==========================================
    # 分頁
    # ==========================================
    def _refresh_tab_bar(self):
        for w in self.tabbar.winfo_children():
            w.destroy()
        self.tab_cells = []
        for i, t in enumerate(self.tabset.tabs):
            active = i == self.tabset.active
            bg = C["bg_panel"] if active else C["bg_main"]
            fg = C["fg_highlight"] if active else C["fg_dim"]
            cell = tk.Frame(self.tabbar, bg=bg)
            cell.pack(side=tk.LEFT, padx=(0, 2))
            stripe = tk.Frame(cell, bg=C["accent"] if active else bg, height=2)
            stripe.pack(side=tk.TOP, fill=tk.X)
            title = t.title if len(t.title) <= 18 else t.title[:17] + "…"
            icon = "📌" if t.pinned else "📂" if active else "📁"
            label = tk.Label(cell, text=f" {title}", image=self.icons.get(icon) or "",
                             compound="left", bg=bg, fg=fg, font=(FONT, 9), padx=8, pady=3, cursor="hand2")
            label.pack(side=tk.LEFT)
            if t.pinned:
                # 釘選的分頁沒有關閉鈕，右邊留一點空間讓寬度接近一般分頁
                tk.Frame(cell, bg=bg, width=6).pack(side=tk.LEFT)
                tip = f"{t.folder}\n已釘選：每次開啟都會打開（右鍵可取消釘選）"
            else:
                close = tk.Label(cell, text="✕", bg=bg, fg=C["fg_dim"], font=(FONT, 8), padx=6, cursor="hand2")
                close.pack(side=tk.LEFT)
                close.bind("<Button-1>", lambda e, i=i: self.close_tab(i))
                close.bind("<Enter>", lambda e, w=close: w.config(fg=C["fg_highlight"]))
                close.bind("<Leave>", lambda e, w=close: w.config(fg=C["fg_dim"]))
                tip = t.folder or "空白分頁：從左邊選一個資料夾"
            for w in (cell, stripe, label):
                w.bind("<ButtonPress-1>", lambda e, i=i: self._tab_press(e, i))
                w.bind("<B1-Motion>", self._tab_drag)
                w.bind("<ButtonRelease-1>", self._tab_release)
                w.bind("<Button-2>", lambda e, i=i: self.close_tab(i))
                w.bind("<Button-3>", lambda e, i=i: self._tab_menu(e, i))
                w.bind("<Enter>", lambda e, i=i, tip=tip: self.drag or self.tip.schedule(
                    ("tab", i, tip), tip, e.x_root, e.y_root))
                w.bind("<Leave>", lambda e: self.tip.hide())
            self.tab_cells.append(cell)
        plus = tk.Label(self.tabbar, text="＋", bg=C["bg_main"], fg=C["fg_text"], font=(FONT, 10), padx=8,
                        cursor="hand2")
        plus.pack(side=tk.LEFT)
        plus.bind("<Button-1>", lambda e: self.new_tab())
        plus.bind("<Enter>", lambda e: self.tip.schedule(("tab", "+"), "新分頁（Ctrl+T）", e.x_root, e.y_root))
        plus.bind("<Leave>", lambda e: self.tip.hide())
        self.drop_marker = tk.Frame(self.tabbar, bg=C["link"], width=3)

    # 拖曳分頁：按下時先記住，移動超過一點點才算拖曳，放開時沒拖曳就當成點一下
    def _tab_press(self, event, index):
        self.tip.hide()
        self.drag = {"index": index, "x": event.x_root, "target": None, "widget": event.widget}

    def _tab_drag(self, event):
        d = self.drag
        if not d:
            return
        if d["target"] is None:
            if abs(event.x_root - d["x"]) < 8 * self.scale:
                return
            d["widget"].config(cursor="sb_h_double_arrow")
        target = len(self.tab_cells)
        for i, cell in enumerate(self.tab_cells):
            if event.x_root < cell.winfo_rootx() + cell.winfo_width() / 2:
                target = i
                break
        target = self.tabset.drop_position(d["index"], target)
        d["target"] = target
        if target < len(self.tab_cells):
            x = self.tab_cells[target].winfo_x() - 2
        else:
            last = self.tab_cells[-1]
            x = last.winfo_x() + last.winfo_width()
        self.drop_marker.place(x=max(x, 0), y=0, relheight=1)
        self.drop_marker.lift()

    def _tab_release(self, event):
        d, self.drag = self.drag, None
        if not d:
            return
        self.drop_marker.place_forget()
        if d["target"] is None:
            self.switch_tab(d["index"])
            return
        try:
            d["widget"].config(cursor="hand2")
        except tk.TclError:
            pass
        self._remember_tab_state()
        self.tabset.move(d["index"], d["target"])
        self._tabs_changed()
        self._refresh_tab_bar()

    def _tab_menu(self, event, index):
        self.tip.hide()
        t = self.tabset.tabs[index]
        menu = tk.Menu(self.root, tearoff=0, bg=C["bg_panel"], fg=C["fg_highlight"],
                       activebackground=C["accent"], activeforeground=C["fg_highlight"], borderwidth=0,
                       font=(FONT, 9))

        def item(label, icon, command, enabled=True):
            menu.add_command(label=f" {label}", image=self.icons.get(icon) or "", compound="left", command=command,
                             state=tk.NORMAL if enabled else tk.DISABLED)

        if t.pinned:
            item("取消釘選", "📌", lambda: self.set_tab_pinned(index, False))
        else:
            item("釘選分頁（每次開啟都會打開）", "📌", lambda: self.set_tab_pinned(index, True), enabled=bool(t.folder))
        if t.folder:
            item("用檔案總管開啟", "📂", lambda: self.open_in_explorer(t.folder))
            if t.folder not in self.config.favorites:
                item("加入最愛", "⭐", lambda: self._add_favorite_path(t.folder))
        menu.add_separator()
        item("關閉分頁", "❌", lambda: self.close_tab(index), enabled=not t.pinned)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def set_tab_pinned(self, index, pinned):
        folder = self.tabset.tabs[index].folder
        if not self.tabset.set_pinned(index, pinned):
            return
        self._tabs_changed()
        self._refresh_tab_bar()
        if pinned:
            self.set_status(f"已釘選：{folder}　｜　每次開啟都會打開這個分頁", icon="📌")
        else:
            self.set_status(f"已取消釘選：{folder}", icon="📌")

    def open_in_explorer(self, folder):
        try:
            os.startfile(folder)
        except OSError as e:
            messagebox.showerror("無法開啟", f"{folder}\n\n{e}")

    def _remember_tab_state(self):
        self.tab.query = self.search_var.get()

    def _show_tab(self):
        """把目前分頁的搜尋字、時間篩選與資料夾顯示出來。"""
        self.tip.hide()
        self.switching = True
        self.search_var.set(self.tab.query)
        self.switching = False
        self._refresh_time_buttons()
        self._refresh_tab_bar()
        # 清掉左邊樹的選取，再點同一個資料夾才會觸發開啟
        self.tree.selection_set(())
        if self.tab.folder:
            self.open_folder(self.tab.folder)
        else:
            self._show_placeholder("從左邊選一個資料夾，或按「瀏覽目錄」")
            self.set_status("空白分頁：從左邊選一個資料夾")

    def switch_tab(self, index):
        if index == self.tabset.active or not 0 <= index < len(self.tabset.tabs):
            return
        self._remember_tab_state()
        self.tabset.active = index
        self._show_tab()

    def _step_tab(self, delta):
        if len(self.tabset.tabs) > 1:
            self._remember_tab_state()
            self.tabset.step(delta)
            self._show_tab()

    def new_tab(self, folder=""):
        self._remember_tab_state()
        self.tabset.active = self.tabset.add(folder)
        self._tabs_changed()
        self._show_tab()

    def close_tab(self, index):
        if self.tabset.tabs[index].pinned:
            self.set_status("釘選的分頁要先取消釘選才能關閉（在分頁上按右鍵）", warn=True)
            return
        self._remember_tab_state()
        was_active = index == self.tabset.active
        self.tabset.close(index)
        self._tabs_changed()
        if was_active:
            self._show_tab()
        else:
            self._refresh_tab_bar()

    def _tabs_changed(self):
        self.wanted = self.tabset.folders()
        self._store_tabs()
        self._save_config()

    def _store_tabs(self):
        self.config.tabs = self.tabset.saved_folders()
        self.config.pinned_tabs = self.tabset.saved_pinned()
        self.config.active_tab = self.tabset.saved_active()

    # ==========================================
    # 掃描（背景執行緒）
    # ==========================================
    def open_folder(self, path, force=False):
        """在目前分頁開啟資料夾；掃過的先顯示上次的結果，再在背景重掃，有變動才更新畫面。"""
        if self.tab.folder != path:
            self.tab.folder = path
            self.tab.expanded.clear()
            self._tabs_changed()
            self._refresh_tab_bar()
        if path in self.cache and not force:
            self.render()
            self.refresh_quietly(path)
            return
        self.set_status(f"掃描中…　{path}（深度 {scan_depth_for(path)}）", icon="⏳")
        self._show_placeholder("掃描中…", icon="⏳")
        self._start_scan(path)

    def refresh_quietly(self, path, min_age=REFRESH_MIN_AGE):
        """背景重掃已經顯示過的資料夾；剛掃過的不重掃。"""
        if not path or path in self.scanning or path not in self.cache:
            return
        if time.monotonic() - self.scanned_at.get(path, 0) < min_age:
            return
        self._start_scan(path)

    def _start_scan(self, path):
        if path in self.scanning:
            return
        depth = scan_depth_for(path)

        def work():
            res = scan_folder(path, depth, cancelled=lambda: path not in self.wanted)
            self.results.put((path, res))

        self.scanning.add(path)
        threading.Thread(target=work, daemon=True).start()
        if not self.polling:
            self.polling = True
            self.root.after(40, self._poll_results)

    def _poll_results(self):
        while True:
            try:
                path, res = self.results.get_nowait()
            except queue.Empty:
                break
            self.scanning.discard(path)
            if res.cancelled:
                # 分頁關掉後掃描已經停了，但同一個資料夾又被重新開啟：重掃一次
                if path == self.current_folder:
                    self.open_folder(path)
                continue
            old = self.cache.get(path)
            self.cache[path] = res
            self.scanned_at[path] = time.monotonic()
            if path != self.current_folder:
                continue
            if old is None:
                self.render()
            else:
                note = _change_note(old, res)
                if note:
                    self.render(keep_view=True, note=note)
        if self.scanning:
            self.root.after(40, self._poll_results)
        else:
            self.polling = False

    def _check_app_inactive(self):
        try:
            focused = self.root.focus_get()
        except (KeyError, tk.TclError):  # 右鍵選單開著時 focus_get 會找不到選單內部的元件
            focused = True
        if focused is None:
            self.app_active = False

    def _on_focus_in(self, event):
        if not self.app_active:
            self.app_active = True
            self.refresh_quietly(self.current_folder)

    def _auto_refresh(self):
        if self.root.state() != "iconic":
            self.refresh_quietly(self.current_folder, min_age=AUTO_REFRESH_MS / 1000 - 1)
        self.root.after(AUTO_REFRESH_MS, self._auto_refresh)

    def rescan(self):
        path = self.current_folder
        if path and path not in self.scanning:
            self.cache.pop(path, None)
            self.open_folder(path, force=True)

    # ==========================================
    # 分類、搜尋、時間篩選
    # ==========================================
    def _refresh_filters(self):
        for w in self.filter_frame.winfo_children():
            w.destroy()
        for g in self.groups:
            var = self.group_vars.get(g["name"])
            if var is None:
                var = self.group_vars[g["name"]] = tk.BooleanVar(value=g["on"])
            self._checkbox(self.filter_frame, g["name"], var, lambda n=g["name"]: self._on_group_toggled(n),
                           icon=g["icon"] if self.icons.get(g["icon"]) else "📁").pack(side=tk.LEFT, padx=3)
        self._checkbox(self.filter_frame, OTHERS_NAME, self.others_var, self._on_others_toggled,
                       icon=OTHERS_ICON).pack(side=tk.LEFT, padx=3)

    def _on_group_toggled(self, name):
        self.config.group_states[name] = self.group_vars[name].get()
        self._save_config()
        self.render()

    def _on_others_toggled(self):
        self.config.group_states[OTHERS_NAME] = self.others_var.get()
        self._save_config()
        self.render()

    def _on_remember_toggled(self):
        self.config.remember_session = self.remember_var.get()
        self._save_config()
        if self.config.remember_session:
            self.set_status("下次開啟會還原現在開著的分頁和加入的副檔名", icon="🕘")
        else:
            self.set_status("下次開啟只會打開釘選的分頁，加入的副檔名不保留（這次開著的不受影響）", icon="📌")

    def add_extension(self):
        text = self.entry_ext.get()
        self.entry_ext.delete(0, tk.END)
        group = self.config.add_extension(text)
        if group is None:
            if text.strip():
                self.set_status(f"「{text.strip()}」已經在分類裡了", warn=True)
            return
        self.groups = self.config.groups()
        self._save_config()
        self._refresh_filters()
        self.render()

    def set_time_filter(self, value):
        self.tab.time_filter = value
        self._refresh_time_buttons()
        self.render()

    def _refresh_time_buttons(self):
        for value, btn in self.time_buttons.items():
            on = value == self.tab.time_filter
            btn.config(bg=C["accent"] if on else C["bg_element"], fg=C["fg_highlight"] if on else C["fg_text"])

    def _since(self):
        v, now = self.tab.time_filter, datetime.now()
        if v is None:
            return None
        if v == "today":
            return datetime(now.year, now.month, now.day).timestamp()
        if v == "year":
            return datetime(now.year, 1, 1).timestamp()
        return (now - timedelta(days=v)).timestamp()

    def _on_search_changed(self, *args):
        if self.switching:
            return
        if self.search_after:
            self.root.after_cancel(self.search_after)
        self.search_after = self.root.after(200, self._apply_search)

    def _apply_search(self):
        self.search_after = None
        self.tab.query = self.search_var.get()
        self.tab.expanded.clear()
        self.render()

    # ==========================================
    # 右側分欄
    # ==========================================
    def render(self, keep_view=False, note=""):
        """keep_view：背景重掃後更新時，各欄捲動的位置維持不動。note：附在狀態列最後的說明。"""
        res = self.cache.get(self.current_folder)
        if res is None:
            return
        self.tip.hide()
        active = [g for g in self.groups if self.group_vars[g["name"]].get()]
        names = [g["name"] for g in active]
        query = self.search_var.get().strip()
        since = self._since()
        by_group, others = classify(res.files, self.groups, names, query, since)
        filtering = bool(query) or since is not None
        has_others = self.others_var.get()

        keys = names + ([OTHERS_KEY] if has_others else [])
        scroll = {}
        if keys != self.column_keys:
            self._build_columns(active, has_others)
            self.column_keys = keys
        elif keep_view:
            scroll = {key: col[2].yview()[0] for key, col in self.columns.items()}

        self.item_files.clear()
        self.more_items.clear()
        for g in active:
            self._fill_column(g["name"], by_group[g["name"]], g["icon"], PAGE_SIZE, filtering)
        if has_others:
            self._fill_column(OTHERS_KEY, others, OTHERS_ICON, OTHERS_PAGE_SIZE, filtering)
        for key, top in scroll.items():
            self.columns[key][2].yview_moveto(top)

        status = f"{res.root}　｜　掃描深度 {res.depth}　｜　{len(res.files):,} 個檔案　｜　{res.elapsed:.2f} 秒"
        if filtering:
            shown = sum(len(v) for v in by_group.values()) + (len(others) if has_others else 0)
            status += f"　｜　符合條件 {shown:,} 個"
        if note:
            status += f"　｜　{note}"
        if res.unreadable:
            status += f"　｜　{len(res.unreadable)} 個資料夾無法讀取（點這裡查看）"
        self.set_status(status, warn=bool(res.unreadable), icon="⚠" if res.unreadable else "📂")

    def _build_columns(self, active, has_others):
        self._new_grid_frame()
        self.columns = {}
        specs = [(g["name"], g["name"], g["icon"]) for g in active]
        if has_others:
            specs.append((OTHERS_KEY, "其他", OTHERS_ICON))
        if not specs:
            tk.Label(self.grid_frame, text="上方「顯示分類」至少勾選一個", bg=C["bg_main"], fg=C["fg_dim"],
                     font=(FONT, 12)).pack(expand=True)
            return
        for key, title, icon in specs:
            frame = tk.LabelFrame(self.grid_frame, bg=C["bg_panel"])
            image = self.icons.get(icon) or self.icons.get("📁")
            title_label = tk.Label(frame, text=f" {title}", image=image or "", compound="left",
                                   font=(FONT, 10, "bold"), bg=C["bg_panel"], fg=C["fg_highlight"])
            frame.configure(labelwidget=title_label)
            inner = tk.Frame(frame, bg=C["bg_panel"])
            inner.pack(fill=tk.BOTH, expand=True)
            tree = ttk.Treeview(inner, show="tree headings", selectmode="browse", style="Files.Treeview")
            tree.heading("#0", text="時間 | 檔名", anchor="w")
            tree.column("#0", stretch=True, width=200, minwidth=120)
            scroll = ttk.Scrollbar(inner, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            tree.tag_configure("year", foreground=C["year_fg"], background=C["year_bg"])
            tree.tag_configure("more", foreground=C["link"])
            tree.tag_configure("empty", foreground=C["fg_dim"])
            tree.bind("<Double-1>", self._on_file_activate)
            tree.bind("<Return>", self._on_file_activate)
            tree.bind("<ButtonRelease-1>", self._on_file_click)
            tree.bind("<<TreeviewSelect>>", self._on_file_select)
            tree.bind("<Button-3>", self._on_right_click)
            # 只綁在檔案清單上：焦點在搜尋框時，Ctrl+C 照常複製文字
            tree.bind("<Control-c>", self._on_file_copy)
            tree.bind("<Control-C>", self._on_file_copy)
            tree.bind("<Motion>", self._on_motion)
            tree.bind("<Leave>", lambda e: self.tip.hide())
            self.columns[key] = (frame, title_label, tree, title)

        n = len(specs)
        cols = n if n <= 3 else 2 if n == 4 else 3 if n <= 6 else 4
        rows = (n + cols - 1) // cols
        for i in range(cols):
            self.grid_frame.grid_columnconfigure(i, weight=1, uniform="col")
        for i in range(rows):
            self.grid_frame.grid_rowconfigure(i, weight=1, uniform="row")
        for idx, (key, _, _) in enumerate(specs):
            self.columns[key][0].grid(row=idx // cols, column=idx % cols, sticky="nsew", padx=4, pady=4)

    def _next_iid(self):
        self.item_seq += 1
        return f"item_{self.item_seq}"

    def _fill_column(self, key, files, icon, page, filtering):
        frame, title_label, tree, title = self.columns[key]
        tree.delete(*tree.get_children())
        title_label.config(text=f" {title}　{len(files):,} ")
        if not files:
            tree.insert("", tk.END, text="   （沒有符合條件的檔案）" if filtering else "   （沒有檔案）", tags=("empty",))
            return
        image = self.icons.get(icon) or self.icons.get("📁") or ""
        calendar = self.icons.get("📅") or ""
        limit = len(files) if key in self.tab.expanded else page
        year = None
        for f in files[:limit]:
            dt = datetime.fromtimestamp(f.mtime)
            if dt.year != year:
                tree.insert("", tk.END, text=f" {dt.year} 年", image=calendar, tags=("year",))
                year = dt.year
            iid = self._next_iid()
            tree.insert("", tk.END, iid=iid, text=f" [{dt:%m/%d %H:%M}] {f.name}", image=image, tags=("file",))
            self.item_files[iid] = f
        if len(files) > limit:
            iid = self._next_iid()
            tree.insert("", tk.END, iid=iid, text=f"   ⋯ 還有 {len(files) - limit:,} 份，點這裡顯示全部", tags=("more",))
            self.more_items[iid] = key

    # ==========================================
    # 檔案操作
    # ==========================================
    def _on_file_click(self, event):
        iid = event.widget.identify_row(event.y)
        if iid in self.more_items:
            self.tab.expanded.add(self.more_items[iid])
            self.render()

    def _on_file_activate(self, event):
        tree = event.widget
        iid = tree.focus()
        if iid in self.item_files:
            self.open_file(self.item_files[iid].path)

    def _on_file_select(self, event):
        sel = event.widget.selection()
        f = self.item_files.get(sel[0]) if sel else None
        if f:
            self.set_status(f"{f.rel_path}　｜　{format_size(f.size)}　｜　"
                            f"修改於 {datetime.fromtimestamp(f.mtime):%Y-%m-%d %H:%M}", icon="📄")

    def _on_motion(self, event):
        iid = event.widget.identify_row(event.y)
        f = self.item_files.get(iid)
        if f:
            self.tip.schedule(iid, f.rel_path, event.x_root, event.y_root)
        else:
            self.tip.hide()

    def _on_right_click(self, event):
        tree = event.widget
        iid = tree.identify_row(event.y)
        f = self.item_files.get(iid)
        if not f:
            return
        tree.selection_set(iid)
        tree.focus(iid)
        self.tip.hide()
        menu = tk.Menu(self.root, tearoff=0, bg=C["bg_panel"], fg=C["fg_highlight"],
                       activebackground=C["accent"], activeforeground=C["fg_highlight"], borderwidth=0,
                       font=(FONT, 9))

        def item(label, icon, command, accelerator=""):
            menu.add_command(label=f" {label}", image=self.icons.get(icon) or "", compound="left", command=command,
                             accelerator=accelerator)

        item("開啟", "▶", lambda: self.open_file(f.path))
        if f.ext == ".pdf" and self.wlpdf:
            item("用 WingLightPDF 開啟", "⚡", lambda: self.open_with_winglightpdf(f.path))
        item("開啟檔案位置", "📂", lambda: self.open_location(f.path))
        menu.add_separator()
        item("複製檔案", "📋", lambda: self.copy_file(f.path), accelerator="Ctrl+C")
        item("複製完整路徑", "📋", lambda: self.copy_text(f.path, "完整路徑"))
        item("複製檔名", "📋", lambda: self.copy_text(f.name, "檔名"))
        menu.add_separator()
        item("檔案詳細資訊", "📑", lambda: self.show_properties(f.path))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def open_file(self, path):
        try:
            os.startfile(path)
        except OSError as e:
            messagebox.showerror("無法開啟", f"{path}\n\n{e}\n\n檔案可能已經被移動或刪除，可以按 F5 重新掃描。")

    def open_with_winglightpdf(self, path):
        try:
            subprocess.Popen([self.wlpdf, path])
        except OSError as e:
            messagebox.showerror("無法啟動 WingLightPDF", str(e))

    def open_location(self, path):
        if os.path.exists(path):
            subprocess.Popen(f'explorer /select,"{path}"')
        else:
            messagebox.showwarning("找不到檔案", f"{path}\n\n檔案可能已經被移動或刪除，可以按 F5 重新掃描。")

    def _on_file_copy(self, event):
        sel = event.widget.selection()
        f = self.item_files.get(sel[0]) if sel else None
        if f:
            self.copy_file(f.path)
        return "break"

    def copy_file(self, path):
        if not os.path.exists(path):
            messagebox.showwarning("找不到檔案", f"{path}\n\n檔案可能已經被移動或刪除，可以按 F5 重新掃描。")
            return
        try:
            copy_file_to_clipboard(path)
        except OSError as e:
            messagebox.showerror("無法複製檔案", f"{e}\n\n請稍後再試一次，或改用「複製完整路徑」。")
            return
        self.set_status(f"已複製檔案：{os.path.basename(path)}", icon="📋")

    def copy_text(self, text, what):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(f"已複製{what}：{text}", icon="📋")

    def show_properties(self, path):
        try:
            st = os.stat(path)
        except OSError as e:
            messagebox.showerror("錯誤", f"無法讀取檔案資訊：\n{e}")
            return
        messagebox.showinfo("檔案詳細資訊", (
            f"📄 檔案名稱：{os.path.basename(path)}\n\n"
            f"📁 完整位置：\n{path}\n\n"
            f"💾 檔案大小：{format_size(st.st_size)}\n\n"
            f"📅 建立時間：{datetime.fromtimestamp(st.st_ctime):%Y-%m-%d %H:%M:%S}\n"
            f"✏️ 修改時間：{datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}"
        ))

    def _show_unreadable(self):
        res = self.cache.get(self.current_folder)
        if not res or not res.unreadable:
            return
        lines = res.unreadable[:20]
        more = f"\n…另外還有 {len(res.unreadable) - 20} 個" if len(res.unreadable) > 20 else ""
        messagebox.showwarning("無法讀取的資料夾", "以下資料夾讀不到（可能是網路磁碟斷線、沒有權限或路徑太長）：\n\n"
                               + "\n".join(lines) + more)

    # ==========================================
    # 設定
    # ==========================================
    def _save_config(self):
        try:
            self.config.save()
        except OSError as e:
            self.set_status(f"設定檔存不進去：{e}", warn=True)

    def on_close(self):
        self.config.geometry = self.root.geometry()
        self._store_tabs()
        self._save_config()
        self.root.destroy()
