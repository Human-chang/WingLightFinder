# -*- mode: python ; coding: utf-8 -*-
# 打包設定：python -m PyInstaller --noconfirm --clean WingLightFinder.spec
# 用資料夾模式（onedir）而不是單一執行檔：單一執行檔每次開啟都要先解壓縮到暫存資料夾，會多等 1~2 秒。

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('assets/app_icon.ico', 'assets'), ('assets/icons', 'assets/icons')],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='WingLightFinder',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX 壓縮容易被防毒軟體誤判
    console=False,
    disable_windowed_traceback=False,
    icon='assets/app_icon.ico',
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name='WingLightFinder',
)
