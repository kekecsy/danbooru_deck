import os
import shutil
import sys
from pathlib import Path


def _resource_dir() -> Path:
    configured = os.environ.get("DANBOORU_DECK_RESOURCE_DIR")
    if configured:
        return Path(configured).resolve()
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS).resolve()
    return Path(__file__).resolve().parent


RESOURCE_DIR = _resource_dir()
DATA_DIR = Path(os.environ.get("DANBOORU_DECK_DATA_DIR", RESOURCE_DIR)).resolve()
HOT_PIC_DIR = DATA_DIR / "hot_pic"
DRAWER_DIR = DATA_DIR / "drawer"

# 打码（马赛克编辑器）的预设贴图资源目录。
# dev 模式（DATA_DIR == RESOURCE_DIR）沿用源码里的 pic_web/present，
# 打包分发时改用用户数据目录 danbooru_DATA/present——首次启动会把 exe 内同梱的
# 种子贴图播种过去（见下方 _seed_preset_dir），此后用户增删的贴图不会被覆盖。
if DATA_DIR == RESOURCE_DIR:
    PRESET_DIR = RESOURCE_DIR / "pic_web" / "present"
else:
    PRESET_DIR = DATA_DIR / "present"

# 打包分发时随 exe 一起同梱、首次启动需要「播种」到用户数据目录的文件。
# 只在用户目录里还不存在时复制一次，之后用户编辑的内容永远不会被覆盖。
# 不含 character.json（程序自带资源，从 RESOURCE_DIR 读）、
# library_roots.json（本机磁盘路径，分发到别的电脑会失效）、
# env_config.json（仅 dev 用）等。
_SEED_FILES = (
    # 翻译字典
    "custom_translation.json",
    "character_chinese_search.json",
    "character_supplement.json",
    # 收藏（画师 / 角色 / 图片）
    "artist_favorites.json",
    "character_favorites.json",
    "image_favorites.json",
)


def _seed_user_data() -> None:
    # dev 模式下 DATA_DIR == RESOURCE_DIR，源即目标，无需播种。
    if DATA_DIR == RESOURCE_DIR:
        return
    for name in _SEED_FILES:
        src = RESOURCE_DIR / name
        dst = DATA_DIR / name
        if dst.exists() or not src.exists():
            continue
        try:
            shutil.copy2(src, dst)
        except Exception as exc:
            print(f"[runtime_paths] seed {name} failed: {exc}")


def _seed_preset_dir() -> None:
    # dev 模式下 PRESET_DIR 本就是源码目录，无需播种。
    if DATA_DIR == RESOURCE_DIR:
        return
    src_dir = RESOURCE_DIR / "pic_web" / "present"
    if not src_dir.exists():
        return
    # 用户已经有 present 目录（哪怕是空的、或已被编辑过）就完全不碰，避免覆盖用户的增删。
    if PRESET_DIR.exists():
        return
    try:
        PRESET_DIR.mkdir(parents=True, exist_ok=True)
        for src in src_dir.iterdir():
            if not src.is_file():
                continue
            dst = PRESET_DIR / src.name
            if dst.exists():
                continue
            shutil.copy2(src, dst)
    except Exception as exc:
        print(f"[runtime_paths] seed preset failed: {exc}")


def ensure_user_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HOT_PIC_DIR.mkdir(parents=True, exist_ok=True)
    DRAWER_DIR.mkdir(parents=True, exist_ok=True)
    _seed_user_data()
    _seed_preset_dir()
