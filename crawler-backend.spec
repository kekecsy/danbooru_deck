# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all


curl_datas, curl_binaries, curl_hiddenimports = collect_all("curl_cffi")

datas = [
    ("static", "static"),
    ("pic_web/index.html", "pic_web"),
    ("pic_web/static", "pic_web/static"),
    # 预设贴图：作为「种子」同梱，首次启动播种到 danbooru_DATA/present（见 runtime_paths._seed_preset_dir）。
    # 运行时读的是用户数据目录里的副本，不是这里的同梱资源，用户增删的贴图会被保留。
    ("pic_web/present", "pic_web/present"),
    ("character.json", "."),
    # 随 exe 同梱、首次启动播种到用户数据目录的文件（见 runtime_paths._seed_user_data）
    # 翻译字典
    ("custom_translation.json", "."),
    ("character_chinese_search.json", "."),
    ("character_supplement.json", "."),
    # 收藏（画师 / 角色 / 图片）
    ("artist_favorites.json", "."),
    ("character_favorites.json", "."),
    ("image_favorites.json", "."),
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
