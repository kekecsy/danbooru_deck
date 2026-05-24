import os
import re
import sys
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
    get_proxies_for_url
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
# 1. 爬虫全局配置与初始化
# ==========================================
db_data = DanbooruData()
base_download_dir = db_data.base_dir
today_str = db_data.today_str
save_dir = db_data.save_dir

runtime_snapshot_path = os.path.join(save_dir, "_runtime_snapshot.json")
runtime_snapshot = load_json(runtime_snapshot_path, {})
daily_viewer_data = db_data.load_viewer_data()

if runtime_snapshot:
    db_data.log_data.update(runtime_snapshot.get("log_data", {}))
    db_data.artist_stats.update(runtime_snapshot.get("artist_stats", {}))
    daily_viewer_data = merge_daily_viewer_data(
        daily_viewer_data,
        runtime_snapshot.get("daily_viewer_data", [])
    )
    db_data.save_global_data()
    db_data.save_viewer_data(daily_viewer_data)
    clear_runtime_snapshot(runtime_snapshot_path)


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

def resolve_selected_date(requested_date=None):
    available_dates = get_available_date_folders()
    if requested_date:
        try:
            datetime.datetime.strptime(requested_date, "%Y-%m-%d")
            if requested_date in available_dates:
                return requested_date, available_dates
        except ValueError:
            pass

    if today_str in available_dates:
        return today_str, available_dates
    if available_dates:
        return available_dates[0], available_dates
    return today_str, available_dates

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


class ScraperState:
    def __init__(self):
        self.is_running = False
        self.play_event = threading.Event()
        self.play_event.set()
        self.logs = []
        self.filter_tags = []
        self.sent_image_count = len(daily_viewer_data)

state = ScraperState()
scraper_thread = None

def append_log(msg):
    text = str(msg)
    try:
        print(text)  # 控制台也打印一份
    except UnicodeEncodeError:
        safe_text = text.encode("gbk", errors="replace").decode("gbk")
        print(safe_text)
    state.logs.append(text)
    state.logs = state.logs[-500:]


def get_today_save_dir():
    current_day = datetime.datetime.now().strftime('%Y-%m-%d')
    target_dir = os.path.join(base_download_dir, current_day)
    os.makedirs(target_dir, exist_ok=True)
    return current_day, target_dir, os.path.join(target_dir, "_runtime_snapshot.json")

class StartRequest(BaseModel):
    start_page: int
    end_page: int
    tags: str
    mode: str = "rank"  # rank | collect_ids | download_ids | popular | popular_range
    target_date: str = ""  # popular 模式用，可指定日期
    start_date: str = ""   # popular_range
    end_date: str = ""     # popular_range
    ids: list = []         # download_ids 模式可选：内联 IDs；非空则覆盖目标日期的 ids_data.json

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
# 3. 核心爬虫逻辑 (融入了打断检测)
# ==========================================
def _ensure_today(db_data_inst, target_date=None):
    """如果跨天或指定了目标日期，更新全局变量"""
    global daily_viewer_data, today_str, save_dir, runtime_snapshot_path
    current_day = target_date if target_date else datetime.datetime.now().strftime('%Y-%m-%d')
    
    if db_data_inst.today_str != current_day:
        db_data_inst.__init__(current_day)
        
    if today_str != current_day:
        today_str = db_data_inst.today_str
        save_dir = db_data_inst.save_dir
        runtime_snapshot_path = os.path.join(save_dir, "_runtime_snapshot.json")
        daily_viewer_data = db_data_inst.load_viewer_data()

