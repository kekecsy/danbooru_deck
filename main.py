import os
import re
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep
import datetime
import json
import threading
import concurrent.futures
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import FileResponse, PlainTextResponse, Response

# Windows asyncio ProactorEventLoop 在客户端中途断开时会抛
# ConnectionResetError(WinError 10054)，是已知无害噪音 (bpo-39010)。
# 给 _call_connection_lost 打补丁，让它静默吞掉这一类异常。
if sys.platform == 'win32':
    from functools import wraps
    from asyncio.proactor_events import _ProactorBasePipeTransport
    _orig_call_connection_lost = _ProactorBasePipeTransport._call_connection_lost

    @wraps(_orig_call_connection_lost)
    def _silenced_call_connection_lost(self, exc):
        try:
            return _orig_call_connection_lost(self, exc)
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
    _ProactorBasePipeTransport._call_connection_lost = _silenced_call_connection_lost
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from my_utils import (
    clear_runtime_snapshot,
    dedup_viewer_data,
    load_json,
    merge_daily_viewer_data,
    save_runtime_snapshot,
    get_proxies_for_url,
    sanitize_tag_folder,
    is_tag_folder,
    tag_folder_display,
)
import danbooru_api
from danbooru_data import DanbooruData

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pic_web.main import app as mosaic_editor_app
from translator import translator

BASE_DIR = Path(__file__).resolve().parent
# 画师收藏存盘文件，结构 {group_name: [artist, ...]}；同一画师可在多个分组
ARTIST_FAVORITES_JSON = BASE_DIR / "artist_favorites.json"
# 角色收藏存盘文件，结构 {group_name: [character_display_token, ...]}；
# 角色 token 形如 "初音未来 [vocaloid]"，分组通常按 source_hint 命名
CHARACTER_FAVORITES_JSON = BASE_DIR / "character_favorites.json"
# 图片收藏存盘文件，结构 {"date/filename": {date, filename, artist, ...}}
IMAGE_FAVORITES_JSON = BASE_DIR / "image_favorites.json"

# ==========================================
# 1. 共享资源服务（log.json / artist_stats.json 跨任务共享）
# ==========================================
# 临时实例只用来拿 base_dir / log_path / stats_path 这些固定路径，
# 之后所有下载任务都会用 _make_job() 各自 new 一个 DanbooruData
_bootstrap = DanbooruData()
base_download_dir = _bootstrap.base_dir
_LOG_PATH = _bootstrap.log_path
_STATS_PATH = _bootstrap.stats_path
del _bootstrap


def _resolve_today() -> str:
    """系统日历今天，永远是真今天，不会被任何下载任务的目标日期污染。"""
    return datetime.datetime.now().strftime('%Y-%m-%d')


class LogStore:
    """log.json 的内存视图：所有下载任务共用一份，写入串行化。"""
    def __init__(self, path):
        self._path = path
        self._lock = threading.RLock()
        self._data = load_json(path, {}) or {}

    def __contains__(self, post_id):
        with self._lock:
            return str(post_id) in self._data

    def get(self, post_id, default=None):
        with self._lock:
            return self._data.get(str(post_id), default)

    def record(self, post_id, url):
        with self._lock:
            self._data[str(post_id)] = url

    def bulk_merge(self, mapping):
        if not mapping:
            return
        with self._lock:
            self._data.update({str(k): v for k, v in mapping.items()})

    def save_atomic(self):
        with self._lock:
            snap = dict(self._data)
        tmp = self._path + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False, indent=4)
        os.replace(tmp, self._path)

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def filename_to_id_map(self):
        result = {}
        for pid, url in self.snapshot().items():
            if not url:
                continue
            fn = url.split('/')[-1].split('?')[0]
            if fn:
                result[fn] = pid
        return result


class StatsStore:
    """artist_stats.json 的内存视图，同样跨任务共享。"""
    def __init__(self, path):
        self._path = path
        self._lock = threading.RLock()
        self._data = load_json(path, {}) or {}

    def increment(self, artist):
        with self._lock:
            self._data[artist] = self._data.get(artist, 0) + 1

    def bulk_merge(self, mapping):
        if not mapping:
            return
        with self._lock:
            for k, v in mapping.items():
                try:
                    inc = int(v or 0)
                except (TypeError, ValueError):
                    continue
                self._data[k] = self._data.get(k, 0) + inc

    def save_atomic(self):
        with self._lock:
            snap = dict(self._data)
        tmp = self._path + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False, indent=4)
        os.replace(tmp, self._path)

    def snapshot(self):
        with self._lock:
            return dict(self._data)


log_store = LogStore(_LOG_PATH)
stats_store = StatsStore(_STATS_PATH)


def _recover_orphan_snapshots():
    """启动时扫所有 hot_pic/<folder>/_runtime_snapshot.json：
    把里面的 log/stats 合并进全局 store，把 viewer_data 增量合并进该目录的
    viewer_data.json，然后删除 snapshot。这样 tag 模式或跨日期模式被打断的
    任务都能被正确恢复（原逻辑只看模块全局 runtime_snapshot_path，跨日期 snapshot 永远漏）。"""
    base = Path(base_download_dir)
    if not base.exists():
        return
    log_dirty = False
    stats_dirty = False
    for folder in base.iterdir():
        if not folder.is_dir():
            continue
        snap_path = folder / "_runtime_snapshot.json"
        if not snap_path.exists():
            continue
        try:
            snap = load_json(str(snap_path), {}) or {}
        except Exception:
            snap = {}
        if not snap:
            clear_runtime_snapshot(str(snap_path))
            continue
        if snap.get("log_data"):
            log_store.bulk_merge(snap["log_data"])
            log_dirty = True
        if snap.get("artist_stats"):
            stats_store.bulk_merge(snap["artist_stats"])
            stats_dirty = True
        snap_items = snap.get("daily_viewer_data") or []
        if snap_items:
            try:
                folder_db = DanbooruData(folder.name)
                merged = merge_daily_viewer_data(folder_db.load_viewer_data(), snap_items)
                folder_db.save_viewer_data(merged)
            except Exception as e:
                print(f"[snapshot] 合并 {folder.name} 的 snapshot 失败: {e}")
        clear_runtime_snapshot(str(snap_path))
    if log_dirty:
        log_store.save_atomic()
    if stats_dirty:
        stats_store.save_atomic()


_recover_orphan_snapshots()


# ==========================================
# 2. DownloadJob + JobRegistry
# ==========================================

@dataclass
class DownloadJob:
    """单个下载任务的全部状态。每个任务有自己的 save_dir / viewer_data / snapshot /
    pause-event / logs，互不干扰；refresh_visible 也按 target_folder 去这里查实例。"""
    job_id: str
    target_folder: str   # "YYYY-MM-DD" 或 "tag_xxx"
    mode: str            # rank / popular / popular_range / tags / collect_ids / download_ids
    label: str
    save_dir: str
    snapshot_path: str
    db: DanbooruData
    viewer_data: list = field(default_factory=list)
    viewer_lock: threading.RLock = field(default_factory=threading.RLock)
    play_event: threading.Event = field(default_factory=threading.Event)
    is_running: bool = False
    logs: list = field(default_factory=list)
    sent_image_count: int = 0
    filter_tags: list = field(default_factory=list)
    thread: object = None
    started_at: object = None
    tag_query: str = ""                                   # tags 模式：供「重试失败页」重建请求
    failed_pages: list = field(default_factory=list)      # [{"folder":..., "page":...}]，自动重试后仍失败的页

    def __post_init__(self):
        if not self.play_event.is_set():
            self.play_event.set()

    def record_failed_page(self, page):
        """记录一个自动重试后仍失败的页（按 folder+page 去重），供前端一键重试。"""
        entry = {"folder": self.target_folder, "page": int(page)}
        with self.viewer_lock:
            for e in self.failed_pages:
                if e.get("folder") == entry["folder"] and e.get("page") == entry["page"]:
                    return
            self.failed_pages.append(entry)


    @property
    def is_paused(self):
        return self.is_running and not self.play_event.is_set()

    def append_log(self, msg):
        text = str(msg)
        try:
            print(text)
        except UnicodeEncodeError:
            print(text.encode("gbk", errors="replace").decode("gbk"))
        self.logs.append(text)
        if len(self.logs) > 500:
            self.logs = self.logs[-500:]

    def append_viewer_entry(self, ids, artist, saved_filename, post):
        """同进程内防止同一 id/filename 被追加两次（见原 _append_viewer 的 popular_range 重复条目修复）。"""
        if not saved_filename:
            return
        artist_for_record = artist or "未知"
        post_url = danbooru_api.post_url(ids)
        web_url = f"/images/{self.target_folder}/{saved_filename}"
        with self.viewer_lock:
            for existing in self.viewer_data:
                if existing.get("post_url") == post_url:
                    return
                if existing.get("filename") == saved_filename and existing.get("web_url") == web_url:
                    return
            self.viewer_data.append({
                "artist": artist_for_record,
                "filename": saved_filename,
                "local_path": os.path.join(self.save_dir, saved_filename),
                "post_url": post_url,
                "web_url": web_url,
                "score": post.get('score', 0) or 0,
                "fav_count": post.get('fav_count', 0) or 0,
                "tags": {
                    "tag_string_general": post.get('tag_string_general', ''),
                    "tag_string_character": post.get('tag_string_character', ''),
                    "tag_string_copyright": post.get('tag_string_copyright', ''),
                    "tag_string_artist": post.get('tag_string_artist', '')
                }
            })
            self._write_snapshot_locked()

    def _write_snapshot_locked(self):
        save_runtime_snapshot(
            log_store.snapshot(),
            stats_store.snapshot(),
            self.viewer_data,
            self.snapshot_path
        )

    def write_snapshot(self):
        with self.viewer_lock:
            self._write_snapshot_locked()

    def flush_viewer_data(self):
        with self.viewer_lock:
            self.db.save_viewer_data(self.viewer_data)

    def clear_snapshot(self):
        clear_runtime_snapshot(self.snapshot_path)

    def switch_target(self, new_folder):
        """popular_range 模式按日期迭代时用：先把当前 folder 落盘，再换到下一天。"""
        with self.viewer_lock:
            try:
                self.db.save_viewer_data(self.viewer_data)
            except Exception as e:
                self.append_log(f"切换目录前落盘失败 ({self.target_folder}): {e}")
            self.clear_snapshot()
            self.target_folder = new_folder
            self.db = DanbooruData(new_folder)
            self.save_dir = self.db.save_dir
            self.snapshot_path = os.path.join(self.save_dir, "_runtime_snapshot.json")
            self.viewer_data = self.db.load_viewer_data()
            self.sent_image_count = len(self.viewer_data)


