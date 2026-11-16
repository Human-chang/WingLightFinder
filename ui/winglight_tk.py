# -*- coding: utf-8 -*-
"""WingLight 設計規範第 6 版：Tkinter 用的色票、深淺色主題與元件（只用 Python 標準庫）。

複製到新專案（例如 ui/winglight_tk.py）後：

    ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 在建立 Tk() 之前
    root = tk.Tk()
    theme = Theme(root, 設定檔裡的主題 or system_theme())
    bar = theme.paint(tk.Frame(root), bg="chrome")
    tool_button(bar, theme, "重新掃描", rescan).pack(side=tk.LEFT)
    theme.set("dark")   # 即時切換：登記過的元件、ttk 樣式、標題列一起換

- 色票名稱與 tokens.json 相同，每個值是 (淺色, 深色)。theme["accent"] 取目前主題的顏色。
- Tk 沒有樣式表：元件建立時用 theme.paint(元件, 選項=色票名) 登記，切換主題時逐一重設；
  ttk.Treeview 的 tag 這類登記不了的顏色，用 theme.on_change(函式) 在切換後自己重設。
- tk.Frame 做不出圓角：卡片、清單外框一律直角加 1px line 框（box），不要用圖片硬拼。
- 不要把 emoji 寫進 Tk 文字：啟動會慢好幾秒，帶 U+FE0F 的會變方框。小圖示用 PNG。
"""

import ctypes
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from tkinter import ttk

T = {
    "bg": ("#F6F5F3", "#1C1B1A"),
    "panel": ("#FFFFFF", "#272625"),
    "chrome": ("#EEECE8", "#141312"),
    "input": ("#F6F5F3", "#1C1B1A"),
    "hover": ("#E6E3DD", "#33312F"),
    "soft": ("#E6ECFB", "#2A3152"),
    "line": ("#E3E0DB", "#3D3B38"),
    "text": ("#1F1E1C", "#F2F0EC"),
    "muted": ("#5F5B55", "#B0ABA3"),
    "disabled": ("#A39E96", "#6E6A64"),
    "accent": ("#2B55D4", "#8FA8FF"),
    "accent-hover": ("#2347B8", "#A9BCFF"),
    "on-accent": ("#FFFFFF", "#10142B"),
    "ok": ("#1B7550", "#6FD3A6"),
    "warn": ("#8A5300", "#F2C46B"),
    "hi": ("#C2362F", "#FF8A80"),
    # 下面幾個深淺色相同：產品色只用在 App 圖示，glow 只給處理中的圓點
    "product-pdf": ("#D8433B", "#D8433B"),
    "product-finder": ("#C77700", "#C77700"),
    "product-gis": ("#1E8F63", "#1E8F63"),
    "glow": ("#FFC83D", "#FFC83D"),
    "splash-bg": ("#151A2E", "#151A2E"),
    "splash-text": ("#F2F0EC", "#F2F0EC"),
    "splash-muted": ("#B0ABA3", "#B0ABA3"),
}
THEMES = ("light", "dark")
THEME_NAMES = {"light": "淺色", "dark": "深色"}

UI = "Microsoft JhengHei UI"
MONO = "Consolas"
# 規範的字級換成 Tk 的 pt：不要再加別的大小
FONTS = {
    "page-title": (UI, 15, "bold"),
    "card-title": (UI, 11, "bold"),
    "body": (UI, 10),
    "body-strong": (UI, 10, "bold"),
    "caption": (UI, 9),
    "overline": (UI, 8, "bold"),
    # Consolas 沒有中文字，中文會改用另一套較粗的字補上；含中文的路徑用 caption
    "mono": (MONO, 9),
}


def system_theme():
    """Windows「選擇您的應用程式模式」：AppsUseLightTheme = 0 代表深色。"""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "dark" if value == 0 else "light"
    except (OSError, ImportError):
        return "light"


def set_title_bar_dark(toplevel, dark):
    """Windows 畫的標題列跟著深淺色（Windows 10 2004 以後；更舊的版本不理會）。"""
    try:
        if not toplevel.winfo_ismapped():  # 還沒顯示（或隱藏中）的視窗沒有標題列
            return
        hwnd = ctypes.windll.user32.GetParent(toplevel.winfo_id())
        if not hwnd:
            return
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE：2004 以後是 20，之前是 19
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value),
                                                          ctypes.sizeof(value)) == 0:
                break
        # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED：讓標題列馬上重畫
        ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x37)
    except (AttributeError, OSError):
        pass


