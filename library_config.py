# library_config.py
# 图库根（library roots）配置的唯一来源：main.py / deck_db.py / danbooru_data.py 共用。
# 从 main.py 抽出，避免 DB 迁移层反向 import main.py（那会拉起整个 FastAPI 应用）。
import re
import threading
from pathlib import Path

from my_utils import load_json
from runtime_paths import BASE_DIR as _BASE_DIR, HOT_PIC_DIR, LIBRARY_ROOTS_JSON

# 兼容旧调用方：部分模块习惯从 runtime_paths 之外拿 BASE_DIR，这里保留同名导出
BASE_DIR = _BASE_DIR


def _safe_library_id(raw: str, fallback: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9_-]+", "_", (raw or "").strip()).strip("_")
    return value or fallback


def _parse_roots_config():
    """Return ordered gallery roots. Missing config keeps the historical ./hot_pic behavior.

    library_roots.json accepts either:
      ["D:/pics/hot_pic", {"id": "archive", "label": "Archive", "path": "E:/hot_pic"}]
    or {"roots": [...]}.
    """
    default_path = HOT_PIC_DIR.resolve()
    roots = [{
        "id": "default",
        "label": "hot_pic",
        "path": default_path,
        "is_default": True,
    }]
    if not LIBRARY_ROOTS_JSON.exists():
        return roots
    try:
        raw = load_json(str(LIBRARY_ROOTS_JSON), [])
    except Exception:
        raw = []
    entries = raw.get("roots", []) if isinstance(raw, dict) else raw
    if not isinstance(entries, list):
        return roots

    seen_paths = {str(default_path).lower()}
    seen_ids = {"default"}
    for idx, entry in enumerate(entries):
        if isinstance(entry, str):
            raw_path = entry
            raw_id = ""
            label = ""
            lazy_scan = False
        elif isinstance(entry, dict):
            raw_path = entry.get("path") or entry.get("root") or ""
            raw_id = entry.get("id") or ""
            label = entry.get("label") or entry.get("name") or ""
            # 机械盘 / 外置盘 / 网盘根目录上默认开懒扫：只枚举日期目录名，不数图。
            # 用户在 JSON 里显式写 "lazy_scan": false 可以覆盖（例如该 root 是本地 SSD）。
            lazy_scan = bool(entry.get("lazy_scan", False))
        else:
            continue
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = (BASE_DIR / path).resolve()
        else:
            path = path.resolve()
        path_key = str(path).lower()
        if path_key in seen_paths:
            continue
        lib_id = _safe_library_id(raw_id, f"lib{idx + 1}")
        base_id = lib_id
        suffix = 2
        while lib_id in seen_ids:
            lib_id = f"{base_id}_{suffix}"
            suffix += 1
        seen_paths.add(path_key)
        seen_ids.add(lib_id)
        roots.append({
            "id": lib_id,
            "label": label or path.name or lib_id,
            "path": path,
            "is_default": False,
            "lazy_scan": lazy_scan,
        })
    return roots


# 配置文件极小且很少变，但旧实现每次调用都重新解析 JSON（gallery 构建、is_path 判定
# 等热路径会调几十次）。按 library_roots.json 的 mtime_ns 缓存，配置被外部改写后
# 下一次调用自动刷新。
_LOCK = threading.Lock()
_cache_roots = None
_cache_mtime = None


def get_library_roots(force_reload: bool = False):
    global _cache_roots, _cache_mtime
    try:
        mtime = LIBRARY_ROOTS_JSON.stat().st_mtime_ns if LIBRARY_ROOTS_JSON.exists() else None
    except OSError:
        mtime = None
    with _LOCK:
        if force_reload or _cache_roots is None or mtime != _cache_mtime:
            _cache_roots = _parse_roots_config()
            _cache_mtime = mtime
        return _cache_roots


# 旧名保留（main.py 内部个别地方按私有函数名调用）
_load_library_roots_config = get_library_roots


def get_library_roots_payload():
    return [
        {
            "id": root["id"],
            "label": root["label"],
            "path": str(root["path"]),
            "is_default": root.get("is_default", False),
            "lazy_scan": bool(root.get("lazy_scan", False)),
        }
        for root in get_library_roots()
    ]


def is_path_in_library_roots(target_path) -> bool:
    try:
        resolved = Path(target_path).resolve()
    except Exception:
        return False
    for root in get_library_roots():
        try:
            resolved.relative_to(root["path"].resolve())
            return True
        except ValueError:
            continue
    return False


# 路径 -> library_id 的反查结果缓存（base_dir / save_dir 在一个进程里只有几种）。
# 落库时 viewer_entries / dl_queue 都要带 library_id；外置 save_dir 覆盖 base_dir
# （main._make_job）后也必须能正确归属到对应外置 root。
_path_id_cache = {}
_path_id_lock = threading.Lock()


def library_id_for_path(target_path) -> str:
    """target_path 落在哪个 library root 下就返回谁的 id；都不匹配返回 'default'。

    Path.resolve(strict=False) 不需要磁盘在线，所以外置盘未挂载时字符串归属仍正确；
    但注意未挂载 root 的写入本身会在别处被 _resolve_save_dir_for_date 拦截。
    """
    try:
        key = str(Path(target_path).resolve()).lower()
    except Exception:
        return "default"
    with _path_id_lock:
        cached = _path_id_cache.get(key)
        if cached is not None:
            return cached
    resolved = Path(key)
    lib_id = "default"
    for root in get_library_roots():
        try:
            resolved.relative_to(root["path"].resolve())
            lib_id = root["id"]
            break
        except ValueError:
            continue
    with _path_id_lock:
        _path_id_cache[key] = lib_id
    return lib_id