class JobRegistry:
    MAX_CONCURRENT = 1  # 默认 1（与旧行为一致）。改 2+ 即可允许并发下载不同目录，但
                        # Danbooru 有限流，并发 = QPS 翻倍，要注意撞风控的可能。

    def __init__(self):
        self._jobs: dict = {}
        self._lock = threading.RLock()

    def list_active(self):
        with self._lock:
            return [j for j in self._jobs.values() if j.is_running]

    def can_start(self):
        return len(self.list_active()) < self.MAX_CONCURRENT

    def primary(self):
        """优先返回正在跑的 job；若都没活跃，回退到最近 started_at 的（保留 30 秒，
        让前端 syncStatus 能拉到最后一批 new_logs / new_images / "任务完成" 提示）。"""
        with self._lock:
            active = [j for j in self._jobs.values() if j.is_running]
            if active:
                return active[0]
            all_jobs = list(self._jobs.values())
            if not all_jobs:
                return None
            all_jobs.sort(
                key=lambda j: j.started_at or datetime.datetime.min,
                reverse=True,
            )
            return all_jobs[0]

    def get(self, job_id):
        with self._lock:
            return self._jobs.get(job_id)

    def get_by_folder(self, folder):
        with self._lock:
            for job in self._jobs.values():
                if job.is_running and job.target_folder == folder:
                    return job
        return None

    def add(self, job):
        with self._lock:
            self._jobs[job.job_id] = job

    def remove(self, job_id):
        with self._lock:
            self._jobs.pop(job_id, None)


jobs = JobRegistry()


def append_log(msg):
    """模块级日志：打印到控制台，并 push 到 primary job 的 logs 环（若有）。
    refresh/backfill 等非 job 上下文的提示会用这个，没有任务在跑就只 print。"""
    text = str(msg)
    try:
        print(text)
    except UnicodeEncodeError:
        print(text.encode("gbk", errors="replace").decode("gbk"))
    job = jobs.primary()
    if job is not None:
        job.logs.append(text)
        if len(job.logs) > 500:
            job.logs = job.logs[-500:]


def _make_job(target_folder, mode, filter_tags, label=None):
    db = DanbooruData(target_folder)
    viewer_data = db.load_viewer_data()
    job = DownloadJob(
        job_id=uuid.uuid4().hex[:8],
        target_folder=target_folder,
        mode=mode,
        label=label or f"{mode} · {target_folder}",
        save_dir=db.save_dir,
        snapshot_path=os.path.join(db.save_dir, "_runtime_snapshot.json"),
        db=db,
        viewer_data=viewer_data,
        filter_tags=list(filter_tags or []),
        started_at=datetime.datetime.now(),
    )
    job.sent_image_count = len(viewer_data)
    return job


def get_available_date_folders():
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    folders = []
    for item in Path(base_download_dir).iterdir():
        if not item.is_dir() or not date_pattern.match(item.name):
            continue
        try:
            datetime.datetime.strptime(item.name, "%Y-%m-%d")
        except ValueError:
            continue
        folders.append(item.name)
    return sorted(folders, reverse=True)

def get_available_tag_folders():
    """扫描 hot_pic/ 下所有以 tag_ 开头的文件夹，返回 [{"folder": ..., "display": ...}]。
    用于和日期文件夹并行：日期=按时间归档，tag=按主题归档，二者共用同一份 log.json。"""
    folders = []
    base = Path(base_download_dir)
    if not base.exists():
        return folders
    for item in base.iterdir():
        if not item.is_dir() or not is_tag_folder(item.name):
            continue
        folders.append({
            "folder": item.name,
            "display": tag_folder_display(item.name),
        })
    folders.sort(key=lambda x: x["folder"].lower())
    return folders

def resolve_selected_date(requested_date=None):
    """兼容日期 (YYYY-MM-DD) 和 tag 文件夹 (tag_xxx)：
    - 日期：按已有列表过滤
    - tag 文件夹：只要 hot_pic/<name> 存在就接受
    - 其它情况：fallback 到 today / 列表首项"""
    available_dates = get_available_date_folders()
    if requested_date:
        # 1) tag 文件夹直接放行（只要磁盘上有）
        if is_tag_folder(requested_date):
            tag_dir = Path(base_download_dir) / requested_date
            if tag_dir.exists() and tag_dir.is_dir():
                return requested_date, available_dates
        # 2) 日期：照旧校验 + 命中已有
        try:
            datetime.datetime.strptime(requested_date, "%Y-%m-%d")
            if requested_date in available_dates:
                return requested_date, available_dates
        except ValueError:
            pass

    today = _resolve_today()
    if today in available_dates:
        return today, available_dates
    if available_dates:
        return available_dates[0], available_dates
    return today, available_dates

def build_local_image_library(selected_date=None):
    library = []
    resolved_date, _ = resolve_selected_date(selected_date)
    current_day_dir = Path(base_download_dir) / resolved_date
    viewer_files = [current_day_dir / "viewer_data.json"]
    known_paths = set()
    seen_filenames = set()

    for viewer_file in viewer_files:
        if not viewer_file.exists():
            continue
        day_folder = viewer_file.parent.name
        items = dedup_viewer_data(load_json(str(viewer_file), []))
        for item in reversed(items):
            filename = item.get("filename")
            web_url = item.get("web_url")
            if not filename:
                continue
            if filename in seen_filenames:
                continue
            seen_filenames.add(filename)
            if not web_url:
                web_url = f"/images/{day_folder}/{filename}"
            local_key = os.path.join(day_folder, filename).replace("\\", "/")
            known_paths.add(local_key)
            tags_dict = item.get("tags") or {}
            characters_str = tags_dict.get("tag_string_character", "")
            
            translated_chars = []
            for c in characters_str.split():
                c = c.strip()
                if c:
                    info = translator.get_tag_info(c)
                    chinese_name = info.get("chinese_name") or translator._format_tag(c)
                    hint = info.get("source_hint", "")
                    alias = translator.get_source_hint_alias(hint) if hint else ""
                    
                    # 组合成一个包含搜索元数据的字符串，前端显示时会截取
                    meta = chinese_name
                    if hint: meta += f" [{hint}]"
                    if alias: meta += f" [{alias}]"
                    translated_chars.append(meta)

            library.append({
                "artist": item.get("artist") or "未知",
                "filename": filename,
                "local_path": item.get("local_path") or os.path.join(base_download_dir, day_folder, filename),
                "post_url": item.get("post_url") or "#",
                "web_url": web_url,
                "tags": tags_dict,
                "characters": translated_chars,
                "score": item.get("score", 0) or 0,
                "fav_count": item.get("fav_count", 0) or 0
            })

    if current_day_dir.exists():
        for image_path in sorted(current_day_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not image_path.is_file():
                continue
            suffix = image_path.suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".zip", ".mp4", ".webm", ".mov", ".mkv", ".avi"}:
                continue
            # 跳过已有对应 zip 的 gif（属于已转换的动画），避免重复显示
            if suffix == ".gif" and image_path.with_suffix(".zip").exists():
                continue
            local_key = f"{current_day_dir.name}/{image_path.name}"
            if local_key in known_paths:
                continue
            library.append({
                "artist": "未知",
                "filename": image_path.name,
                "local_path": str(image_path),
                "post_url": "#",
                "web_url": f"/images/{current_day_dir.name}/{image_path.name}",
                "tags": {},
                "characters": [],
                "score": 0,
                "fav_count": 0
            })

    return library



# ==========================================
# 2. FastAPI 后端与状态管理
# ==========================================
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/images", StaticFiles(directory="hot_pic"), name="images")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/mosaic", mosaic_editor_app)


# ==========================================
# 缩略图接口（解决收藏/抓图页加载原图导致的卡顿）
# 原图常是数 MB ~ 几十 MB，但卡片只显示 ~200px，缩到磁盘缓存后体积可降到 30KB 量级。
# ==========================================
_THUMB_CACHE_DIR = BASE_DIR / "hot_pic" / ".thumb_cache"
_THUMB_VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}
_THUMB_ALLOWED_SIZES = (200, 400, 800)
_THUMB_LOCK = threading.Lock()


def _generate_thumbnail(src_path: Path, dst_path: Path, max_dim: int) -> bool:
    """生成 JPEG 缩略图到 dst_path。返回是否成功。Pillow 是 thread-safe 的，
    但同一文件并发生成会浪费 CPU——外层 _THUMB_LOCK 串行保护。"""
    try:
        from PIL import Image
        with Image.open(src_path) as im:
            # 对 GIF / 动图取首帧；对带透明的 PNG/WebP 合成到白底，避免 JPEG 全黑
            if getattr(im, "is_animated", False):
                im.seek(0)
            im = im.convert("RGB") if im.mode != "RGB" else im.copy()
            im.thumbnail((max_dim, max_dim), Image.LANCZOS)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst_path, "JPEG", quality=80, optimize=True, progressive=True)
        return True
    except Exception as e:
        print(f"[thumb] 生成失败 {src_path}: {e}")
        return False