class Theme:
    """目前的主題、登記過的元件與 ttk 樣式。"""

    def __init__(self, root, name="light", scale=None, icon_px=None):
        """icon_px：清單裡實際載入的圖示邊長（像素），列高至少要放得下；None 代表 16px × 縮放倍率。"""
        self.root = root
        self.name = name if name in THEMES else "light"
        self.scale = scale or max(1.0, root.winfo_fpixels("1i") / 96)
        self.icon_px = icon_px or self.px(16)
        self._items = {}
        self._limit = 500
        self._callbacks = []
        self.style = ttk.Style(root)
        self.style.theme_use("clam")
        self._style_ttk()
        root.configure(bg=self["bg"])
        # 主視窗第一次顯示時才有標題列可以設定
        root.bind("<Map>", lambda e: e.widget is root and set_title_bar_dark(root, self.dark), add="+")

    def __getitem__(self, token):
        return T[token][self.name == "dark"]

    @property
    def dark(self):
        return self.name == "dark"

    def px(self, n):
        """設計稿的 px 依螢幕縮放換成實際像素。"""
        return round(n * self.scale)

    def paint(self, widget, **options):
        """設定顏色並登記：options 是「元件選項＝色票名」，例如 bg="panel", fg="muted"。
        同一個元件再呼叫一次會換掉登記的色票（例如切換勾選狀態）。"""
        self._items[str(widget)] = (widget, options)
        widget.configure(**{k: self[v] for k, v in options.items()})
        if len(self._items) > self._limit:
            self._items = {k: v for k, v in self._items.items() if v[0].winfo_exists()}
            self._limit = max(500, 2 * len(self._items))
        return widget

    def repaint(self, widget):
        """還原成登記的顏色（例如滑鼠移開時）。"""
        entry = self._items.get(str(widget))
        if entry:
            widget.configure(**{k: self[v] for k, v in entry[1].items()})

    def on_change(self, callback):
        self._callbacks.append(callback)

    def set(self, name):
        """即時切換主題；回傳是否有換。"""
        if name not in THEMES or name == self.name:
            return False
        self.name = name
        self.root.configure(bg=self["bg"])
        self._style_ttk()
        for key, (widget, options) in list(self._items.items()):
            try:
                widget.configure(**{k: self[v] for k, v in options.items()})
            except tk.TclError:  # 已經關掉的元件
                del self._items[key]
        for callback in self._callbacks:
            callback()
        set_title_bar_dark(self.root, self.dark)
        return True

    def row_height(self):
        """清單列高：依字型 linespace 與圖示大小計算，不要寫死。"""
        line = tkfont.Font(root=self.root, font=FONTS["body"]).metrics("linespace")
        return max(line, self.icon_px) + self.px(8)

    def _style_ttk(self):
        s, c = self.style, self.__getitem__
        # List：panel 底；外框由外面的 box 畫，選取列 soft 底、文字不變色
        s.configure("Treeview", background=c("panel"), fieldbackground=c("panel"), foreground=c("text"),
                    bordercolor=c("panel"), lightcolor=c("panel"), darkcolor=c("panel"), borderwidth=0,
                    font=FONTS["body"], rowheight=self.row_height(), indicatorsize=self.px(10))
        s.map("Treeview", background=[("selected", c("soft"))], foreground=[("selected", c("text"))])
        # 表頭：bg 底、muted 字、caption；表頭不能點，滑鼠移過不變色
        s.configure("Treeview.Heading", background=c("bg"), foreground=c("muted"), font=FONTS["caption"],
                    relief="flat", borderwidth=0, bordercolor=c("line"), lightcolor=c("bg"), darkcolor=c("bg"),
                    padding=(self.px(8), self.px(3)))
        s.map("Treeview.Heading", background=[("active", c("bg"))])
        # 沒有階層的清單（例如依時間排列的檔案欄）：拿掉展開箭頭佔的縮排，用 style="Flat.Treeview"
        s.configure("Flat.Treeview", indent=0)
        s.layout("Flat.Treeview.Item", [
            ("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": ""}),
                ("Treeitem.focus", {"side": "left", "sticky": "", "children": [
                    ("Treeitem.text", {"side": "left", "sticky": ""}),
                ]}),
            ]}),
        ])
        # 捲軸：沒有箭頭，只有細的滑塊
        for orient, sticky in (("Vertical", "ns"), ("Horizontal", "we")):
            s.layout(f"{orient}.TScrollbar", [(f"{orient}.Scrollbar.trough", {"sticky": sticky, "children": [
                (f"{orient}.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
            s.configure(f"{orient}.TScrollbar", background=c("line"), troughcolor=c("panel"),
                        bordercolor=c("panel"), lightcolor=c("line"), darkcolor=c("line"), gripcount=0,
                        arrowsize=self.px(10))
            s.map(f"{orient}.TScrollbar", background=[("active", c("disabled"))],
                  lightcolor=[("active", c("disabled"))], darkcolor=[("active", c("disabled"))])
        s.configure("TPanedwindow", background=c("bg"))
        s.configure("Sash", sashthickness=self.px(8), gripcount=0, background=c("bg"),
                    lightcolor=c("bg"), darkcolor=c("bg"), bordercolor=c("bg"))
        s.configure("TSeparator", background=c("line"))


# ==========================================
# 元件
# ==========================================
def box(parent, theme, bg="panel", **kw):
    """卡片、清單外框：直角加 1px line 框。"""
    return theme.paint(tk.Frame(parent, highlightthickness=1, borderwidth=0, **kw),
                       bg=bg, highlightbackground="line", highlightcolor="line")


def separator(parent, theme, bg="chrome"):
    """工具列分組用的直線：1px × 20px line，左右各留 6px。"""
    holder = theme.paint(tk.Frame(parent, width=theme.px(13), height=theme.px(20)), bg=bg)
    holder.pack_propagate(False)
    theme.paint(tk.Frame(holder, width=1), bg="line").pack(fill=tk.Y, expand=True)
    return holder


def _text(text, image):
    return f" {text}" if image and text else text


def tool_button(parent, theme, text, command, image=None, bg="chrome", fg="text"):
    """工具列按鈕：平常沒有底色和外框，滑鼠移過 hover 底；勾選中（set_checked）soft 底、accent 粗體字。"""
    btn = tk.Button(parent, text=_text(text, image), image=image or "", compound="left", command=command,
                    relief=tk.FLAT, borderwidth=0, highlightthickness=0, padx=theme.px(10), pady=theme.px(5),
                    cursor="hand2", font=FONTS["body"])
    btn.wl = {"bg": bg, "fg": fg, "checked": False}
    theme.paint(btn, bg=bg, fg=fg, activebackground="hover", activeforeground="text",
                disabledforeground="disabled")
    btn.bind("<Enter>", lambda e: str(btn["state"]) != "disabled" and not btn.wl["checked"]
             and btn.configure(bg=theme["hover"], fg=theme["text"]))
    btn.bind("<Leave>", lambda e: theme.repaint(btn))
    return btn


def set_checked(btn, theme, checked):
    btn.wl["checked"] = checked
    if checked:
        theme.paint(btn, bg="soft", fg="accent", activebackground="soft", activeforeground="accent",
                    disabledforeground="disabled")
    else:
        theme.paint(btn, bg=btn.wl["bg"], fg=btn.wl["fg"], activebackground="hover", activeforeground="text",
                    disabledforeground="disabled")
    btn.configure(font=FONTS["body-strong" if checked else "body"])


class Segmented(tk.Frame):
    """分段選擇：軌道 input 填色、1px line 框；選中的那一格 panel 填色、accent 粗體字。
    每一格先依粗體字量好寬度，切換時不會左右跳。"""

    def __init__(self, parent, theme, options, command, value=None):
        super().__init__(parent, highlightthickness=1, borderwidth=0, padx=theme.px(2), pady=theme.px(2))
        self.theme = theme
        theme.paint(self, bg="input", highlightbackground="line", highlightcolor="line")
        bold = tkfont.Font(root=self, font=FONTS["body-strong"])
        height = bold.metrics("linespace") + 2 * theme.px(4)
        self.cells = {}
        for label, v in options:
            cell = tk.Frame(self, width=bold.measure(label) + 2 * theme.px(12), height=height)
            cell.pack_propagate(False)
            cell.pack(side=tk.LEFT)
            text = tk.Label(cell, text=label, cursor="hand2", borderwidth=0)
            text.pack(fill=tk.BOTH, expand=True)
            for w in (cell, text):
                w.bind("<Button-1>", lambda e, v=v: command(v))
                w.bind("<Enter>", lambda e, v=v: self._hover(v, True))
                w.bind("<Leave>", lambda e, v=v: self._hover(v, False))
            self.cells[v] = (cell, text)
        self.value = object()
        self.select(value)

    def select(self, value):
        self.value = value
        for v, (cell, text) in self.cells.items():
            on = v == value
            self.theme.paint(cell, bg="panel" if on else "input")
            self.theme.paint(text, bg="panel" if on else "input", fg="accent" if on else "muted")
            text.configure(font=FONTS["body-strong" if on else "body"])

    def _hover(self, v, inside):
        if v == self.value:
            return
        cell, text = self.cells[v]
        if inside:
            cell.configure(bg=self.theme["hover"])
            text.configure(bg=self.theme["hover"], fg=self.theme["text"])
        else:
            self.theme.repaint(cell)
            self.theme.repaint(text)


def checkbox(parent, theme, text, variable, command, image=None, bg="chrome"):
    return theme.paint(tk.Checkbutton(parent, text=_text(text, image), image=image or "", compound="left",
                                      variable=variable, command=command, font=FONTS["body"], borderwidth=0,
                                      highlightthickness=0, cursor="hand2"),
                       bg=bg, fg="text", activebackground=bg, activeforeground="text", selectcolor="panel",
                       disabledforeground="disabled")


def entry_box(parent, theme, width, bg="panel", image=None):
    """輸入框：填色跟周圍不同（放在 chrome 或 bg 上用 panel）、1px line 框，有焦點時外框換 accent。
    回傳 (外框, Entry)；外框裡可以再 pack 其他東西（例如清除按鈕）。"""
    frame = box(parent, theme, bg=bg)
    if image:
        theme.paint(tk.Label(frame, image=image, borderwidth=0, padx=0), bg=bg).pack(side=tk.LEFT,
                                                                                    padx=(theme.px(8), 0))
    entry = tk.Entry(frame, width=width, relief=tk.FLAT, borderwidth=0, highlightthickness=0, font=FONTS["body"])
    theme.paint(entry, bg=bg, fg="text", insertbackground="text", selectbackground="soft",
                selectforeground="text", disabledbackground="bg", disabledforeground="disabled")
    entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(theme.px(8), theme.px(4)), pady=theme.px(5))
    entry.bind("<FocusIn>", lambda e: theme.paint(frame, bg=bg, highlightbackground="accent",
                                                  highlightcolor="accent"), add="+")
    entry.bind("<FocusOut>", lambda e: theme.paint(frame, bg=bg, highlightbackground="line",
                                                   highlightcolor="line"), add="+")
    return frame, entry


def popup_menu(root, theme):
    """右鍵選單：panel 底，滑鼠移過 hover 底。每次開啟時建立，所以不用登記。"""
    return tk.Menu(root, tearoff=0, bg=theme["panel"], fg=theme["text"], activebackground=theme["hover"],
                   activeforeground=theme["text"], disabledforeground=theme["disabled"], borderwidth=0,
                   font=FONTS["body"])


class StatusBar(tk.Frame):
    """狀態列：chrome 底、上緣 1px line、caption 字。主要那句用 text（提醒 warn、錯誤 hi），後面的數字用 muted。
    要在主內容之前 pack(side=BOTTOM)，視窗縮小時才不會被擠掉。"""

    KINDS = {"info": "text", "ok": "ok", "warn": "warn", "error": "hi"}
    SEP = "｜"

    def __init__(self, parent, theme):
        super().__init__(parent)
        self.theme = theme
        theme.paint(self, bg="chrome")
        theme.paint(tk.Frame(self, height=1), bg="line").pack(side=tk.TOP, fill=tk.X)
        row = theme.paint(tk.Frame(self, padx=theme.px(12), pady=theme.px(5)), bg="chrome")
        row.pack(fill=tk.X)
        self.icon = theme.paint(tk.Label(row, borderwidth=0, padx=0), bg="chrome")
        self.main = theme.paint(tk.Label(row, anchor="w", borderwidth=0, padx=0, font=FONTS["caption"]),
                                bg="chrome", fg="text")
        self.detail = theme.paint(tk.Label(row, anchor="w", borderwidth=0, padx=0, font=FONTS["caption"]),
                                  bg="chrome", fg="muted")
        self.main.pack(side=tk.LEFT)
        self.detail.pack(side=tk.LEFT)
        self.parts = (self, row, self.icon, self.main, self.detail)

    def set(self, text, detail="", kind="info", image=None):
        if image:
            self.icon.configure(image=image)
            self.icon.pack(side=tk.LEFT, padx=(0, self.theme.px(6)), before=self.main)
        else:
            self.icon.pack_forget()
        self.theme.paint(self.main, bg="chrome", fg=self.KINDS.get(kind, "text"))
        self.main.configure(text=text)
        self.detail.configure(text=f"{self.SEP}{detail}" if detail and text else detail)

    def text(self):
        return self.main.cget("text") + self.detail.cget("text")

    def bind_click(self, callback):
        for w in self.parts:
            w.bind("<Button-1>", lambda e: callback())


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long),
                ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _RECT), ("rcWork", _RECT), ("dwFlags", wintypes.DWORD)]


def work_area(x, y):
    """(x, y) 所在螢幕扣掉工作列的範圍 (左, 上, 右, 下)；多螢幕也對。"""
    try:
        user32 = ctypes.windll.user32
        user32.MonitorFromPoint.argtypes = [wintypes.POINT, wintypes.DWORD]
        user32.MonitorFromPoint.restype = wintypes.HANDLE
        monitor = user32.MonitorFromPoint(wintypes.POINT(x, y), 2)  # MONITOR_DEFAULTTONEAREST
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(info)
        if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    except (AttributeError, OSError):
        pass
    return None


class Tooltip:
    """滑鼠提示：text 底、bg 字（跟介面相反）、沒有外框；停住約 0.45 秒後出現在游標右下方 12px，
    靠近螢幕右邊或下邊時改到左邊或上面。最寬 420px，超過就換行。"""

    DELAY_MS = 450

    def __init__(self, root, theme):
        self.root = root
        self.theme = theme
        self.win = None
        self.after_id = None
        self.key = None

    def schedule(self, key, text, x, y, font="caption"):
        """key 相同時不重來（滑鼠在同一列上移動）。"""
        if key == self.key:
            return
        self.hide()
        self.key = key
        self.after_id = self.root.after(self.DELAY_MS, lambda: self._show(text, x, y, font))

    def attach(self, widget, text):
        """固定說明的提示：滑鼠移入排程、移出或按下就收起來。"""
        widget.bind("<Enter>", lambda e: self.schedule(("attach", str(widget)), text, e.x_root, e.y_root), add="+")
        widget.bind("<Leave>", lambda e: self.hide(), add="+")
        widget.bind("<ButtonPress>", lambda e: self.hide(), add="+")

    def _show(self, text, x, y, font):
        self.after_id = None
        t = self.theme
        self.win = tk.Toplevel(self.root)
        self.win.wm_overrideredirect(True)
        label = tk.Label(self.win, text=text, bg=t["text"], fg=t["bg"], font=FONTS[font], justify=tk.LEFT,
                         padx=t.px(8), pady=t.px(4), borderwidth=0, wraplength=t.px(420))
        label.pack()
        self.win.update_idletasks()
        w, h = self.win.winfo_reqwidth(), self.win.winfo_reqheight()
        gap = t.px(12)
        left, top, right, bottom = work_area(x, y) or (0, 0, self.root.winfo_screenwidth(),
                                                       self.root.winfo_screenheight())
        nx = x + gap if x + gap + w <= right else max(left, x - gap - w)
        ny = y + gap if y + gap + h <= bottom else max(top, y - gap - h)
        self.win.wm_geometry(f"+{nx}+{ny}")

    def hide(self, *args):
        if self.after_id:
            self.root.after_cancel(self.after_id)
            self.after_id = None
        if self.win:
            self.win.destroy()
            self.win = None
        self.key = None
