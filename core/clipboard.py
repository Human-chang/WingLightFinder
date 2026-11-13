# -*- coding: utf-8 -*-
"""把檔案本體放進 Windows 剪貼簿（CF_HDROP），跟在檔案總管按 Ctrl+C 一樣，
之後可以在檔案總管、桌面、LINE、Email 按 Ctrl+V 貼上檔案。只用標準庫的 ctypes。
"""

import os
import struct
import time
import ctypes
from ctypes import wintypes

CF_HDROP = 15
GMEM_MOVEABLE = 0x0002

# 另外載入一份 DLL 再設定參數型別，不改到其他模組共用的 ctypes.windll。
# 64 位元的記憶體與剪貼簿 handle 是 8 bytes，沒設 restype 會被截成 4 bytes。
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_user32 = ctypes.WinDLL("user32", use_last_error=True)

_kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
_kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
_kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalLock.restype = ctypes.c_void_p
_kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalUnlock.restype = wintypes.BOOL
_kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
_kernel32.GlobalFree.restype = wintypes.HGLOBAL
_user32.OpenClipboard.argtypes = [wintypes.HWND]
_user32.OpenClipboard.restype = wintypes.BOOL
_user32.EmptyClipboard.argtypes = []
_user32.EmptyClipboard.restype = wintypes.BOOL
_user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
_user32.SetClipboardData.restype = wintypes.HANDLE
_user32.CloseClipboard.argtypes = []
_user32.CloseClipboard.restype = wintypes.BOOL


def dropfiles_bytes(path) -> bytes:
    """DROPFILES 表頭（20 bytes，最後的 1 代表路徑是 UTF-16LE），後面接路徑和兩個結尾 NULL。"""
    header = struct.pack("<IIIII", 20, 0, 0, 0, 1)
    return header + os.path.abspath(path).encode("utf-16-le") + b"\x00\x00\x00\x00"


def _open_clipboard(tries=10):
    # 別的程式正在讀寫剪貼簿時會開不了，稍等一下再試
    for _ in range(tries):
        if _user32.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def copy_file_to_clipboard(path):
    """失敗時丟出 OSError，訊息可以直接顯示給使用者看。"""
    data = dropfiles_bytes(path)
    handle = _kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        raise OSError("記憶體不足，無法複製檔案。")
    owned_by_clipboard = False
    try:
        pointer = _kernel32.GlobalLock(handle)
        if not pointer:
            raise OSError("記憶體不足，無法複製檔案。")
        ctypes.memmove(pointer, data, len(data))
        _kernel32.GlobalUnlock(handle)

        if not _open_clipboard():
            raise OSError("剪貼簿正被其他程式使用。")
        try:
            _user32.EmptyClipboard()
            if not _user32.SetClipboardData(CF_HDROP, handle):
                raise OSError(f"無法寫入剪貼簿（錯誤碼 {ctypes.get_last_error()}）。")
            owned_by_clipboard = True  # 成功後這塊記憶體歸系統管，不能再釋放
        finally:
            _user32.CloseClipboard()
    finally:
        if not owned_by_clipboard:
            _kernel32.GlobalFree(handle)