@app.get("/thumb/{date_str}/{filename}")
def api_thumbnail(date_str: str, filename: str, w: int = 400):
    """返回磁盘缓存的 JPEG 缩略图。非图片格式返回 404 让前端 fallback 到占位符。"""
    if w not in _THUMB_ALLOWED_SIZES:
        w = 400  # 限定档位，避免无限大小占满磁盘

    src_path = (BASE_DIR / "hot_pic" / date_str / filename).resolve()
    # 路径穿越防护：解析后必须仍在 hot_pic 下
    hot_pic_root = (BASE_DIR / "hot_pic").resolve()
    try:
        src_path.relative_to(hot_pic_root)
    except ValueError:
        return PlainTextResponse("invalid path", status_code=400)

    if not src_path.exists() or not src_path.is_file():
        return PlainTextResponse("not found", status_code=404)

    ext = src_path.suffix.lower()
    if ext not in _THUMB_VALID_EXTS:
        return PlainTextResponse("not an image", status_code=404)

    cache_path = _THUMB_CACHE_DIR / str(w) / date_str / (filename + ".jpg")

    # 命中缓存且不比源文件旧 -> 直接返回
    if cache_path.exists():
        try:
            if cache_path.stat().st_mtime >= src_path.stat().st_mtime:
                return FileResponse(
                    cache_path,
                    media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"},
                )
        except OSError:
            pass

    # 同一缩略图避免并发生成多份；锁粒度全局，但生成本身是 CPU 密集型，全局串行 OK
    with _THUMB_LOCK:
        # 双重检查：拿到锁后可能别人刚生成完
        if not (cache_path.exists()
                and cache_path.stat().st_mtime >= src_path.stat().st_mtime):
            if not _generate_thumbnail(src_path, cache_path, w):
                return PlainTextResponse("thumb error", status_code=500)

    return FileResponse(
        cache_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


class StartRequest(BaseModel):
    start_page: int
    end_page: int
    tags: str
    mode: str = "rank"  # rank | collect_ids | download_ids | popular | popular_range | tags
    target_date: str = ""  # popular 模式用，可指定日期
    start_date: str = ""   # popular_range
    end_date: str = ""     # popular_range
    ids: list = []         # download_ids 模式可选：内联 IDs；非空则覆盖目标日期的 ids_data.json
    tag_query: str = ""    # tags 模式：Danbooru 多 tag 查询串，如 "hatsune_miku rating:safe"
    pages: list = []       # 定向重试：非空时只抓这些页码（覆盖 start_page~end_page 区间）

class OpenLocalRequest(BaseModel):
    local_path: str

class TranslationImportRequest(BaseModel):
    translations: dict

class ConvertLocalZipRequest(BaseModel):
    local_path: str


class RefreshVisibleRequest(BaseModel):
    date: str
    filenames: list[str] = []


class TranslateCharacterRequest(BaseModel):
    tag: str


class SaveCharacterTranslationRequest(BaseModel):
    tag: str
    has_chinese: bool = True
    chinese_name: str = ""
    source_hint: str = ""
    translated_description_zh: str = ""


class ArtistFavoritesRequest(BaseModel):
    # {group_name: [artist1, artist2, ...]}，同一画师可在多个分组
    groups: dict[str, list[str]]


class CharacterFavoritesRequest(BaseModel):
    # {group_name: [character_display_token, ...]}，分组名通常 = source_hint
    groups: dict[str, list[str]]


class ImageFavoriteItem(BaseModel):
    # 收藏单张图片时前端传过来的元数据快照，方便收藏页直接渲染缩略
    date: str
    filename: str
    artist: str = ""
    characters: list[str] = []
    score: int = 0
    fav_count: int = 0
    local_path: str = ""
    post_url: str = ""
    web_url: str = ""


class ImageFavoriteToggleRequest(BaseModel):
    item: ImageFavoriteItem


class ImageFavoriteRemoveRequest(BaseModel):
    key: str

# ==========================================
# 3. 核心爬虫逻辑：所有 grabber 都按 job 跑，不再读模块全局
# ==========================================

def _fetch_page_with_retry(fetch_fn, job, label, attempts=3, base_delay=3):
    """对「页面列表抓取」做有限次自动重试，消化偶发网络波动（curl 28 超时等）。
    重试间隔可被暂停/停止打断。成功返回 fetch_fn() 的结果；attempts 次后仍失败返回 None。"""
    for i in range(1, attempts + 1):
        if not job.is_running:
            return None
        try:
            return fetch_fn()
        except Exception as e:
            job.append_log(f"{label} 第 {i}/{attempts} 次失败: {e}")
            if i < attempts:
                # 可中断的退避等待（复用 popular_range 的 sleep + is_running 模式）
                slept = 0
                while slept < base_delay * i and job.is_running:
                    job.play_event.wait()
                    sleep(1)
                    slept += 1
    return None


def _process_post(post, job, do_download=True):
    """处理单个 post：过滤、提取画师、可选下载。返回 (ids, artist, saved_filename) 或 None。
    log.json 走全局 log_store；下载目录由 job.save_dir 决定。"""
    ids = str(post.get('id'))
    if not ids or ids in log_store:
        return None

    tag_string = post.get('tag_string', '')
    if any(tag in tag_string for tag in job.filter_tags):
        job.append_log(f"跳过 ID {ids}，包含过滤标签。")
        return None

    artist = ""
    if 'tag_string_artist' in post:
        drawer_list = post['tag_string_artist'].split(' ')
        drawer_list = [s for s in drawer_list if not s.lower().endswith("(voice_actor)")]
        if drawer_list:
            artist = ' '.join(drawer_list)

    saved_filename = None
    if do_download:
        image_url = post.get('file_url') or post.get('large_file_url')
        if not image_url:
            return None

        # 文件已在 job.save_dir 时的早跳过：避免被 download_image 的"文件已存在"分支
        # 卡 sleep(1) + 刷屏。识别后只补 log_store，下一次同 ID 直接静默跳过。
        peek_name = image_url.split('/')[-1].split('?')[0]
        if peek_name and os.path.exists(os.path.join(job.save_dir, peek_name)):
            saved_filename = peek_name
            log_store.record(ids, image_url)
            job.write_snapshot()
            return ids, artist, saved_filename

        job.play_event.wait()
        if not job.is_running:
            return None

        try:
            saved_filename = danbooru_api.download_image(image_url, job.save_dir, job.append_log, raise_on_transient=True)
        except danbooru_api.TransientImageError as e:
            # 瞬时网络失败（已内部重试耗尽）：返回哨兵，让 grabber 把本页记入「失败页」供重试
            job.append_log(f"图片下载失败(网络)，ID {ids} 将计入失败页: {e}")
            return "__TRANSIENT__"
        if saved_filename:
            log_store.record(ids, image_url)
            job.write_snapshot()
            sleep(1)
        else:
            job.append_log(f"跳过 ID {ids}，下载失败。")
            return None

    return ids, artist, saved_filename


def _update_artist_stats(job, artist, page_need_update, new_hot_artists):
    """更新画师统计并归类。stats 走全局 stats_store；disk_drawer/all_drawer 在 job.db 上读。"""
    if not artist:
        return
    stats_store.increment(artist)
    if artist in job.db.all_drawer:
        disk_key = job.db.get_disk_key(artist)
        page_need_update[disk_key].append(artist)
    else:
        new_hot_artists.append(artist)


def _persist_global_data():
    """grabber 每页末尾调一次：把 log/stats 原子落盘。"""
    log_store.save_atomic()
    stats_store.save_atomic()


# --- mode: rank ---
def grabber_rank(job, page_num):
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    job.append_log(f"[Rank] 正在获取第 {page_num} 页... (host={danbooru_api.get_host()})")
    posts = _fetch_page_with_retry(
        lambda: danbooru_api.get_posts_by_rank(page_num), job, f"[Rank] 第 {page_num} 页"
    )
    if posts is None:
        job.append_log(f"[Rank] 第 {page_num} 页获取失败（已自动重试），已记入失败页可手动重试")
        job.record_failed_page(page_num)
        job.write_snapshot()
        return [], {"1": [], "2": []}

    page_success = page_skipped = page_failed = 0
    page_had_transient = False
    for post in posts:
        if not job.is_running:
            break
        job.play_event.wait()
        result = _process_post(post, job, do_download=True)
        if result == "__TRANSIENT__":
            page_failed += 1
            page_had_transient = True
            continue
        if result is None:
            page_skipped += 1
            continue
        ids, artist, saved_filename = result
        if saved_filename:
            page_success += 1
        else:
            page_failed += 1
        _update_artist_stats(job, artist, page_need_update, new_hot_artists)
        job.append_viewer_entry(ids, artist, saved_filename, post)

    if page_had_transient:
        job.record_failed_page(page_num)
    job.append_log(
        f"[Rank] 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
    )
    _persist_global_data()
    job.flush_viewer_data()
    job.clear_snapshot()
    return new_hot_artists, page_need_update


# --- mode: popular ---
def grabber_popular(job, page_num, target_date):
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    job.append_log(f"[Popular] 正在获取 {target_date} 第 {page_num} 页... (host={danbooru_api.get_host()})")
    posts = _fetch_page_with_retry(
        lambda: danbooru_api.get_popular_posts(target_date, page_num),
        job, f"[Popular] {target_date} 第 {page_num} 页"
    )
    if posts is None:
        job.append_log(f"[Popular] {target_date} 第 {page_num} 页获取失败（已自动重试），已记入失败页可手动重试")
        job.record_failed_page(page_num)
        return [], {"1": [], "2": []}

    page_success = page_skipped = page_failed = 0
    page_had_transient = False
    for post in posts:
        if not job.is_running:
            break
        job.play_event.wait()
        result = _process_post(post, job, do_download=True)
        if result == "__TRANSIENT__":
            page_failed += 1
            page_had_transient = True
            continue
        if result is None:
            page_skipped += 1
            continue
        ids, artist, saved_filename = result
        if saved_filename:
            page_success += 1
        else:
            page_failed += 1
        _update_artist_stats(job, artist, page_need_update, new_hot_artists)
        job.append_viewer_entry(ids, artist, saved_filename, post)

    if page_had_transient:
        job.record_failed_page(page_num)
    job.append_log(
        f"[Popular] {target_date} 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
    )
    _persist_global_data()
    job.flush_viewer_data()
    return new_hot_artists, page_need_update


# --- mode: tags ---
def grabber_tags(job, page_num, tag_query):
    """按 Danbooru tag 查询下载到 tag 文件夹。共享全局 log_store 避免和日期文件夹重复下载。"""
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    job.append_log(f"[Tags] 正在获取 [{tag_query}] 第 {page_num} 页... (host={danbooru_api.get_host()})")
    posts = _fetch_page_with_retry(
        lambda: danbooru_api.get_posts_by_tags(tag_query, page_num),
        job, f"[Tags] [{tag_query}] 第 {page_num} 页"
    )
    if posts is None:
        job.append_log(f"[Tags] [{tag_query}] 第 {page_num} 页获取失败（已自动重试），已记入失败页可手动重试")
        job.record_failed_page(page_num)
        return [], {"1": [], "2": []}

    page_success = page_skipped = page_failed = 0
    page_had_transient = False
    for post in posts:
        if not job.is_running:
            break
        job.play_event.wait()
        result = _process_post(post, job, do_download=True)
        if result == "__TRANSIENT__":
            page_failed += 1
            page_had_transient = True
            continue
        if result is None:
            page_skipped += 1
            continue
        ids, artist, saved_filename = result
        if saved_filename:
            page_success += 1
        else:
            page_failed += 1
        _update_artist_stats(job, artist, page_need_update, new_hot_artists)
        job.append_viewer_entry(ids, artist, saved_filename, post)

    if page_had_transient:
        job.record_failed_page(page_num)
    job.append_log(
        f"[Tags] [{tag_query}] 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
    )
    _persist_global_data()
    job.flush_viewer_data()
    return new_hot_artists, page_need_update


# --- mode: collect_ids ---
def grabber_collect_ids(job, page_num):
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    daily_ids_data = job.db.load_ids_data()

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    job.append_log(f"[CollectIDs] 正在获取第 {page_num} 页... (host={danbooru_api.get_host()})")
    posts = _fetch_page_with_retry(
        lambda: danbooru_api.get_posts_by_rank(page_num), job, f"[CollectIDs] 第 {page_num} 页"
    )
    if posts is None:
        job.append_log(f"[CollectIDs] 第 {page_num} 页获取失败（已自动重试），跳过")
        return [], {"1": [], "2": []}

    for post in posts:
        if not job.is_running:
            break
        job.play_event.wait()
        result = _process_post(post, job, do_download=False)
        if not result:
            continue
        ids, artist, _ = result
        _update_artist_stats(job, artist, page_need_update, new_hot_artists)
        if artist:
            daily_ids_data.append(ids)

    _persist_global_data()
    daily_ids_data = list(set(daily_ids_data))
    job.db.save_ids_data(daily_ids_data)
    job.append_log(f"[CollectIDs] 当前已收集 {len(daily_ids_data)} 个 ID")
    return new_hot_artists, page_need_update


# --- mode: download_ids ---
def task_download_ids(job, inline_ids=None):
    if inline_ids:
        # 粘贴的 IDs：去重 + 只留纯数字串，写入当天 ids_data.json
        cleaned = []
        seen = set()
        for raw in inline_ids:
            s = str(raw).strip()
            if not s or not s.isdigit() or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
        ids_data = cleaned
        if ids_data:
            job.db.save_ids_data(ids_data)
            job.append_log(f"[DownloadIDs] 已写入 {len(ids_data)} 个粘贴的 ID 到 {job.target_folder}/ids_data.json")
    else:
        ids_data = job.db.load_ids_data()

    if not ids_data:
        job.append_log("[DownloadIDs] 没有可下载的 ID（既未粘贴也未先用「仅收集ID」模式收集）。")
        return

    job.append_log(f"[DownloadIDs] 开始下载，共 {len(ids_data)} 个 ID")
    success_count = 0

    for pid_str in ids_data:
        if not job.is_running:
            job.append_log("任务已被强制终止。")
            break
        job.play_event.wait()

        if pid_str in log_store:
            continue

        job.append_log(f"[DownloadIDs] 正在处理 ID: {pid_str}")
        post_data = danbooru_api.fetch_data_with_retry(pid_str)
        if not post_data:
            job.append_log(f"ID {pid_str} 获取数据失败，跳过")
            continue

        if job.filter_tags:
            tag_string = post_data.get('tag_string', '')
            if any(tag in tag_string for tag in job.filter_tags):
                job.append_log(f"跳过 ID {pid_str}，包含过滤标签。")
                continue

        image_url = post_data.get('file_url') or post_data.get('large_file_url')
        if not image_url:
            continue

        job.play_event.wait()
        if not job.is_running:
            break

        try:
            saved_filename = danbooru_api.download_image(image_url, job.save_dir, job.append_log, raise_on_transient=True)
        except danbooru_api.TransientImageError as e:
            job.append_log(f"ID {pid_str} 下载失败(网络)，跳过: {e}")
            continue
        if not saved_filename:
            job.append_log(f"ID {pid_str} 下载失败，跳过")
            continue

        log_store.record(pid_str, image_url)
        success_count += 1

        artist = ""
        if 'tag_string_artist' in post_data:
            artist_list = post_data['tag_string_artist'].split()
            artist_list = [a for a in artist_list if not a.lower().endswith("(voice_actor)")]
            if artist_list:
                artist = ' '.join(artist_list)

        if artist:
            stats_store.increment(artist)

        job.append_viewer_entry(pid_str, artist, saved_filename, post_data)
        job.write_snapshot()

    _persist_global_data()
    job.flush_viewer_data()
    job.clear_snapshot()
    job.append_log(f"[DownloadIDs] 完成，成功下载 {success_count} 张图片。")


def _run_job(job, start_page, end_page, mode, target_date, start_date, end_date, inline_ids, tag_query, pages=None):
    """单个 job 的执行入口（在 job.thread 里跑）。根据 mode 分发到各 grabber。
    pages 非空时为「定向重试模式」：只抓这些页码，忽略 start_page~end_page 区间。"""
    pages = pages or []

    def _page_seq(s, e):
        return list(pages) if pages else list(range(s, e + 1))

    try:
        if mode == "download_ids":
            task_download_ids(job, inline_ids)
        elif mode == "tags":
            output = job.db.load_hot_drawer()
            nu_sets = job.db.load_need_update()
            for n in _page_seq(start_page, end_page):
                if not job.is_running:
                    job.append_log("任务已被强制终止。")
                    break
                job.play_event.wait()
                job.append_log(f"--- 正在处理 tag [{tag_query}] 第 {n} 页 ---")
                o, n_u_dict = grabber_tags(job, n, tag_query)
                output = list(set(output + o) - job.db.all_drawer)
                for k in ["1", "2"]:
                    nu_sets[k].update(n_u_dict[k])
                job.db.save_hot_drawer(list(set(output)))
                job.db.save_need_update(nu_sets)
            if job.is_running:
                job.clear_snapshot()
        elif mode == "popular_range":
            if not start_date or not end_date:
                job.append_log("日期范围缺失。")
                return
            s_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
            e_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d")
            if s_dt > e_dt:
                s_dt, e_dt = e_dt, s_dt

            total_days = (e_dt - s_dt).days + 1
            REST_AFTER_DAYS = 2
            REST_SECONDS = 600
            need_throttle = total_days > REST_AFTER_DAYS
            if need_throttle:
                job.append_log(f"日期范围共 {total_days} 天，将每 {REST_AFTER_DAYS} 天休息 {REST_SECONDS // 60} 分钟防风控。")

            curr_dt = s_dt
            days_since_rest = 0
            while curr_dt <= e_dt:
                if not job.is_running:
                    break
                pop_date = curr_dt.strftime("%Y-%m-%d")
                # popular_range 在同一个 job 里按日期迭代：换日时把当前 viewer_data 落盘
                # 再加载新日期的盘上数据，job.target_folder / save_dir / snapshot_path
                # 同步更新 —— refresh_visible 会跟着 get_by_folder 找到正确的实例
                if job.target_folder != pop_date:
                    job.switch_target(pop_date)
                job.append_log(f"=== 开始抓取日期: {pop_date} ===")

                output = job.db.load_hot_drawer()
                nu_sets = job.db.load_need_update()

                for n in _page_seq(start_page, end_page):
                    if not job.is_running:
                        break
                    job.play_event.wait()
                    job.append_log(f"--- 正在处理 {pop_date} 第 {n} 页 ---")
                    o, n_u_dict = grabber_popular(job, n, pop_date)
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)

                if job.is_running:
                    job.clear_snapshot()
                days_since_rest += 1
                curr_dt += datetime.timedelta(days=1)

                if (need_throttle
                        and job.is_running
                        and curr_dt <= e_dt
                        and days_since_rest >= REST_AFTER_DAYS):
                    job.append_log(f"已抓取 {days_since_rest} 天，休息 {REST_SECONDS // 60} 分钟防风控（可暂停/停止打断）...")
                    slept = 0
                    while slept < REST_SECONDS:
                        if not job.is_running:
                            break
                        job.play_event.wait()
                        sleep(1)
                        slept += 1
                    days_since_rest = 0
                    if job.is_running:
                        job.append_log("休息结束，继续抓取下一天。")
        else:
            # rank / popular（单日期）/ collect_ids 共用这个分支
            output = job.db.load_hot_drawer()
            nu_sets = job.db.load_need_update()

            mode_label = {"rank": "Rank", "popular": "Popular", "collect_ids": "CollectIDs"}.get(mode, mode)
            if pages:
                job.append_log(f"开始抓取 [{mode_label}]，定向重试页码: {pages}")
            else:
                job.append_log(f"开始抓取 [{mode_label}]，从第 {start_page} 页到第 {end_page} 页")
            job.append_log(f"当前过滤 Tags: {job.filter_tags}")

            for n in _page_seq(start_page, end_page):
                if not job.is_running:
                    job.append_log("任务已被强制终止。")
                    break
                job.play_event.wait()
                job.append_log(f"--- 正在处理第 {n} 页 ---")

                if mode == "popular":
                    pop_date = target_date or _resolve_today()
                    # popular 单日期模式：job 创建时就钉死 target_folder=pop_date，
                    # 这里直接复用，不再 new pop_db
                    o, n_u_dict = grabber_popular(job, n, pop_date)
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)
                    if job.is_running:
                        job.clear_snapshot()
                elif mode == "collect_ids":
                    o, n_u_dict = grabber_collect_ids(job, n)
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)
                else:
                    o, n_u_dict = grabber_rank(job, n)
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)

    except Exception as e:
        job.write_snapshot()
        job.append_log(f"抓取任务异常中断，已写入临时快照: {e}")
    finally:
        job.is_running = False
        job.append_log("所有页面处理完毕或已结束。")
        # 任务结束后保留 30 秒让前端拉走最后一批 new_logs/new_images，再从注册表里清掉。
        # registry 里 is_running=False 不算占用 MAX_CONCURRENT 槽位，所以不影响立刻起下一个 job。
        def _delayed_remove(job_id=job.job_id):
            sleep(30)
            jobs.remove(job_id)
        threading.Thread(target=_delayed_remove, daemon=True).start()

