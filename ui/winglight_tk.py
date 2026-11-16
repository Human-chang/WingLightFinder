# -*- coding: utf-8 -*-
"""WingLight 設計規範：純 Tkinter 用的色票、深淺色主題、線條圖示與元件（只用 Python 標準庫）。

這個檔在 WingLightFinder 實際使用（ui/winglight_tk.py）；設計規範 kit/winglight_tk.py 是同一份，改了要兩邊一起改。
複製到新專案後：

    ctypes.windll.shcore.SetProcessDpiAwareness(1)   # 在建立 Tk() 之前
    root = tk.Tk()
    theme = Theme(root, 設定檔裡的主題 or system_theme())
    icons = Icons(theme, "assets/icons")              # make_icons.py 轉好的遮罩 PNG
    bar = theme.paint(tk.Frame(root), bg="chrome")
    tool_button(bar, theme, "重新掃描", rescan, icons.get("refresh")).pack(side=tk.LEFT)
    theme.set("dark")   # 即時切換：登記過的元件、ttk 樣式、圖示、標題列一起換

- 色票名稱與 tokens.json 相同，每個值是 (淺色, 深色)。theme["accent"] 取目前主題的顏色。
- Tk 沒有樣式表：元件建立時用 theme.paint(元件, 選項=色票名) 登記，切換主題時逐一重設；
  ttk.Treeview 的 tag 這類登記不了的顏色，用 theme.on_change(函式) 在切換後自己重設。
- 圖示：icons.get("folder", "accent") 回傳塗好色的 PhotoImage。切換主題時就地重塗同一張圖，
  用到它的元件不必重設。執行時不需要 Pillow：遮罩 PNG 用 zlib 解開，塗色後再組成 PNG 交給 Tk。
- tk.Frame 做不出圓角：卡片、清單外框一律直角加 1px line 框（box），不要用圖片硬拼。
- 不要把 emoji 寫進 Tk 文字：啟動會慢好幾秒，帶 U+FE0F 的會變方框。小圖示用 PNG。
"""

import base64
import ctypes
import math
import os
import struct
import tkinter as tk
import tkinter.font as tkfont
import zlib
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


def animations_enabled():
    """Windows「顯示動畫效果」關掉時回傳 False（SPI_GETCLIENTAREAANIMATION）：處理中的圓點就不閃，停在最亮。"""
    try:
        value = wintypes.BOOL(True)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
            return bool(value.value)
    except (AttributeError, OSError):
        pass
    return True


ICON_SIZES = (16, 20, 24, 32)


def icon_size(scale, px=16):
    """要顯示 px 大小的圖示時用哪一張：不小於 px × 縮放倍率的最小那張（100% 用 16、125% 用 20、150% 用 24）。"""
    want = px * scale
    return next((s for s in ICON_SIZES if s >= want - 0.5), ICON_SIZES[-1])


class Theme:
    """目前的主題、登記過的元件與 ttk 樣式。"""

    def __init__(self, root, name="light", scale=None, icon_px=None):
        """icon_px：清單裡實際載入的圖示邊長（像素），列高至少要放得下；None 代表依縮放挑的那張（icon_size）。"""
        self.root = root
        self.name = name if name in THEMES else "light"
        self.scale = scale or max(1.0, root.winfo_fpixels("1i") / 96)
        self.icon_px = icon_px or icon_size(self.scale)
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
# 線條圖示：make_icons.py 的遮罩 PNG，執行時塗色（只用 zlib，不需要 Pillow）
# ==========================================
class Mask:
    """灰階遮罩：rows 的每一列是 bytes，值＝不透明度（0～255）。"""

    __slots__ = ("width", "height", "rows")

    def __init__(self, width, height, rows):
        self.width, self.height, self.rows = width, height, rows