def _process_post(post, db_data_inst, filter_tags, do_download=True):
    """处理单个 post：过滤、提取画师、可选下载。返回 (ids, artist, saved_filename) 或 None"""
    global daily_viewer_data
    ids = str(post.get('id'))
    if not ids or ids in db_data_inst.log_data:
        return None

    tag_string = post.get('tag_string', '')
    if any(tag in tag_string for tag in filter_tags):
        append_log(f"跳过 ID {ids}，包含过滤标签。")
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

        state.play_event.wait()
        if not state.is_running:
            return None

        saved_filename = danbooru_api.download_image(image_url, save_dir, append_log)
        if saved_filename:
            db_data_inst.log_data[ids] = image_url
            save_runtime_snapshot(db_data_inst.log_data, db_data_inst.artist_stats, daily_viewer_data, runtime_snapshot_path)
            sleep(1)
        else:
            append_log(f"跳过 ID {ids}，下载失败。")
            return None

    return ids, artist, saved_filename

def _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists):
    """更新画师统计并归类"""
    if not artist:
        return
    db_data_inst.artist_stats[artist] = db_data_inst.artist_stats.get(artist, 0) + 1
    if artist in db_data_inst.all_drawer:
        disk_key = db_data_inst.get_disk_key(artist)
        page_need_update[disk_key].append(artist)
    else:
        new_hot_artists.append(artist)

def _append_viewer(ids, artist, saved_filename, post):
    """往 daily_viewer_data 追加一条记录"""
    global daily_viewer_data
    if not saved_filename:
        return
    # Danbooru 上有些帖子（如部分纯角色/无主帖）tag_string_artist 是空的，
    # 之前直接 return 会让图片下载到本地却没有热度信息条目 —— 改成用 "未知"
    # 作者占位，至少把 score / fav_count / post_url 这些热度元数据保留下来。
    artist_for_record = artist or "未知"
    post_url = danbooru_api.post_url(ids)
    web_url = f"/images/{today_str}/{saved_filename}"
    # 同进程内防止同一个 id / filename 被追加两次（修复 popular_range 中
    # 历史出现的 17 条数据被重复写入的现象）
    for existing in daily_viewer_data:
        if existing.get("post_url") == post_url:
            return
        if existing.get("filename") == saved_filename and existing.get("web_url") == web_url:
            return
    daily_viewer_data.append({
        "artist": artist_for_record,
        "filename": saved_filename,
        "local_path": os.path.join(save_dir, saved_filename),
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
    save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)

# --- mode: rank (原有默认模式) ---
def grabber_rank(db_data_inst, page_num, filter_tags):
    global daily_viewer_data
    _ensure_today(db_data_inst)
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    state.play_event.wait()
    if not state.is_running:
        return [], page_need_update

    try:
        append_log(f"[Rank] 正在获取第 {page_num} 页... (host={danbooru_api.get_host()})")
        posts = danbooru_api.get_posts_by_rank(page_num)
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        save_runtime_snapshot(db_data_inst.log_data, db_data_inst.artist_stats, daily_viewer_data, runtime_snapshot_path)
        return [], {"1": [], "2": []}

    page_success = 0
    page_skipped = 0
    page_failed = 0
    for post in posts:
        if not state.is_running:
            break
        state.play_event.wait()
        result = _process_post(post, db_data_inst, filter_tags, do_download=True)
        if result is None:
            page_skipped += 1
            continue
        ids, artist, saved_filename = result
        if saved_filename:
            page_success += 1
        else:
            page_failed += 1
        _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists)
        _append_viewer(ids, artist, saved_filename, post)

    append_log(
        f"[Rank] 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
    )
    db_data_inst.save_global_data()
    db_data_inst.save_viewer_data(daily_viewer_data)
    clear_runtime_snapshot(runtime_snapshot_path)
    return new_hot_artists, page_need_update