# ==========================================
# 4. API 路由定义
# ==========================================
@app.get("/api/proxy_check")
def check_proxy():
    url = f"https://{danbooru_api.get_host()}"
    proxies = danbooru_api.PROXIES
    try:
        resp = danbooru_api.requests.get(url, timeout=5, headers=danbooru_api.HEADERS, proxies=proxies, impersonate="chrome120")
        if resp.status_code == 200:
            if proxies:
                return {"status": "success", "msg": "代理可用（已连通）", "color": "green"}
            else:
                return {"status": "warning", "msg": "直连可用（未使用代理）", "color": "orange"}
        else:
            return {"status": "error", "msg": f"访问异常 ({resp.status_code})", "color": "red"}
    except Exception as e:
        return {"status": "error", "msg": f"无法访问: {str(e)}", "color": "red"}

class SafeModeRequest(BaseModel):
    safe: bool = True

@app.post("/api/set_safe_mode")
def set_safe_mode_endpoint(req: SafeModeRequest):
    """前端 SFW 开关：True 走 safebooru.donmai.us，False 走 danbooru.donmai.us。
    设置后立即生效（影响所有后续 danbooru_api 请求），前端在 onMounted 和切换时调用。"""
    danbooru_api.set_safe_mode(req.safe)
    return {"safe": req.safe, "host": danbooru_api.get_host()}