def read_mask(path):
    """讀 make_icons.py 輸出的 8 位元灰階 PNG（<名稱>_<尺寸>.png）。"""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"不是 PNG：{path}")
    pos, idat, header = 8, [], None
    while pos + 8 <= len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat.append(body)
        elif kind == b"IEND":
            break
        pos += 12 + length
    if header is None or header[2:5] != (8, 0, 0) or header[6]:
        raise ValueError(f"只支援 8 位元灰階、不交錯的 PNG：{path}")
    width, height = header[:2]
    raw = zlib.decompress(b"".join(idat))
    rows, prev = [], bytearray(width)
    for y in range(height):
        start = y * (width + 1)
        kind, line = raw[start], bytearray(raw[start + 1:start + 1 + width])
        # PNG 的五種逐列濾波（每個像素 1 位元組）
        for i in range(width) if kind else ():
            left = line[i - 1] if i else 0
            if kind == 1:
                line[i] = (line[i] + left) & 255
            elif kind == 2:
                line[i] = (line[i] + prev[i]) & 255
            elif kind == 3:
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 255
            elif kind == 4:
                up_left = prev[i - 1] if i else 0
                p = left + prev[i] - up_left
                pa, pb, pc = abs(p - left), abs(p - prev[i]), abs(p - up_left)
                line[i] = (line[i] + (left if pa <= pb and pa <= pc else prev[i] if pb <= pc else up_left)) & 255
            else:
                raise ValueError(f"不認得的 PNG 濾波 {kind}：{path}")
        rows.append(bytes(line))
        prev = line
    return Mask(width, height, rows)


def rotate_mask(mask, quarter_turns):
    """逆時針轉 90° × quarter_turns。規範只有 chevron-down，朝右、朝上用轉的。"""
    for _ in range(quarter_turns % 4):
        w, h, rows = mask.width, mask.height, mask.rows
        mask = Mask(h, w, [bytes(rows[x][w - 1 - y] for x in range(h)) for y in range(w)])
    return mask


def shape_mask(width, height, inside, samples=4):
    """畫一個形狀的遮罩：inside(x, y) 判斷點在不在形狀裡，每個像素取 samples × samples 點平均，邊緣才平滑。"""
    n, rows = samples * samples, []
    offsets = [(i + 0.5) / samples for i in range(samples)]
    for y in range(height):
        row = bytearray(width)
        for x in range(width):
            hit = sum(1 for dy in offsets for dx in offsets if inside(x + dx, y + dy))
            row[x] = (255 * hit + n // 2) // n
        rows.append(bytes(row))
    return Mask(width, height, rows)


def round_rect(x0, y0, x1, y1, r):
    """圓角矩形的 inside 判斷，給 shape_mask 用。"""
    def inside(x, y):
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        cx, cy = min(max(x, x0 + r), x1 - r), min(max(y, y0 + r), y1 - r)
        return (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return inside


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def _png_rgba(width, height, pixels):
    def chunk(kind, body):
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body) & 0xFFFFFFFF)
    stride = width * 4
    raw = b"".join(b"\0" + bytes(pixels[y * stride:(y + 1) * stride]) for y in range(height))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def render(width, height, layers):
    """把遮罩塗色後由下往上疊：layers 是 [(遮罩, "#RRGGBB", 不透明度, (x, y)), ...]。
    回傳 base64 的 PNG，給 tk.PhotoImage(data=…) 或 image.configure(data=…)。"""
    pixels = bytearray(width * height * 4)
    if len(layers) == 1 and layers[0][0].width == width and layers[0][3] == (0, 0):
        # 只有一層（一般的圖示）：整排一起塗，比逐點快很多
        mask, color, opacity, _ = layers[0]
        alpha = b"".join(mask.rows)
        if opacity < 1:
            alpha = bytes(round(a * opacity) for a in alpha)
        r, g, b = _rgb(color)
        count = width * height
        pixels[0::4], pixels[1::4], pixels[2::4], pixels[3::4] = bytes([r]) * count, bytes([g]) * count, \
            bytes([b]) * count, alpha
    else:
        for mask, color, opacity, (ox, oy) in layers:
            cr, cg, cb = _rgb(color)
            for y in range(mask.height):
                for x, a in enumerate(mask.rows[y]):
                    if not a:
                        continue
                    i = ((oy + y) * width + ox + x) * 4
                    top = a * opacity / 255
                    if top <= 0:
                        continue
                    below = pixels[i + 3] / 255
                    out = top + below * (1 - top)
                    keep = below * (1 - top) / out
                    pixels[i] = round(cr * top / out + pixels[i] * keep)
                    pixels[i + 1] = round(cg * top / out + pixels[i + 1] * keep)
                    pixels[i + 2] = round(cb * top / out + pixels[i + 2] * keep)
                    pixels[i + 3] = round(out * 255)
    return base64.b64encode(_png_rgba(width, height, pixels)).decode("ascii")


