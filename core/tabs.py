# -*- coding: utf-8 -*-
"""分頁：每個分頁各自記住資料夾、搜尋字、時間篩選與展開的欄；掃描結果由所有分頁共用。"""

import os
from dataclasses import dataclass, field


@dataclass
class Tab:
    folder: str = ""
    query: str = ""
    time_filter: object = None
    expanded: set = field(default_factory=set)

    @property
    def title(self) -> str:
        if not self.folder:
            return "新分頁"
        return os.path.basename(self.folder.rstrip("\\/")) or self.folder


class TabList:
    def __init__(self, folders=(), active=0):
        self.tabs = [Tab(folder=f) for f in folders if isinstance(f, str)] or [Tab()]
        self.active = min(max(int(active or 0), 0), len(self.tabs) - 1)

    @property
    def current(self) -> Tab:
        return self.tabs[self.active]

    def add(self, folder="") -> int:
        """在目前分頁右邊新增一個分頁，回傳它的位置（不會自動切換過去）。"""
        index = self.active + 1
        self.tabs.insert(index, Tab(folder=folder))
        return index

    def close(self, index):
        """關掉分頁；全部關掉時留一個空白分頁。"""
        if not 0 <= index < len(self.tabs):
            return
        del self.tabs[index]
        if not self.tabs:
            self.tabs = [Tab()]
            self.active = 0
        elif index < self.active or self.active >= len(self.tabs):
            self.active -= 1

    def step(self, delta):
        self.active = (self.active + delta) % len(self.tabs)

    def folders(self):
        return {t.folder for t in self.tabs if t.folder}

    def saved_folders(self):
        """存進設定檔的分頁清單（空白分頁不存）。"""
        return [t.folder for t in self.tabs if t.folder]

    def saved_active(self):
        before = self.tabs[:self.active]
        return sum(1 for t in before if t.folder)
