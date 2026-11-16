# -*- coding: utf-8 -*-
"""WingLightFinder 主視窗：左邊資料夾樹，右邊依軟體類型分欄、由新到舊列出檔案。"""

import os
import time
import queue
import string
import ctypes
import winreg
import threading
import subprocess
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from datetime import datetime, timedelta

from core.clipboard import copy_file_to_clipboard
from core.config import Config, assets_dir
from core.scanner import scan_folder, scan_depth_for, classify
from core.tabs import TabList
from core.version import APP_NAME, SUBTITLE
from ui.about import AboutDialog
from ui.winglight_tk import (FONTS, THEME_NAMES, Icons, Theme, Segmented, StatusBar, Tooltip, box, checkbox,
                             entry_box, popup_menu, separator, system_theme, tool_button, TreeChevrons)

APP_TITLE = f"{APP_NAME} — {SUBTITLE}"
PAGE_SIZE = 100          # 每欄先列出的筆數，其餘按「顯示全部」再展開
OTHERS_PAGE_SIZE = 25
OTHERS_KEY = "__others__"
OTHERS_NAME = "其他"       # 不在任何分類裡的檔案；預設不顯示，在「顯示分類」勾選
OTHERS_ICON = "paperclip"
REFRESH_MIN_AGE = 3        # 秒：剛掃過的資料夾不重掃
AUTO_REFRESH_MS = 30_000   # 視窗開著時，每 30 秒在背景重掃目前的資料夾
SEP = "｜"

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


