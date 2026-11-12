# -*- coding: utf-8 -*-
"""分頁：每個分頁各自記住資料夾、搜尋字、時間篩選與展開的欄；掃描結果由所有分頁共用。

釘選的分頁固定排在最左邊，每次開啟程式都會打開，要先取消釘選才能關閉。
"""

import os
from dataclasses import dataclass, field


@dataclass(eq=False)  # 用物件本身比對：兩個分頁開同一個資料夾也是不同分頁
class Tab:
    folder: str = ""
    query: str = ""
    time_filter: object = None
    expanded: set = field(default_factory=set)
    pinned: bool = False

    @property
    def title(self) -> str:
        if not self.folder:
            return "新分頁"
        return os.path.basename(self.folder.rstrip("\\/")) or self.folder


class TabList:
    def __init__(self, folders=(), active=0, pinned=()):
        tabs = [Tab(folder=f, pinned=True) for f in pinned if isinstance(f, str) and f]
        tabs += [Tab(folder=f) for f in folders if isinstance(f, str)]
        self.tabs = tabs or [Tab()]
        self.active = min(max(int(active or 0), 0), len(self.tabs) - 1)

    @property
    def current(self) -> Tab:
        return self.tabs[self.active]

    @property
    def pinned_count(self) -> int:
        return sum(1 for t in self.tabs if t.pinned)

    def _index_of(self, tab) -> int:
        return next(i for i, t in enumerate(self.tabs) if t is tab)

    def add(self, folder="") -> int:
        """在目前分頁右邊新增一個分頁（不會插進釘選區），回傳它的位置（不會自動切換過去）。"""
        index = max(self.active + 1, self.pinned_count)
        self.tabs.insert(index, Tab(folder=folder))
        return index

    def close(self, index) -> bool:
        """關掉分頁；釘選的分頁不能關。全部關掉時留一個空白分頁。"""
        if not 0 <= index < len(self.tabs) or self.tabs[index].pinned:
            return False
        del self.tabs[index]
        if not self.tabs:
            self.tabs = [Tab()]
            self.active = 0
        elif index < self.active or self.active >= len(self.tabs):
            self.active -= 1
        return True

    def drop_position(self, src, dst) -> int:
        """拖曳時能放的位置：釘選的分頁只能在釘選區裡移動，一般分頁只能在釘選區右邊。

        dst 是插入點（0 代表最左邊，len 代表最右邊），以移動前的位置計算。
        """
        n = self.pinned_count
        lo, hi = (0, n) if self.tabs[src].pinned else (n, len(self.tabs))
        return min(max(dst, lo), hi)

    def move(self, src, dst) -> int:
        """把 src 的分頁移到插入點 dst，回傳它的新位置；目前分頁不變。"""
        dst = self.drop_position(src, dst)
        current = self.current
        tab = self.tabs.pop(src)
        if dst > src:
            dst -= 1
        self.tabs.insert(dst, tab)
        self.active = self._index_of(current)
        return dst

    def set_pinned(self, index, pinned) -> bool:
        """釘選的分頁移到釘選區最右邊；取消釘選的移到釘選區右邊第一個。空白分頁不能釘選。"""
        tab = self.tabs[index]
        if tab.pinned == pinned or (pinned and not tab.folder):
            return False
        current = self.current
        del self.tabs[index]
        tab.pinned = pinned
        self.tabs.insert(self.pinned_count, tab)
        self.active = self._index_of(current)
        return True

    def step(self, delta):
        self.active = (self.active + delta) % len(self.tabs)

    def folders(self):
        return {t.folder for t in self.tabs if t.folder}

    def saved_pinned(self):
        return [t.folder for t in self.tabs if t.pinned]

    def saved_folders(self):
        """存進設定檔的一般分頁（釘選的另外存，空白分頁不存）。"""
        return [t.folder for t in self.tabs if t.folder and not t.pinned]

    def saved_active(self):
        """目前分頁在「釘選＋一般分頁」清單裡的位置。"""
        before = self.tabs[:self.active]
        return sum(1 for t in before if t.folder)
