import os
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


def ensure_user_directories() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    HOT_PIC_DIR.mkdir(parents=True, exist_ok=True)
    DRAWER_DIR.mkdir(parents=True, exist_ok=True)