@app.get("/api/safe_mode")
def get_safe_mode_endpoint():
    return {"safe": danbooru_api.get_host() == danbooru_api.HOST_SAFE, "host": danbooru_api.get_host()}

class ProxyModeRequest(BaseModel):
    use_proxy: bool = True

@app.post("/api/set_proxy")
def set_proxy_endpoint(req: ProxyModeRequest):
    """前端代理开关：True 走代理（实时重读系统代理，读不到用默认端口兜底），
    False 强制直连。设置后立即生效，解决「开代理启动后端→关代理后下载仍走死代理」。"""
    return {"ok": True, **danbooru_api.set_proxy_mode(req.use_proxy)}

@app.get("/api/proxy_state")
def proxy_state_endpoint():
    return {"ok": True, **danbooru_api.get_proxy_state()}

@app.post("/api/start")
def start_scraper(req: StartRequest, background_tasks: BackgroundTasks):
    if not jobs.can_start():
        active = jobs.list_active()
        return {
            "ok": False,
            "msg": f"已有 {len(active)} 个任务在跑，达到并发上限 {jobs.MAX_CONCURRENT}",
        }

    filter_tags = [t.strip() for t in req.tags.split(',') if t.strip()]

    # 不同 mode 落到不同的 target_folder：rank/collect_ids 跟随真今天；popular/download_ids
    # 跟随用户指定 target_date；popular_range 用 start_date 做起点，之后 job 自己用
    # switch_target 按日期迭代；tags 算出 tag_xxx 文件夹名。
    if req.mode == "tags":
        if not (req.tag_query or "").strip():
            return {"ok": False, "msg": "tags 模式需要填写 tag 查询串。"}
        folder_name = sanitize_tag_folder(req.tag_query)
        if not folder_name:
            return {"ok": False, "msg": f"tag 查询串 [{req.tag_query}] 转文件夹名失败。"}
        target_folder = folder_name
        label = f"tags · {req.tag_query}"
    elif req.mode == "popular":
        target_folder = req.target_date or _resolve_today()
        label = f"popular · {target_folder}"
    elif req.mode == "popular_range":
        if not req.start_date or not req.end_date:
            return {"ok": False, "msg": "日期范围缺失。"}
        target_folder = req.start_date
        label = f"popular_range · {req.start_date}~{req.end_date}"
    elif req.mode == "download_ids":
        target_folder = req.target_date or _resolve_today()
        label = f"download_ids · {target_folder}"
    else:
        target_folder = _resolve_today()
        label = f"{req.mode} · {target_folder}"

    job = _make_job(target_folder, req.mode, filter_tags, label)
    job.tag_query = req.tag_query or ""
    job.is_running = True
    job.play_event.set()
    jobs.add(job)

    job.thread = threading.Thread(
        target=_run_job,
        args=(job, req.start_page, req.end_page, req.mode, req.target_date,
              req.start_date, req.end_date, req.ids, req.tag_query,
              [int(p) for p in (req.pages or [])]),
        daemon=True,
    )
    job.thread.start()

    mode_labels = {
        "rank": "排行抓取", "popular": "Popular热门", "collect_ids": "仅收集ID",
        "download_ids": "按ID下载", "popular_range": "日期范围", "tags": "Tag下载",
    }
    return {
        "ok": True,
        "msg": f"{mode_labels.get(req.mode, '任务')}已启动",
        "job_id": job.job_id,
        "target_folder": job.target_folder,
    }


def _resolve_job(job_id: str):
    """endpoint 工具：有 job_id 就按 id 取，否则取 primary（最近活跃 / 最近完成）。"""
    return jobs.get(job_id) if job_id else jobs.primary()


@app.post("/api/pause")
def pause_scraper(job_id: str = ""):
    job = _resolve_job(job_id)
    if not job or not job.is_running:
        return {"msg": "没有在跑的任务"}
    job.play_event.clear()
    job.append_log("任务已暂停（正在等待当前动作完成）...")
    return {"msg": "已暂停", "job_id": job.job_id}


@app.post("/api/resume")
def resume_scraper(job_id: str = ""):
    job = _resolve_job(job_id)
    if not job:
        return {"msg": "没有任务"}
    job.play_event.set()
    job.append_log("任务已恢复。")
    return {"msg": "已恢复", "job_id": job.job_id}


@app.post("/api/stop")
def stop_scraper(job_id: str = ""):
    job = _resolve_job(job_id)
    if not job:
        return {"msg": "没有任务"}
    job.is_running = False
    job.play_event.set()
    job.write_snapshot()
    job.append_log("已强制结束任务，当前进度已写入临时快照。")
    return {"msg": "已强制结束任务", "job_id": job.job_id}


@app.get("/api/status")
def get_status(job_id: str = ""):
    """返回 primary job（或指定 job_id）的状态。
    没活跃任务时所有关键字段都返回 falsy，前端 sync 看到 is_running=False 自动收尾。"""
    job = _resolve_job(job_id)

    if job is None:
        return {
            "is_running": False,
            "is_paused": False,
            "target_folder": "",
            "new_logs": [],
            "new_images": [],
            "failed_pages": [],
            "jobs": [],
        }

    # drain logs：push 给前端后从 job 缓冲清掉，下次轮询不重复
    with job.viewer_lock:
        logs = list(job.logs)
        job.logs = []
        current_count = len(job.viewer_data)
        new_images = []
        if current_count > job.sent_image_count:
            new_images = job.viewer_data[job.sent_image_count:current_count]
            job.sent_image_count = current_count

    return {
        "is_running": job.is_running,
        "is_paused": job.is_paused,
        # 当前下载实际写入的子目录名（日期 "YYYY-MM-DD" 或 tag 文件夹 "tag_xxx"）。
        # 前端用这个匹配 selectedDate 决定 new_images 该不该追加进当前画廊。
        "target_folder": job.target_folder,
        "new_logs": logs,
        "new_images": new_images,
        # 自动重试后仍失败的页（[{folder, page}]），不 drain，保留到 job 被清；前端据此弹「重试失败页」
        "failed_pages": list(job.failed_pages),
        "mode": job.mode,
        "tag_query": job.tag_query,
        # 给前端将来扩展 "多任务 UI" 用的列表；目前只有 1 个（MAX_CONCURRENT=1）
        "jobs": [
            {
                "job_id": j.job_id,
                "target_folder": j.target_folder,
                "mode": j.mode,
                "label": j.label,
                "is_running": j.is_running,
            }
            for j in jobs.list_active()
        ],
    }

@app.get("/api/gallery_data")
def get_gallery_data():
    selected_date, available_dates = resolve_selected_date()
    return {
        "latest_images": [],
        "local_images": build_local_image_library(selected_date),
        "selected_date": selected_date,
        "available_dates": available_dates,
        "available_tags": get_available_tag_folders(),
        # 这里返回真正的系统日历今天 —— 不能用模块全局 today_str。
        # today_str 会被 _ensure_today 在下载非今日日期 / tag 文件夹时改写成
        # 那个目标，导致前端"今天"按钮跳到正在下载的目录而不是真正的今天。
        "today": datetime.datetime.now().strftime("%Y-%m-%d")
    }

@app.get("/api/gallery_data/{date_str}")
def get_gallery_data_by_date(date_str: str):
    selected_date, available_dates = resolve_selected_date(date_str)
    return {
        "latest_images": [],
        "local_images": build_local_image_library(selected_date),
        "selected_date": selected_date,
        "available_dates": available_dates,
        "available_tags": get_available_tag_folders(),
        # 同上，使用系统日历今天而不是会被下载任务 hijack 的 today_str。
        "today": datetime.datetime.now().strftime("%Y-%m-%d"),
        "requested_date": date_str
    }

@app.post("/api/open_local")
def open_local_file(req: OpenLocalRequest):
    try:
        target_path = Path(req.local_path).resolve()
        base_path = Path(base_download_dir).resolve()
        if not str(target_path).startswith(str(base_path)):
            return {"ok": False, "msg": "路径不在允许范围内"}
        if not target_path.exists():
            return {"ok": False, "msg": "文件不存在"}
        if hasattr(os, "startfile"):
            os.startfile(str(target_path))
            return {"ok": True, "msg": "已打开本地文件"}
        return {"ok": False, "msg": "当前系统不支持直接打开本地文件"}
    except Exception as e:
        return {"ok": False, "msg": f"打开失败: {e}"}


# ---------------- 热度刷新 (score / fav_count) ----------------

class RefreshState:
    def __init__(self):
        self.is_running = False
        self.date_str = ""
        self.done = 0
        self.total = 0
        self.error = ""
        self.recent = []  # 待推送给前端的增量更新 [{post_id, score, fav_count}]
        self.lock = threading.Lock()

refresh_state = RefreshState()
refresh_thread = None


def _extract_post_id(post_url: str) -> str:
    if not post_url:
        return ""
    tail = post_url.rstrip('/').rsplit('/', 1)[-1]
    return tail if tail.isdigit() else ""


_MEDIA_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif",
               ".zip", ".mp4", ".webm", ".mov", ".mkv", ".avi"}


