# -*- coding: utf-8 -*-
"""WingLightFinder 主視窗：左邊資料夾樹，右邊依軟體類型分欄、由新到舊列出檔案。"""

import os
import sys
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

from core.config import Config, app_dir
from core.scanner import scan_folder, scan_depth_for, classify
from ui.icons import Icons

APP_TITLE = "WingLightFinder — 專案檔案速查"
FONT = "Microsoft JhengHei"
PAGE_SIZE = 100          # 每欄先列出的筆數，其餘按「顯示全部」再展開
OTHERS_PAGE_SIZE = 25
OTHERS_KEY = "__others__"
OTHERS_ICON = "📌"

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
        self.current_folder = ""
        self.cache = {}             # 資料夾 → ScanResult，切換分類、搜尋時不必重掃
        self.scan_token = 0
        self.pending = 0
        self.results = queue.Queue()
        self.columns = {}           # 欄 key → (frame, title label, tree, title)
        self.column_keys = None
        self.expanded = set()       # 按過「顯示全部」的欄
        self.item_files = {}        # Treeview item id → FileEntry
        self.more_items = {}        # 「顯示全部」那一列 → 欄 key
        self.item_seq = 0
        self.time_filter = None
        self.search_after = None
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

        self._setup_styles()
        self._build_ui()
        self.tip = HoverTip(root)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Control-f>", lambda e: self._focus_search())
        root.bind("<F5>", lambda e: self.rescan())

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

        self.right = tk.Frame(paned, bg=C["bg_main"])
        paned.add(self.right, weight=4)
        self.grid_frame = None
        self._show_placeholder("從左邊選一個資料夾，或按「瀏覽目錄」")

        self._init_tree()

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
        if self.current_folder not in self.config.favorites:
            self.config.favorites.append(self.current_folder)
            self._save_config()
            self._refresh_favorites()
            self.set_status(f"已加入最愛：{self.current_folder}", icon="⭐")

    def remove_favorite(self):
        sel = self.tree.selection()
        if not sel or self.tree.parent(sel[0]) != self.fav_node:
            self.set_status("請先在「我的最愛」底下選一個資料夾", warn=True)
            return
        path = self.nodes.get(sel[0])
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
            self.open_folder(self.nodes[sel[0]])

    # ==========================================
    # 掃描（背景執行緒）
    # ==========================================
    def open_folder(self, path, force=False):
        self.current_folder = path
        self.expanded.clear()
        if not force and path in self.cache:
            self.render()
            return
        self.scan_token += 1
        token = self.scan_token
        depth = scan_depth_for(path)
        self.set_status(f"掃描中…　{path}（深度 {depth}）", icon="⏳")
        self._show_placeholder("掃描中…", icon="⏳")

        def work():
            res = scan_folder(path, depth, cancelled=lambda: token != self.scan_token)
            self.results.put((token, path, res))

        self.pending += 1
        threading.Thread(target=work, daemon=True).start()
        if self.pending == 1:
            self.root.after(40, self._poll_results)

    def _poll_results(self):
        while True:
            try:
                token, path, res = self.results.get_nowait()
            except queue.Empty:
                break
            self.pending -= 1
            if res.cancelled:
                continue
            self.cache[path] = res
            if token == self.scan_token and path == self.current_folder:
                self.render()
        if self.pending > 0:
            self.root.after(40, self._poll_results)

    def rescan(self):
        if self.current_folder:
            self.cache.pop(self.current_folder, None)
            self.open_folder(self.current_folder, force=True)

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
            image = self.icons.get(g["icon"]) or self.icons.get("📁")
            tk.Checkbutton(self.filter_frame, text=f" {g['name']}", image=image or "", compound="left",
                           variable=var, command=lambda n=g["name"]: self._on_group_toggled(n),
                           bg=C["bg_panel"], fg=C["fg_text"], selectcolor=C["bg_element"],
                           activebackground=C["bg_panel"], activeforeground=C["fg_highlight"],
                           font=(FONT, 9)).pack(side=tk.LEFT, padx=3)

    def _on_group_toggled(self, name):
        self.config.group_states[name] = self.group_vars[name].get()
        self._save_config()
        self.render()

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
        self.time_filter = value
        self._refresh_time_buttons()
        self.render()

    def _refresh_time_buttons(self):
        for value, btn in self.time_buttons.items():
            on = value == self.time_filter
            btn.config(bg=C["accent"] if on else C["bg_element"], fg=C["fg_highlight"] if on else C["fg_text"])

    def _since(self):
        v, now = self.time_filter, datetime.now()
        if v is None:
            return None
        if v == "today":
            return datetime(now.year, now.month, now.day).timestamp()
        if v == "year":
            return datetime(now.year, 1, 1).timestamp()
        return (now - timedelta(days=v)).timestamp()

    def _on_search_changed(self, *args):
        if self.search_after:
            self.root.after_cancel(self.search_after)
        self.search_after = self.root.after(200, self._apply_search)

    def _apply_search(self):
        self.search_after = None
        self.expanded.clear()
        self.render()

    # ==========================================
    # 右側分欄
    # ==========================================
    def render(self):
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
        if filtering:
            # 「其他」欄是否出現只看資料夾本身，避免打字時版面跳來跳去
            has_others = bool(classify(res.files, self.groups, names)[1])
        else:
            has_others = bool(others)

        keys = names + ([OTHERS_KEY] if has_others else [])
        if keys != self.column_keys:
            self._build_columns(active, has_others)
            self.column_keys = keys

        self.item_files.clear()
        self.more_items.clear()
        for g in active:
            self._fill_column(g["name"], by_group[g["name"]], g["icon"], PAGE_SIZE, filtering)
        if has_others:
            self._fill_column(OTHERS_KEY, others, "📁", OTHERS_PAGE_SIZE, filtering)

        status = f"{res.root}　｜　掃描深度 {res.depth}　｜　{len(res.files):,} 個檔案　｜　{res.elapsed:.2f} 秒"
        if filtering:
            shown = sum(len(v) for v in by_group.values()) + len(others)
            status += f"　｜　符合條件 {shown:,} 個"
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
        limit = len(files) if key in self.expanded else page
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
            self.expanded.add(self.more_items[iid])
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

        def item(label, icon, command):
            menu.add_command(label=f" {label}", image=self.icons.get(icon) or "", compound="left", command=command)

        item("開啟", "▶", lambda: self.open_file(f.path))
        if f.ext == ".pdf" and self.wlpdf:
            item("用 WingLightPDF 開啟", "⚡", lambda: self.open_with_winglightpdf(f.path))
        item("開啟檔案位置", "📂", lambda: self.open_location(f.path))
        menu.add_separator()
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
        self._save_config()
        self.root.destroy()
