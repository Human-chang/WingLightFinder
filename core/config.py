# -*- coding: utf-8 -*-
"""設定檔：存在程式旁邊，不管從哪裡啟動都讀得到；那裡不能寫入時改存到 %APPDATA%。"""

import os
import sys
import json

APP_NAME = "WingLightFinder"
CONFIG_NAME = "WingLightFinder.json"
LEGACY_CONFIG_NAME = "explorer_config.json"  # 舊版「專案檔案速查儀表板」的設定檔
THEMES = ("light", "dark")

DEFAULT_GROUPS = [
    {"name": "PDF 報告", "icon": "file", "exts": [".pdf"], "default_on": True},
    {"name": "CAD 圖面", "icon": "ruler", "exts": [".dwg", ".dxf", ".dgn", ".dwf"], "default_on": True},
    {"name": "簡報", "icon": "slides", "exts": [".ppt", ".pptx", ".odp"], "default_on": True},
    {"name": "試算表", "icon": "table", "exts": [".xlsx", ".xls", ".xlsm", ".csv", ".ods"], "default_on": True},
    {"name": "文件", "icon": "file-text", "exts": [".docx", ".doc", ".odt", ".rtf"], "default_on": False},
    {"name": "圖片", "icon": "image", "exts": [".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".heic"], "default_on": False},
    {"name": "GIS 圖資", "icon": "pin", "exts": [".shp", ".kml", ".kmz", ".geojson", ".gpkg", ".las", ".laz"], "default_on": False},
    {"name": "影片", "icon": "video", "exts": [".mp4", ".mov", ".mts", ".avi"], "default_on": False},
    {"name": "壓縮檔", "icon": "package", "exts": [".zip", ".7z", ".rar"], "default_on": False},
]


def app_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def assets_dir() -> str:
    """圖示與標誌：打包後在 _internal/assets（sys._MEIPASS），從原始碼執行時在專案的 assets。"""
    return os.path.join(getattr(sys, "_MEIPASS", app_dir()), "assets")


def _appdata_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME, CONFIG_NAME)


class Config:
    def __init__(self, base_dir=None):
        self.base_dir = base_dir or app_dir()
        self.path = os.path.join(self.base_dir, CONFIG_NAME)
        if not os.path.exists(self.path) and os.path.exists(_appdata_path()):
            self.path = _appdata_path()
        self.favorites = []
        self.custom_groups = []   # 使用者用「+ 加入其他」新增的分類
        self.group_states = {}    # 分類名稱 → 是否勾選
        self.geometry = ""
        self.tabs = []            # 上次開著的一般分頁（資料夾路徑）
        self.pinned_tabs = []     # 釘選的分頁：每次開啟都會打開
        self.active_tab = 0       # 在「釘選＋一般分頁」裡的位置
        self.remember_session = True  # 關掉時，下次開啟只打開釘選的分頁，也不保留加入的副檔名
        self.theme = None             # "light"／"dark"；None 代表還沒選過，跟著 Windows
        self.load()

    def load(self):
        data = self._read(self.path)
        if data is None:
            # 第一次啟動：沿用舊版放在同一個資料夾的「我的最愛」
            legacy = self._read(os.path.join(self.base_dir, LEGACY_CONFIG_NAME)) or {}
            self.favorites = [p for p in legacy.get("favorites", []) if isinstance(p, str)]
            return
        self.favorites = [p for p in data.get("favorites", []) if isinstance(p, str)]
        self.custom_groups = [g for g in data.get("custom_groups", []) if isinstance(g, dict) and g.get("exts")]
        self.group_states = {k: bool(v) for k, v in data.get("group_states", {}).items()}
        self.geometry = data.get("geometry", "") or ""
        self.tabs = [p for p in data.get("tabs", []) if isinstance(p, str)]
        self.pinned_tabs = [p for p in data.get("pinned_tabs", []) if isinstance(p, str) and p]
        active = data.get("active_tab", 0)
        self.active_tab = active if isinstance(active, int) else 0
        self.remember_session = data.get("remember_session", True) is not False
        self.theme = data.get("theme") if data.get("theme") in THEMES else None

    @staticmethod
    def _read(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def save(self):
        remember = self.remember_session
        data = {
            "favorites": self.favorites,
            "custom_groups": self.custom_groups if remember else [],
            "group_states": self.group_states,
            "geometry": self.geometry,
            "pinned_tabs": self.pinned_tabs,
            "tabs": self.tabs if remember else [],
            "active_tab": self.active_tab if remember else 0,
            "remember_session": remember,
        }
        if self.theme in THEMES:
            data["theme"] = self.theme
        try:
            self._write(self.path, data)
        except OSError:
            # 程式放在不能寫入的位置（例如 Program Files）時，改存到使用者的 AppData
            self.path = _appdata_path()
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            self._write(self.path, data)

    @staticmethod
    def _write(path, data):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)

    def groups(self):
        """預設分類加上自訂分類，並套用記住的勾選狀態。"""
        result = []
        # 自訂分類一律用資料夾圖示（舊版設定檔存的是 emoji，不再使用）
        for g in DEFAULT_GROUPS + [dict(c, icon="folder") for c in self.custom_groups]:
            g = dict(g)
            g["on"] = self.group_states.get(g["name"], g.get("default_on", True))
            result.append(g)
        return result

    def add_extension(self, ext):
        """新增自訂副檔名；已經在某個分類裡就回傳 None。"""
        ext = ext.strip().lower()
        if not ext:
            return None
        if not ext.startswith("."):
            ext = "." + ext
        if any(ext in g["exts"] for g in DEFAULT_GROUPS + self.custom_groups):
            return None
        group = {"name": f"{ext.upper()} 檔", "icon": "folder", "exts": [ext], "default_on": True}
        self.custom_groups.append(group)
        self.group_states[group["name"]] = True
        return group