def _backfill_orphan_entries(date_str: str, dd: DanbooruData) -> int:
    """扫描 date_str 目录里没有 viewer_data 条目的孤立文件，
    通过全局 log.json 反查 post_id 后再去 danbooru 取热度元数据补回去。
    返回成功补全的条目数。"""
    date_dir = Path(base_download_dir) / date_str
    if not date_dir.exists():
        return 0

    data = dd.load_viewer_data()
    known_fns = {item.get("filename") for item in data if item.get("filename")}

    # 反查表：filename -> post_id（取自全局 log_store；snapshot 一份避免下载线程并发写入）
    fn_to_pid = log_store.filename_to_id_map()

    orphans = []
    for image_path in date_dir.iterdir():
        if not image_path.is_file():
            continue
        name = image_path.name
        if name.startswith('_') or name.endswith('.json'):
            continue
        if image_path.suffix.lower() not in _MEDIA_EXTS:
            continue
        # 与 build_local_image_library 保持一致：有 zip 时跳过同名 gif
        if image_path.suffix.lower() == '.gif' and image_path.with_suffix('.zip').exists():
            continue
        if name in known_fns:
            continue
        pid = fn_to_pid.get(name)
        if pid:
            orphans.append((name, pid))

    if not orphans:
        return 0

    append_log(f"[Backfill] {date_str} 发现 {len(orphans)} 个孤立文件，开始反查 post_id 补全热度信息...")
    added = 0
    for name, pid in orphans:
        if not refresh_state.is_running:
            break
        try:
            post = danbooru_api.fetch_data_with_retry(int(pid))
        except Exception as e:
            append_log(f"[Backfill] 拉取 post {pid} 失败: {e}")
            post = None
        if not post:
            sleep(0.3)
            continue

        tag_artist = post.get('tag_string_artist', '') or ''
        artist_tokens = [s for s in tag_artist.split(' ')
                         if s and not s.lower().endswith("(voice_actor)")]
        artist = ' '.join(artist_tokens) if artist_tokens else "未知"

        data.append({
            "artist": artist,
            "filename": name,
            "local_path": str(date_dir / name),
            "post_url": danbooru_api.post_url(pid),
            "web_url": f"/images/{date_str}/{name}",
            "score": post.get('score', 0) or 0,
            "fav_count": post.get('fav_count', 0) or 0,
            "tags": {
                "tag_string_general": post.get('tag_string_general', ''),
                "tag_string_character": post.get('tag_string_character', ''),
                "tag_string_copyright": post.get('tag_string_copyright', ''),
                "tag_string_artist": tag_artist,
                "tag_string_meta": post.get('tag_string_meta', '')
            }
        })
        added += 1
        sleep(0.3)

    if added:
        dd.save_viewer_data(data)
    append_log(f"[Backfill] {date_str} 完成: 补全 {added} / {len(orphans)} 条")
    return added


def _run_refresh_scores(date_str: str):
    """刷新指定日期的 score / fav_count，使用线程池并发。
    刷新前先扫描孤立文件做一次反向补全，避免「下载到本地但没有热度」的图。"""
    MAX_WORKERS = 5
    PER_TASK_SLEEP = 0.3  # 每个 worker 单次任务后的限速

    def _task(post_id, item):
        if not refresh_state.is_running:
            return False
        try:
            post = danbooru_api.fetch_data_with_retry(int(post_id))
        except Exception:
            post = None
        if not post:
            sleep(PER_TASK_SLEEP)
            return False
        new_score = post.get("score", 0) or 0
        new_fav = post.get("fav_count", 0) or 0
        changed = item.get("score") != new_score or item.get("fav_count") != new_fav
        if changed:
            item["score"] = new_score
            item["fav_count"] = new_fav
            with refresh_state.lock:
                refresh_state.recent.append({
                    "post_id": post_id,
                    "score": new_score,
                    "fav_count": new_fav
                })
        sleep(PER_TASK_SLEEP)
        return changed

    try:
        dd = DanbooruData(target_date=date_str)
        # 在刷新前先把孤立文件回填进 viewer_data.json，否则它们永远没有热度
        try:
            _backfill_orphan_entries(date_str, dd)
        except Exception as e:
            append_log(f"[Backfill] 异常（不阻塞刷新）: {e}")
        data = dd.load_viewer_data()
        save_lock = threading.Lock()

        # 构建任务表
        tasks = []
        for item in data:
            pid = _extract_post_id(item.get("post_url", ""))
            if pid:
                tasks.append((pid, item))

        with refresh_state.lock:
            refresh_state.total = len(tasks)
            refresh_state.done = 0
            refresh_state.recent = []
        append_log(f"开始刷新 {date_str}: {len(tasks)} 条记录, {MAX_WORKERS} 线程")

        change_count = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(_task, pid, item): pid for pid, item in tasks}
            for fut in concurrent.futures.as_completed(futures):
                changed = False
                try:
                    changed = bool(fut.result())
                except Exception:
                    pass
                with refresh_state.lock:
                    refresh_state.done += 1
                if changed:
                    change_count += 1
                    if change_count % 20 == 0:
                        with save_lock:
                            dd.save_viewer_data(data)

        with save_lock:
            dd.save_viewer_data(data)
        append_log(f"刷新完成 {date_str}: {refresh_state.done}/{refresh_state.total} 条已查询，{change_count} 条数值变化")
    except Exception as e:
        with refresh_state.lock:
            refresh_state.error = str(e)
        append_log(f"刷新任务异常: {e}")
    finally:
        with refresh_state.lock:
            refresh_state.is_running = False


@app.post("/api/refresh_scores/{date_str}")
def refresh_scores_start(date_str: str):
    """刷新指定日期所有图的 score / fav_count（多线程并发）。"""
    global refresh_thread
    with refresh_state.lock:
        if refresh_state.is_running:
            return {"ok": False, "msg": "已有刷新任务在运行"}
        refresh_state.is_running = True
        refresh_state.date_str = date_str
        refresh_state.done = 0
        refresh_state.total = 0
        refresh_state.error = ""
        refresh_state.recent = []
    refresh_thread = threading.Thread(target=_run_refresh_scores, args=(date_str,), daemon=True)
    refresh_thread.start()
    return {"ok": True, "msg": f"已开始刷新 {date_str} 的热度"}


@app.post("/api/refresh_scores_stop")
def refresh_scores_stop():
    with refresh_state.lock:
        refresh_state.is_running = False
    return {"ok": True}


@app.get("/api/refresh_scores_status")
def refresh_scores_status():
    """返回当前刷新进度并清空 recent 增量（前端调用一次即取走，避免重复应用）。"""
    with refresh_state.lock:
        recent = list(refresh_state.recent)
        refresh_state.recent = []
        return {
            "is_running": refresh_state.is_running,
            "date_str": refresh_state.date_str,
            "done": refresh_state.done,
            "total": refresh_state.total,
            "error": refresh_state.error,
            "recent": recent
        }


@app.get("/api/refresh_score/{post_id}")
def refresh_single_score(post_id: int, date: str = ""):
    """单个 post 同步刷新，写回对应日期的 viewer_data.json。"""
    try:
        post = danbooru_api.fetch_data_with_retry(post_id)
    except Exception as e:
        return {"ok": False, "msg": f"拉取失败: {e}"}
    if not post:
        return {"ok": False, "msg": "拉取失败"}
    new_score = post.get("score", 0) or 0
    new_fav = post.get("fav_count", 0) or 0

    if date:
        try:
            dd = DanbooruData(target_date=date)
            data = dd.load_viewer_data()
            updated = False
            for item in data:
                if _extract_post_id(item.get("post_url", "")) == str(post_id):
                    item["score"] = new_score
                    item["fav_count"] = new_fav
                    updated = True
                    break
            if updated:
                dd.save_viewer_data(data)
        except Exception as e:
            append_log(f"单图刷新写盘失败 {post_id}: {e}")

    return {"ok": True, "post_id": post_id, "score": new_score, "fav_count": new_fav}


def _translate_characters_str(chars_str: str):
    """与 build_local_image_library 保持一致地翻译角色串。"""
    out = []
    for c in (chars_str or '').split():
        c = c.strip()
        if not c:
            continue
        info = translator.get_tag_info(c)
        chinese_name = info.get("chinese_name") or translator._format_tag(c)
        hint = info.get("source_hint", "")
        alias = translator.get_source_hint_alias(hint) if hint else ""
        meta = chinese_name
        if hint:
            meta += f" [{hint}]"
        if alias:
            meta += f" [{alias}]"
        out.append(meta)
    return out


