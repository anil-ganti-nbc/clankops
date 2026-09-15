# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller ONEDIR spec for ClankOps Desktop Alpha."""

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules

ROOT = Path(SPECPATH).resolve().parent
SRC = str(ROOT / "src")

datas = []
binaries = []
hiddenimports = [
    "clankops",
    "clankops.desktop",
    "clankops.desktop.core",
    "clankops.desktop.webview_host",
    "clankops.terminal",
    "clankops.terminal_views",
    "clankops.terminal_query",
    "clr",
    "clr_loader",
    "pythonnet",
    "webview",
    "webview.platforms.winforms",
    "webview.platforms.edgechromium",
    "webview.platforms.win32",
]
for package in ("webview", "clr_loader", "pythonnet", "bottle", "proxy_tools", "cffi"):
    try:
        collected_datas, collected_binaries, collected_hidden = collect_all(package)
        datas += collected_datas
        binaries += collected_binaries
        hiddenimports += collected_hidden
    except Exception:
        hiddenimports += collect_submodules(package)

a = Analysis(
    [str(ROOT / "scripts" / "clankops_desktop_entry.py")],
    pathex=[SRC],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name="ClankOps",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    icon=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ClankOps",
)
