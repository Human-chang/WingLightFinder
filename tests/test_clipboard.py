# -*- coding: utf-8 -*-
import os
import sys
import struct
import tempfile
import unittest
import tkinter as tk
import ctypes
from ctypes import wintypes

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import clipboard  # noqa: E402
from core.config import Config  # noqa: E402
from core.demo import make_demo_project  # noqa: E402


class DropFilesTest(unittest.TestCase):
    def test_header_and_path(self):
        path = os.path.join("X:\\計畫案", "第1次審查會議紀錄.pdf")
        data = clipboard.dropfiles_bytes(path)
        self.assertEqual(struct.unpack("<IIIII", data[:20]), (20, 0, 0, 0, 1))
        self.assertEqual(data[20:], os.path.abspath(path).encode("utf-16-le") + b"\x00\x00\x00\x00")
        self.assertEqual(data[20:-4].decode("utf-16-le"), path)

    def test_relative_path_becomes_absolute(self):
        data = clipboard.dropfiles_bytes("報告.pdf")
        self.assertEqual(data[20:-4].decode("utf-16-le"), os.path.abspath("報告.pdf"))


def _read_clipboard_files():
    user32 = ctypes.WinDLL("user32")
    shell32 = ctypes.WinDLL("shell32")
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
    shell32.DragQueryFileW.restype = wintypes.UINT
    if not user32.OpenClipboard(None):
        raise OSError("剪貼簿打不開")
    try:
        handle = user32.GetClipboardData(clipboard.CF_HDROP)
        if not handle:
            return []
        files = []
        for i in range(shell32.DragQueryFileW(handle, 0xFFFFFFFF, None, 0)):
            buf = ctypes.create_unicode_buffer(shell32.DragQueryFileW(handle, i, None, 0) + 1)
            shell32.DragQueryFileW(handle, i, buf, len(buf))
            files.append(buf.value)
        return files
    finally:
        user32.CloseClipboard()


@unittest.skipUnless(os.environ.get("WINGLIGHT_CLIPBOARD_TEST"),
                     "會蓋掉目前剪貼簿的內容；要測時設 WINGLIGHT_CLIPBOARD_TEST=1")
class RealClipboardTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "第1次審查會議紀錄.pdf")
            open(path, "wb").close()
            clipboard.copy_file_to_clipboard(path)
            self.assertEqual(_read_clipboard_files(), [path])


class CopyFileUiTest(unittest.TestCase):
    """右鍵與 Ctrl+C 都走 FinderApp.copy_file；不碰真的剪貼簿。"""

    def setUp(self):
        from ui import app as app_module
        self.app_module = app_module
        self.copied = []
        self._orig = app_module.copy_file_to_clipboard
        app_module.copy_file_to_clipboard = self.copied.append

        self.tmp = tempfile.TemporaryDirectory()
        self.project = make_demo_project(self.tmp.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.app = app_module.FinderApp(self.root, Config(base_dir=self.tmp.name))
        self.app.open_folder(self.project)
        for _ in range(400):
            self.root.update()
            if self.project in self.app.cache and self.app.item_files:
                break
            self.root.after(25)

    def tearDown(self):
        self.app_module.copy_file_to_clipboard = self._orig
        self.root.destroy()
        self.tmp.cleanup()

    def _file_tree(self):
        for _, _, tree, _ in self.app.columns.values():
            for iid in tree.get_children():
                if iid in self.app.item_files:
                    return tree, iid
        self.fail("畫面上沒有檔案")

    def test_ctrl_c_on_selected_file(self):
        tree, iid = self._file_tree()
        tree.selection_set(iid)
        result = self.app._on_file_copy(type("E", (), {"widget": tree})())
        f = self.app.item_files[iid]
        self.assertEqual(result, "break")
        self.assertEqual(self.copied, [f.path])
        self.assertEqual(self.app.status.cget("text").strip(), f"已複製檔案：{f.name}")

    def test_ctrl_c_on_year_row_does_nothing(self):
        tree, _ = self._file_tree()
        year = next(i for i in tree.get_children() if i not in self.app.item_files)
        tree.selection_set(year)
        self.app._on_file_copy(type("E", (), {"widget": tree})())
        self.assertEqual(self.copied, [])

    def test_ctrl_c_bound_only_on_file_lists(self):
        for _, _, tree, _ in self.app.columns.values():
            self.assertIn("_on_file_copy", tree.bind("<Control-c>"))
            self.assertIn("_on_file_copy", tree.bind("<Control-C>"))
        # 搜尋框、視窗、全域都不攔 Ctrl+C，搜尋框照常複製文字
        self.assertEqual(self.app.search_entry.bind("<Control-c>"), "")
        self.assertEqual(self.root.bind("<Control-c>"), "")
        self.assertEqual(self.root.bind_all("<Control-c>"), "")

    def test_missing_file_is_not_copied(self):
        from tkinter import messagebox
        shown = []
        orig = messagebox.showwarning
        messagebox.showwarning = lambda *a, **k: shown.append(a)
        try:
            self.app.copy_file(os.path.join(self.project, "已經刪掉的檔案.pdf"))
        finally:
            messagebox.showwarning = orig
        self.assertEqual(self.copied, [])
        self.assertEqual(len(shown), 1)


if __name__ == "__main__":
    unittest.main()