class Icons:
    """make_icons.py 轉好的遮罩（<名稱>_<尺寸>.png），依目前主題塗色。

    同一個（名稱, 色票, 旋轉）只建一張 PhotoImage；切換主題時就地重塗，用到它的元件、ttk 元素都不必重設。
    尺寸依螢幕縮放挑（icon_size）；找不到檔案時 get() 回傳 None，介面照樣能用，只是少了圖示。"""

    def __init__(self, theme, icon_dir, px=16):
        self.theme = theme
        self.root = theme.root
        self.dir = icon_dir
        self.size = icon_size(theme.scale, px)
        self._masks = {}
        self._images = {}   # key → (PhotoImage, 寬, 高, [(遮罩, 色票, 不透明度, 位置), ...])
        theme.on_change(self.recolor)

    def mask(self, name, rotate=0):
        key = (name, rotate % 4)
        if key not in self._masks:
            try:
                self._masks[key] = rotate_mask(read_mask(os.path.join(self.dir, f"{name}_{self.size}.png")), rotate)
            except (OSError, ValueError, zlib.error):
                self._masks[key] = None
        return self._masks[key]

    def get(self, name, color="muted", rotate=0):
        """name：規範 Icons 群組的檔名（不含 .svg）；color：色票名；rotate：逆時針轉幾個 90°。"""
        if not name:
            return None
        mask = self.mask(name, rotate)
        if mask is None:
            return None
        return self.compose(("icon", name, color, rotate % 4), mask.width, mask.height, [(mask, color, 1, (0, 0))])

    def compose(self, key, width, height, layers):
        """自己疊的圖（例如勾選框）：layers 是 [(遮罩, 色票, 不透明度, (x, y)), ...]，切換主題時一樣重塗。"""
        if key not in self._images:
            image = tk.PhotoImage(master=self.root, data=self._render(width, height, layers))
            self._images[key] = (image, width, height, layers)
        return self._images[key][0]

    def blank(self):
        """透明的空白圖，跟圖示一樣大。"""
        if "blank" not in self._images:
            self._images["blank"] = (tk.PhotoImage(master=self.root, width=self.size, height=self.size), 0, 0, [])
        return self._images["blank"][0]

    def checkbox(self, checked, gap=0):
        """規範 Input 的勾選框：圓角 4px；沒勾 panel 底、1px muted 框，勾了 accent 填色、on-accent 打勾。
        gap：右邊留給文字的間距（Tk 的圖文之間沒有空隙）。"""
        s, t = self.size, self.theme
        border, radius = max(1, t.px(1)), t.px(4)
        outer = self._shape(("box", s), s, s, round_rect(0, 0, s, s, radius))
        inner = self._shape(("box-inner", s, border), s - 2 * border, s - 2 * border,
                            round_rect(0, 0, s - 2 * border, s - 2 * border, max(0, radius - border)))
        if checked:
            layers = [(outer, "accent", 1, (0, 0))]
            tick = self.mask("check")
            if tick is not None:
                layers.append((tick, "on-accent", 1, (0, 0)))
        else:
            layers = [(outer, "muted", 1, (0, 0)), (inner, "panel", 1, (border, border))]
        return self.compose(("checkbox", checked, gap), s + gap, s, layers)

    def dot(self, opacity=1.0, diameter=8):
        """處理中的 glow 圓點（規範 StatusBar）：直徑 8px，放在圖示大小的方塊正中間。"""
        s, d = self.size, self.theme.px(diameter)
        c = s / 2
        mask = self._shape(("dot", s, d), s, s, lambda x, y: (x - c) ** 2 + (y - c) ** 2 <= (d / 2) ** 2)
        return self._render(s, s, [(mask, "glow", opacity, (0, 0))])

    def _shape(self, key, width, height, inside):
        if key not in self._masks:
            self._masks[key] = shape_mask(width, height, inside)
        return self._masks[key]

    def _render(self, width, height, layers):
        return render(width, height, [(m, self.theme[color], a, xy) for m, color, a, xy in layers])

    def recolor(self):
        for image, width, height, layers in self._images.values():
            if layers:
                image.configure(data=self._render(width, height, layers))