# --- mode: popular (按日期热门) ---
def grabber_popular(db_data_inst, page_num, filter_tags, target_date):
    global daily_viewer_data
    _ensure_today(db_data_inst, target_date)
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    state.play_event.wait()
    if not state.is_running:
        return [], page_need_update

    try:
        append_log(f"[Popular] 正在获取 {target_date} 第 {page_num} 页... (host={danbooru_api.get_host()})")
        posts = danbooru_api.get_popular_posts(target_date, page_num)
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    page_success = 0
    page_skipped = 0
    page_failed = 0
    for post in posts:
        if not state.is_running:
            break
        state.play_event.wait()
        result = _process_post(post, db_data_inst, filter_tags, do_download=True)
        if result is None:
            page_skipped += 1
            continue
        ids, artist, saved_filename = result
        if saved_filename:
            page_success += 1
        else:
            page_failed += 1
        _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists)
        _append_viewer(ids, artist, saved_filename, post)

    append_log(
        f"[Popular] {target_date} 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
    )
    db_data_inst.save_global_data()
    db_data_inst.save_viewer_data(daily_viewer_data)
    return new_hot_artists, page_need_update

# --- mode: collect_ids (只收集 ID 不下载) ---
def grabber_collect_ids(db_data_inst, page_num, filter_tags):
    _ensure_today(db_data_inst)
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    daily_ids_data = db_data_inst.load_ids_data()

    state.play_event.wait()
    if not state.is_running:
        return [], page_need_update

    try:
        append_log(f"[CollectIDs] 正在获取第 {page_num} 页... (host={danbooru_api.get_host()})")
        posts = danbooru_api.get_posts_by_rank(page_num)
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    for post in posts:
        if not state.is_running:
            break
        state.play_event.wait()
        result = _process_post(post, db_data_inst, filter_tags, do_download=False)
        if not result:
            continue
        ids, artist, _ = result
        _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists)
        if artist:
            daily_ids_data.append(ids)

    db_data_inst.save_global_data()
    daily_ids_data = list(set(daily_ids_data))
    db_data_inst.save_ids_data(daily_ids_data)
    append_log(f"[CollectIDs] 当前已收集 {len(daily_ids_data)} 个 ID")
    return new_hot_artists, page_need_update

# --- mode: download_ids (从已收集的 IDs 批量下载) ---
def task_download_ids(db_data_inst, filter_tags, inline_ids=None):
    global daily_viewer_data
    _ensure_today(db_data_inst)
    daily_viewer_data = db_data_inst.load_viewer_data()

    if inline_ids:
        # 用户从别的客户端复制 IDs 粘贴过来：去重 + 仅保留纯数字串，写入当天 ids_data.json
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
            db_data_inst.save_ids_data(ids_data)
            append_log(f"[DownloadIDs] 已写入 {len(ids_data)} 个粘贴的 ID 到 {db_data_inst.today_str}/ids_data.json")
    else:
        ids_data = db_data_inst.load_ids_data()

    if not ids_data:
        append_log("[DownloadIDs] 没有可下载的 ID（既未粘贴也未先用「仅收集ID」模式收集）。")
        return

    append_log(f"[DownloadIDs] 开始下载，共 {len(ids_data)} 个 ID")
    success_count = 0

    for pid_str in ids_data:
        if not state.is_running:
            append_log("任务已被强制终止。")
            break
        state.play_event.wait()

        if pid_str in db_data_inst.log_data:
            continue

        append_log(f"[DownloadIDs] 正在处理 ID: {pid_str}")
        post_data = danbooru_api.fetch_data_with_retry(pid_str)
        if not post_data:
            append_log(f"ID {pid_str} 获取数据失败，跳过")
            continue

        if filter_tags:
            tag_string = post_data.get('tag_string', '')
            if any(tag in tag_string for tag in filter_tags):
                append_log(f"跳过 ID {pid_str}，包含过滤标签。")
                continue

        image_url = post_data.get('file_url') or post_data.get('large_file_url')
        if not image_url:
            continue

        state.play_event.wait()
        if not state.is_running:
            break

        saved_filename = danbooru_api.download_image(image_url, save_dir, append_log)
        if not saved_filename:
            append_log(f"ID {pid_str} 下载失败，跳过")
            continue

        db_data_inst.log_data[pid_str] = image_url
        success_count += 1

        artist = ""
        if 'tag_string_artist' in post_data:
            artist_list = post_data['tag_string_artist'].split()
            artist_list = [a for a in artist_list if not a.lower().endswith("(voice_actor)")]
            if artist_list:
                artist = ' '.join(artist_list)

        if artist:
            db_data_inst.artist_stats[artist] = db_data_inst.artist_stats.get(artist, 0) + 1

        _append_viewer(pid_str, artist, saved_filename, post_data)
        save_runtime_snapshot(db_data_inst.log_data, db_data_inst.artist_stats, daily_viewer_data, runtime_snapshot_path)

    db_data_inst.save_global_data()
    db_data_inst.save_viewer_data(daily_viewer_data)
    clear_runtime_snapshot(runtime_snapshot_path)
    append_log(f"[DownloadIDs] 完成，成功下载 {success_count} 张图片。")