@app.post("/api/refresh_visible")
def refresh_visible(req: RefreshVisibleRequest):
    """同步刷新一组 filename 的热度信息；对孤立文件用全局 log_store 反查 post_id 后回填。
    返回 {ok, updates: [{filename, ok, score, fav_count, post_url, artist, characters, tags}]}。

    被设计成 stateless / 同步，前端的「刷新热度」按钮拿当前页 15 张图调用即可，
    避免一次刷全日 600+ 张被 Danbooru 风控。

    并发模型：
    - 网络 I/O（Danbooru post 拉取）一律放在锁外，避免阻塞下载线程的每页落盘。
    - 如果该 date_str 当前正好有 job 在跑（jobs.get_by_folder 命中），就直接共用
      job.viewer_data + job.viewer_lock：内存里改完，job 自己每页末尾会写盘，无需再
      额外 save_viewer_data 一次（也避免和下载线程互相覆盖）。
    - 没 job 在跑时走干净路径：new 一个 DanbooruData(date_str)，读盘 → 改 → 写盘。
      永远按 date_str 实例化，不再读模块级 db_data（那是个早就过时的 bug 根源）。"""
    date_str = req.date or _resolve_today()
    filenames = [fn for fn in (req.filenames or []) if fn]
    if not filenames:
        return {"ok": False, "msg": "filenames 为空", "updates": []}

    # 关键路径：根据 target_folder 找当前正在跑的 job；命中就走共享内存路径，否则建临时 dd
    active_job = jobs.get_by_folder(date_str)
    dd = None if active_job else DanbooruData(target_date=date_str)

    # Step 1: 在锁内快照 filename -> post_id 的反查表（不持锁做网络 I/O）
    fn_to_pid_log = log_store.filename_to_id_map()

    if active_job is not None:
        with active_job.viewer_lock:
            source_data = active_job.viewer_data
            fn_to_pid_initial = {}
            for it in source_data:
                fn = it.get("filename")
                if not fn:
                    continue
                pid = _extract_post_id(it.get("post_url", ""))
                if pid:
                    fn_to_pid_initial[fn] = pid
    else:
        source_data = dd.load_viewer_data()
        fn_to_pid_initial = {}
        for it in source_data:
            fn = it.get("filename")
            if not fn:
                continue
            pid = _extract_post_id(it.get("post_url", ""))
            if pid:
                fn_to_pid_initial[fn] = pid

    def _resolve_one(filename):
        post_id = fn_to_pid_initial.get(filename) or fn_to_pid_log.get(filename)
        if not post_id:
            return filename, None, None
        try:
            post = danbooru_api.fetch_data_with_retry(int(post_id))
        except Exception:
            post = None
        return filename, post_id, post

    # Step 2: 锁外并发拉取所有 post 数据 —— 慢操作（每个 post 都要打 Danbooru）
    MAX_WORKERS = 5
    fetched = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_resolve_one, fn) for fn in filenames]
        for fut in concurrent.futures.as_completed(futures):
            try:
                fetched.append(fut.result())
            except Exception as e:
                fetched.append(("?", None, None, e))

    # Step 3: 锁内 merge + 落盘
    updates = []
    changed = False

    def _apply_updates(target_data):
        """共用的合并逻辑：把 fetched 的结果 merge 进 target_data（in-place）。
        返回 changed 标记。"""
        nonlocal updates
        fn_to_item = {it.get("filename"): it for it in target_data if it.get("filename")}
        any_changed = False

        for entry in fetched:
            if len(entry) == 4:
                updates.append({"filename": "?", "ok": False, "msg": str(entry[3])})
                continue
            filename, post_id, post = entry

            if not post_id:
                updates.append({"filename": filename, "ok": False, "msg": "无法反查 post_id"})
                continue
            if not post:
                updates.append({"filename": filename, "ok": False, "msg": "拉取失败"})
                continue

            new_score = post.get("score", 0) or 0
            new_fav = post.get("fav_count", 0) or 0
            tag_artist = post.get('tag_string_artist', '') or ''
            artist_tokens = [s for s in tag_artist.split(' ')
                             if s and not s.lower().endswith("(voice_actor)")]
            artist = ' '.join(artist_tokens) if artist_tokens else "未知"
            chars_str = post.get('tag_string_character', '') or ''
            post_url = danbooru_api.post_url(post_id)
            tags_full = {
                "tag_string_general": post.get('tag_string_general', ''),
                "tag_string_character": chars_str,
                "tag_string_copyright": post.get('tag_string_copyright', ''),
                "tag_string_artist": tag_artist,
                "tag_string_meta": post.get('tag_string_meta', '')
            }

            item = fn_to_item.get(filename)
            if item:
                item["score"] = new_score
                item["fav_count"] = new_fav
                item["artist"] = artist
                item["post_url"] = post_url
                merged_tags = item.get("tags") or {}
                merged_tags.update(tags_full)
                item["tags"] = merged_tags
                any_changed = True
            else:
                # 跨目录污染防护：磁盘上不存在该文件就不创建孤立条目（避免脏数据）
                expected_local = os.path.join(base_download_dir, date_str, filename)
                if not os.path.exists(expected_local):
                    updates.append({
                        "filename": filename,
                        "ok": False,
                        "msg": "文件不在该日期目录，已拒绝写入孤立条目"
                    })
                    continue
                new_item = {
                    "artist": artist,
                    "filename": filename,
                    "local_path": expected_local,
                    "post_url": post_url,
                    "web_url": f"/images/{date_str}/{filename}",
                    "score": new_score,
                    "fav_count": new_fav,
                    "tags": tags_full
                }
                target_data.append(new_item)
                fn_to_item[filename] = new_item
                any_changed = True

            updates.append({
                "filename": filename,
                "ok": True,
                "post_id": str(post_id),
                "post_url": post_url,
                "score": new_score,
                "fav_count": new_fav,
                "artist": artist,
                "characters": _translate_characters_str(chars_str),
                "tags": tags_full
            })
        return any_changed

    if active_job is not None:
        # 命中正在跑的 job：直接改它的 in-memory viewer_data，落盘由 job 自己负责
        with active_job.viewer_lock:
            changed = _apply_updates(active_job.viewer_data)
            if changed:
                # 立即落一次盘，避免下载线程崩溃前丢失这次刷新结果
                active_job.db.save_viewer_data(active_job.viewer_data)
    else:
        target_data = source_data
        changed = _apply_updates(target_data)
        if changed:
            dd.save_viewer_data(target_data)

    return {"ok": True, "updates": updates}


@app.get("/")
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>找不到 index.html</h1>")

