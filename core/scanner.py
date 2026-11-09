# -*- coding: utf-8 -*-
"""資料夾掃描與檔案分類：不碰介面，方便測試，也可以在背景執行緒裡跑。"""

import os
import time
import pathlib
from dataclasses import dataclass, field

# 不往下掃的系統資料夾（另外以 $ 或 . 開頭的也一律略過）
SKIP_DIR_NAMES = {
    "windows", "program files", "program files (x86)", "programdata",
    "system volume information", "node_modules", "__pycache__",
}
# Office 開啟中產生的鎖定檔（~$報告.docx）與系統雜檔
SKIP_FILE_PREFIXES = ("~$",)
SKIP_FILE_NAMES = {"thumbs.db", "desktop.ini"}
# 不放進「其他」欄的副檔名：CAD 鎖定檔、暫存檔、Shapefile 附屬檔、影像的 world file
IGNORED_EXTS = {
    ".dwl", ".dwl2", ".tmp",
    ".shx", ".sbn", ".sbx", ".cpg", ".qix", ".prj",
    ".pgw", ".jgw", ".tfw", ".wld",
}


@dataclass(frozen=True)
class FileEntry:
    name: str
    path: str
    rel_dir: str  # 相對於掃描起點的子資料夾，"" 代表就在起點
    ext: str
    mtime: float
    size: int

    @property
    def rel_path(self) -> str:
        return os.path.join(self.rel_dir, self.name) if self.rel_dir else self.name


@dataclass
class ScanResult:
    root: str
    depth: int
    files: list = field(default_factory=list)
    unreadable: list = field(default_factory=list)  # 讀不到的資料夾（斷線、權限、路徑過長）
    elapsed: float = 0.0
    cancelled: bool = False


def scan_depth_for(path: str) -> int:
    """磁碟根目錄不往下掃，第二層掃 2 層，更深的掃 3 層。"""
    parts = len(pathlib.Path(path).parts)
    if parts <= 1:
        return 0
    if parts == 2:
        return 2
    return 3


def _skip_dir(name: str) -> bool:
    return name.startswith(("$", ".")) or name.lower() in SKIP_DIR_NAMES


def _skip_file(name: str) -> bool:
    return name.startswith(SKIP_FILE_PREFIXES) or name.lower() in SKIP_FILE_NAMES


def scan_folder(root: str, depth: int, cancelled=lambda: False) -> ScanResult:
    result = ScanResult(root=root, depth=depth)
    start = time.perf_counter()

    def walk(path, rel, level):
        if cancelled():
            result.cancelled = True
            return
        try:
            with os.scandir(path) as it:
                entries = list(it)
        except OSError:
            result.unreadable.append(path)
            return
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if level < depth and not _skip_dir(entry.name):
                        walk(entry.path, os.path.join(rel, entry.name) if rel else entry.name, level + 1)
                elif entry.is_file() and not _skip_file(entry.name):
                    st = entry.stat()
                    result.files.append(FileEntry(
                        name=entry.name,
                        path=entry.path,
                        rel_dir=rel,
                        ext=os.path.splitext(entry.name)[1].lower(),
                        mtime=st.st_mtime,
                        size=st.st_size,
                    ))
            except OSError:
                continue

    walk(root, "", 0)
    result.elapsed = time.perf_counter() - start
    return result


def classify(files, groups, active_names, query="", since=None):
    """依分類分欄，每欄由新到舊。

    groups: [{"name", "exts", ...}, ...]，同一個副檔名以排在前面的分類為準。
    已知副檔名但分類沒勾選的檔案不會跑到「其他」。
    query: 以空白分隔的關鍵字，全部都要出現在「子資料夾\\檔名」裡（不分大小寫）。
    since: 只保留修改時間晚於這個時間戳的檔案。
    回傳 (by_group, others)，by_group 的順序跟 active_names 一致。
    """
    ext_to_group = {}
    for g in groups:
        for ext in g["exts"]:
            ext_to_group.setdefault(ext, g["name"])

    terms = query.lower().split()
    by_group = {name: [] for name in active_names}
    others = []
    for f in sorted(files, key=lambda f: f.mtime, reverse=True):
        if since is not None and f.mtime < since:
            continue
        if terms:
            haystack = f.rel_path.lower()
            if not all(t in haystack for t in terms):
                continue
        group = ext_to_group.get(f.ext)
        if group is None:
            if f.ext not in IGNORED_EXTS:
                others.append(f)
        elif group in by_group:
            by_group[group].append(f)
    return by_group, others