class TreeChevrons:
    """有階層的清單（資料夾樹）的展開箭頭用線條圖示：收合朝右（chevron-down 轉 90°）、展開朝下、沒有子項目時空白。

    Tk 8.6 的 ttk 元素讀不到項目「展開了沒」（只讀得到沒有子項目的 user2），所以箭頭畫進每一列的圖示裡：
    image 是「箭頭＋間距＋圖示」，上面疊一個透明的 indicator 元素，點箭頭照樣由 ttk 內建的綁定展開或收合。
    用法：插入項目時 image=chevrons.image("folder", "closed")；<<TreeviewOpen>>、<<TreeviewClose>> 之後
    依結果再設一次 "open"／"closed"／"leaf"（這兩個事件發生時 -open 還沒改）。用 style="Treeview" 的清單才有效。"""

    def __init__(self, theme, icons):
        self.icons = icons
        self.gap = theme.px(4)
        s = icons.size
        # 同一個 Tk 裡第二次建立時（例如測試）元素名稱不能重複
        name = f"Chevron{sum(1 for e in theme.style.element_names() if e.startswith('Chevron'))}.Treeitem.indicator"
        theme.style.element_create(name, "image", icons.blank(), sticky="")
        theme.style.layout("Treeview.Item", [
            ("Treeitem.padding", {"sticky": "nswe", "children": [
                ("Treeitem.image", {"side": "left", "sticky": "", "children": [
                    (name, {"side": "left", "sticky": ""}),
                ]}),
                ("Treeitem.focus", {"side": "left", "sticky": "", "children": [
                    ("Treeitem.text", {"side": "left", "sticky": ""}),
                ]}),
            ]}),
        ])
        # 子項目的箭頭對齊上一層的圖示
        theme.style.configure("Treeview", indent=s + self.gap)

    def image(self, icon, state):
        """state："closed"、"open"、"leaf"（沒有子項目，不畫箭頭）。"""
        icons, s = self.icons, self.icons.size
        layers = []
        if state != "leaf":
            chevron = icons.mask("chevron-down", 0 if state == "open" else 1)
            if chevron is not None:
                layers.append((chevron, "muted", 1, (0, 0)))
        mask = icons.mask(icon)
        if mask is not None:
            layers.append((mask, "muted", 1, (s + self.gap, 0)))
        return icons.compose(("tree", icon, state), 2 * s + self.gap, s, layers)


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


def button(parent, theme, text, command, image=None, kind="secondary", bg="panel"):
    """按鈕（規範 Button）：secondary 是 input 填色、1px line 框；primary 是 accent 填色、on-accent 粗體字，
    一個畫面最多一個。直接放在 bg 底上的次要按鈕改用 panel 填色（bg="bg"）。有鍵盤焦點時外框換 accent。"""
    primary = kind == "primary"
    fill = "accent" if primary else "panel" if bg == "bg" else "input"
    hover = "accent-hover" if primary else "hover"
    fg = "on-accent" if primary else "text"
    btn = tk.Button(parent, text=_text(text, image), image=image or "", compound="left", command=command,
                    relief=tk.FLAT, borderwidth=0, highlightthickness=1, cursor="hand2",
                    padx=theme.px(28 if primary else 12), pady=theme.px(5),
                    font=FONTS["body-strong" if primary else "body"])
    theme.paint(btn, bg=fill, fg=fg, activebackground=hover, activeforeground=fg, disabledforeground="disabled",
                highlightbackground="accent" if primary else "line", highlightcolor="accent")
    btn.bind("<Enter>", lambda e: str(btn["state"]) != "disabled" and btn.configure(bg=theme[hover]))
    btn.bind("<Leave>", lambda e: theme.repaint(btn))
    return btn


def checkbox(parent, theme, text, variable, command, icons=None, bg="chrome"):
    """勾選框（規範 Input）：有 icons 時用畫好的方框（圓角、muted 框，勾了 accent 填色），文字在右邊、間距 8px；
    沒有 icons 時退回 Tk 內建的樣子。點文字也能切換。"""
    w = tk.Checkbutton(parent, text=text, variable=variable, command=command, font=FONTS["body"], borderwidth=0,
                       highlightthickness=0, cursor="hand2", compound="left")
    if icons is not None:
        gap = theme.px(8)
        w.configure(image=icons.checkbox(False, gap), selectimage=icons.checkbox(True, gap), indicatoron=False,
                    relief=tk.FLAT, offrelief=tk.FLAT, overrelief=tk.FLAT, padx=0, pady=theme.px(2))
    return theme.paint(w, bg=bg, fg="text", activebackground=bg, activeforeground="text",
                       selectcolor=bg if icons is not None else "panel", disabledforeground="disabled")


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