@app.post("/api/import_translation")
def api_import_translation(req: TranslationImportRequest):
    try:
        translator.update_custom_dict(req.translations)
        return {"ok": True, "msg": "导入成功"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ---------------- 角色增量翻译 ----------------

@app.get("/api/untranslated_characters")
def api_untranslated_characters(date: str = ""):
    """聚合指定日期 viewer_data 里所有「翻译字典查不到」的角色 token。
    返回 {tags: [{tag, post_count, fallback_name}]}，按出现次数倒序。"""
    date_str = date or _resolve_today()
    dd = DanbooruData(target_date=date_str)
    data = dd.load_viewer_data()

    counter = {}
    for item in data:
        chars_str = (item.get("tags") or {}).get("tag_string_character", "") or ""
        for token in chars_str.split():
            token = token.strip()
            if not token:
                continue
            counter[token] = counter.get(token, 0) + 1

    pending = []
    for token, count in counter.items():
        if translator.get_tag_info(token):
            continue
        pending.append({
            "tag": token,
            "post_count": count,
            "fallback_name": translator._format_tag(token),
        })
    pending.sort(key=lambda x: (-x["post_count"], x["tag"]))
    return {"date": date_str, "tags": pending}


@app.get("/api/character_source/{tag}")
def api_character_source(tag: str):
    """返回 character.json 里的英文描述 + other_names + 已组装好的 manual prompt。
    tag 在 URL 路径上需 URL-encode（前端用 encodeURIComponent）。"""
    source = translator.get_character_source(tag)
    manual_prompt = translator.build_manual_prompt(tag, source)
    return {
        "tag": tag,
        "exists": source.get("exists", False),
        "matched_key": source.get("matched_key", ""),
        "description": source.get("description", ""),
        "other_names": source.get("other_names", []),
        "fallback_name": translator._format_tag(tag),
        "manual_prompt": manual_prompt,
    }


@app.post("/api/fetch_character_source")
def api_fetch_character_source(req: TranslateCharacterRequest):
    """character.json 里没有该 tag 时，按 character_tags.py 的思路从 Danbooru wiki 在线拉描述。
    成功会写入 character_supplement.json 并刷新内存源；返回与 /api/character_source 同样的 shape。"""
    tag = (req.tag or "").strip()
    if not tag:
        return {"ok": False, "msg": "tag 为空"}
    source = translator.fetch_character_source(tag)
    if not source.get("exists"):
        return {
            "ok": False,
            "msg": "Danbooru wiki 中没有这个 tag",
            "tag": tag,
            "exists": False,
            "matched_key": "",
            "description": "",
            "other_names": [],
            "fallback_name": translator._format_tag(tag),
            "manual_prompt": translator.build_manual_prompt(tag, source),
        }
    manual_prompt = translator.build_manual_prompt(tag, source)
    return {
        "ok": True,
        "tag": tag,
        "exists": True,
        "matched_key": source.get("matched_key", ""),
        "description": source.get("description", ""),
        "other_names": source.get("other_names", []),
        "fallback_name": translator._format_tag(tag),
        "manual_prompt": manual_prompt,
    }


@app.post("/api/translate_character")
def api_translate_character(req: TranslateCharacterRequest):
    """调用 openrouter 给出 has_chinese/chinese_name/source_hint/translated_description_zh。
    返回的 entry 不主动写盘，等用户在 UI 上校对后点保存。
    失败时附带 raw（LLM 原文）+ error，前端可以让用户手工修复 raw 后再次解析。"""
    tag = (req.tag or "").strip()
    if not tag:
        return {"ok": False, "msg": "tag 为空", "entry": {}, "raw": "", "error": "tag 为空"}
    source = translator.get_character_source(tag)
    result = translator.call_rich_translation(tag, source)
    return {
        "ok": bool(result.get("ok")),
        "entry": result.get("entry", {}),
        "raw": result.get("raw", ""),
        "error": result.get("error", ""),
        "msg": result.get("error", "") or "ok",
        "exists": source.get("exists", False),
    }


@app.post("/api/save_character_translation")
def api_save_character_translation(req: SaveCharacterTranslationRequest):
    """把单条翻译落到 character_chinese_search.json。"""
    try:
        translator.upsert_search_entry(req.tag, {
            "has_chinese": req.has_chinese,
            "chinese_name": req.chinese_name,
            "source_hint": req.source_hint,
            "translated_description_zh": req.translated_description_zh,
        })
        return {"ok": True, "msg": "已保存到 character_chinese_search.json"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


@app.post("/api/import_character_chinese_search")
def api_import_character_chinese_search():
    """把 character_chinese_search.json 整体合并到 custom_translation.json，画廊立即生效。"""
    try:
        result = translator.import_search_to_custom()
        return {"ok": True, **result, "msg": f"已导入 {result['imported']}/{result['total']} 条"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ---------------- 画师收藏 ----------------

def _load_artist_favorites() -> dict:
    if not ARTIST_FAVORITES_JSON.exists():
        return {}
    try:
        import json as _json
        with open(ARTIST_FAVORITES_JSON, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, dict):
            return {}
        # 兼容：值若不是 list 就丢弃；每个 list 内统一 strip + 去重保留顺序
        out: dict[str, list[str]] = {}
        for k, v in data.items():
            if not isinstance(k, str):
                continue
            if not isinstance(v, list):
                continue
            seen = set()
            cleaned: list[str] = []
            for item in v:
                if not isinstance(item, str):
                    continue
                name = item.strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                cleaned.append(name)
            out[k] = cleaned
        return out
    except Exception as e:
        print(f"Failed to load artist favorites: {e}")
        return {}


def _save_artist_favorites(groups: dict) -> None:
    import json as _json
    with open(ARTIST_FAVORITES_JSON, "w", encoding="utf-8") as f:
        _json.dump(groups, f, ensure_ascii=False, indent=2)


@app.get("/api/artist_favorites")
def api_artist_favorites_get():
    """返回 {groups: {name: [artist, ...]}}。"""
    return {"ok": True, "groups": _load_artist_favorites()}


@app.post("/api/artist_favorites")
def api_artist_favorites_set(req: ArtistFavoritesRequest):
    """整体覆盖式写入 {groups}。文件小且单用户，免去分散的 CRUD 端点。"""
    try:
        cleaned: dict[str, list[str]] = {}
        for raw_name, artists in (req.groups or {}).items():
            name = (raw_name or "").strip()
            if not name:
                continue
            seen = set()
            uniq: list[str] = []
            for a in artists or []:
                if not isinstance(a, str):
                    continue
                a = a.strip()
                if not a or a in seen:
                    continue
                seen.add(a)
                uniq.append(a)
            cleaned[name] = uniq
        _save_artist_favorites(cleaned)
        return {"ok": True, "groups": cleaned}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ---------------- 角色收藏 ----------------

def _load_character_favorites() -> dict:
    if not CHARACTER_FAVORITES_JSON.exists():
        return {}
    try:
        import json as _json
        with open(CHARACTER_FAVORITES_JSON, "r", encoding="utf-8") as f:
            data = _json.load(f)
        if not isinstance(data, dict):
            return {}
        out: dict[str, list[str]] = {}
        for k, v in data.items():
            if not isinstance(k, str) or not isinstance(v, list):
                continue
            seen = set()
            cleaned: list[str] = []
            for item in v:
                if not isinstance(item, str):
                    continue
                name = item.strip()
                if not name or name in seen:
                    continue
                seen.add(name)
                cleaned.append(name)
            out[k] = cleaned
        return out
    except Exception as e:
        print(f"Failed to load character favorites: {e}")
        return {}


def _save_character_favorites(groups: dict) -> None:
    import json as _json
    with open(CHARACTER_FAVORITES_JSON, "w", encoding="utf-8") as f:
        _json.dump(groups, f, ensure_ascii=False, indent=2)


@app.get("/api/character_favorites")
def api_character_favorites_get():
    return {"ok": True, "groups": _load_character_favorites()}


@app.post("/api/character_favorites")
def api_character_favorites_set(req: CharacterFavoritesRequest):
    """整体覆盖式写入 {groups}，分组通常按 source_hint 命名以达成「按出处合并」。"""
    try:
        cleaned: dict[str, list[str]] = {}
        for raw_name, chars in (req.groups or {}).items():
            name = (raw_name or "").strip()
            if not name:
                continue
            seen = set()
            uniq: list[str] = []
            for c in chars or []:
                if not isinstance(c, str):
                    continue
                c = c.strip()
                if not c or c in seen:
                    continue
                seen.add(c)
                uniq.append(c)
            cleaned[name] = uniq
        _save_character_favorites(cleaned)
        return {"ok": True, "groups": cleaned}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ---------------- 图片收藏 ----------------

def _image_fav_key(date: str, filename: str) -> str:
    return f"{(date or '').strip()}/{(filename or '').strip()}"


def _load_image_favorites() -> dict:
    if not IMAGE_FAVORITES_JSON.exists():
        return {}
    try:
        import json as _json
        with open(IMAGE_FAVORITES_JSON, "r", encoding="utf-8") as f:
            data = _json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"Failed to load image favorites: {e}")
        return {}


def _save_image_favorites(data: dict) -> None:
    import json as _json
    with open(IMAGE_FAVORITES_JSON, "w", encoding="utf-8") as f:
        _json.dump(data, f, ensure_ascii=False, indent=2)


@app.get("/api/image_favorites")
def api_image_favorites_get():
    """返回 {ok, items: [...], keys: [...]}。items 按 added_at 倒序，keys 给前端快速做 Set 查询。"""
    data = _load_image_favorites()
    items = []
    for key, v in data.items():
        if not isinstance(v, dict):
            continue
        items.append({"key": key, **v})
    items.sort(key=lambda x: x.get("added_at", 0), reverse=True)
    return {"ok": True, "items": items, "keys": list(data.keys()), "count": len(items)}


@app.post("/api/image_favorites/toggle")
def api_image_favorites_toggle(req: ImageFavoriteToggleRequest):
    """切换单张图片的收藏状态，幂等。"""
    item = req.item
    if not item.date or not item.filename:
        return {"ok": False, "msg": "date / filename 不能为空"}
    data = _load_image_favorites()
    key = _image_fav_key(item.date, item.filename)
    if key in data:
        del data[key]
        favorited = False
    else:
        import time as _time
        try:
            payload = item.model_dump()  # pydantic v2
        except AttributeError:
            payload = item.dict()
        payload["added_at"] = int(_time.time())
        data[key] = payload
        favorited = True
    _save_image_favorites(data)
    return {"ok": True, "favorited": favorited, "key": key, "count": len(data)}


@app.post("/api/image_favorites/remove")
def api_image_favorites_remove(req: ImageFavoriteRemoveRequest):
    """按 key 显式移除（收藏页用，避免依赖整条 item）。"""
    data = _load_image_favorites()
    if req.key in data:
        del data[req.key]
        _save_image_favorites(data)
        return {"ok": True, "removed": True, "count": len(data)}
    return {"ok": True, "removed": False, "count": len(data)}

@app.post("/api/convert_local_zip")
def api_convert_local_zip(req: ConvertLocalZipRequest):
    try:
        from pic_web.main import convert_zip_to_gif
        target_path = Path(req.local_path).resolve()
        if not target_path.exists() or target_path.suffix.lower() != '.zip':
            return {"ok": False, "msg": "无效的 ZIP 文件路径"}

        output_path = target_path.with_suffix('.gif')
        if not output_path.exists():
            convert_zip_to_gif(target_path, output_path)

        return {"ok": True, "gif_path": str(output_path)}
    except Exception as e:
        return {"ok": False, "msg": str(e)}


# ---------------- Caption 手动模式：返回提示词供用户复制 ----------------
# 用户在 caption 浮窗里点「复制提示词」时调；本端点不调用任何 LLM，只构造
# system + user prompt 给前端写到剪贴板。前端再单独把图片复制到剪贴板，
# 用户就能粘到任意 chat LLM (Claude / ChatGPT / Gemini Web) 手动跑一次。

class CaptionPromptRequest(BaseModel):
    image_path: str
    with_artist: bool = False
    stage: int | None = None        # 1/2/3 → 3 阶段 pipeline；None → 旧的单轮模式
    verify_json: str | None = None  # stage=3 时用，注入 verify 校验结果


def _find_caption_meta(image_path: Path):
    """在图片同目录查 viewer_data.json 并按 filename 匹配，取出该图元数据
    （角色 / 作品 / 画师 / tags），供构造手动模式提示词用。"""
    viewer_json = image_path.parent / "viewer_data.json"
    if not viewer_json.exists():
        return None
    try:
        with open(viewer_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    target = image_path.name
    for entry in data:
        if entry.get("filename") == target:
            return entry
    return None


@app.post("/api/caption_prompt")
def api_caption_prompt(req: CaptionPromptRequest):
    """返回给前端用于手动模式的提示词组合。

    支持两种调用：
    - 缺省 stage：旧的单轮模式，返回 {ok, system, user, combined, meta_used, filename}。
    - stage=1/2/3：3 阶段 pipeline。stage 1 给 system+observe；stage 2 给
      verify user prompt；stage 3 给 compose user prompt（可选注入 verify_json
      中的 fallback_description / consistent 标记）。前端在外部 LLM 同一对话
      里连续粘贴 3 段即可。
    """
    from caption_prompt import (
        DANBOORU_SYSTEM_PROMPT,
        build_user_prompt,
        build_combined_prompt,
        PIPELINE_SYSTEM_PROMPT,
        OBSERVE_USER_PROMPT,
        build_verify_user_prompt,
        build_compose_user_prompt,
    )
    try:
        image_path = Path(req.image_path).resolve()
    except Exception as e:
        return {"ok": False, "msg": f"无效路径: {e}"}
    if not image_path.exists():
        return {"ok": False, "msg": "图片不存在"}
    # 限制只看 hot_pic 下的图，避免任意路径泄露
    try:
        image_path.relative_to(Path(base_download_dir).resolve())
    except ValueError:
        return {"ok": False, "msg": "路径不在 hot_pic 目录下"}

    meta = _find_caption_meta(image_path)
    stage = req.stage

    if stage is None:
        # 旧单轮契约：保持向后兼容
        user_prompt = build_user_prompt(meta, include_artist=bool(req.with_artist))
        combined = build_combined_prompt(meta, include_artist=bool(req.with_artist))
        return {
            "ok": True,
            "system": DANBOORU_SYSTEM_PROMPT,
            "user": user_prompt,
            "combined": combined,
            "meta_used": bool(meta),
            "filename": image_path.name,
        }

    if stage not in (1, 2, 3):
        return {"ok": False, "msg": f"stage 必须为 1/2/3，收到 {stage}"}

    if stage == 1:
        user_prompt = OBSERVE_USER_PROMPT
        combined = f"{PIPELINE_SYSTEM_PROMPT.rstrip()}\n\n---\n\n{user_prompt}"
        return {
            "ok": True,
            "stage": 1,
            "system": PIPELINE_SYSTEM_PROMPT,
            "user": user_prompt,
            "combined": combined,
            "meta_used": bool(meta),
            "filename": image_path.name,
        }

    if stage == 2:
        user_prompt = build_verify_user_prompt(meta)
        return {
            "ok": True,
            "stage": 2,
            "system": None,
            "user": user_prompt,
            "combined": user_prompt,
            "meta_used": bool(meta),
            "filename": image_path.name,
        }

    # stage == 3
    verify_result: dict | None = None
    skip_verify_note = False
    if req.verify_json:
        try:
            parsed = json.loads(req.verify_json)
            if isinstance(parsed, dict):
                verify_result = parsed
            else:
                skip_verify_note = True
        except Exception:
            skip_verify_note = True
    else:
        skip_verify_note = True

    user_prompt = build_compose_user_prompt(
        meta,
        include_artist=bool(req.with_artist),
        verify_result=verify_result,
        skip_verify_note=skip_verify_note,
    )
    return {
        "ok": True,
        "stage": 3,
        "system": None,
        "user": user_prompt,
        "combined": user_prompt,
        "output": "json",   # stage 3 现在产出结构化 JSON（caption_en/caption_zh/verified_tags），前端按 JSON 解析
        "meta_used": bool(meta),
        "filename": image_path.name,
        "verify_used": verify_result is not None,
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