def scraper_task(start_page, end_page, mode="rank", target_date="", start_date="", end_date="", inline_ids=None):
    global scraper_thread
    try:
        if mode == "download_ids":
            task_download_ids(db_data, state.filter_tags, inline_ids)
        elif mode == "popular_range":
            if not start_date or not end_date:
                append_log("日期范围缺失。")
                return
            s_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
            e_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d")
            if s_dt > e_dt: s_dt, e_dt = e_dt, s_dt

            # 按时间顺序（早 → 晚）抓取；超过 2 天时每抓完 2 天休息 10 分钟防风控
            total_days = (e_dt - s_dt).days + 1
            REST_AFTER_DAYS = 2
            REST_SECONDS = 600
            need_throttle = total_days > REST_AFTER_DAYS
            if need_throttle:
                append_log(f"日期范围共 {total_days} 天，将每 {REST_AFTER_DAYS} 天休息 {REST_SECONDS // 60} 分钟防风控。")

            curr_dt = s_dt
            days_since_rest = 0
            while curr_dt <= e_dt:
                if not state.is_running: break
                pop_date = curr_dt.strftime("%Y-%m-%d")
                append_log(f"=== 开始抓取日期: {pop_date} ===")
                pop_db = DanbooruData(pop_date)
                pop_snapshot_path = os.path.join(pop_db.save_dir, "_runtime_snapshot.json")

                output = pop_db.load_hot_drawer()
                nu_sets = pop_db.load_need_update()
                n = start_page

                while n <= end_page:
                    if not state.is_running: break
                    state.play_event.wait()
                    append_log(f"--- 正在处理 {pop_date} 第 {n} 页 ---")
                    o, n_u_dict = grabber_popular(pop_db, n, state.filter_tags, pop_date)
                    output = list(set(output + o) - pop_db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])

                    pop_db.save_hot_drawer(list(set(output)))
                    pop_db.save_need_update(nu_sets)
                    n += 1

                # 本日期所有页面处理完毕（或被打断），清理这天的临时快照，
                # 否则会留下陈旧的 _runtime_snapshot.json
                if state.is_running:
                    clear_runtime_snapshot(pop_snapshot_path)
                days_since_rest += 1
                curr_dt += datetime.timedelta(days=1)

                # 还有剩余日期、累计达到 REST_AFTER_DAYS 时休息（可被暂停/停止打断）
                if (need_throttle
                        and state.is_running
                        and curr_dt <= e_dt
                        and days_since_rest >= REST_AFTER_DAYS):
                    append_log(f"已抓取 {days_since_rest} 天，休息 {REST_SECONDS // 60} 分钟防风控（可暂停/停止打断）...")
                    slept = 0
                    while slept < REST_SECONDS:
                        if not state.is_running: break
                        state.play_event.wait()
                        sleep(1)
                        slept += 1
                    days_since_rest = 0
                    if state.is_running:
                        append_log("休息结束，继续抓取下一天。")
        else:
            output = db_data.load_hot_drawer()
            nu_sets = db_data.load_need_update()

            n = start_page
            mode_label = {"rank": "Rank", "popular": "Popular", "collect_ids": "CollectIDs"}.get(mode, mode)
            append_log(f"开始抓取 [{mode_label}]，从第 {start_page} 页到第 {end_page} 页")
            append_log(f"当前过滤 Tags: {state.filter_tags}")

            while n <= end_page:
                if not state.is_running: 
                    append_log("任务已被强制终止。")
                    break
                state.play_event.wait()
                append_log(f"--- 正在处理第 {n} 页 ---")

                if mode == "popular":
                    pop_date = target_date or db_data.today_str
                    pop_db = DanbooruData(pop_date)
                    pop_snapshot_path = os.path.join(pop_db.save_dir, "_runtime_snapshot.json")
                    o, n_u_dict = grabber_popular(pop_db, n, state.filter_tags, pop_date)

                    output = list(set(output + o) - pop_db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])

                    pop_db.save_hot_drawer(list(set(output)))
                    pop_db.save_need_update(nu_sets)
                    # popular 单日期模式同样需要在每页处理完后清理临时快照
                    if state.is_running:
                        clear_runtime_snapshot(pop_snapshot_path)
                elif mode == "collect_ids":
                    o, n_u_dict = grabber_collect_ids(db_data, n, state.filter_tags)
                    output = list(set(output + o) - db_data.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    db_data.save_hot_drawer(list(set(output)))
                    db_data.save_need_update(nu_sets)
                else:
                    o, n_u_dict = grabber_rank(db_data, n, state.filter_tags)
                    output = list(set(output + o) - db_data.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    db_data.save_hot_drawer(list(set(output)))
                    db_data.save_need_update(nu_sets)

                n += 1
    except Exception as e:
        save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)
        append_log(f"抓取任务异常中断，已写入临时快照: {e}")
    finally:
        state.is_running = False
        scraper_thread = None
        append_log("所有页面处理完毕或已结束。")

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

@app.post("/api/start")
def start_scraper(req: StartRequest, background_tasks: BackgroundTasks):
    global scraper_thread
    if state.is_running:
        return {"msg": "爬虫已经在运行中"}
    
    state.filter_tags = [t.strip() for t in req.tags.split(',') if t.strip()]
    state.is_running = True
    state.play_event.set()
    state.logs = []
    state.sent_image_count = len(daily_viewer_data)

    scraper_thread = threading.Thread(
        target=scraper_task,
        args=(req.start_page, req.end_page, req.mode, req.target_date, req.start_date, req.end_date, req.ids),
        daemon=True
    )
    scraper_thread.start()
    mode_labels = {"rank": "排行抓取", "popular": "Popular热门", "collect_ids": "仅收集ID", "download_ids": "按ID下载", "popular_range": "日期范围热门"}
    return {"msg": f"{mode_labels.get(req.mode, '任务')}已启动"}

@app.post("/api/pause")
def pause_scraper():
    state.play_event.clear()
    append_log("任务已暂停（正在等待当前动作完成）...")
    return {"msg": "已暂停"}

@app.post("/api/resume")
def resume_scraper():
    state.play_event.set()
    append_log("任务已恢复。")
    return {"msg": "已恢复"}

@app.post("/api/stop")
def stop_scraper():
    state.is_running = False
    state.play_event.set()
    save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)
    append_log("已强制结束任务，当前进度已写入临时快照。")
    return {"msg": "已强制结束任务"}

