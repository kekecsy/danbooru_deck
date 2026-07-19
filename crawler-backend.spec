# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


curl_datas, curl_binaries, curl_hiddenimports = collect_all("curl_cffi")

datas = [
    ("static", "static"),
    ("pic_web/index.html", "pic_web"),
    ("pic_web/static", "pic_web/static"),
    ("pic_web/present", "pic_web/present"),
    ("character.json", "."),
]

a = Analysis(
    ["main.py"],
    pathex=["."],
    binaries=curl_binaries,
    datas=datas + curl_datas,
    hiddenimports=curl_hiddenimports,
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
    name="crawler-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="crawler-backend",
)