class FinderApp:
    def __init__(self, root, config=None):
        self.root = root
        self.config = config or Config()
        self.groups = self.config.groups()
        self.group_vars = {}
        self.nodes = {}             # 資料夾樹節點 → 路徑
        self.node_icons = {}        # 資料夾樹節點 → 圖示名稱（箭頭和圖示畫在同一張圖裡）
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
        self.columns = {}           # 欄 key → (frame, 檔案數, tree, 標題, 圖示)
        self.column_keys = None
        self.item_files = {}        # Treeview item id → FileEntry
        self.more_items = {}        # 「顯示全部」那一列 → 欄 key
        self.item_seq = 0
        self.search_after = None
        self.switching = False      # 切換分頁時還原搜尋字，不要當成使用者在打字
        self.wlpdf = find_winglightpdf()

        self.scale = max(1.0, root.winfo_fpixels("1i") / 96)
        self.theme = Theme(root, self.config.theme or system_theme(), self.scale)
        # 圖示先登記：切換主題時先重塗圖示，再跑 _on_theme_changed
        self.icons = Icons(self.theme, os.path.join(assets_dir(), "icons"))
        self.theme.on_change(self._on_theme_changed)
        self.chevrons = TreeChevrons(self.theme, self.icons)
        self.px = self.theme.px
        root.title(APP_TITLE)
        root.geometry(self.config.geometry or f"{int(1280 * self.scale)}x{int(780 * self.scale)}")
        root.minsize(int(900 * self.scale), int(560 * self.scale))
        icon = os.path.join(assets_dir(), "app_icon.ico")
        if os.path.exists(icon):
            try:
                root.iconbitmap(default=icon)
            except tk.TclError:
                pass

        self.tip = Tooltip(root, self.theme)
        self.hover = None           # 滑鼠移過的清單列 (Treeview, item id)
        self.others_var = tk.BooleanVar(value=self.config.group_states.get(OTHERS_NAME, False))
        self.remember_var = tk.BooleanVar(value=remember)
        self.search_var = tk.StringVar()
        self._build_ui()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.bind("<Control-f>", lambda e: self._focus_search())
        root.bind("<F5>", lambda e: self.rescan())
        root.bind_all("<Control-t>", lambda e: (self.new_tab(), "break")[1])
        root.bind_all("<Control-w>", lambda e: (self.close_tab(self.tabset.active), "break")[1])
        root.bind_all("<Control-Tab>", lambda e: (self._step_tab(1), "break")[1])
        root.bind_all("<Control-Shift-Tab>", lambda e: (self._step_tab(-1), "break")[1])
        # 滑鼠提示在按下按鍵或捲動時立刻收起來
        root.bind_all("<KeyPress>", self.tip.hide, add="+")
        root.bind_all("<MouseWheel>", self.tip.hide, add="+")
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
    # 版面
    # ==========================================
    def _tool_button(self, parent, text, command, icon=None, **kw):
        return tool_button(parent, self.theme, text, command, self.icons.get(icon), **kw)

    def _caption(self, parent, text, bg="chrome", icon=None):
        """「修改時間」「顯示分類」這類欄位名稱：caption、muted。"""
        image = self.icons.get(icon)
        return self.theme.paint(tk.Label(parent, text=f" {text}" if image else text, image=image or "",
                                         compound="left", font=FONTS["caption"], borderwidth=0), bg=bg, fg="muted")

    def _style_tree(self, tree):
        """清單的 tag 顏色。hover 要最先建立：先建立的 tag 優先權比較高。"""
        t = self.theme
        tree.tag_configure("hover", background=t["hover"])
        tree.tag_configure("year", background=t["bg"], foreground=t["warn"], font=FONTS["body-strong"])
        tree.tag_configure("more", foreground=t["accent"], font=FONTS["body-strong"])
        tree.tag_configure("empty", foreground=t["muted"])

    def _build_ui(self):
        t, px = self.theme, self.px
        # Toolbar：chrome 底、下緣 1px line，兩列
        top = t.paint(tk.Frame(self.root, padx=px(8), pady=px(6)), bg="chrome")
        top.pack(side=tk.TOP, fill=tk.X)
        t.paint(tk.Frame(self.root, height=1), bg="line").pack(side=tk.TOP, fill=tk.X)

        # 第一列：資料夾、時間；搜尋框靠右
        row1 = t.paint(tk.Frame(top), bg="chrome")
        row1.pack(fill=tk.X)
        for text, command, icon in (("瀏覽目錄", self.select_directory, "folder"), ("加入最愛", self.add_favorite, "star"),
                                    ("移除最愛", self.remove_favorite, "close"), ("重新掃描", self.rescan, "refresh")):
            self._tool_button(row1, text, command, icon=icon).pack(side=tk.LEFT, padx=(0, px(2)))
        separator(row1, t).pack(side=tk.LEFT)
        self._caption(row1, "修改時間", icon="clock").pack(side=tk.LEFT, padx=(px(4), px(8)))
        self.time_filter = Segmented(row1, t, TIME_FILTERS, self.set_time_filter, self.tab.time_filter)
        self.time_filter.pack(side=tk.LEFT)

        search_box, self.search_entry = entry_box(row1, t, 28, image=self.icons.get("search"))
        search_box.pack(side=tk.RIGHT)
        self.search_entry.configure(textvariable=self.search_var)
        self.search_var.trace_add("write", self._on_search_changed)
        self.search_entry.bind("<Escape>", lambda e: self.search_var.set(""))
        clear = t.paint(tk.Label(search_box, image=self.icons.get("close"), cursor="hand2", borderwidth=0,
                                 padx=px(8)), bg="panel")
        clear.pack(side=tk.RIGHT, fill=tk.Y)
        clear.bind("<Button-1>", lambda e: self.search_var.set(""))
        clear.bind("<Enter>", lambda e: clear.configure(image=self.icons.get("close", "text")))
        clear.bind("<Leave>", lambda e: clear.configure(image=self.icons.get("close")))
        self.tip.attach(clear, "清除搜尋（Esc）")
        self._caption(row1, "搜尋").pack(side=tk.RIGHT, padx=(0, px(8)))

        # 第二列：分類
        row2 = t.paint(tk.Frame(top), bg="chrome")
        row2.pack(fill=tk.X, pady=(px(4), 0))
        self._caption(row2, "顯示分類").pack(side=tk.LEFT, padx=(px(10), px(6)))
        self.filter_frame = t.paint(tk.Frame(row2), bg="chrome")
        self.filter_frame.pack(side=tk.LEFT)
        self._refresh_filters()
        separator(row2, t).pack(side=tk.LEFT)
        ext_box, self.entry_ext = entry_box(row2, t, 8)
        ext_box.pack(side=tk.LEFT, padx=(0, px(2)))
        self.entry_ext.bind("<Return>", lambda e: self.add_extension())
        self._tool_button(row2, "加入其他副檔名", self.add_extension, icon="plus").pack(side=tk.LEFT)

        remember = checkbox(row2, t, "記住上次畫面", self.remember_var, self._on_remember_toggled, self.icons)
        remember.pack(side=tk.RIGHT)
        self.tip.attach(remember, "勾選：下次開啟還原這次開著的分頁和加入的副檔名\n不勾：下次開啟只打開釘選的分頁")

        # 狀態列（先 pack 才不會被主畫面擠掉）
        self.status = StatusBar(self.root, t, self.icons)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)
        self.status.bind_click(self._show_unreadable)

        paned = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=px(8), pady=px(8))

        # 左邊：資料夾樹，下方放深淺色切換和「關於」（muted 字）
        left = t.paint(tk.Frame(paned), bg="bg")
        paned.add(left, weight=1)
        bottom = t.paint(tk.Frame(left), bg="bg")
        bottom.pack(side=tk.BOTTOM, fill=tk.X, pady=(px(6), 0))
        self.theme_button = self._tool_button(bottom, "", self.toggle_theme, bg="bg", fg="muted")
        self.theme_button.pack(side=tk.LEFT)
        self.tip.attach(self.theme_button, "切換淺色／深色（會記住）")
        self._refresh_theme_button()
        self._tool_button(bottom, "關於", self.show_about, icon="info", bg="bg", fg="muted").pack(
            side=tk.LEFT, padx=(px(2), 0))

        tree_box = box(left, t)
        tree_box.pack(fill=tk.BOTH, expand=True)
        scroll = ttk.Scrollbar(tree_box, orient="vertical")
        self.tree = ttk.Treeview(tree_box, yscrollcommand=scroll.set)
        scroll.config(command=self.tree.yview)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.heading("#0", text="專案與磁碟目錄", anchor="w")
        self._style_tree(self.tree)
        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewClose>>", lambda e: self._sync_chevron(self.tree.focus(), False))
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Button-2>", self._on_tree_middle_click)
        self.tree.bind("<Button-3>", self._on_tree_right_click)
        self.tree.bind("<Motion>", lambda e: self._set_hover(self.tree, self.tree.identify_row(e.y)))
        self.tree.bind("<Leave>", lambda e: self._set_hover(None, None))

        # 右邊：分頁軌道（chrome 底），下面是分欄
        self.right = t.paint(tk.Frame(paned), bg="bg")
        paned.add(self.right, weight=4)
        self.tabbar = t.paint(tk.Frame(self.right, padx=px(4), pady=px(4)), bg="chrome")
        self.tabbar.pack(side=tk.TOP, fill=tk.X, pady=(0, px(8)))
        self.grid_frame = None
        self._show_placeholder("從左邊選一個資料夾，或按「瀏覽目錄」")

        self._init_tree()
        self._refresh_tab_bar()

    def _show_placeholder(self, text, icon=None):
        self._new_grid_frame()
        self.column_keys = None
        self.columns = {}
        image = self.icons.get(icon)
        self.theme.paint(tk.Label(self.grid_frame, text=f" {text}" if image else text, image=image or "",
                                  compound="left", font=FONTS["body"]), bg="bg", fg="muted").pack(expand=True)

    def _new_grid_frame(self):
        if self.grid_frame is not None:
            self.grid_frame.destroy()
        self.hover = None
        self.grid_frame = self.theme.paint(tk.Frame(self.right), bg="bg")
        self.grid_frame.pack(fill=tk.BOTH, expand=True)

    def set_status(self, text, detail="", kind="info", icon=None):
        """kind：info（icon 是線條圖示名稱，可省略）、busy、ok、warn、error，見 StatusBar。"""
        self.status.set(text, detail, kind, icon)

    def _set_hover(self, tree, iid):
        """滑鼠移過的清單列加上 hover 底色；選取列維持 soft 底。"""
        if self.hover == (tree, iid) or (self.hover is None and not iid):
            return
        if self.hover:
            old_tree, old = self.hover
            try:
                if old_tree.exists(old):
                    old_tree.item(old, tags=[x for x in old_tree.item(old, "tags") if x != "hover"])
            except tk.TclError:  # 清單已經重建
                pass
        self.hover = None
        if tree is not None and iid and tree.item(iid, "text") != "dummy":
            tree.item(iid, tags=list(tree.item(iid, "tags")) + ["hover"])
            self.hover = (tree, iid)

    # ==========================================
    # 深淺色主題
    # ==========================================
    def toggle_theme(self):
        name = "light" if self.theme.dark else "dark"
        self.theme.set(name)
        self.config.theme = name
        self._save_config()

    def _on_theme_changed(self):
        self._style_tree(self.tree)
        for col in self.columns.values():
            self._style_tree(col[2])
        self._refresh_tab_bar()
        self._refresh_theme_button()

    def _refresh_theme_button(self):
        """顯示目前是哪一種：太陽＝淺色、月亮＝深色。"""
        image = self.icons.get("moon" if self.theme.dark else "sun")
        self.theme_button.configure(text=f" {THEME_NAMES[self.theme.name]}" if image else THEME_NAMES[self.theme.name],
                                    image=image or "")

    def show_about(self):
        self.tip.hide()
        AboutDialog(self.root, self.theme, assets_dir()).show()

    def _focus_search(self):
        self.search_entry.focus_set()
        self.search_entry.select_range(0, tk.END)

    # ==========================================
    # 資料夾樹與我的最愛
    # ==========================================
    def _init_tree(self):
        self.fav_node = self._add_group("", "end", " 我的最愛", "star", True)
        self._refresh_favorites()

        home = os.path.expanduser("~")
        quick = self._add_group("", "end", " 快速存取", "pushpin", False)
        for label, sub in (("桌面", "Desktop"), ("文件", "Documents"), ("下載", "Downloads")):
            self._add_node(quick, label, os.path.join(home, sub))

        local = self._add_group("", "end", " 本機與外接裝置", "computer", False)
        net = self._add_group("", "end", " 網路磁碟", "globe", False)
        bitmask = ctypes.windll.kernel32.GetLogicalDrives()
        for letter in string.ascii_uppercase:
            if bitmask & 1:
                drive = f"{letter}:\\"
                kind = ctypes.windll.kernel32.GetDriveTypeW(drive)
                if kind in (2, 3):      # 2=USB, 3=硬碟
                    self._add_node(local, f"磁碟 {letter}:", drive, icon="computer")
                elif kind == 4:         # 4=網路磁碟
                    self._add_node(net, f"網路槽 {letter}:", drive, icon="globe")
            bitmask >>= 1
        for node in (quick, local, net):
            self._sync_chevron(node)

    def _add_node(self, parent, text, path, check=True, icon="folder"):
        if check and not os.path.exists(path):
            return None
        node = self.tree.insert(parent, "end", text=f" {text}", image=self.chevrons.image(icon, "closed"))
        self.nodes[node] = path
        self.node_icons[node] = icon
        self.tree.insert(node, "end", text="dummy")
        return node

    def _add_group(self, parent, index, text, icon, opened):
        """資料夾樹最上層的分組（我的最愛、快速存取…）。"""
        node = self.tree.insert(parent, index, text=text, open=opened,
                                image=self.chevrons.image(icon, "open" if opened else "closed"))
        self.node_icons[node] = icon
        return node

    def _sync_chevron(self, node, opened=None):
        """依展開與否、有沒有子項目換箭頭。展開、收合事件發生時 -open 還沒改，所以由呼叫的地方告訴我。"""
        icon = self.node_icons.get(node)
        if icon is None or not self.tree.exists(node):
            return
        if opened is None:
            opened = bool(self.tree.item(node, "open"))
        state = "leaf" if not self.tree.get_children(node) else "open" if opened else "closed"
        self.tree.item(node, image=self.chevrons.image(icon, state))

    def _refresh_favorites(self):
        for child in self.tree.get_children(self.fav_node):
            self.nodes.pop(child, None)
            self.node_icons.pop(child, None)
            self.tree.delete(child)
        for path in self.config.favorites:
            name = os.path.basename(path.rstrip("\\/")) or path
            # 網路磁碟沒連上時也要列出來，點下去會在狀態列說明讀不到
            self._add_node(self.fav_node, name, path, check=False)
        self._sync_chevron(self.fav_node)

    def add_favorite(self):
        if not self.current_folder:
            self.set_status("先在左邊選一個資料夾，再加入最愛", kind="warn")
            return
        self._add_favorite_path(self.current_folder)

    def _add_favorite_path(self, path):
        if path not in self.config.favorites:
            self.config.favorites.append(path)
            self._save_config()
            self._refresh_favorites()
            self.set_status(f"已加入最愛：{path}", kind="ok")

    def remove_favorite(self):
        sel = self.tree.selection()
        if not sel or self.tree.parent(sel[0]) != self.fav_node:
            self.set_status("請先在「我的最愛」底下選一個資料夾", kind="warn")
            return
        self._remove_favorite_path(self.nodes.get(sel[0]))

    def _remove_favorite_path(self, path):
        if path in self.config.favorites:
            self.config.favorites.remove(path)
            self._save_config()
            self._refresh_favorites()
            self.set_status(f"已移除最愛：{path}", kind="ok")

    def select_directory(self):
        path = filedialog.askdirectory()
        if not path:
            return
        path = os.path.normpath(path)
        if self.recent_node is None:
            self.recent_node = self._add_group("", 1, " 最近瀏覽", "history", True)
        node = next((n for n in self.tree.get_children(self.recent_node) if self.nodes.get(n) == path), None)
        if node is None:
            node = self._add_node(self.recent_node, os.path.basename(path) or path, path, check=False)
            self._sync_chevron(self.recent_node)
        self.tree.see(node)
        self.tree.selection_set(node)

    def _on_tree_open(self, event):
        node = self.tree.focus()
        children = self.tree.get_children(node)
        if len(children) == 1 and self.tree.item(children[0], "text") == "dummy":
            self.tree.delete(children[0])
            if node in self.nodes:
                self._load_subfolders(node, self.nodes[node])
        self._sync_chevron(node, True)

    def _load_subfolders(self, parent, path):
        try:
            names = sorted(os.listdir(path), key=str.upper)
        except OSError:
            self.set_status(f"無法讀取：{path}", kind="error")
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
        menu = popup_menu(self.root, self.theme)
        menu.add_command(label=" 在新分頁開啟", image=self.icons.get("plus") or "", compound="left",
                         command=lambda: self.new_tab(path))
        if not self.tab.pinned:
            menu.add_command(label=" 在目前分頁開啟", image=self.icons.get("folder-open") or "", compound="left",
                             command=lambda: self.open_folder(path))
        menu.add_separator()
        if path in self.config.favorites:
            menu.add_command(label=" 移除最愛", image=self.icons.get("close") or "", compound="left",
                             command=lambda: self._remove_favorite_path(path))
        else:
            menu.add_command(label=" 加入最愛", image=self.icons.get("star") or "", compound="left",
                             command=lambda: self._add_favorite_path(path))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # ==========================================
    # 分頁
    # ==========================================
    def _refresh_tab_bar(self):
        """分頁軌道：chrome 底；目前分頁 panel 底、accent 粗體字，其他 muted 字，滑鼠移過 hover 底。"""
        th, px = self.theme, self.px
        for w in self.tabbar.winfo_children():
            w.destroy()
        self.tab_cells = []
        for i, t in enumerate(self.tabset.tabs):
            active = i == self.tabset.active
            bg = "panel" if active else "chrome"
            cell = th.paint(tk.Frame(self.tabbar, cursor="hand2"), bg=bg)
            cell.pack(side=tk.LEFT, padx=(0, px(2)))
            title = t.title if len(t.title) <= 18 else t.title[:17] + "…"
            icon = "pushpin" if t.pinned else "folder-open" if active else "folder"
            image = self.icons.get(icon, "accent" if active else "muted") or ""
            label = th.paint(tk.Label(cell, text=f" {title}", image=image, compound="left",
                                      font=FONTS["body-strong" if active else "body"], borderwidth=0, padx=0,
                                      pady=0, cursor="hand2"), bg=bg, fg="accent" if active else "muted")
            parts = [cell, label]
            if t.pinned:
                # 釘選的分頁沒有關閉鈕
                label.pack(side=tk.LEFT, padx=px(12), pady=px(6))
                tip = f"{t.folder}\n已釘選：每次開啟都會打開（右鍵可取消釘選）"
            else:
                label.pack(side=tk.LEFT, padx=(px(12), px(2)), pady=px(6))
                close = th.paint(tk.Label(cell, image=self.icons.get("close") or "", borderwidth=0, padx=px(2),
                                          pady=0, cursor="hand2"), bg=bg)
                close.pack(side=tk.LEFT, padx=(0, px(8)))
                close.bind("<Button-1>", lambda e, i=i: self.close_tab(i))
                parts.append(close)
                tip = t.folder or "空白分頁：從左邊選一個資料夾"
            for w in (cell, label):
                w.bind("<ButtonPress-1>", lambda e, i=i: self._tab_press(e, i))
                w.bind("<B1-Motion>", self._tab_drag)
                w.bind("<ButtonRelease-1>", self._tab_release)
                w.bind("<Button-2>", lambda e, i=i: self.close_tab(i))
                w.bind("<Button-3>", lambda e, i=i: self._tab_menu(e, i))
            for w in parts:
                for sequence in ("<Enter>", "<Leave>"):
                    w.bind(sequence, lambda e, i=i, tip=tip, parts=parts, active=active:
                           self._tab_crossing(e, i, tip, parts, active))
            self.tab_cells.append(cell)
        plus = th.paint(tk.Label(self.tabbar, image=self.icons.get("plus") or "", borderwidth=0, padx=px(10),
                                 pady=px(6), cursor="hand2"), bg="chrome")
        plus.pack(side=tk.LEFT, fill=tk.Y)
        plus.bind("<Button-1>", lambda e: self.new_tab())
        plus.bind("<Enter>", lambda e: plus.configure(bg=th["hover"], image=self.icons.get("plus", "text") or ""))
        plus.bind("<Leave>", lambda e: (th.repaint(plus), plus.configure(image=self.icons.get("plus") or "")))
        self.tip.attach(plus, "新分頁（Ctrl+T）")
        self.drop_marker = th.paint(tk.Frame(self.tabbar, width=px(2)), bg="accent")

    def _tab_crossing(self, event, index, tip, parts, active):
        """滑鼠進出分頁（含裡面的文字和叉叉）：整個分頁一起變 hover 底，叉叉單獨變亮。"""
        th, cell = self.theme, parts[0]
        try:
            under = cell.winfo_containing(*self.root.winfo_pointerxy())
        except (KeyError, tk.TclError):
            under = None
        inside = under is not None and (str(under) == str(cell) or str(under).startswith(str(cell) + "."))
        for w in parts:
            th.repaint(w)
        if inside and not active:
            for w in parts:
                w.configure(bg=th["hover"])
            parts[1].configure(fg=th["text"])
        if len(parts) > 2:
            parts[2].configure(image=self.icons.get("close", "text" if under is parts[2] else "muted") or "")
        if inside and not self.drag:
            self.tip.schedule(("tab", index, tip), tip, event.x_root, event.y_root)
        elif not inside:
            self.tip.hide()

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
        menu = popup_menu(self.root, self.theme)

        def item(label, icon, command, enabled=True):
            menu.add_command(label=f" {label}", image=self.icons.get(icon) or "", compound="left", command=command,
                             state=tk.NORMAL if enabled else tk.DISABLED)

        if t.pinned:
            item("取消釘選", "pushpin", lambda: self.set_tab_pinned(index, False))
        else:
            item("釘選分頁（每次開啟都會打開）", "pushpin", lambda: self.set_tab_pinned(index, True), enabled=bool(t.folder))
        if t.folder:
            item("用檔案總管開啟", "folder-open", lambda: self.open_in_explorer(t.folder))
            if t.folder not in self.config.favorites:
                item("加入最愛", "star", lambda: self._add_favorite_path(t.folder))
        menu.add_separator()
        item("關閉分頁", "close", lambda: self.close_tab(index), enabled=not t.pinned)
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
            self.set_status(f"已釘選：{folder}", "每次開啟都會打開這個分頁", kind="ok")
        else:
            self.set_status(f"已取消釘選：{folder}", kind="ok")

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
            self.set_status("釘選的分頁要先取消釘選才能關閉（在分頁上按右鍵）", kind="warn")
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
        self.set_status("掃描中…", f"{path}{SEP}掃描深度 {scan_depth_for(path)}", kind="busy")
        self._show_placeholder("掃描中…", icon="hourglass")
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
            checkbox(self.filter_frame, self.theme, g["name"], var, lambda n=g["name"]: self._on_group_toggled(n),
                     self.icons).pack(side=tk.LEFT, padx=(0, self.px(12)))
        checkbox(self.filter_frame, self.theme, OTHERS_NAME, self.others_var, self._on_others_toggled,
                 self.icons).pack(side=tk.LEFT, padx=(0, self.px(12)))

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
            self.set_status("下次開啟會還原現在開著的分頁和加入的副檔名", icon="history")
        else:
            self.set_status("下次開啟只會打開釘選的分頁，加入的副檔名不保留（這次開著的不受影響）", icon="pushpin")

    def add_extension(self):
        text = self.entry_ext.get()
        self.entry_ext.delete(0, tk.END)
        group = self.config.add_extension(text)
        if group is None:
            if text.strip():
                self.set_status(f"「{text.strip()}」已經在分類裡了", kind="warn")
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
        self.time_filter.select(self.tab.time_filter)

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
        if self.search_after:  # 直接呼叫時（例如自我測試），取消還在排程中的那一次
            self.root.after_cancel(self.search_after)
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

        detail = [f"掃描深度 {res.depth}", f"{len(res.files):,} 個檔案", f"{res.elapsed:.2f} 秒"]
        if filtering:
            shown = sum(len(v) for v in by_group.values()) + (len(others) if has_others else 0)
            detail.append(f"符合條件 {shown:,} 個")
        if note:
            detail.append(note)
        if res.unreadable:
            detail.append(f"{len(res.unreadable)} 個資料夾無法讀取（點這裡查看）")
        self.set_status(res.root, SEP.join(detail), kind="warn" if res.unreadable else "ok")

    def _build_columns(self, active, has_others):
        self._new_grid_frame()
        self.columns = {}
        specs = [(g["name"], g["name"], g["icon"]) for g in active]
        if has_others:
            specs.append((OTHERS_KEY, "其他", OTHERS_ICON))
        t, px = self.theme, self.px
        if not specs:
            t.paint(tk.Label(self.grid_frame, text="上方「顯示分類」至少勾選一個", font=FONTS["body"]),
                    bg="bg", fg="muted").pack(expand=True)
            return
        for key, title, icon in specs:
            # 分欄：直角加 1px line 框；標題列是卡片標題，後面接檔案數
            frame = box(self.grid_frame, t)
            head = t.paint(tk.Frame(frame, padx=px(12), pady=px(8)), bg="panel")
            head.pack(side=tk.TOP, fill=tk.X)
            image = self.icons.get(icon) or self.icons.get("folder")
            title_label = t.paint(tk.Label(head, text=f" {title}", image=image or "", compound="left",
                                           font=FONTS["card-title"], borderwidth=0, padx=0), bg="panel", fg="text")
            title_label.pack(side=tk.LEFT)
            count_label = t.paint(tk.Label(head, font=FONTS["caption"], borderwidth=0, padx=0),
                                  bg="panel", fg="muted")
            count_label.pack(side=tk.LEFT, padx=(px(8), 0))
            inner = t.paint(tk.Frame(frame), bg="panel")
            inner.pack(fill=tk.BOTH, expand=True)
            tree = ttk.Treeview(inner, show="tree headings", selectmode="browse", style="Flat.Treeview")
            tree.heading("#0", text=f"時間{SEP}檔名", anchor="w")
            tree.column("#0", stretch=True, width=200, minwidth=120)
            scroll = ttk.Scrollbar(inner, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scroll.set)
            tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            scroll.pack(side=tk.RIGHT, fill=tk.Y)
            self._style_tree(tree)
            tree.bind("<Double-1>", self._on_file_activate)
            tree.bind("<Return>", self._on_file_activate)
            tree.bind("<ButtonRelease-1>", self._on_file_click)
            tree.bind("<<TreeviewSelect>>", self._on_file_select)
            tree.bind("<Button-3>", self._on_right_click)
            # 只綁在檔案清單上：焦點在搜尋框時，Ctrl+C 照常複製文字
            tree.bind("<Control-c>", self._on_file_copy)
            tree.bind("<Control-C>", self._on_file_copy)
            tree.bind("<Motion>", self._on_motion)
            tree.bind("<Leave>", self._on_leave_list)
            self.columns[key] = (frame, count_label, tree, title, icon)

        n = len(specs)
        cols = n if n <= 3 else 2 if n == 4 else 3 if n <= 6 else 4
        rows = (n + cols - 1) // cols
        for i in range(cols):
            self.grid_frame.grid_columnconfigure(i, weight=1, uniform="col")
        for i in range(rows):
            self.grid_frame.grid_rowconfigure(i, weight=1, uniform="row")
        gap = px(6)  # 欄與欄之間 12px，外緣跟分頁軌道對齊
        for idx, (key, _, _) in enumerate(specs):
            r, c = divmod(idx, cols)
            self.columns[key][0].grid(row=r, column=c, sticky="nsew",
                                      padx=(gap if c else 0, gap if c < cols - 1 else 0),
                                      pady=(gap if r else 0, gap if r < rows - 1 else 0))

    def _next_iid(self):
        self.item_seq += 1
        return f"item_{self.item_seq}"

    def _fill_column(self, key, files, icon, page, filtering):
        frame, count_label, tree, title, _ = self.columns[key]
        tree.delete(*tree.get_children())
        count_label.config(text=f"{len(files):,} 個")
        if not files:
            tree.insert("", tk.END, text="   （沒有符合條件的檔案）" if filtering else "   （沒有檔案）", tags=("empty",))
            return
        image = self.icons.get(icon) or self.icons.get("folder") or ""
        calendar = self.icons.get("calendar", "warn") or ""
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
            tree.insert("", tk.END, iid=iid, text=f"   還有 {len(files) - limit:,} 份，點這裡顯示全部", tags=("more",))
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
            icon = next((col[4] for col in self.columns.values() if col[2] is event.widget), "file")
            self.set_status(f.rel_path, f"{format_size(f.size)}{SEP}"
                            f"修改於 {datetime.fromtimestamp(f.mtime):%Y-%m-%d %H:%M}", icon=icon)

    def _on_motion(self, event):
        tree = event.widget
        iid = tree.identify_row(event.y)
        self._set_hover(tree, iid if iid in self.item_files or iid in self.more_items else None)
        f = self.item_files.get(iid)
        if f:
            self.tip.schedule(iid, f.rel_path, event.x_root, event.y_root)
        else:
            self.tip.hide()

    def _on_leave_list(self, event):
        self.tip.hide()
        self._set_hover(None, None)

    def _on_right_click(self, event):
        tree = event.widget
        iid = tree.identify_row(event.y)
        f = self.item_files.get(iid)
        if not f:
            return
        tree.selection_set(iid)
        tree.focus(iid)
        self.tip.hide()
        menu = popup_menu(self.root, self.theme)

        def item(label, icon, command, accelerator=""):
            menu.add_command(label=f" {label}", image=self.icons.get(icon) or "", compound="left", command=command,
                             accelerator=accelerator)

        item("開啟", "external", lambda: self.open_file(f.path))
        if f.ext == ".pdf" and self.wlpdf:
            item("用 WingLightPDF 開啟", "file", lambda: self.open_with_winglightpdf(f.path))
        item("開啟檔案位置", "folder-open", lambda: self.open_location(f.path))
        menu.add_separator()
        item("複製檔案", "copy", lambda: self.copy_file(f.path), accelerator="Ctrl+C")
        item("複製完整路徑", "copy", lambda: self.copy_text(f.path, "完整路徑"))
        item("複製檔名", "copy", lambda: self.copy_text(f.name, "檔名"))
        menu.add_separator()
        item("檔案詳細資訊", "info", lambda: self.show_properties(f.path))
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
        self.set_status(f"已複製檔案：{os.path.basename(path)}", kind="ok")

    def copy_text(self, text, what):
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.set_status(f"已複製{what}：{text}", kind="ok")

    def show_properties(self, path):
        try:
            st = os.stat(path)
        except OSError as e:
            messagebox.showerror("錯誤", f"無法讀取檔案資訊：\n{e}")
            return
        messagebox.showinfo("檔案詳細資訊", (
            f"檔案名稱：{os.path.basename(path)}\n\n"
            f"完整位置：\n{path}\n\n"
            f"檔案大小：{format_size(st.st_size)}\n\n"
            f"建立時間：{datetime.fromtimestamp(st.st_ctime):%Y-%m-%d %H:%M:%S}\n"
            f"修改時間：{datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}"
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
            self.set_status(f"設定檔存不進去：{e}", kind="error")

    def on_close(self):
        self.config.geometry = self.root.geometry()
        self._store_tabs()
        self._save_config()
        self.root.destroy()