@app.get("/api/status")
def get_status():
    global daily_viewer_data
    logs = state.logs.copy()
    state.logs.clear()
    
    # 获取新增的图片数据
    new_images = []
    current_count = len(daily_viewer_data)
    if current_count > state.sent_image_count:
        # 只切片取出新增加的部分
        new_images = daily_viewer_data[state.sent_image_count : current_count]
        state.sent_image_count = current_count

    return {
        "is_running": state.is_running,
        "is_paused": not state.play_event.is_set() if state.is_running else False,
        "new_logs": logs,
        "new_images": new_images  # 发送给前端渲染
    }

@app.get("/api/gallery_data")
def get_gallery_data():
    selected_date, available_dates = resolve_selected_date()
    return {
        "latest_images": [],
        "local_images": build_local_image_library(selected_date),
        "selected_date": selected_date,
        "available_dates": available_dates,
        "today": today_str
    }

@app.get("/api/gallery_data/{date_str}")
def get_gallery_data_by_date(date_str: str):
    selected_date, available_dates = resolve_selected_date(date_str)
    return {
        "latest_images": [],
        "local_images": build_local_image_library(selected_date),
        "selected_date": selected_date,
        "available_dates": available_dates,
        "today": today_str,
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

    # 反查表：filename -> post_id（取自全局 log.json）
    # 用 list() 快照一份，避免后台下载线程同时写入 log_data 导致 RuntimeError
    global_log = db_data.log_data or {}
    fn_to_pid = {}
    for pid, url in list(global_log.items()):
        if not url:
            continue
        fn = url.split('/')[-1].split('?')[0]
        if fn:
            fn_to_pid[fn] = pid

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
    """同步刷新一组 filename 的热度信息；对孤立文件用全局 log.json 反查 post_id 后回填。
    返回 {ok, updates: [{filename, ok, score, fav_count, post_url, artist, characters, tags}]}。

    被设计成 stateless / 同步，前端的「刷新热度」按钮拿当前页 15 张图调用即可，
    避免一次刷全日 600+ 张被 Danbooru 风控。"""
    date_str = req.date or db_data.today_str
    filenames = [fn for fn in (req.filenames or []) if fn]
    if not filenames:
        return {"ok": False, "msg": "filenames 为空", "updates": []}

    dd = DanbooruData(target_date=date_str)
    data = dd.load_viewer_data()
    fn_to_item = {it.get("filename"): it for it in data if it.get("filename")}

    # 全局 log.json 的反查表 —— 给孤立文件用
    # 用 list() 快照一份，避免后台下载线程同时写入 log_data 导致 RuntimeError
    global_log = db_data.log_data or {}
    fn_to_pid_log = {}
    for pid, url in list(global_log.items()):
        if not url:
            continue
        fn = url.split('/')[-1].split('?')[0]
        if fn:
            fn_to_pid_log[fn] = pid

    def _resolve_one(filename):
        item = fn_to_item.get(filename)
        post_id = None
        if item:
            post_id = _extract_post_id(item.get("post_url", ""))
        if not post_id:
            post_id = fn_to_pid_log.get(filename)
        if not post_id:
            return filename, item, None, None
        try:
            post = danbooru_api.fetch_data_with_retry(int(post_id))
        except Exception:
            post = None
        return filename, item, post_id, post

    MAX_WORKERS = 5
    updates = []
    changed = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = [executor.submit(_resolve_one, fn) for fn in filenames]
        for fut in concurrent.futures.as_completed(futures):
            try:
                filename, item, post_id, post = fut.result()
            except Exception as e:
                updates.append({"filename": "?", "ok": False, "msg": str(e)})
                continue

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

            if item:
                item["score"] = new_score
                item["fav_count"] = new_fav
                # 孤立文件之前可能是 artist="未知"、post_url="#"，这里一并刷新
                item["artist"] = artist
                item["post_url"] = post_url
                merged_tags = item.get("tags") or {}
                merged_tags.update(tags_full)
                item["tags"] = merged_tags
                changed = True
            else:
                data.append({
                    "artist": artist,
                    "filename": filename,
                    "local_path": os.path.join(base_download_dir, date_str, filename),
                    "post_url": post_url,
                    "web_url": f"/images/{date_str}/{filename}",
                    "score": new_score,
                    "fav_count": new_fav,
                    "tags": tags_full
                })
                changed = True

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

    if changed:
        dd.save_viewer_data(data)

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
    date_str = date or today_str
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