def dialog(parent, theme, title):
    """對話框：panel 底、Esc 關閉。先藏起來，放好內容後呼叫 show_modal()。"""
    win = tk.Toplevel(parent)
    win.withdraw()
    win.title(title)
    win.resizable(False, False)
    theme.paint(win, bg="panel")
    win.bind("<Escape>", lambda e: win.destroy())
    return win


def show_modal(win, parent, theme):
    """放在主視窗中間偏上、標題列跟著深淺色，關掉之前主視窗不能操作。"""
    top = parent.winfo_toplevel()
    win.transient(top)
    win.update_idletasks()
    x = top.winfo_rootx() + (top.winfo_width() - win.winfo_reqwidth()) // 2
    y = top.winfo_rooty() + (top.winfo_height() - win.winfo_reqheight()) // 3
    win.geometry(f"+{max(0, x)}+{max(0, y)}")
    win.bind("<Map>", lambda e: e.widget is win and set_title_bar_dark(win, theme.dark), add="+")
    win.deiconify()
    win.grab_set()
    win.focus_set()


def popup_menu(root, theme):
    """右鍵選單：panel 底，滑鼠移過 hover 底。每次開啟時建立，所以不用登記。"""
    return tk.Menu(root, tearoff=0, bg=theme["panel"], fg=theme["text"], activebackground=theme["hover"],
                   activeforeground=theme["text"], disabledforeground=theme["disabled"], borderwidth=0,
                   font=FONTS["body"])


class StatusBar(tk.Frame):
    """狀態列：chrome 底、上緣 1px line、caption 字。主要那句用 text（提醒 warn、錯誤 hi），後面的數字用 muted。
    最前面是狀態圖示（一定同時有圖示和字）：
      busy  處理中：glow 圓點一明一暗，2.2 秒一個週期（Windows 關掉動畫效果時停在最亮）
      ok    完成：ok 的打勾        warn  提醒：warn 的警告        error  錯誤：hi 的警告
      info  一般說明：icon 指定的線條圖示（muted），可以省略
    要在主內容之前 pack(side=BOTTOM)，視窗縮小時才不會被擠掉。"""

    TEXT = {"warn": "warn", "error": "hi"}
    ICONS = {"ok": ("check", "ok"), "warn": ("warning", "warn"), "error": ("warning", "hi")}
    SEP = "｜"
    PULSE_MS = 2200
    PULSE_FRAMES = 22

    def __init__(self, parent, theme, icons=None):
        super().__init__(parent)
        self.theme = theme
        self.icons = icons
        self.kind = None
        self._pulse_after = None
        self._dot = None
        self._dot_frames = []
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

    def set(self, text, detail="", kind="info", icon=None):
        self.kind = kind
        self._stop_pulse()
        image = None
        if self.icons is not None:
            if kind == "busy":
                image = self._start_pulse()
            elif kind in self.ICONS:
                image = self.icons.get(*self.ICONS[kind])
            else:
                image = self.icons.get(icon)
        if image:
            self.icon.configure(image=image)
            self.icon.pack(side=tk.LEFT, padx=(0, self.theme.px(6)), before=self.main)
        else:
            self.icon.pack_forget()
        self.theme.paint(self.main, bg="chrome", fg=self.TEXT.get(kind, "text"))
        self.main.configure(text=text)
        self.detail.configure(text=f"{self.SEP}{detail}" if detail and text else detail)

    def text(self):
        return self.main.cget("text") + self.detail.cget("text")

    def _start_pulse(self):
        if not self._dot_frames:
            # 透明度 0.35～1 的餘弦曲線；圓點的顏色深淺色主題相同，所以只算一次
            self._dot_frames = [self.icons.dot(0.35 + 0.65 * (0.5 + 0.5 * math.cos(2 * math.pi * i / self.PULSE_FRAMES)))
                                for i in range(self.PULSE_FRAMES)]
            self._dot = tk.PhotoImage(master=self, data=self._dot_frames[0])
        if animations_enabled():
            self._pulse(0)
        else:
            self._dot.configure(data=self._dot_frames[0])
        return self._dot

    def _pulse(self, frame):
        self._dot.configure(data=self._dot_frames[frame])
        self._pulse_after = self.after(self.PULSE_MS // self.PULSE_FRAMES,
                                       lambda: self._pulse((frame + 1) % self.PULSE_FRAMES))

    def _stop_pulse(self):
        if self._pulse_after:
            self.after_cancel(self._pulse_after)
            self._pulse_after = None

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
