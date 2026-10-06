import os
import re
import sys
import uuid
import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from time import sleep
import datetime
import json
import shutil
import threading
import time
import concurrent.futures
from contextlib import nullcontext
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
    _viewer_item_key,
    atomic_write_json,
    dedup_viewer_data,
    load_json,
    merge_daily_viewer_data,
    sanitize_tag_folder,
    is_tag_folder,
    tag_folder_display,
)
import http_client
import danbooru_api
import gelbooru_api
import deck_db
from danbooru_data import DanbooruData
from runtime_paths import DATA_DIR, HOT_PIC_DIR, RESOURCE_DIR, ensure_user_directories

ensure_user_directories()

PROJECT_ROOT = RESOURCE_DIR
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pic_web.main import app as mosaic_editor_app
from translator import translator

BASE_DIR = DATA_DIR
# 画师收藏存盘文件，结构 {group_name: [artist, ...]}；同一画师可在多个分组
ARTIST_FAVORITES_JSON = BASE_DIR / "artist_favorites.json"
# 角色收藏存盘文件，结构 {group_name: [character_display_token, ...]}；
# 角色 token 形如 "初音未来 [vocaloid]"，分组通常按 source_hint 命名
CHARACTER_FAVORITES_JSON = BASE_DIR / "character_favorites.json"
# 图片收藏存盘文件，结构 {"date/filename": {date, filename, artist, ...}}
IMAGE_FAVORITES_JSON = BASE_DIR / "image_favorites.json"
LIBRARY_ROOTS_JSON = BASE_DIR / "library_roots.json"


# 图库根配置已抽到 library_config.py（deck_db / danbooru_data 也要共用，
# 不能反向 import 整个 main.py）。带 mtime 缓存，行为与旧实现一致。
from library_config import (
    _safe_library_id,
    get_library_roots,
    get_library_roots_payload,
    is_path_in_library_roots,
    library_id_for_path,
)
# 旧调用点有按私有名字调的，保留别名
_load_library_roots_config = get_library_roots


def _resolve_save_dir_for_date(target_date: str):
    """查找目标日期在哪个 library root 下真的存在。
    返回 (save_dir, root_id) 或 (None, None)。
    用途：popular_recover 在启动任务前先校验目标日期是否可达，避免断盘/路径漂移时
    静默写到错位置（makedirs 兜底会把图下载到 HOT_PIC_DIR 的空目录里而不是原盘）。"""
    for root in get_library_roots():
        try:
            candidate = Path(root["path"]) / target_date
            if candidate.is_dir():
                return str(candidate), root["id"]
        except OSError:
            # 盘没接 / 路径不可访问：is_dir 抛 OSError 也算"找不到"，继续下一个 root
            continue
    return None, None

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
    """log.json 时代的跨任务共享视图，现薄封装 deck_db.post_log：方法名全部保留，
    所有调用点（record/get/contains/snapshot/filename_to_id_map）零改动。
    写入即时落 WAL，不再有 47MB 全量解析和每页一次的全量重写。"""
    def __init__(self, path):
        self._path = path

    def __contains__(self, post_id):
        return deck_db.log_has(post_id)

    def get(self, post_id, default=None):
        return deck_db.log_get(str(post_id), default)

    def record(self, post_id, url):
        deck_db.log_record(str(post_id), url)

    def bulk_merge(self, mapping):
        if not mapping:
            return
        deck_db.log_bulk_upsert({str(k): v for k, v in mapping.items()})

    def save_atomic(self):
        # 已逐条落 SQLite；保留方法名兼容 _persist_global_data 的 8 个调用点。
        return

    def snapshot(self):
        return deck_db.log_snapshot_urls()

    def filename_to_id_map(self):
        return deck_db.log_filename_to_id_map()


class StatsStore:
    """artist_stats.json 时代的跨任务视图，委托 deck_db.artist_stats。"""
    def __init__(self, path):
        self._path = path

    def increment(self, artist):
        deck_db.stats_inc(artist, 1)

    def bulk_merge(self, mapping):
        deck_db.stats_bulk_add(mapping)

    def save_atomic(self):
        return

    def snapshot(self):
        return deck_db.stats_snapshot()


log_store = LogStore(_LOG_PATH)
stats_store = StatsStore(_STATS_PATH)


# ==========================================
# 2. DownloadJob + JobRegistry
# ==========================================

@dataclass
class DownloadJob:
    """单个下载任务的全部状态。每个任务有自己的 save_dir / viewer_data /
    pause-event / logs，互不干扰；refresh_visible 也按 target_folder 去这里查实例。"""
    job_id: str
    target_folder: str   # "YYYY-MM-DD" 或 "tag_xxx"
    mode: str            # rank / popular / popular_range / tags / collect_ids / download_ids
    label: str
    save_dir: str
    db: DanbooruData
    # popular_recover force_local 模式标志：True 表示图片下载到 HOT_PIC_DIR 本地临时目录，
    # 完成后需手动同步到原盘；任务日志/状态展示据此提示用户。
    local_only: bool = False
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
    tag_source: str = "danbooru"                          # tags 模式：danbooru / gelbooru
    failed_pages: list = field(default_factory=list)      # [{"folder":..., "page":...}]，页面列表抓取失败（页级）
    failed_ids: dict = field(default_factory=dict)        # {ids: folder}，图片下载失败（图级），供前端按 id 重试
    consecutive_page_failures: int = 0                     # 页面抓取静默重试全部失败的「连续」页数（任一成功即清零），
                                                            # 达到 CONSECUTIVE_PAGE_FAILURE_THRESHOLD 时 auto-pause 一次
    pending_ids: set = field(default_factory=set)         # 当前 folder 的 ids_data.json 内存镜像：下载前写入、成功后移除
    pending_dirty: bool = False                            # pending_ids 有移除未落盘：flush_pending_ids 据此跳过无谓的全量重写
    viewer_keys: set = field(default_factory=set)          # viewer_data 的去重 key 集合，把追加判重从 O(n) 线性扫降为 O(1)
    pages_list: list = field(default_factory=list)        # 定向重试页码（如 [10, 11, 12]）；空 = 走 start_page..end_page 全段
    total_planned: int = 0                                # 本次任务总目标数（task_download_ids 入口一次性设置）
    success_count: int = 0                                # 已成功落盘的图片数
    skip_count: int = 0                                   # 已处理但无需下载的图片数
    fail_count: int = 0                                   # 永久失败 + 瞬时网络失败（不可重试的也按失败计）
    page_current: int = 0                                 # 抓取 ID 阶段：当前处理的页码（相对当前 scope）
    page_total: int = 0                                   # 抓取 ID 阶段：当前 scope 的总页数（0 = 隐藏页进度条）
    page_done_count: int = 0                              # 抓取 ID 阶段：已完整跑完的页数（不论成功失败）
                                                           # 前端用 (page_done_count - failed_pages.length) 算"成功 X 页"
    list_cache: dict = field(default_factory=dict)         # 列表页「窗口缓存」，见 _list_posts_for_page：
                                                           # {(source, date): {"first":窗口首页, "posts":[...],
                                                           #   "failed":bool, "exhausted":bool}}
    list_request_count: int = 0                            # 本次 job 已发出的「列表请求」数（页间休息按它计，
                                                           # 不按页 —— 一个请求覆盖 10~50 页）
    download_concurrency: int = 4                          # 单任务内图片下载并发度（rank→download_ids 阶段也用它）
    skip_logged: bool = True                                # rank·按ID下载 专用：False 时 task_download_ids 不走 log_store 早退
    retry_only: bool = False                                # download_ids 重试失败图专用：True 时只下 inline ids，不消费 folder backlog
    is_idle: bool = False                                  # 空闲下载轮次：只下本轮挑中的 ids，且可被前台任务抢占（见 _yield_idle_jobs）
    outcome: str = "pending"                              # pending / running / completed / completed_with_failures / stopped / error
    error_message: str = ""                               # 仅记录任务自身的致命异常，不混用服务 stderr

    def __post_init__(self):
        if not self.play_event.is_set():
            self.play_event.set()
        # 载入当前 folder 已收集的 ids 作为「待下载队列」镜像（权威在 deck.db 的 dl_queue，
        # load_ids_data 返回 pending+failed 全部行）。收集id / 失败残留的 id 都在这里。
        try:
            self.pending_ids = set(str(x) for x in (self.db.load_ids_data() or []))
        except Exception:
            self.pending_ids = set()
        # 图级失败记录现在持久化在 dl_queue(status=failed)：重启后 hydrate 回失败横幅
        try:
            self.failed_ids = {
                str(pid): self.target_folder for pid in (self.db.queue_load_failed_ids() or [])
            }
        except Exception:
            self.failed_ids = {}
        # 「停止」落盘结果（finalize_on_stop 写入），/api/status 透给前端做 toast 文案。
        # 默认 0/空串：运行中和未触发 finalize 的状态都按"无新保存"展示。
        self.last_saved_ids_count = 0
        self.last_ids_data_path = ""
        # viewer_data 去重索引：与 my_utils.dedup_viewer_data 同一套 key 规则，
        # append_viewer_entry 在 4 下载线程下不再每条线性扫整个 list（旧实现 O(n²)）。
        self.viewer_keys = {
            _viewer_item_key(it) for it in self.viewer_data if isinstance(it, dict)
        }

    def record_failed_page(self, page):
        """记录一个页面列表抓取失败的页（按 folder+page 去重），供前端一键重试。
        同时持久化到 deck.db failed_pages（旧实现纯内存，重启即丢）。"""
        page = int(page)
        entry = {"folder": self.target_folder, "page": page}
        with self.viewer_lock:
            for e in self.failed_pages:
                if e.get("folder") == entry["folder"] and e.get("page") == entry["page"]:
                    return
            self.failed_pages.append(entry)
        try:
            deck_db.failed_page_add(
                self.target_folder, page, self.tag_query or "", self.tag_source or "danbooru"
            )
        except Exception as e:
            self.append_log(f"失败页落库失败 (p{page}): {e}")

    def clear_failed_page(self, page):
        """该页后续抓取成功：从内存失败列表和 deck.db 同时移除（重试成功后不再挂横幅）。"""
        page = int(page)
        with self.viewer_lock:
            self.failed_pages = [
                e for e in self.failed_pages
                if not (e.get("folder") == self.target_folder and e.get("page") == page)
            ]
        try:
            deck_db.failed_page_remove(
                self.target_folder, page, self.tag_query or "", self.tag_source or "danbooru"
            )
        except Exception as e:
            self.append_log(f"清除失败页落库失败 (p{page}): {e}")

    def hydrate_failed_pages(self):
        """target_folder / tag_query / tag_source 都就位后（_make_job 之后），
        从 deck.db 恢复本 scope 的失败页到内存横幅。start 端点和 switch_target 调用。"""
        try:
            self.failed_pages = deck_db.failed_page_load_scope(
                self.target_folder, self.tag_query or "", self.tag_source or "danbooru"
            )
        except Exception:
            self.failed_pages = []

    def queue_pending_id(self, ids):
        """下载某张图片前先把它的 id 记入 folder 的 ids_data.json（性质同「收集id」）。
        无论以何种方式下载，都先落盘 id，保证中断/失败后该 id 仍在文件里可被重试。
        这一步保持「立即全量写」：它是崩溃安全的关键，不能降频。"""
        ids = str(ids)
        with self.viewer_lock:
            if ids in self.pending_ids:
                return
            self.pending_ids.add(ids)
            # 单行 upsert 立即提交 + ids 镜像立即导出（不再全量覆写队列，只覆写小镜像文件）。
            # failed 行重新入队时状态翻回 pending。
            self.db.queue_add_id(ids)
            # 刚写完，镜像与盘一致；resolve 若只移除刚写入的 id 也不必再写一次
            self.pending_dirty = False

    def resolve_pending_id(self, ids):
        """一张图片处理完毕（下载成功 / 已存在 / 永久不可用）后，把它从待下载队列移除。
        只置 dirty 位、不立即落盘 —— 旧实现每张图成功都全量重写 ids_data.json，
        由 flush_pending_ids() 在批次收尾 / 停止 / 切目录时统一写。
        崩溃窗口的代价仅是：已完成 id 残留在 ids_data，重启重抓时命中「文件已存在」早退，安全。
        同时清掉失败标记 —— 重试成功后该 id 不应再出现在失败横幅里。"""
        ids = str(ids)
        with self.viewer_lock:
            if ids in self.pending_ids:
                self.pending_ids.discard(ids)
                self.pending_dirty = True
                # DB 行立即删（点操作，无写放大）；镜像按 P0 节奏 dirty 批量导出
                self.db.queue_resolve_id(ids)
            self.failed_ids.pop(ids, None)

    def flush_pending_ids(self):
        """把 pending_ids 的移除增量落盘（dirty 才写）。批次收尾 / 每 20 张 / 停止 / 切目录时调。"""
        with self.viewer_lock:
            if not self.pending_dirty:
                return
            self.db.save_ids_data(sorted(self.pending_ids))
            self.pending_dirty = False

    def record_failed_id(self, ids):
        """图片下载瞬时失败（已内部重试耗尽）：dl_queue 行标记 failed（重启不丢），
        id 仍留在 ids 镜像里，并记入内存失败表供前端按 id 重试。"""
        ids = str(ids)
        with self.viewer_lock:
            try:
                self.db.queue_mark_failed_id(ids)
            except Exception as e:
                self.append_log(f"标记失败 id 落库失败 ({ids}): {e}")
            self.failed_ids[ids] = self.target_folder


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
        post_url = post.get("post_url") or danbooru_api.post_url(ids)
        web_url = f"/images/{self.target_folder}/{saved_filename}"
        tags_full = {
            "tag_string_general": post.get('tag_string_general', ''),
            "tag_string_character": post.get('tag_string_character', ''),
            "tag_string_copyright": post.get('tag_string_copyright', ''),
            "tag_string_artist": post.get('tag_string_artist', '')
        }
        for extra_key in ("tag_string_meta", "tag_string", "rating", "md5"):
            value = post.get(extra_key)
            if value:
                tags_full[extra_key] = value
        entry = {
            "artist": artist_for_record,
            "filename": saved_filename,
            "local_path": os.path.join(self.save_dir, saved_filename),
            "post_url": post_url,
            "web_url": web_url,
            "score": post.get('score', 0) or 0,
            "fav_count": post.get('fav_count', 0) or 0,
            "tags": tags_full
        }
        with self.viewer_lock:
            # O(1) set 判重替代旧实现的逐条线性扫描（4 线程下旧实现是 O(n²)）。
            key = _viewer_item_key(entry)
            if key in self.viewer_keys:
                return
            self.viewer_keys.add(key)
            self.viewer_data.append(entry)

    def flush_viewer_data(self):
        with self.viewer_lock:
            # 刷新热度接口可能在下载任务运行期间更新同一 folder。写盘前先合并
            # 数据库中的最新快照，避免旧的下载内存快照把热度或新条目覆盖掉。
            latest = self.db.load_viewer_data()
            current = dedup_viewer_data(self.viewer_data)
            latest_keys = {_viewer_item_key(item) for item in latest if isinstance(item, dict)}
            merged = list(latest)
            merged.extend(item for item in current
                          if _viewer_item_key(item) not in latest_keys)
            self.viewer_data = dedup_viewer_data(merged)
            self.viewer_keys = {
                _viewer_item_key(item) for item in self.viewer_data
                if isinstance(item, dict)
            }
            self.db.save_viewer_data(self.viewer_data)

    def finalize_on_stop(self):
        """「停止」按钮触发后强制把当前状态落盘，避免用户点了停止却发现
        ids_data.json / viewer_data.json 没及时更新导致重启后丢进度。

        - collect 阶段：把内存里的 pending_ids 同步到 ids_data.json，
          让用户下次进来还能看到「已收集 N 个 ID」可继续下载。
        - download 阶段：把内存里的 viewer_data 全部 flush 到 viewer_data.json，
          让右侧画廊立刻能看到停止前已经下好的图。
        - 两个阶段都会持久化 log_store / stats_store 等全局数据。
        - 这里捕获所有异常：stop 是终止路径，再抛错也救不回来，只能记日志。

        同时把本次落盘条数 + 路径写到 `last_saved_ids_count` / `last_ids_data_path`，
        供 /api/status 透给前端，让用户在 toast 里看到"已增量保存 N 个 ID 到 folder/ids_data.json"。
        """
        try:
            with self.viewer_lock:
                # dirty（有完成移除的增量）才重写；toast 数字按本次实际落盘条数给
                if self.pending_dirty:
                    self.db.save_ids_data(sorted(self.pending_ids))
                    self.pending_dirty = False
                    self.last_saved_ids_count = len(self.pending_ids)
                else:
                    self.last_saved_ids_count = 0
                # 路径每次 finalize 都更新（save_dir 不会变），保证前端能拿到当前 folder 的 ids_data.json
                self.last_ids_data_path = os.path.join(self.save_dir, "ids_data.json")
        except Exception as e:
            self.append_log(f"[stop] 落盘 ids_data 失败: {e}")
            self.last_saved_ids_count = 0
            self.last_ids_data_path = ""
        try:
            self.flush_viewer_data()
        except Exception as e:
            self.append_log(f"[stop] 落盘 viewer_data 失败: {e}")
        try:
            _persist_global_data()
        except Exception as e:
            self.append_log(f"[stop] 持久化全局数据失败: {e}")

    def switch_target(self, new_folder):
        """popular_range 模式按日期迭代时用：先把当前 folder 落盘，再换到下一天。"""
        with self.viewer_lock:
            try:
                self.flush_viewer_data()
            except Exception as e:
                self.append_log(f"切换目录前落盘失败 ({self.target_folder}): {e}")
            # 旧 folder 的 pending 移除增量也要先落盘，否则会残留在旧 ids_data.json
            try:
                if self.pending_dirty:
                    self.db.save_ids_data(sorted(self.pending_ids))
                    self.pending_dirty = False
            except Exception as e:
                self.append_log(f"切换目录前落盘 ids_data 失败 ({self.target_folder}): {e}")
            self.target_folder = new_folder
            self.db = DanbooruData(new_folder)
            self.save_dir = self.db.save_dir
            self.viewer_data = self.db.load_viewer_data()
            self.sent_image_count = len(self.viewer_data)
            # 去重索引按新 folder 的 viewer_data 重建
            self.viewer_keys = {
                _viewer_item_key(it) for it in self.viewer_data if isinstance(it, dict)
            }
            # 换 folder 后待下载队列也要跟着换成新 folder 的 ids_data.json 镜像。
            try:
                self.pending_ids = set(str(x) for x in (self.db.load_ids_data() or []))
            except Exception:
                self.pending_ids = set()
            self.pending_dirty = False
            # 页级失败按 folder+page 跨日累积（横幅需要保留前几天的记录）：
            # 只把新 folder 在「上次运行」持久化、本进程还没见过的失败页合并进来，
            # 不覆盖内存里已有的条目。图级 failed_ids 同理已带 folder，无需重扫。
            try:
                seen = {
                    (e.get("folder"), e.get("page")) for e in self.failed_pages
                }
                for e in deck_db.failed_page_load_scope(
                    new_folder, self.tag_query or "", self.tag_source or "danbooru"
                ):
                    key = (e.get("folder"), e.get("page"))
                    if key not in seen:
                        self.failed_pages.append(e)
                        seen.add(key)
            except Exception:
                pass


class JobRegistry:
    MAX_CONCURRENT = 1  # 默认 1（与旧行为一致）。改 2+ 即可允许并发下载不同目录，但
                        # Danbooru 有限流，并发 = QPS 翻倍，要注意撞风控的可能。

    def __init__(self):
        self._jobs: dict = {}
        self._lock = threading.RLock()

    def list_active(self):
        with self._lock:
            return [
                j for j in self._jobs.values()
                if j.is_running or (j.thread is not None and j.thread.is_alive())
            ]

    def can_start(self):
        return len(self.list_active()) < self.MAX_CONCURRENT

    def primary(self):
        """优先返回正在跑的 job；若都没活跃，回退到最近 started_at 的（保留 30 秒，
        让前端 syncStatus 能拉到最后一批 new_logs / new_images / "任务完成" 提示）。"""
        with self._lock:
            active = [
                j for j in self._jobs.values()
                if j.is_running or (j.thread is not None and j.thread.is_alive())
            ]
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

# ───────────────────────── 起 job 的「检查 + 注册」闸门 ─────────────────────────
# 有两条路径会起 job：用户点「开始」(start_scraper) 与空闲下载调度器
# (_idle_start_round)。两边都是「先看有没有并发位、再构造 job、最后注册」，
# 而检查与注册之间隔着构造 job（new DanbooruData：建目录 / 读 viewer_data）与
# 挑目标（查 deck.db / stat 磁盘目录）等耗时操作 —— 对方完全可能在这段缝里
# 各注册一个，于是 MAX_CONCURRENT = 1 的约束被突破，两个任务同时在跑。
# 最坏情况：两个 job 落在**同一个 save_dir**（用户手动选了空闲轮次正在下的那天），
# 并发写同一目录、抢同一批 ID。
_START_GATE = threading.Lock()


def _register_job(job) -> bool:
    """原子地「占位 + 注册」。返回 False = 并发位已被占（调用方决定让位重试还是报错）。

    ⚠ 调用前提：job.is_running 必须已经置 True —— can_start() 靠它认这个 job
    （JobRegistry.list_active 判的是 is_running / thread 存活），否则闸门内的检查
    看不到刚刚注册的对方，等于没加锁。
    """
    with _START_GATE:
        if not jobs.can_start():
            return False
        jobs.add(job)
        return True


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


def _make_job(target_folder, mode, filter_tags, label=None, tag_source="danbooru", download_concurrency=4, save_dir=None, local_only=False, skip_logged=True, retry_only=False):
    db = DanbooruData(target_folder)
    # 断盘保护：传 save_dir 覆盖 db 默认值（覆盖路径必须在 library_roots 里，否则后续
    # stage 1 的 os.path.isdir 检查会兜底拒绝）。覆盖后同步 base_dir 让 ids_data.json
    # / log.json / viewer_data.json 全部落到指定目录。
    if save_dir:
        db.save_dir = save_dir
        db.base_dir = os.path.dirname(save_dir)
    viewer_data = db.load_viewer_data()
    job = DownloadJob(
        job_id=uuid.uuid4().hex[:8],
        target_folder=target_folder,
        mode=mode,
        label=label or f"{mode} · {target_folder}",
        save_dir=db.save_dir,
        db=db,
        viewer_data=viewer_data,
        filter_tags=list(filter_tags or []),
        tag_source=tag_source,
        started_at=datetime.datetime.now(),
        download_concurrency=max(1, min(int(download_concurrency or 4), 16)),
        local_only=local_only,
        skip_logged=skip_logged,
        retry_only=retry_only,
    )
    job.sent_image_count = len(viewer_data)
    return job


GALLERY_MEDIA_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif",
    ".zip", ".mp4", ".webm", ".mov", ".mkv", ".avi",
}


def count_gallery_media_files(folder: Path) -> int:
    if not folder.exists() or not folder.is_dir():
        return 0
    count = 0
    for media_path in folder.iterdir():
        if not media_path.is_file():
            continue
        suffix = media_path.suffix.lower()
        if suffix not in GALLERY_MEDIA_EXTENSIONS:
            continue
        if suffix == ".gif" and media_path.with_suffix(".zip").exists():
            continue
        count += 1
    return count


def _pending_counts_map() -> dict:
    """日历 pending 角标：一次 GROUP BY 取全库 {(lib_id, folder): pending+failed 行数}。
    权威在 deck.db dl_queue（入队即提交）；ids_data.json 只是派生镜像，不再逐文件解析。"""
    try:
        return deck_db.queue_counts_grouped()
    except Exception as e:
        print(f"读取待下载计数失败（按 0 计）: {e}")
        return {}


def get_available_date_folder_details():
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    folders = {}
    # pending 计数走 DB 一次取完（与盘是否在线无关），不再逐目录解析 ids_data.json
    pending_map = _pending_counts_map()
    for root in get_library_roots():
        base = root["path"]
        lib_id = root["id"]
        if not base.exists():
            # 离线根：日历仍以「磁盘可见」为准（避免冒出打不开的幽灵日期），
            # DB 里的 queue 行等盘接回后自然出现
            continue
        # 懒扫根：只枚举日期目录名，不数图、不数媒体文件（pending 走 DB 仍然统计）。
        # 机械盘 / 外置盘 / 网盘上有几百个日期文件夹时，逐目录 iterdir 几次就卡死。
        # 图数留 None，让前端走"有图片但未统计"分支；点进具体日期时由
        # build_local_image_library 单日扫描（单目录 IO 很快）补上。
        is_lazy = bool(root.get("lazy_scan", False))
        for item in base.iterdir():
            if not item.is_dir() or not date_pattern.match(item.name):
                continue
            try:
                datetime.datetime.strptime(item.name, "%Y-%m-%d")
            except ValueError:
                continue
            rec = folders.setdefault(item.name, {
                "date": item.name,
                "image_count": None if is_lazy else 0,
                "source_count": 0,
                "has_images": True if is_lazy else False,
                "pending_ids": 0,
                "count_known": not is_lazy,
                # 内部标记：是否有懒扫 root 贡献该日期（不落前端）
                "_has_lazy_source": is_lazy,
            })
            rec["source_count"] += 1
            # DB 队列计数不碰磁盘，懒扫根也能拿到角标
            rec["pending_ids"] += pending_map.get((lib_id, item.name), 0)
            if is_lazy:
                # 不调 count_gallery_media_files，直接进入下一目录
                rec["_has_lazy_source"] = True
                continue
            rec["image_count"] += count_gallery_media_files(item)
            rec["has_images"] = rec["image_count"] > 0
    # 跨 root 混合的日期：只要有一个懒扫 root（机械盘/外置盘）贡献，图数就是未知，
    # 不能被另一个非懒扫 root 的 0 计数覆盖成「空文件夹」——否则外置盘有上千张图、
    # 本地只剩空占位目录时，日历会把该日期画成空（实测 19 个日期中招）。
    # 统一回归懒扫语义：有图（未统计），点进去由 build_local_image_library 单日扫描补数。
    for rec in folders.values():
        if rec.pop("_has_lazy_source", False):
            rec["image_count"] = None
            rec["count_known"] = False
            rec["has_images"] = True
    return sorted(folders.values(), key=lambda x: x["date"], reverse=True)


def get_available_date_folders():
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    folders = set()
    for root in get_library_roots():
        base = root["path"]
        if not base.exists():
            continue
        for item in base.iterdir():
            if not item.is_dir() or not date_pattern.match(item.name):
                continue
            try:
                datetime.datetime.strptime(item.name, "%Y-%m-%d")
            except ValueError:
                continue
            folders.add(item.name)
    return sorted(folders, reverse=True)

def get_available_tag_folders():
    """扫描所有图库根目录下以 tag_ 开头的文件夹，按 folder 虚拟合并。"""
    by_folder = {}
    for root in get_library_roots():
        base = root["path"]
        if not base.exists():
            continue
        for item in base.iterdir():
            if not item.is_dir() or not is_tag_folder(item.name):
                continue
            rec = by_folder.setdefault(item.name, {
                "folder": item.name,
                "display": tag_folder_display(item.name),
                "source_count": 0,
            })
            rec["source_count"] += 1
    folders = list(by_folder.values())
    folders.sort(key=lambda x: x["folder"].lower())
    return folders

def _create_empty_date_folder(date_str: str):
    """用户主动选中一个还没有文件夹的日期 → 在 default 根目录下建空文件夹。
    路径越界 / 非法日期 / 写盘失败时返回 None，上层继续回退到 today 行为。
    这样前端选了什么日期就进入什么日期，不会被 resolve_selected_date 偷偷改写成 today。"""
    try:
        datetime.datetime.strptime(date_str, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None
    roots = get_library_roots()
    if not roots:
        return None
    base_root = Path(roots[0]["path"]).resolve()
    target_dir = (base_root / date_str).resolve()
    try:
        # 防路径穿越：解析后必须仍在 default 根目录下
        target_dir.relative_to(base_root)
    except ValueError:
        return None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir
    except OSError:
        return None


def resolve_selected_date(requested_date=None):
    """兼容日期 (YYYY-MM-DD) 和 tag 文件夹 (tag_xxx)：
    - 日期：按已有列表过滤；未命中时若格式合法则建空文件夹并放行（用户主动选择优先于回退到 today）
    - tag 文件夹：只要 hot_pic/<name> 存在就接受
    - 其它情况：fallback 到 today / 列表首项"""
    available_dates = get_available_date_folders()
    if requested_date:
        # 1) tag 文件夹直接放行（只要磁盘上有）
        if is_tag_folder(requested_date):
            if any((root["path"] / requested_date).is_dir() for root in get_library_roots()):
                return requested_date, available_dates
        # 2) 日期：照旧校验 + 命中已有
        try:
            datetime.datetime.strptime(requested_date, "%Y-%m-%d")
            if requested_date in available_dates:
                return requested_date, available_dates
            # 用户主动选中一个还没有文件夹的日期 → 建空文件夹并进入，
            # 避免后端回退到 today 之后前端又被「被改写的 selected_date」拽回去。
            if _create_empty_date_folder(requested_date) is not None:
                # 重新扫盘，让响应里的 available_dates / available_date_folders 包含新建的文件夹
                return requested_date, get_available_date_folders()
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
    known_paths = set()
    seen_identity = set()

    for root in get_library_roots():
        root_id = root["id"]
        root_label = root["label"]
        root_path = str(root["path"])
        day_folder = resolved_date
        current_day_dir = root["path"] / resolved_date
        viewer_file = current_day_dir / "viewer_data.json"
        # viewer 元数据以 deck.db 为权威：先按镜像指纹吸收外部直写，再从 DB 按 id 正序
        # 取出（下方 reversed → 最新在前，与旧 JSON 列表顺序一致）。离线根目录（盘没接）
        # 不吐 DB 行——旧版同样读不到盘上镜像，避免刷出一堆打不开的死链卡片。
        if not current_day_dir.exists():
            items = []
        else:
            try:
                deck_db.reconcile_viewer(root_id, day_folder, str(viewer_file))
                items = deck_db.viewer_load_entries(root_id, day_folder)
            except Exception as _e:
                print(f"[deck_db] viewer DB 读取失败，回退 JSON {root_id}/{day_folder}: {_e}")
                items = (
                    dedup_viewer_data(load_json(str(viewer_file), []))
                    if viewer_file.exists() else []
                )
        for item in reversed(items):
            filename = item.get("filename")
            web_url = item.get("web_url")
            if not filename:
                continue
            image_path = (current_day_dir / filename).resolve()
            fallback_raw = item.get("local_path") or ""
            if fallback_raw:
                fallback_path = Path(fallback_raw)
                if not fallback_path.is_absolute():
                    fallback_path = (BASE_DIR / fallback_path).resolve()
                if not image_path.exists() and fallback_path.exists():
                    image_path = fallback_path.resolve()
            post_url = item.get("post_url") or "#"
            identity = post_url if post_url and post_url != "#" else str(image_path).lower()
            if identity in seen_identity:
                continue
            seen_identity.add(identity)
            if not web_url:
                web_url = f"/images/{day_folder}/{filename}"
            local_key = str(image_path).lower()
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
                "local_path": str(image_path),
                "post_url": post_url,
                "web_url": web_url,
                "tags": tags_dict,
                "characters": translated_chars,
                "score": item.get("score", 0) or 0,
                "fav_count": item.get("fav_count", 0) or 0,
                "library_id": root_id,
                "library_label": root_label,
                "library_root": root_path,
                "date": day_folder,
                "source_dir": str(current_day_dir),
            })

        if not current_day_dir.exists():
            continue
        for image_path in sorted(current_day_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not image_path.is_file():
                continue
            suffix = image_path.suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif", ".zip", ".mp4", ".webm", ".mov", ".mkv", ".avi"}:
                continue
            # 跳过已有对应 zip 的 gif（属于已转换的动画），避免重复显示
            if suffix == ".gif" and image_path.with_suffix(".zip").exists():
                continue
            local_key = str(image_path.resolve()).lower()
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
                "fav_count": 0,
                "library_id": root_id,
                "library_label": root_label,
                "library_root": root_path,
                "date": current_day_dir.name,
                "source_dir": str(current_day_dir),
            })

    return library


def _expand_search_tokens(q: str, kind: str):
    """把搜索框输入归一成 deck_db.viewer_search 的 token 组（组间 AND、组内 OR）。

    含中文时走离线 translator 字典展开成候选角色 tag（中文名可能对应多个 tag）；
    返回 (token_groups, expanded_tags)，expanded_tags 供前端提示「已展开为哪些 tag」。
    """
    raw = (q or "").strip()
    if not raw:
        return [], []
    has_cjk = bool(re.search(r"[一-鿿]", raw))
    ascii_token = re.sub(r"\s+", "_", raw.lower()).strip("_")

    if has_cjk and kind in ("auto", "character"):
        try:
            hits = translator.search_translation_entries(raw, limit=50)
        except Exception:
            hits = []
        # 描述里「提到」该词的条目噪声大（如 ironmouse 简介提到初音），
        # 中文名直接包含查询词的候选优先；一个都没有时才退回全文匹配。
        name_hits = [h for h in hits if raw in (h.get("chinese_name") or "")]
        chosen = name_hits or hits
        expanded = list(dict.fromkeys(h["tag"] for h in chosen if h.get("tag")))[:12]
        if expanded:
            group = expanded[:]
            # 原文里若带英文/数字片段（如「初音 miku」），也作为 OR 候选
            if re.search(r"[a-z0-9]", ascii_token) and ascii_token not in group:
                group.insert(0, ascii_token)
            return [group], expanded

    # 逗号分隔多个词 = 组间 AND（如 "hatsune_miku, kagamine_rin"）；
    # 词内空格按下划线归一（Danbooru tag 本来就用下划线，"hatsune miku" → hatsune_miku）。
    parts = [p for p in re.split(r"[,，]+", raw) if p.strip()]
    groups = []
    for part in parts:
        token = re.sub(r"\s+", "_", part.lower()).strip("_")
        # 纯中文且没有翻译命中时，它不可能是 deck.db 里的 tag —— 别制造必空的全表扫描
        if token and not re.fullmatch(r"[一-鿿_]+", token):
            groups.append([token])
    return groups, []


def _search_rows_to_items(rows, roots_by_id):
    """把 viewer_search 的原始行归一化成与 build_local_image_library 相同的卡片结构。

    行可能跨多个库 / 多个日期；文件已不在盘上（DB 行残留）的死链直接跳过。
    """
    items = []
    seen_identity = set()
    for row in rows:
        entry = deck_db._row_to_entry(row)
        lib_id = row["library_id"]
        root = roots_by_id.get(lib_id)
        if root is None:
            continue
        folder = row["folder"]
        filename = entry.get("filename")
        if not filename:
            continue
        day_dir = root["path"] / folder
        image_path = day_dir / filename
        if not image_path.exists():
            fallback_raw = entry.get("local_path") or ""
            if fallback_raw:
                fallback_path = Path(fallback_raw)
                if not fallback_path.is_absolute():
                    fallback_path = BASE_DIR / fallback_path
                if fallback_path.exists():
                    image_path = fallback_path.resolve()
            if not image_path.exists():
                continue
        image_path = image_path.resolve()
        post_url = entry.get("post_url") or "#"
        identity = post_url if post_url and post_url != "#" else str(image_path).lower()
        if identity in seen_identity:
            continue
        seen_identity.add(identity)

        web_url = entry.get("web_url") or f"/images/{folder}/{filename}"
        tags_dict = entry.get("tags") or {}
        translated_chars = []
        for c in (tags_dict.get("tag_string_character", "") or "").split():
            info = translator.get_tag_info(c)
            chinese_name = info.get("chinese_name") or translator._format_tag(c)
            hint = info.get("source_hint", "")
            alias = translator.get_source_hint_alias(hint) if hint else ""
            meta = chinese_name
            if hint:
                meta += f" [{hint}]"
            if alias:
                meta += f" [{alias}]"
            translated_chars.append(meta)

        items.append({
            "artist": entry.get("artist") or "未知",
            "filename": filename,
            "local_path": str(image_path),
            "post_url": post_url,
            "web_url": web_url,
            "tags": tags_dict,
            "characters": translated_chars,
            "score": entry.get("score", 0) or 0,
            "fav_count": entry.get("fav_count", 0) or 0,
            "library_id": lib_id,
            "library_label": root["label"],
            "library_root": str(root["path"]),
            "date": folder,
            "source_dir": str(day_dir),
        })
    return items


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

# 注意：/images 不再是只挂 HOT_PIC_DIR 的 StaticFiles —— 移动硬盘/外置 root 上的
# 视频（2676+ 个）和收藏页看图都要靠它跨 root 取文件。见下方 api_library_media 路由。
app.mount("/static", StaticFiles(directory=str(RESOURCE_DIR / "static")), name="static")
app.mount("/mosaic", mosaic_editor_app)


# ==========================================
# 缩略图接口（解决收藏/抓图页加载原图导致的卡顿）
# 原图常是数 MB ~ 几十 MB，但卡片只显示 ~200px，缩到磁盘缓存后体积可降到 30KB 量级。
# ==========================================
_THUMB_CACHE_DIR = HOT_PIC_DIR / ".thumb_cache"
_THUMB_VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif",
                     ".mp4", ".webm", ".avi", ".mov", ".mkv"}
_THUMB_VIDEO_EXTS = {".mp4", ".webm", ".avi", ".mov", ".mkv"}
_THUMB_ALLOWED_SIZES = (200, 400, 800)
_THUMB_LOCK = threading.Lock()


def _generate_video_thumbnail(src_path: Path, dst_path: Path, max_dim: int) -> bool:
    """取视频首帧 -> Pillow 缩放 -> 存 JPEG。
    拆成两步：ffmpeg 只负责"解出 1 帧"（最简单最稳的调用，避开不同版本
    ffmpeg 在 scale filter / force_original_aspect_ratio 上的语法差异）；
    缩放和 JPEG 编码统一交给 Pillow，跟普通图片缩略图走同一条路径。
    ffmpeg 不可用或解码失败返回 False，由前端走 NO PREVIEW 占位。"""
    import tempfile
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        result = subprocess.run(
            [
                "ffmpeg", "-y",
                "-i", str(src_path),
                "-vframes", "1",
                "-an",                  # 不解音频，省时间
                "-f", "image2",         # 显式 image2 muxer
                str(tmp_path),
            ],
            capture_output=True, text=True, check=False, timeout=30,
        )
        if result.returncode != 0 or not tmp_path.exists() or tmp_path.stat().st_size == 0:
            err = (result.stderr or "").strip().splitlines()
            tail = err[-3:] if err else ["(no stderr)"]
            print(f"[thumb] ffmpeg 首帧失败 {src_path.name}: {' | '.join(tail)[:300]}")
            return False
        from PIL import Image
        with Image.open(tmp_path) as im:
            im = im.convert("RGB") if im.mode != "RGB" else im.copy()
            im.thumbnail((max_dim, max_dim), Image.LANCZOS)
            dst_path.parent.mkdir(parents=True, exist_ok=True)
            im.save(dst_path, "JPEG", quality=80, optimize=True, progressive=True)
        return True
    except FileNotFoundError:
        print(f"[thumb] ffmpeg 不在 PATH，跳过视频缩略图 {src_path.name}（请参考教程安装 ffmpeg）")
        return False
    except subprocess.TimeoutExpired:
        print(f"[thumb] ffmpeg 取首帧超时 {src_path.name}")
        return False
    except Exception as e:
        print(f"[thumb] 视频缩略图生成异常 {src_path.name}: {e}")
        return False
    finally:
        if tmp_path is not None:
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _generate_thumbnail(src_path: Path, dst_path: Path, max_dim: int) -> bool:
    """生成 JPEG 缩略图到 dst_path。返回是否成功。Pillow 是 thread-safe 的，
    但同一文件并发生成会浪费 CPU——外层 _THUMB_LOCK 串行保护。
    视频走 ffmpeg 取首帧（见 _generate_video_thumbnail）。"""
    ext = src_path.suffix.lower()
    if ext in _THUMB_VIDEO_EXTS:
        return _generate_video_thumbnail(src_path, dst_path, max_dim)
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


def _resolve_media_in_library(date_str: str, filename: str):
    """跨所有在线 library root 查找 <root>/<date_str>/<filename>，返回 (Path, root)。
    外置盘（library_roots.json 里配的 D:/hot_danbooru 等）上的视频播放与缩略图
    全靠它：旧实现把 /images 静态目录和 /thumb 查找都钉死在 HOT_PIC_DIR，
    移动硬盘上的媒体一律 404。离线 root 自然落到 is_file()=False，无需特殊处理。

    严格防穿越：两段都只允许单段路径（不含 / \\ ..），resolve 后必须仍在该 root 内。
    """
    if not date_str or not filename:
        return None, None
    if ("/" in date_str or "\\" in date_str or date_str in (".", "..")
            or "/" in filename or "\\" in filename or filename in (".", "..")):
        return None, None
    for root in get_library_roots():
        try:
            base = root["path"].resolve()
            candidate = (base / date_str / filename).resolve()
            candidate.relative_to(base)
        except (OSError, ValueError):
            continue
        try:
            if candidate.is_file():
                return candidate, root
        except OSError:
            continue
    return None, None


@app.get("/images/{date_str}/{filename}")
async def api_library_media(date_str: str, filename: str):
    """跨 library root 的媒体文件服务（替代只挂 hot_pic 的 StaticFiles）。

    前端大图/视频查看器对视频固定走 http://127.0.0.1:18765/images/<日期>/<文件>
    （要 byte-range seek，避开 local:// 的媒体限制），收藏页看图也走这里。
    FileResponse 原生支持 Range / 条件请求，行为与旧 StaticFiles 一致。
    同一 date+filename 按 roots 顺序命中第一个（default 优先），文件名是 md5 哈希，
    跨盘同名即同内容，去重语义不受影响。
    """
    path, _root = _resolve_media_in_library(date_str, filename)
    if path is None:
        return PlainTextResponse("not found", status_code=404)
    return FileResponse(str(path))


@app.get("/thumb/{date_str}/{filename}")
def api_thumbnail(date_str: str, filename: str, w: int = 400):
    """返回磁盘缓存的 JPEG 缩略图。非图片格式返回 404 让前端 fallback 到占位符。"""
    if w not in _THUMB_ALLOWED_SIZES:
        w = 400  # 限定档位，避免无限大小占满磁盘

    # 跨 library root 找源文件（外置盘视频首帧/GIF 首帧同样走这里）。
    # 缩略图缓存仍按 <w>/<date>/<filename>.jpg 平铺：文件名是内容 md5，跨盘同名即同内容。
    src_path, _src_root = _resolve_media_in_library(date_str, filename)
    if src_path is None:
        # 穿越/不存在统一 404（旧实现对穿越回 400，但前端处理两者完全相同）
        return PlainTextResponse("not found", status_code=404)

    ext = src_path.suffix.lower()
    if ext not in _THUMB_VALID_EXTS:
        return PlainTextResponse("not an image", status_code=404)

    cache_path = _THUMB_CACHE_DIR / str(w) / date_str / (filename + ".jpg")

    # 命中缓存且不比源文件旧 -> 直接返回
    # 注意：0 字节文件视为无效（之前失败尝试可能留下的残骸），不命中
    if cache_path.exists():
        try:
            if (cache_path.stat().st_size > 0
                    and cache_path.stat().st_mtime >= src_path.stat().st_mtime):
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
                and cache_path.stat().st_size > 0
                and cache_path.stat().st_mtime >= src_path.stat().st_mtime):
            if not _generate_thumbnail(src_path, cache_path, w):
                return PlainTextResponse("thumb error", status_code=500)

    return FileResponse(
        cache_path,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _purge_thumb_cache_dirs() -> dict:
    """清空本地磁盘上的两个缩略图缓存：.thumb_cache（/thumb/ 端点）和
    .browse_thumb_cache（/api/proxy_thumb）。返回各类删除的文件数。
    路径必须在 _THUMB_CACHE_DIR / _BROWSE_THUMB_CACHE_DIR 之内，防越界。"""
    summary = {}
    for label, base in [
        ("thumb_cache", _THUMB_CACHE_DIR),
        ("browse_thumb_cache", _BROWSE_THUMB_CACHE_DIR),
    ]:
        deleted = 0
        try:
            base_resolved = base.resolve()
            for path in base_resolved.rglob("*"):
                try:
                    # 二次校验：解析后必须仍在 base 之下
                    path.resolve().relative_to(base_resolved)
                except ValueError:
                    continue
                if path.is_file():
                    try:
                        path.unlink()
                        deleted += 1
                    except OSError:
                        pass
            # 顺手清掉空目录
            for path in sorted(base_resolved.rglob("*"), reverse=True):
                if path.is_dir():
                    try:
                        path.rmdir()
                    except OSError:
                        pass
        except FileNotFoundError:
            pass
        summary[label] = deleted
    return summary


@app.delete("/api/thumb_cache")
def api_thumb_cache_clear():
    """清空 .thumb_cache 和 .browse_thumb_cache 两个磁盘缓存目录。
    用途：之前 ffmpeg 失败留下的残骸、MD5 旧格式残留、或单纯想腾空间。"""
    with _THUMB_LOCK:
        with _BROWSE_THUMB_LOCK:
            summary = _purge_thumb_cache_dirs()
    total = sum(summary.values())
    return {"ok": True, "deleted": total, "by_dir": summary}


class StartRequest(BaseModel):
    start_page: int
    end_page: int
    tags: str
    mode: str = "rank"
    # 合法值：
    #   rank | collect_ids | download_ids | popular | popular_collect_ids | popular_download_ids
    #   | popular_range | popular_range_collect_ids | popular_range_download_ids | tags
    #   | popular_recover | recover_popular
    # popular_*/popular_range_* 为日期热门两阶段子动作（先收 ID 后下载），与
    # 排行榜 rank 的两阶段语义对齐。popular_recover 为热门补全/补齐子操作（popular form
    # 第三档），按文件存在性补全热门页范围内丢失的图片；recover_popular 为其 legacy alias。
    target_date: str = ""  # popular 模式用，可指定日期
    start_date: str = ""   # popular_range
    end_date: str = ""     # popular_range
    ids: list = []         # download_ids 模式可选：内联 IDs；非空则覆盖目标日期的 ids_data.json
    tag_query: str = ""    # tags 模式：多 tag 查询串，如 "hatsune_miku rating:safe"
    tag_source: str = "danbooru"  # tags 模式：danbooru | gelbooru
    pages: list = []       # 定向重试：非空时只抓这些页码（覆盖 start_page~end_page 区间）
    download_concurrency: int = 4  # 单任务内图片下载并发度（rank→download_ids 阶段也用它）
    force_local: bool = False  # popular_recover 用：盘没接时强制写到 HOT_PIC_DIR 本地临时目录，完成后手动同步到原盘
    skip_logged: bool = True  # rank·按ID下载 专用：False 时无视 log.json 是否已记录、强制重下（补齐 50 页场景）
                             # 其他 download_ids 入口不传此字段时走默认 True（保持原行为）
    retry_only: bool = False  # download_ids 重试失败图专用：True 时只下 ids 这批，不消费 folder backlog
                              # False（默认）保持原行为：把 inline ids 写进 queue 然后下整个 folder 的 pending
                              # 之前的设计会让"重试 9 张失败"顺手把 folder 里几百个积压 id 全跑一遍

class OpenLocalRequest(BaseModel):
    local_path: str

class TranslationImportRequest(BaseModel):
    translations: dict

class ConvertLocalZipRequest(BaseModel):
    local_path: str


class ConvertAllZipsRequest(BaseModel):
    date: str
    overwrite: bool = False          # True 时强制覆盖已存在的 .gif


class RefreshVisibleRequest(BaseModel):
    date: str
    filenames: list[str] = []
    local_paths: list[str] = []


class MergeViewerDataRequest(BaseModel):
    """把一个 library root 下某日期的 viewer_data.json 增量合并到另一个 root 的 viewer_data.json。
    典型场景：本地 hot_pic 下载 → 用户把文件搬到移动盘 → 把本地的 viewer_data 同步到移动盘，
    让移动盘侧的画廊/缩略图能显示新搬来的图。"""
    date: str
    source_root: str = ""  # 源 root 的绝对路径；空 = 默认 hot_pic
    target_root: str       # 目标 root 的绝对路径（必填）
    dry_run: bool = False  # True = 只返回将合并的条目数，不写盘
    move_files: bool = False  # 将源日期目录中的媒体文件迁移到目标日期目录


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
    library_id: str = "default"


class ImageFavoriteToggleRequest(BaseModel):
    item: ImageFavoriteItem


class ImageFavoriteRemoveRequest(BaseModel):
    key: str

# ==========================================
# 3. 核心爬虫逻辑：所有 grabber 都按 job 跑，不再读模块全局
# ==========================================

def _note_failed_page(job, page=None):
    """页级抓取失败的统一记账：记 failed_pages → 累计连续失败数 → 达阈值 auto-pause。

    抽出来是因为有两个入口都要记账：`_fetch_page_or_pause` 里真打了请求并撞上永久
    错误的这次，以及「窗口缓存命中同窗口已失败」的后续页（那次没打请求，但页确实
    也失败了，不记的话它会被前端算成「成功 X 页」）。

    page=None 时只累计连续失败数、不记 failed_pages —— 与旧行为的条件分支一致。
    返回 False 表示任务已被用户停止，调用方必须立刻收尾。
    """
    if page is not None:
        job.record_failed_page(page)
    job.consecutive_page_failures += 1
    if job.consecutive_page_failures >= CONSECUTIVE_PAGE_FAILURE_THRESHOLD:
        job.append_log(
            f"⚠ 连续 {job.consecutive_page_failures} 张/页永久失败，"
            f"已自动暂停任务等待用户决策。"
        )
        job.play_event.clear()
        job.play_event.wait()
        if not job.is_running:
            return False
        job.consecutive_page_failures = 0
    return True


def _fetch_page_or_pause(fetch_fn, job, label, page=None, on_permanent_error=None):
    """对「页面列表抓取」的统一容错层。区分两类失败：

    - 永久错误（400 非法查询 / 403 / 404，PermanentHTTPError）：等待无意义，
      立即记 failed_pages 并返回 [] 跳过本页。连续 CONSECUTIVE_PAGE_FAILURE_THRESHOLD
      页永久失败时 auto-pause 一次让用户决策。
    - 瞬时错误（429 / 5xx / 超时 / 断网 —— http_client 自己的 5 次重试也耗尽了）：
      先在本轮内静默重试 PAGE_FETCH_SILENT_RETRIES 次（PAGE_FETCH_BACKOFF 退避），
      吸收几秒级的抖动；仍失败则「原地暂停」——不记失败页、不跳页、不动连续计数，
      play_event 挂起等用户。用户等风控冷却后点「继续」会重新抓同一页（外层
      while 再走一整轮尝试），点「停止」则收尾返回 None。这样 1-50 页任务在第 10
      页撞限流时不会牺牲 10,11,12…，等多久都行，恢复后零遗漏。

    on_permanent_error：命中永久错误时的回调，让调用方把「这一整块都坏了」记下来
    （窗口缓存用）—— 少了它，热门一个 50 页的窗口失败会连打 50 次注定失败的请求、
    连原地暂停 50 次。成功返回前还会走一次 _rest_after_list_request（按**请求**计数的
    防风控休息，不再按页计 —— 一个请求现在覆盖 10~50 页）。

    返回值约定：
      - list：成功（fetch_fn 返回值）或永久错误跳过后的 []。
      - None：任务已停（原地暂停 / 永久错 auto-pause 后用户点停止），grabber 收尾。
    """
    total_attempts = PAGE_FETCH_SILENT_RETRIES + 1  # 1 初次 + N 静默重试
    while True:  # 瞬时失败原地暂停后，用户点继续 → 重新走一整轮尝试
        last_err = None
        for attempt in range(1, total_attempts + 1):
            if attempt > 1:
                delay = PAGE_FETCH_BACKOFF[attempt - 2]
                job.append_log(
                    f"{label} 抓取失败（{last_err}），{delay} 秒后第 {attempt - 1}/{PAGE_FETCH_SILENT_RETRIES} 次重试..."
                )
                # 退避 sleep 走 play_event 兼容暂停：暂停时不真正数 sleep，由用户「继续/停止」决定
                slept = 0
                while slept < delay:
                    if not job.is_running:
                        return None
                    job.play_event.wait()
                    if not job.is_running:
                        return None
                    sleep(1)
                    slept += 1
            try:
                result = fetch_fn()
            except danbooru_api.PermanentHTTPError as e:
                # 400（非法 tag 查询）/404/403 等永久错误：重试纯属浪费时间，
                # 直接记失败页跳过；连续多页都永久失败时 auto-pause 让用户决策。
                job.append_log(
                    f"{label} 永久失败（HTTP {e.status_code}），不再重试：{e.url or e}"
                )
                if on_permanent_error is not None:
                    # 让调用方把「这一整块都坏了」记下来（窗口缓存用）。少了这个回调，
                    # 同窗口的后续页会各发一次注定失败的请求、各原地暂停一次 ——
                    # 热门一个窗口 50 页就是 50 次暂停。
                    on_permanent_error()
                if not _note_failed_page(job, page):
                    return None
                return []
            except Exception as e:
                last_err = e
                continue
            # 成功（初次成功 或 静默重试成功）：清零连续失败计数
            if attempt > 1:
                job.append_log(f"{label} 第 {attempt - 1} 次重试成功。")
            if job.consecutive_page_failures:
                job.consecutive_page_failures = 0
            # 该页曾失败、这次拿到了：内存 + deck.db 双清，失败横幅不再挂它
            if page is not None:
                job.clear_failed_page(page)
            _rest_after_list_request(job)
            return result

        # 瞬时错误：静默重试全部耗尽。不记失败页、不跳页 —— 原地暂停等用户冷却后继续。
        job.append_log(
            f"{label} 抓取失败：{last_err}（已重试 {PAGE_FETCH_SILENT_RETRIES} 次仍失败）。"
            f"疑似风控/超时，已在本页原地暂停等待（不跳过、不记失败）。"
        )
        job.append_log("点「继续」会重新抓取本页（可以等限流解除后再点，等多久都行）；点「停止」结束任务。")
        job.play_event.clear()
        job.play_event.wait()
        if not job.is_running:
            return None
        job.append_log(f"用户已继续，重新抓取 {label}…")


def _rest_after_list_request(job, n=5, rest_seconds=15):
    """每发出 N 个「列表请求」休息 rest_seconds 秒（防风控）。

    ⚠ 计数单位是**请求**，不是页。旧版按页计（每 5 页休息 15 秒）在「一页 = 一个请求」
    的年代与按请求计完全等价；现在一个请求覆盖 10 页（rank/tags）/ 50 页（popular），
    再按页计就是纯白等 —— 一次 1000 页的热门任务要休息 200 次 × 15s ≈ 50 分钟。
    按请求计则**请求速率与旧实现逐字相同**（都是 5 个请求 / 20 秒窗口），只是不再为
    已经省掉的请求付休息费。

    在 _fetch_page_or_pause 的「成功」分支里调用：所有列表抓取（rank / collect_ids /
    popular* / popular_range* / tags / recover）都从那里过一遍，一处覆盖全部。
    可被 is_running / play_event（暂停）打断，风格与 popular_range 跨日 throttle 一致。
    """
    job.list_request_count += 1
    if job.list_request_count % n != 0:
        return
    job.append_log(
        f"已发出 {job.list_request_count} 个列表请求，休息 {rest_seconds} 秒防风控（可暂停/停止打断）..."
    )
    slept = 0
    while slept < rest_seconds:
        if not job.is_running:
            break
        job.play_event.wait()
        sleep(1)
        slept += 1
    if job.is_running:
        job.append_log("休息结束，继续抓取。")


# ---------------- 列表页「窗口缓存」：一次请求拿多页 ID ----------------
# Danbooru 没有多页语法（实测 page=1,2,3 被当成 page=1），但 limit 可以开到
# danbooru_api.LIST_LIMIT_MAX —— 实测「大 limit」与「多页顺序拼接」**逐位相同**。
# 于是「第 N 页的 posts」完全可以从一块更大的窗口里切出来，同窗口内的后续页零请求。
#
# 约束：offset 只能是 (page-1) × limit，不能任意指定起点。所以窗口起点必须落在
# 「页网格」上：块大小取端点上限（rank/tags 200 = 10 页，popular 1000 = 50 页），
# 窗口首页 = 1 + k×bpp，正好对应 offset 0 / limit / 2×limit…
#
# 为什么不把页循环直接改成块循环：页游标（deck.db page_cursor）、failed_pages（页级）、
# 前端「N/M 页」进度全按「一页 = LIST_PAGE_SIZE 张」记账。把缓存压在这一层，
# 那些语义一行都不用动，四个 mode 的循环体也保持原样。
STOPPED = object()  # 「任务被停止」哨兵 —— 区别于「这一页本身是空的」

# 窗口请求的响应体比单页大得多（1000 条 × 46 字段 ≈ 1MB），实测热门 limit=1000
# 要 6~10 秒。老的单页默认 timeout=20 太紧，这里单独放宽。
LIST_WINDOW_TIMEOUT = 45


def _window_geometry(source, page_num):
    """第 page_num 页落在哪个服务端窗口。返回 (窗口首页, 窗口覆盖页数, 服务端 page 参数)。

    三个返回值都必要，第三个是最容易搞错的那个 —— Danbooru 的 offset 恒为
    `(page-1) × limit`，所以「窗口首条帖子的 offset」必须用**窗口自己的 limit**
    重新换算成页码，不能把 20 网格里的 first 直接当 page 参数发出去：

        offset      = (first-1) × LIST_PAGE_SIZE
        server_page = offset // limit + 1 = (first-1) // pages_per_window + 1

    例（popular，limit=1000，50 页一窗）：first=1→page=1、first=51→page=2、first=101→page=3。
    发成 page=51&limit=1000 = offset 50000，远超单日 3000 条上限 → **整窗返空**。
    """
    per_req = danbooru_api.LIST_LIMIT_MAX.get(source, danbooru_api.LIST_PAGE_SIZE)
    pages_per_window = max(1, per_req // danbooru_api.LIST_PAGE_SIZE)
    first = ((page_num - 1) // pages_per_window) * pages_per_window + 1
    server_page = (first - 1) // pages_per_window + 1
    return first, pages_per_window, server_page


def _slice_page(window_posts, page_offset_in_window):
    """从窗口里切出第 page_offset_in_window 页（0-based）的那 LIST_PAGE_SIZE 条。"""
    lo = page_offset_in_window * danbooru_api.LIST_PAGE_SIZE
    return window_posts[lo:lo + danbooru_api.LIST_PAGE_SIZE]


def _list_posts_for_page(job, source, page_num, fetch_fn, label, scope=None):
    """取第 page_num 页的 posts，走窗口缓存（同窗口内的后续页零请求）。

    fetch_fn(server_page, limit) -> list，具体打哪个端点由调用方给。⚠ 第一个参数是
    **_window_geometry 换算出的「limit 网格页码」**，不是 20 网格里的窗口首页
    （见 _window_geometry 的 docstring）。
    返回该页的 posts 列表（可能为空 = 这一页确实没有数据）；
    或 STOPPED，表示任务已被停止，调用方必须立刻收尾（语义同 _fetch_page_or_pause 的 None）。

    scope 只用来给缓存分域，传「同一 source 下会变、且变了结果就不同」的维度：
    popular 传 target_date（popular_range 逐日切目标，不隔离的话「第二天的第 1 页」
    会读到「第一天的第 1-50 页」那块缓存），tags 传 tag_query（同一 job 可能换词重试）。
    """
    first, pages_per_window, server_page = _window_geometry(source, page_num)
    last = first + pages_per_window - 1
    key = (source, scope)
    st = job.list_cache.get(key)

    # ⚠ 顺序有讲究：failed 必须排在 exhausted 前面判，而且要判 st["first"] == first。
    #   ① 一次永久错误（403/404/410）会把 failed 置上；若先判 exhausted，后面 9~49 页
    #      会被当成「已经翻到头了」静默返回空 —— 既不进 failed_pages 也不报错，
    #      前端会把这些页算进「成功 X 页」，失败横幅漏报。
    #   ② failed 是「这一块坏了」，只对同一块成立；跨到下一块要重新请求，
    #      不加 first 判断会把失败状态无限传染给后续所有块。
    if st is not None and st.get("first") == first and st.get("failed"):
        # 同一窗口此前已判永久失败：本页也按失败记账，但不再打请求、不再原地暂停一次
        # —— 否则热门一个 50 页的窗口失败会连暂停 50 次。
        job.append_log(
            f"{label} 第 {page_num} 页跳过：同窗口（第 {first}-{last} 页）此前已判失败"
        )
        if not _note_failed_page(job, page_num):
            return STOPPED
        return []

    # 这一块此前已确认翻到数据尽头：后面的页全空，一个请求都不必再发。
    # 热门单日上限 3000 条、排行榜 5699 条，请求页范围超过它时后面全是空页。
    # exhausted 是「数据到此为止」，对后面所有块都成立，所以不判 first。
    if st is not None and st.get("exhausted"):
        return []

    if st is None or st.get("first") != first:
        st = {"first": first, "posts": None, "failed": False, "exhausted": False}
        job.list_cache[key] = st

        def _mark_window_failed():
            st["failed"] = True

        limit = pages_per_window * danbooru_api.LIST_PAGE_SIZE
        # server_page 的来历见 _window_geometry 的 docstring —— 这里发错的代价是
        # 「first > 1 的窗口整块返空」，而且会被误标 exhausted、后续页静默跳过。
        job.append_log(
            f"{label} 批量获取第 {first}-{last} 页"
            f"（1 个请求，page={server_page}&limit={limit}，host={danbooru_api.get_host()}）"
        )
        got = _fetch_page_or_pause(
            lambda: fetch_fn(server_page, limit), job,
            label=f"{label} 第 {first}-{last} 页", page=page_num,
            on_permanent_error=_mark_window_failed,
        )
        if got is None:
            return STOPPED
        if not got:
            if st.get("failed"):
                # 请求整体失败（永久错误）—— 不是「翻到头了」，绝不能标 exhausted，
                # 否则这个窗口剩下的页会被当成空页静默跳过。
                return []
            # 请求成功却一条都没有 = 真的翻到数据末尾了
            st["exhausted"] = True
            job.append_log(f"{label} 第 {first}-{last} 页为空（已到数据末尾），后续页不再请求。")
            return []
        st["posts"] = got
        return _slice_page(got, page_num - first)

    return _slice_page(st.get("posts") or [], page_num - first)


def _process_post(post, job, do_download=True):
    """处理单个 post：过滤、提取画师、可选下载。返回 (ids, artist, saved_filename) 或 None。
    log.json 走全局 log_store；下载目录由 job.save_dir 决定。"""
    source = post.get("_source") or getattr(job, "tag_source", "danbooru") or "danbooru"
    ids = str(post.get('id'))
    if not ids:
        job.append_log("跳过无效 post：缺少 ID。")
        return None
    if ids in log_store:
        job.append_log(f"跳过 ID {ids}：log.json 已有记录。")
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
            job.append_log(f"跳过 ID {ids}：没有可用图片地址。")
            return None

        # 文件已在 job.save_dir 时的早跳过：避免被 download_image 的"文件已存在"分支
        # 卡 sleep(1) + 刷屏。识别后只补 log_store，下一次同 ID 直接静默跳过。
        peek_name = post.get('image') or image_url.split('/')[-1].split('?')[0]
        if peek_name and os.path.exists(os.path.join(job.save_dir, peek_name)):
            saved_filename = peek_name
            log_store.record(ids, image_url)
            job.resolve_pending_id(ids)
            return ids, artist, saved_filename

        job.play_event.wait()
        if not job.is_running:
            return None

        # 无论以何种方式要下载图片，都先把 id 写入 folder 的 ids_data.json（待下载队列）。
        # 这样中断 / 失败后该 id 仍留在文件里，可被「按 id 重试」直接消费。
        job.queue_pending_id(ids)

        download_fn = gelbooru_api.download_image if source == "gelbooru" else danbooru_api.download_image
        try:
            saved_filename = download_fn(image_url, job.save_dir, job.append_log, raise_on_transient=True)
        except (danbooru_api.TransientImageError, gelbooru_api.TransientImageError) as e:
            # 瞬时网络失败（已内部重试耗尽）：id 保留在 ids_data.json，记入失败表供前端按 id 重试
            job.record_failed_id(ids)
            job.fail_count += 1
            job.append_log(f"图片下载失败(网络)，ID {ids} 已留在待下载队列可重试: {e}")
            return "__TRANSIENT__"
        if saved_filename:
            log_store.record(ids, image_url)
            job.resolve_pending_id(ids)
            sleep(1)
        else:
            # 永久失败（404/已删除等）：id 不可重试，从待下载队列移除
            job.resolve_pending_id(ids)
            job.fail_count += 1
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


# 单个下载任务内的图片并发下载度。curl_cffi 的阻塞 requests.get 在网络IO期间释放 GIL，
# 所以线程池能真并发。Danbooru 有限流，4 是速度与撞 429 风险的平衡点。
DOWNLOAD_CONCURRENCY = 4


# 页面列表抓取的容错参数。失败时单页静默重试 3 次（1s/3s/5s 退避），仍失败则记入
# failed_pages 并跳过本张/本页让 grabber 继续抓下一张/下一页；同时累计「连续失败页数」，
# 达到阈值时 auto-pause 一次让用户决策，避免在系统性故障（host 挂了 / 一直 429）时
# 把 50 页全跑完才停下。阈值 5 是经验值：3 张/页瞬时抖动×3 静默重试 = 9s/页，
# 5×9s = 45s 内仍全部失败 → 大概率是站端问题而不是本机抖动。
PAGE_FETCH_SILENT_RETRIES = 3
CONSECUTIVE_PAGE_FAILURE_THRESHOLD = 5
# 静默重试的退避间隔（秒）。最后一次固定 5s 是为了让风控冷却下来再试一次。
PAGE_FETCH_BACKOFF = (1, 3, 5)


def _process_posts_concurrent(job, posts, page_need_update, new_hot_artists,
                              max_workers=None):
    """并发下载一页的所有 post，返回 (成功, 跳过, 失败) 计数。

    分工原则（保证线程安全，无需新增锁）：
      - 工作线程只跑 _process_post（下载 + log_store/pending_ids/viewer 的写，
        这些全走各自的 RLock / 每文件锁，天然线程安全）；
      - _update_artist_stats / append_viewer_entry 里会改 page_need_update、
        new_hot_artists 这两个「未加锁的普通 list」，因此只在主线程的 as_completed
        循环里串行调用，绝不放进工作线程。
    暂停：投递前 play_event.wait()，且 _process_post 内部下载前还会再 wait 一次，
         已投递的在途任务会在各自的下载点暂停。
    停止：投递循环见 is_running=False 即停止投递；在途/排队任务 _process_post 内部
         检测 is_running=False 直接返回 None，快速排空。
    max_workers：None 时回退到 job.download_concurrency（来自用户设置），都没有再用 DOWNLOAD_CONCURRENCY。"""
    if max_workers is None:
        max_workers = max(1, min(int(getattr(job, 'download_concurrency', 0) or DOWNLOAD_CONCURRENCY), 16))
    page_success = page_skipped = page_failed = 0
    if not posts:
        return 0, 0, 0

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_post = {}
        for post in posts:
            if not job.is_running:
                break
            job.play_event.wait()          # 暂停时不再投递新任务
            if not job.is_running:
                break
            future_to_post[executor.submit(_process_post, post, job, True)] = post

        for future in concurrent.futures.as_completed(future_to_post):
            post = future_to_post[future]
            try:
                result = future.result()
            except Exception as e:
                job.append_log(f"下载线程异常: {e}")
                page_failed += 1
                continue
            if result == "__TRANSIENT__":
                page_failed += 1
                continue
            if result is None:
                page_skipped += 1
                job.skip_count += 1
                continue
            ids, artist, saved_filename = result
            if saved_filename:
                page_success += 1
                job.success_count += 1
            else:
                page_failed += 1
                job.fail_count += 1
            # 仅主线程改这两个未加锁的 list
            _update_artist_stats(job, artist, page_need_update, new_hot_artists)
            job.append_viewer_entry(ids, artist, saved_filename, post)

    # 本页所有图片处理完，统一把 pending 移除增量落盘一次（旧实现每张图都全量重写）
    job.flush_pending_ids()
    return page_success, page_skipped, page_failed


# --- mode: rank ---
def grabber_rank(job, page_num):
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    posts = _list_posts_for_page(
        job, "rank", page_num,
        lambda first, limit: danbooru_api.get_posts_by_rank(
            first, limit=limit, timeout=LIST_WINDOW_TIMEOUT
        ),
        label="[Rank]",
    )
    if posts is STOPPED:
        # 任务被停止（用户点「停止」或 _fetch_page_or_pause 内 auto-pause 退出）
        return [], {"1": [], "2": []}

    page_success, page_skipped, page_failed = _process_posts_concurrent(
        job, posts, page_need_update, new_hot_artists
    )

    job.append_log(
        f"[Rank] 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
    )
    _persist_global_data()
    job.flush_viewer_data()
    return new_hot_artists, page_need_update


# --- mode: popular ---
def grabber_popular(job, page_num, target_date):
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    posts = _list_posts_for_page(
        job, "popular", page_num,
        lambda first, limit: danbooru_api.get_popular_posts(
            target_date, first, limit=limit, timeout=LIST_WINDOW_TIMEOUT
        ),
        label=f"[Popular] {target_date}", scope=target_date,
    )
    if posts is STOPPED:
        return [], {"1": [], "2": []}

    page_success, page_skipped, page_failed = _process_posts_concurrent(
        job, posts, page_need_update, new_hot_artists
    )

    job.append_log(
        f"[Popular] {target_date} 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
    )
    _persist_global_data()
    job.flush_viewer_data()
    return new_hot_artists, page_need_update


# --- mode: popular_collect_ids / popular_download_ids ---
def grabber_popular_collect_ids(job, page_num, target_date):
    """日期热门 · 阶段 1：只收 ID 不下载。镜像 grabber_collect_ids（排行榜版本），
    但拉取 /explore/posts/popular.json 而不是 /posts.json?order:rank。
    target_date 由调用方钉死：单日 popular 是 job 创建时的 target_date，
    范围 popular_range 由外层在 switch_target 之后才进入本函数（保证 job.db
    指向当天 folder）。"""
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    daily_ids_data = job.db.load_ids_data()

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    posts = _list_posts_for_page(
        job, "popular", page_num,
        lambda first, limit: danbooru_api.get_popular_posts(
            target_date, first, limit=limit, timeout=LIST_WINDOW_TIMEOUT
        ),
        label=f"[Popular:Collect] {target_date}", scope=target_date,
    )
    if posts is STOPPED:
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
        if not artist:
            job.append_log(f"[Popular:Collect] 收集无画师 ID {ids}（疑似 expunged/匿名帖，仍进入待下载队列）")
        daily_ids_data.append(ids)

    _persist_global_data()
    daily_ids_data = list(set(daily_ids_data))
    job.db.save_ids_data(daily_ids_data)
    # 同步内存镜像，保证同一 job 内后续（或状态查询）看到的待下载队列一致
    with job.viewer_lock:
        job.pending_ids = set(str(x) for x in daily_ids_data)
    job.append_log(f"[Popular:Collect] {target_date} 当前已收集 {len(daily_ids_data)} 个 ID")
    return new_hot_artists, page_need_update


# --- mode: tags ---
def grabber_tags(job, page_num, tag_query, tag_source="danbooru"):
    """按 tag 查询下载到 tag 文件夹。共享全局 log_store 避免和日期文件夹重复下载。"""
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    job.play_event.wait()
    if not job.is_running:
        return [], page_need_update

    source = "gelbooru" if tag_source == "gelbooru" else "danbooru"
    api = gelbooru_api if source == "gelbooru" else danbooru_api
    source_label = "Gelbooru" if source == "gelbooru" else "Danbooru"

    if source == "gelbooru":
        # gelbooru 的 limit 上限没实测过，保持一页一个请求（不套窗口缓存）。
        job.append_log(f"[Tags:{source_label}] 正在获取 [{tag_query}] 第 {page_num} 页... (host={api.get_host()})")
        posts = _fetch_page_or_pause(
            lambda: api.get_posts_by_tags(tag_query, page_num),
            job, label=f"[Tags:{source_label}] [{tag_query}] 第 {page_num} 页", page=page_num
        )
    else:
        posts = _list_posts_for_page(
            job, "tags", page_num,
            lambda first, limit: api.get_posts_by_tags(
                tag_query, first, limit=limit, timeout=LIST_WINDOW_TIMEOUT
            ),
            label=f"[Tags:{source_label}] [{tag_query}]", scope=tag_query,
        )
    if posts is STOPPED or posts is None:
        return [], {"1": [], "2": []}

    page_success, page_skipped, page_failed = _process_posts_concurrent(
        job, posts, page_need_update, new_hot_artists
    )

    job.append_log(
        f"[Tags:{source_label}] [{tag_query}] 第 {page_num} 页完成: 成功 {page_success} / 跳过 {page_skipped} / 失败 {page_failed}"
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

    posts = _list_posts_for_page(
        job, "rank", page_num,
        lambda first, limit: danbooru_api.get_posts_by_rank(
            first, limit=limit, timeout=LIST_WINDOW_TIMEOUT
        ),
        label="[CollectIDs]",
    )
    if posts is STOPPED:
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
        if not artist:
            job.append_log(f"[CollectIDs] 收集无画师 ID {ids}（疑似 expunged/匿名帖，仍进入待下载队列）")
        daily_ids_data.append(ids)

    _persist_global_data()
    daily_ids_data = list(set(daily_ids_data))
    job.db.save_ids_data(daily_ids_data)
    # 同步内存镜像，保证同一 job 内后续（或状态查询）看到的待下载队列一致
    with job.viewer_lock:
        job.pending_ids = set(str(x) for x in daily_ids_data)
    job.append_log(f"[CollectIDs] 当前已收集 {len(daily_ids_data)} 个 ID")
    return new_hot_artists, page_need_update


# --- mode: download_ids ---
def task_download_ids(job, inline_ids=None):
    retry_only = bool(getattr(job, "retry_only", False))
    # 空闲下载轮次语义上等价于「只下 inline ids、不消费整个 folder backlog」，
    # 所以和 retry_only 共用同一条分支 —— 区别只在日志文案（见下）。
    idle_round = bool(getattr(job, "is_idle", False))
    if inline_ids:
        # 粘贴的 IDs：去重 + 只留纯数字串，合并进 folder 的待下载队列（ids_data.json）
        cleaned = []
        seen = set()
        for raw in inline_ids:
            s = str(raw).strip()
            if not s or not s.isdigit() or s in seen:
                continue
            seen.add(s)
            cleaned.append(s)
        if cleaned:
            if retry_only or idle_round:
                # 失败重试：仍写入 queue（让 log.json 记录本次成功 / 失败），
                # 但本次只下这批，**不**顺手把 folder 里积压的 pending id 全跑一遍
                # —— 旧版"重试 9 张失败"会把 folder backlog 几百张一起下。
                # 空闲下载同一条路径：每轮只吃自己挑的那 per_round 张，剩下的留给下一轮。
                for s in cleaned:
                    job.queue_pending_id(s)
                ids_data = sorted(cleaned)
                if idle_round:
                    job.append_log(
                        f"[空闲下载] 本轮下 {len(cleaned)} 个 ID"
                        f"（folder 里剩余的积压留给下一轮，不一次抽干）"
                    )
                else:
                    job.append_log(f"[DownloadIDs] 重试模式：仅下指定的 {len(cleaned)} 个 ID（folder backlog 本次跳过）")
            else:
                for s in cleaned:
                    job.queue_pending_id(s)
                # 下载对象 = 队列里所有待下载 id（含粘贴的 + 之前收集/失败残留的）
                ids_data = sorted(job.pending_ids)
                job.append_log(f"[DownloadIDs] 已写入 {len(cleaned)} 个粘贴的 ID 到 {job.target_folder}/ids_data.json")
        else:
            ids_data = sorted(job.pending_ids) if not retry_only else []
    else:
        ids_data = job.db.load_ids_data()

    if not ids_data:
        job.append_log("[DownloadIDs] 没有可下载的 ID（既未粘贴也未先用「仅收集ID」模式收集）。")
        return

    # 进度条数据：把本轮目标数 / 已成功 / 已失败 这三个计数重置（跨日 / 阶段切换会再次进来）
    job.total_planned = len(ids_data)
    job.success_count = 0
    job.skip_count = 0
    job.fail_count = 0
    # 直进 download 模式时没有 collect 阶段，确保页进度隐藏
    job.page_total = 0
    job.page_current = 0
    job.page_done_count = 0

    concurrency = max(1, min(int(getattr(job, 'download_concurrency', 0) or DOWNLOAD_CONCURRENCY), 16))
    job.append_log(f"[DownloadIDs] 开始下载，共 {len(ids_data)} 个 ID（并发 {concurrency}）")

    # ── 元数据批量预取（2026-10）─────────────────────────────────────────────
    # 原来每个 worker 自己调 fetch_data_with_retry：一次一个请求，而 Danbooru 的
    # JSON API 走**全进程共享的令牌桶**（1 req/s）—— 4 个 worker 也只是把这 1 req/s
    # 分着花，于是「拿 file_url」这一步成了整条下载链路的**真正瓶颈**（下载本体走 CDN，
    # 不过令牌桶，本来就是并发的）。
    # 现在开跑前一次性批量拉回（`tags=id:1,2,3`，一次最多 200 个 id），worker 只剩 CDN 下载。
    # 实测：40 个 id 逐张 17.6s → 批量 0.8s；1000 个 id 的元数据从 1000 个请求降到 5 个。
    #
    # 只预取「log 里没有 URL 缓存」的 id —— log 命中时要么直接 skip（skip_logged），
    # 要么用缓存 URL 强制重下，两种情况都不需要 API。
    need_meta = [pid for pid in ids_data if pid not in log_store]
    post_map, unknown_ids = {}, set()
    if need_meta:
        n_req = (len(need_meta) + danbooru_api.DANBOORU_ID_BATCH_MAX - 1) // danbooru_api.DANBOORU_ID_BATCH_MAX
        job.append_log(f"[DownloadIDs] 批量拉取 {len(need_meta)} 个 ID 的元数据（{n_req} 个请求，每批最多 {danbooru_api.DANBOORU_ID_BATCH_MAX} 个）")
        post_map, unknown = danbooru_api.fetch_data_batch_detail(need_meta)
        unknown_ids = set(unknown)
        job.append_log(f"[DownloadIDs] 元数据回来了 {len(post_map)} 个；{len(unknown_ids)} 个因所在批次请求失败而待重试")
    success_count = 0
    # 每下载 N 张成功就 flush 一次 viewer_data 到磁盘。原先按页刷新（rank 一次约 100 张），
    # 现在统一走 download_ids 后没有自然分页点；用 20 张粒度平衡「频繁落盘开销」和
    # 「进程异常时丢失的进度」。
    flush_every = 20
    flushed_since_start = 0

    def _worker(pid_str):
        """处理单个 id：返回 "ok" / "skip" / "fail"。所有共享写（log_store / stats_store /
        pending_ids / viewer）都走各自的锁，故可安全并发。

        skip_logged（job.skip_logged，默认 True）：
          - True（默认）：log_store 已记录的 id 视为已下过，skip（去重）
          - False（rank·按ID下载 取消勾选时）：无视 log 命中，继续走下载路径。
            log 已有 cdn_url 时直接用缓存（0 API 调用）；无缓存时从**开跑前批量预取的
            post_map** 里取（0 API 调用），不再逐张打 /posts/{id}.json。
        """
        def mark_skipped(reason):
            job.skip_count += 1
            job.append_log(f"[DownloadIDs] 跳过 ID {pid_str}：{reason}")
            return "skip"

        job.play_event.wait()
        if not job.is_running:
            return "stopped"

        cached_cdn_url = log_store.get(pid_str) if pid_str in log_store else None
        if job.skip_logged and cached_cdn_url is not None:
            # 命中 log 且要求去重：直接 skip（移除 pending 即可）
            job.resolve_pending_id(pid_str)
            return mark_skipped("log.json 已有记录")

        # 确保该 id 在待下载队列里（正常情况下已在，防御性补一次）
        job.queue_pending_id(pid_str)

        if cached_cdn_url:
            # log 已有但本轮不跳过：直接用缓存 URL，免一次 API 调用
            job.append_log(f"[DownloadIDs] 正在处理 ID: {pid_str}（log 缓存，强制重下）")
            post_data = None
            image_url = cached_cdn_url
        else:
            job.append_log(f"[DownloadIDs] 正在处理 ID: {pid_str}")
            # 元数据来自开跑前的批量预取（见 task_download_ids 顶部的说明），0 API 调用。
            # 三种「没拿到 post_data」的情形必须区分开，否则会重演旧版的老毛病：
            post_data = post_map.get(pid_str)
            if post_data is None:
                if pid_str in unknown_ids:
                    # ① 所属批次整个请求失败（网络 / 5xx）：**不确定**，等价于旧逻辑
                    #    的「拉取失败」—— 留在队列里可重试，并记入失败表。
                    job.record_failed_id(pid_str)
                    job.fail_count += 1
                    job.append_log(f"ID {pid_str} 元数据拉取失败（所属批次网络异常），保留在待下载队列可重试")
                    return "fail"
                # ② 批次成功但没这个 id：Danbooru 侧确实不可见（已删除 / 已删号 /
                #    safebooru 下被隐藏的 R-18），等价于旧逻辑吃到的 404 PermanentPostError
                #    —— 从队列移除且**不写** failed_ids，避免「重试」按钮每次都列出这批死 id。
                job.resolve_pending_id(pid_str)
                job.fail_count += 1
                job.append_log(f"ID {pid_str} 在 Danbooru 侧不可用（已删除/当前模式不可见），已从待下载队列移除")
                return "fail"

            if job.filter_tags:
                tag_string = post_data.get('tag_string', '')
                if any(tag in tag_string for tag in job.filter_tags):
                    # 被过滤：不会下载，从队列移除
                    job.resolve_pending_id(pid_str)
                    return mark_skipped("包含过滤标签")

            image_url = post_data.get('file_url') or post_data.get('large_file_url')
            if not image_url:
                # ③ 帖子可见但没有可下载地址（典型：画师被封禁 is_banned，Danbooru 会
                #    把 file_url 抹掉）；与旧逻辑一致按「跳过」处理，不计失败。
                job.resolve_pending_id(pid_str)
                return mark_skipped("没有可用图片地址")

        job.play_event.wait()
        if not job.is_running:
            return "stopped"

        try:
            saved_filename = danbooru_api.download_image(image_url, job.save_dir, job.append_log, raise_on_transient=True)
        except danbooru_api.TransientImageError as e:
            # 瞬时失败：id 留在队列并记入失败表供再次重试
            job.record_failed_id(pid_str)
            job.fail_count += 1
            job.append_log(f"ID {pid_str} 下载失败(网络)，保留在待下载队列可重试: {e}")
            return "fail"
        if not saved_filename:
            # 永久失败（已删除等）：不可重试，从队列移除
            job.resolve_pending_id(pid_str)
            job.fail_count += 1
            job.append_log(f"ID {pid_str} 下载失败，跳过")
            return "fail"

        log_store.record(pid_str, image_url)
        job.resolve_pending_id(pid_str)
        job.success_count += 1

        artist = ""
        if post_data and 'tag_string_artist' in post_data:
            artist_list = post_data['tag_string_artist'].split()
            artist_list = [a for a in artist_list if not a.lower().endswith("(voice_actor)")]
            if artist_list:
                artist = ' '.join(artist_list)

        if artist:
            stats_store.increment(artist)

        # cached_cdn_url 分支没有 post_data：构造一个最小 post 字典走 append_viewer_entry。
        # append_viewer_entry 内部会按 post_url 去重 —— 如果 viewer_data 里已有这个 id
        # 的条目（多数情况），它会直接 return，artist 字段不会被改写。
        entry_post = post_data or {"id": int(pid_str), "file_url": image_url}
        job.append_viewer_entry(pid_str, artist, saved_filename, entry_post)
        return "ok"

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for pid_str in ids_data:
            if not job.is_running:
                job.append_log("任务已被强制终止。")
                break
            job.play_event.wait()          # 暂停时不再投递
            if not job.is_running:
                break
            futures.append(executor.submit(_worker, pid_str))
        for future in concurrent.futures.as_completed(futures):
            try:
                result = future.result()
                if result == "ok":
                    success_count += 1
                    flushed_since_start += 1
                    # 满 20 张就 flush 一次 viewer_data 到磁盘，方便手动刷新图库
                    if flushed_since_start >= flush_every:
                        try:
                            job.flush_viewer_data()
                            job.flush_pending_ids()
                        except Exception as e:
                            job.append_log(f"[DownloadIDs] flush viewer_data 失败: {e}")
                        flushed_since_start = 0
            except Exception as e:
                job.fail_count += 1
                job.append_log(f"下载线程异常: {e}")

    _persist_global_data()
    job.flush_viewer_data()
    job.flush_pending_ids()
    job.append_log(
        f"[DownloadIDs] 完成：成功 {job.success_count}，"
        f"跳过 {job.skip_count}，失败 {job.fail_count}，共处理 {job.success_count + job.skip_count + job.fail_count}/{job.total_planned}"
    )


# --- mode: popular_recover (新) / recover_popular (legacy alias) ---
def task_popular_recover(job, start_page, end_page):
    """日期热门·补全/补齐：按文件存在性补全丢失图片。
    数据源：日期热门页 [start_page, end_page]。
    判定逻辑：log.json 仅作为 CDN URL 缓存用，**不作为跳过依据**。
      - log 有 + 文件有 → 跳过（idempotent）
      - log 有 + 文件无 → 下载（用 log 缓存 URL，0 API）
      - log 无 + 文件无 → 下载（调 API 拿 URL；download_image 内部"文件已存在则跳过"会兜底）

    阶段 4（关键）：过滤出 targets 后**先写 ids_data.json**，让：
      - 日历通过 dl_queue 计数（_pending_counts_map）自动展示"有 N 个待下载"
      - 暂停/继续时下载循环从 pending_ids 自然恢复
      - 失败 ID 留在 ids_data.json，可后续用「按ID下载」消费
      - 与 popular_collect_ids / popular_download_ids 链路对齐

    不复用 task_download_ids：那里"pid in log_store → 跳过"会把已下载过、文件丢失的
    ID 误判为"已下载"；这里保留自定义下载循环 + queue_pending_id / resolve_pending_id。
    """
    target_date = job.target_folder
    if not target_date:
        job.append_log("[Recover] 缺少目标日期。")
        return

    # 关键保护：日期目录不存在时立刻终止（防移动硬盘未挂载时把图写到错误盘符）
    if not os.path.isdir(job.save_dir):
        job.append_log(f"[Recover] 日期目录 {job.save_dir} 不存在（盘没接？），终止。")
        return

    # 本地回退模式：图片写到 HOT_PIC_DIR 本地临时目录，完成后需手动同步到原盘。
    # 必须在 stage 1 一开始就告诉用户，避免跑完才发现下错地方。
    if getattr(job, "local_only", False):
        job.append_log(
            f"[Recover] ⚠️ 本地临时目录模式：图片下载到 {job.save_dir}，"
            f"完成后请手动把新增图片移动到原盘（移动硬盘）做增量更新。"
        )

    # 防御性清零：避免上一个 job 的 success_count / fail_count / total_planned 残留到
    # 这个新 job（前端 progress 区域在 collect 阶段会显示「X 已完成」，看上去「在抓 ID 怎么
    # 就有 2 张下完了」就是这个原因）。正常情况下 dataclass 默认就是 0，但显式置 0 更稳。
    job.total_planned = 0
    job.success_count = 0
    job.skip_count = 0
    job.fail_count = 0

    job.append_log(f"[Recover] 扫描 {target_date} 热门页 {start_page}-{end_page}（按文件存在性补全）")
    # 醒目 banner：让用户能直接确认"我现在跑的就是 popular_recover，不是其他 mode 偷跑"
    job.append_log(
        f"[Recover] ━━━ job_id={job.job_id} mode={job.mode!r} "
        f"target_date={target_date!r} save_dir={job.save_dir!r} ━━━"
    )
    candidates = []  # [(post_id, cdn_url_or_None), ...] 抓页阶段只收 ID

    # save_dir 一致性校验：log 里写的 target_date 必须等于 save_dir 的最后一段。
    # 如果不相等说明 start_scraper 把 save_dir_override 算错了，理论上不应该发生，
    # 但加这一道防御能在万一出错时立刻暴露，而不是默默下到错的目录。
    save_dir_leaf = os.path.basename(os.path.normpath(job.save_dir))
    if save_dir_leaf != target_date:
        job.append_log(
            f"[Recover] ❌ save_dir 不一致：target_date={target_date} 但 save_dir={job.save_dir}，"
            f"终止避免下到错的目录。请检查 library_roots.json 配置。"
        )
        return

    # 阶段 1：抓热门页 ID（纯网络，不做 stat()；每 5 页打一行进度，避免刷屏）
    # 关键：必须同步更新 page_current / page_total，否则前端进度条会显示上一次
    # job 的残留数字（如「23/50 页」），看上去「在抓第 23 页」其实是新 job 还在抓第 1 页
    #
    # 定向重试模式：job.pages_list 非空时只抓这些页码（不去 start_page..end_page 全段），
    # 让前端的「一键重试失败页」真正只重抓失败的几页，而不是把整段再跑一遍。
    pages_list = list(getattr(job, "pages_list", None) or [])
    if pages_list:
        sorted_pages = sorted(set(int(p) for p in pages_list))
        job.page_total = len(sorted_pages)
        job.page_current = 0
        page_iter = sorted_pages
        progress_first, progress_last = sorted_pages[0], sorted_pages[-1]
        job.append_log(f"[Recover] 定向重试模式：只重抓 {len(sorted_pages)} 个失败页 {sorted_pages[:5]}{'...' if len(sorted_pages) > 5 else ''}")
    else:
        job.page_total = end_page - start_page + 1
        job.page_current = start_page - 1
        page_iter = range(start_page, end_page + 1)
        progress_first, progress_last = start_page, end_page
    for idx, page_num in enumerate(page_iter, 1):
        if not job.is_running:
            job.append_log("[Recover] 任务已被强制终止。")
            return
        job.play_event.wait()
        if pages_list:
            is_progress_log = (idx == 1 or idx == len(sorted_pages) or idx % 5 == 0)
        else:
            is_progress_log = (page_num == start_page or page_num == end_page
                               or (page_num - start_page + 1) % 5 == 0)
        if is_progress_log:
            job.append_log(f"[Recover] 正在拉取 {target_date} 第 {page_num}/{progress_last} 页...")
        posts = _list_posts_for_page(
            job, "popular", page_num,
            lambda first, limit: danbooru_api.get_popular_posts(
                target_date, first, limit=limit, timeout=LIST_WINDOW_TIMEOUT
            ),
            label=f"[Recover] {target_date}", scope=target_date,
        )
        if posts is STOPPED:
            # 任务已被停止（_fetch_page_or_pause 内 auto-pause 退出路径）—— 与其他 5 个 grabber
            # 一样 return 出本函数，让外层 _run_job 的 finally 跑 finalize_on_stop 落盘。
            # task_popular_recover 不需要返回 (new_hot_artists, page_need_update)（caller 不消费），
            # 所以这里直接 return（隐式 None），与其他 grabber 的 `return [], {...}` 在功能上等价。
            return
        job.page_current = idx
        job.page_done_count += 1

        for post in posts:
            pid = str(post.get("id", ""))
            if not pid:
                continue
            cdn_url = log_store.get(pid)
            candidates.append((pid, cdn_url))

        # 防风控休息已挪进 _fetch_page_or_pause（按「请求」计，不按页）：一个请求
        # 现在覆盖 10~50 页，在这里再按页休一次就是纯白等。

    job.append_log(f"[Recover] ID 收集完成，共 {len(candidates)} 个，开始扫描本地文件...")

    # 阶段 2：一次性枚举本地文件名，O(1) 查重代替 N 次 stat()
    # 外置盘冷缓存下逐个 stat() 1-5ms × 1000 = 1-5s 甚至更久
    try:
        existing_files = set(os.listdir(job.save_dir))
    except OSError as e:
        job.append_log(f"[Recover] 读取目录失败: {e}，终止")
        return
    job.append_log(f"[Recover] 本地目录已有 {len(existing_files)} 个文件")

    # 阶段 3：过滤掉本地已存在的（用文件名匹配：log 有 cdn_url 才能反推 md5.ext）
    targets = []  # [(post_id, cdn_url_or_None), ...] 真正要下载的
    for pid, cdn_url in candidates:
        # 反推本地文件名：log 有 cdn_url 时才能拿到 md5.ext（log 无时无法判断文件是否存在，
        # 会走"重新下载"路径——这正是手动复制 / log 漂移 场景的安全兜底）
        fn = cdn_url.rsplit('/', 1)[-1].split('?')[0] if cdn_url else None
        if fn and fn in existing_files:
            continue
        # 文件不在（或 log 无记录无法判断）→ 加入下载列表
        targets.append((pid, cdn_url))

    if not targets:
        job.append_log("[Recover] 没有需要下载的图片（热门页范围内本地已全部存在）")
        return

    # 阶段 4（关键）：把 targets 写进 ids_data.json，让前端可见 / 可暂停恢复 / 可后续消费
    # 用 queue_pending_id 走 DownloadJob 的标准协议：同时维护内存 pending_ids 镜像
    for pid, _ in targets:
        job.queue_pending_id(pid)
    # 失败 ID 列表先清空（旧残留可能让前端误报）
    with job.viewer_lock:
        job.failed_ids.clear()

    # 进入下载阶段：清零页进度（避免上一阶段「23/50 页」残留），重置 success/fail
    # 计数（防止「14/4 失败」从老 job 串过来）。前端 phase 切到「正在下载…」。
    job.page_total = 0
    job.page_current = 0
    from_log = sum(1 for _, u in targets if u)
    from_api = len(targets) - from_log
    job.append_log(f"[Recover] 待下载 {len(targets)} 张（log 缓存 {from_log} / API {from_api}），已写入 ids_data.json")
    job.total_planned = len(targets)
    job.success_count = 0
    job.skip_count = 0
    job.fail_count = 0

    # 阶段 5：并发下载（保留 cdn_url 优化；并发度复用 job.download_concurrency，
    # 与 task_download_ids 对齐 —— 之前是单 for 循环，热门 100 页补齐要等几十秒到几分钟）
    concurrency = max(1, min(int(getattr(job, 'download_concurrency', 0) or DOWNLOAD_CONCURRENCY), 16))
    job.append_log(f"[Recover] 开始并发下载 {len(targets)} 张（并发 {concurrency}）")

    # 元数据批量预取（理由与 task_download_ids 顶部整段相同）：只针对 targets 里
    # log 没缓存 cdn_url 的那部分。补齐模式常有几百张「log 无记录」的图，逐张打
    # /posts/{id}.json 同样会被 1 req/s 的令牌桶拖成主要耗时。
    need_meta = [pid for pid, url in targets if not url]
    recover_meta, recover_unknown = {}, set()
    if need_meta:
        n_req = (len(need_meta) + danbooru_api.DANBOORU_ID_BATCH_MAX - 1) // danbooru_api.DANBOORU_ID_BATCH_MAX
        job.append_log(f"[Recover] 批量拉取 {len(need_meta)} 个 ID 的元数据（{n_req} 个请求）")
        recover_meta, _unk = danbooru_api.fetch_data_batch_detail(need_meta)
        recover_unknown = set(_unk)

    # 与 task_download_ids 一样：每 20 张成功就 flush 一次 viewer_data 到磁盘，
    # 方便中途手动刷新图库（补齐模式下中途可能想停下来看进度）。
    flush_every = 20
    flushed_since_start = 0

    def _recover_worker(pid_str, cdn_url):
        """处理单个 id：返回 "ok" / "skip" / "fail"。
        与 task_download_ids._worker 的差别：targets 阶段已经把 log 缓存的 cdn_url
        算好了，worker 直接用 —— 命中缓存的图省一次 API 调用；未命中的从开跑前的
        批量预取结果 recover_meta 里取，同样 0 API 调用。
        所有共享写（log_store / viewer_data / pending_ids / 计数）都走各自的锁，可安全并发。"""
        job.play_event.wait()
        if not job.is_running:
            return "skip"

        if cdn_url:
            # log 有记录：直接用缓存 URL（0 API 调用）
            image_url = cdn_url
            post_data = None
        else:
            # log 没有：用批量预取的元数据（0 API 调用）。三种缺失情形见下方注释。
            post_data = recover_meta.get(pid_str)
            if post_data is None:
                if pid_str in recover_unknown:
                    # ① 所属批次整个请求失败：不确定，留在队列可重试
                    job.fail_count += 1
                    job.record_failed_id(pid_str)
                    job.append_log(f"[Recover] ID {pid_str} 元数据拉取失败（所属批次网络异常，保留待重试）")
                    return "fail"
                # ② 批次成功但没这个 id：Danbooru 侧已删除 / 不可见 —— 与旧逻辑吃 404 等价，
                #    移除且不写 failed_ids（否则补齐模式会反复重试已删图）
                job.resolve_pending_id(pid_str)
                job.fail_count += 1
                job.append_log(f"[Recover] ID {pid_str} 在 Danbooru 侧不可用（已删除/当前模式不可见），已移除")
                return "fail"
            image_url = post_data.get('file_url') or post_data.get('large_file_url')
            if not image_url:
                # 永久失败：移除 + 记失败
                job.resolve_pending_id(pid_str)
                job.fail_count += 1
                job.append_log(f"[Recover] ID {pid_str} 没有 file_url，跳过")
                return "skip"

        try:
            saved = danbooru_api.download_image(image_url, job.save_dir, job.append_log)
        except Exception as e:
            # 异常也算瞬时失败：保留 id 让后续重试
            job.append_log(f"[Recover] ID {pid_str} 异常: {e}")
            job.fail_count += 1
            job.record_failed_id(pid_str)
            return "fail"
        if not saved:
            # download_image 内部已重试仍失败：留 ids_data.json 可重试
            job.fail_count += 1
            job.record_failed_id(pid_str)
            job.append_log(f"[Recover] ID {pid_str} 下载失败（保留在 ids_data.json 待重试）")
            return "fail"

        log_store.record(pid_str, image_url)
        entry_post = post_data or {"id": int(pid_str), "file_url": image_url}
        job.append_viewer_entry(pid_str, "", saved, entry_post)
        # 成功：从 ids_data.json 移除
        job.resolve_pending_id(pid_str)
        job.success_count += 1
        # success_count 已 ++，读到的就是刚成功的那一张的序号
        job.append_log(f"[Recover] [{job.success_count}/{len(targets)}] ID {pid_str} 下载成功 → {saved}")
        return "ok"

    with concurrent.futures.ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = []
        for pid_str, cdn_url in targets:
            if not job.is_running:
                job.append_log("[Recover] 任务已被强制终止。")
                break
            job.play_event.wait()
            if not job.is_running:
                break
            futures.append(executor.submit(_recover_worker, pid_str, cdn_url))
        for future in concurrent.futures.as_completed(futures):
            try:
                if future.result() == "ok":
                    flushed_since_start += 1
                    if flushed_since_start >= flush_every:
                        try:
                            job.flush_viewer_data()
                            job.flush_pending_ids()
                        except Exception as e:
                            job.append_log(f"[Recover] flush viewer_data 失败: {e}")
                        flushed_since_start = 0
            except Exception as e:
                job.append_log(f"[Recover] 下载线程异常: {e}")

    _persist_global_data()
    job.flush_viewer_data()
    job.flush_pending_ids()
    remaining = len(job.pending_ids)
    job.append_log(f"[Recover] 完成，下载 {job.success_count} 张，失败 {job.fail_count} 张（{remaining} 个待重试）")


def task_recover_popular(job, start_page, end_page):
    """向后兼容的 alias：旧 mode `recover_popular` 仍走这里，内部转调新实现。"""
    return task_popular_recover(job, start_page, end_page)


def _run_job(job, start_page, end_page, mode, target_date, start_date, end_date,
             inline_ids, tag_query, tag_source="danbooru", pages=None):
    """单个 job 的执行入口（在 job.thread 里跑）。根据 mode 分发到各 grabber。
    pages 非空时为「定向重试模式」：只抓这些页码，忽略 start_page~end_page 区间。"""
    pages = pages or []

    def _page_seq(s, e):
        return list(pages) if pages else list(range(s, e + 1))

    # 页级断点（deck.db page_cursor）：仅普通页范围任务写游标，定向重试（pages 非空）/
    # 下载阶段 / recover / 日期范围 v1 都不写。scope 与 failed_pages 一致。
    cursor_modes = {"rank", "collect_ids", "popular", "popular_collect_ids", "tags"}

    def _save_cursor(n):
        if not pages and mode in cursor_modes:
            try:
                deck_db.cursor_save(job.target_folder, mode, n, start_page, end_page,
                                    tag_query=tag_query, tag_source=tag_source)
            except Exception as e:
                job.append_log(f"[cursor] 断点保存失败（不影响任务）: {e}")

    def _clear_cursor():
        if not pages and mode in cursor_modes:
            try:
                deck_db.cursor_clear(job.target_folder, tag_query, tag_source)
            except Exception:
                pass

    try:
        if mode == "download_ids":
            task_download_ids(job, inline_ids)
        elif mode in ("recover_popular", "popular_recover"):
            # popular_recover 是新版入口（popular form 第三档子操作，支持入队 + 写 ids_data.json）；
            # recover_popular 是 legacy alias，内部都走 task_popular_recover
            # 定向重试：把 pages 挂到 job.pages_list，让 task_popular_recover 阶段 1 只抓这些页
            # （而不是把 start_page..end_page 整段重跑）
            if pages:
                job.pages_list = list(pages)
            task_popular_recover(job, start_page, end_page)
        elif mode == "tags":
            source = "gelbooru" if tag_source == "gelbooru" else "danbooru"
            source_label = "Gelbooru" if source == "gelbooru" else "Danbooru"
            output = job.db.load_hot_drawer()
            nu_sets = job.db.load_need_update()
            if pages:
                job.append_log(f"开始抓取 [Tags:{source_label}]，定向重试页码: {pages}")
            else:
                job.append_log(f"开始抓取 [Tags:{source_label}]，从第 {start_page} 页到第 {end_page} 页")
            job.append_log(f"当前过滤 Tags: {job.filter_tags}")
            # 页进度：定向重试时总页数=len(pages)，否则=end_page-start_page+1
            # page_current 用 idx (1..N) 而非 n（实际页码），避免「30/21 页」这种「分母小于分子」的诡异显示。
            # 日志里仍打 n 给开发者看真实抓的哪一页。
            job.page_total = len(pages) if pages else (end_page - start_page + 1)
            job.page_current = 0
            for idx, n in enumerate(_page_seq(start_page, end_page), 1):
                if not job.is_running:
                    job.append_log("任务已被强制终止。")
                    break
                job.play_event.wait()
                job.page_current = idx
                _save_cursor(n)
                job.append_log(f"--- 正在处理 {source_label} tag [{tag_query}] 第 {n} 页 ---")
                o, n_u_dict = grabber_tags(job, n, tag_query, source)
                job.page_done_count += 1
                output = list(set(output + o) - job.db.all_drawer)
                for k in ["1", "2"]:
                    nu_sets[k].update(n_u_dict[k])
                job.db.save_hot_drawer(list(set(output)))
                job.db.save_need_update(nu_sets)
                # 防风控休息已挪进 _fetch_page_or_pause（按请求计，不按页）
            else:
                # 所有页自然跑完（未 break）：断点清掉
                _clear_cursor()
        elif mode in ("popular_range", "popular_range_collect_ids", "popular_range_download_ids"):
            # 日期范围 · 按天两阶段（per-day two-phase）。每个日期 folder 内串行跑
            # 阶段 1（collect 收 ID）→ 阶段 2（按 ID 下载），跨日用 switch_target 切换
            # folder；阶段 2 直接复用 task_download_ids（含并发度 / 失败 ID 重试 /
            # 每 20 张 flush viewer_data 等所有已有逻辑）。
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

            phase_label = {
                "popular_range": "Two-Phase",
                "popular_range_collect_ids": "Collect",
                "popular_range_download_ids": "DownloadIDs",
            }[mode]
            run_collect = mode in ("popular_range", "popular_range_collect_ids")
            run_download = mode in ("popular_range", "popular_range_download_ids")

            curr_dt = s_dt
            days_since_rest = 0
            while curr_dt <= e_dt:
                if not job.is_running:
                    break
                pop_date = curr_dt.strftime("%Y-%m-%d")
                # popular_range 在同一个 job 里按日期迭代：换日时把当前 viewer_data 落盘
                # 再加载新日期的盘上数据，job.target_folder / save_dir 同步更新
                # —— refresh_visible 会跟着 get_by_folder 找到正确的实例
                if job.target_folder != pop_date:
                    job.switch_target(pop_date)
                job.append_log(f"=== 开始抓取日期: {pop_date} ({phase_label}) ===")

                output = job.db.load_hot_drawer()
                nu_sets = job.db.load_need_update()

                # 阶段 1：按页收集 ID（仅在 collect / two-phase 子动作下执行）
                if run_collect:
                    # 跨日日期范围的页进度按"每天的页范围"统计 —— bar 每天重置一次，
                    # 跨日时通过 page_total/page_current 同时刷新即可。
                    # page_current 用 idx (1..N) 而非 n（实际页码），与 task_popular_recover 对齐，
                    # 避免「50/21 页」这种分母小于分子的显示。
                    job.page_total = len(pages) if pages else (end_page - start_page + 1)
                    job.page_current = 0
                    for idx, n in enumerate(_page_seq(start_page, end_page), 1):
                        if not job.is_running:
                            break
                        job.play_event.wait()
                        job.page_current = idx
                        job.append_log(f"--- [{pop_date}] collect 第 {n} 页 ---")
                        o, n_u_dict = grabber_popular_collect_ids(job, n, pop_date)
                        job.page_done_count += 1
                        output = list(set(output + o) - job.db.all_drawer)
                        for k in ["1", "2"]:
                            nu_sets[k].update(n_u_dict[k])
                        job.db.save_hot_drawer(list(set(output)))
                        job.db.save_need_update(nu_sets)
                        # 防风控休息已挪进 _fetch_page_or_pause（按请求计，不按页）

                # 阶段 2：按 ID 下载（仅在 download / two-phase 子动作下执行）。
                # task_download_ids 内部走 download_concurrency + _worker + 每 20 张 flush
                # viewer_data 等统一路径；switch_target 之后 job.db 已指向当天 folder，
                # load_ids_data() 自然拿到当天收集的 ID。
                if job.is_running and run_download:
                    # 进入下载阶段：清零页进度，避免 collect 阶段的数字残留
                    job.page_total = 0
                    job.page_current = 0
                    try:
                        job.pending_ids = set(str(x) for x in (job.db.load_ids_data() or []))
                    except Exception:
                        pass
                    if not job.pending_ids:
                        job.append_log(f"[{pop_date}] 没有可下载的 ID，跳过下载阶段。")
                    else:
                        job.append_log(f"=== [{pop_date}] ID 收集完毕，开始按 ID 下载 ===")
                        prev_mode = job.mode
                        job.mode = "download_ids"
                        try:
                            task_download_ids(job, inline_ids=None)
                        finally:
                            job.mode = prev_mode

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
            # rank / popular（单日期，含两阶段子动作）/ collect_ids 共用这个分支
            output = job.db.load_hot_drawer()
            nu_sets = job.db.load_need_update()

            mode_label = {
                "rank": "Rank",
                "popular": "Popular:Two-Phase",
                "popular_collect_ids": "Popular:Collect",
                "popular_download_ids": "Popular:DownloadIDs",
                "collect_ids": "CollectIDs",
            }.get(mode, mode)
            if pages:
                job.append_log(f"开始抓取 [{mode_label}]，定向重试页码: {pages}")
            else:
                job.append_log(f"开始抓取 [{mode_label}]，从第 {start_page} 页到第 {end_page} 页")
            job.append_log(f"当前过滤 Tags: {job.filter_tags}")

            job.page_total = len(pages) if pages else (end_page - start_page + 1)
            job.page_current = 0
            for idx, n in enumerate(_page_seq(start_page, end_page), 1):
                if not job.is_running:
                    job.append_log("任务已被强制终止。")
                    break
                job.play_event.wait()
                job.page_current = idx
                _save_cursor(n)
                job.append_log(f"--- 正在处理第 {n} 页 ---")

                if mode == "popular":
                    # popular 单日两阶段：外层 per-page 循环只跑阶段 1（collect），
                    # 阶段 2（按 ID 下载）在所有页跑完后统一执行（与排行榜 1756-1776
                    # 同款"翻 mode + 复原"模式）。这样把"边收边下"的旧行为彻底分开。
                    pop_date = target_date or _resolve_today()
                    o, n_u_dict = grabber_popular_collect_ids(job, n, pop_date)
                    job.page_done_count += 1
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)
                elif mode == "popular_collect_ids":
                    # popular 单日「仅收集 ID」：只跑阶段 1，不进入 download。
                    pop_date = target_date or _resolve_today()
                    o, n_u_dict = grabber_popular_collect_ids(job, n, pop_date)
                    job.page_done_count += 1
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)
                elif mode == "popular_download_ids":
                    # popular 单日「按 ID 下载」：直接调 task_download_ids，
                    # job.db 此时已指向目标 folder，load_ids_data() 拿当天收集的 ID。
                    # 这里不进入 _page_seq 循环，下面 if mode == "popular_download_ids"
                    # 的 post-loop 阶段 2 切换统一处理。
                    pass
                elif mode == "collect_ids":
                    o, n_u_dict = grabber_collect_ids(job, n)
                    job.page_done_count += 1
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)
                elif mode == "rank":
                    # 排行榜：先按页收集所有 ID（不下载），等所有页跑完再统一按 ID 下载
                    o, n_u_dict = grabber_collect_ids(job, n)
                    job.page_done_count += 1
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)
                else:
                    o, n_u_dict = grabber_rank(job, n)
                    job.page_done_count += 1
                    output = list(set(output + o) - job.db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    job.db.save_hot_drawer(list(set(output)))
                    job.db.save_need_update(nu_sets)
                # 防风控休息已挪进 _fetch_page_or_pause（按请求计，不按页）

            # rank / popular（单日两阶段）模式收完所有页后进入「按 ID 下载」阶段。
            # popular_download_ids 在 per-page 循环里是 pass（直接进 download），
            # 也在这里统一处理阶段 2 切换。
            # 这样前端可以看到两次阶段切换：第一阶段"在收 ID"→ 第二阶段"在下载"，
            # 避免一次性把几百张图堆在流式 new_images 里让用户摸不着头脑。
            if mode in ("rank", "popular", "popular_download_ids") and job.is_running:
                # 页已全部收完（或本就没有页循环），续跑断点清掉；下载阶段停止用
                # failed_ids / ids_data 的「按 ID 重试」恢复，不再需要页游标
                _clear_cursor()
                if mode == "popular":
                    banner = "=== [Popular:Two-Phase] ID 收集完毕，开始按 ID 下载 ==="
                    empty_log = "[Popular:Two-Phase] 没有收集到任何 ID，跳过下载阶段。"
                elif mode == "popular_download_ids":
                    banner = "=== [Popular:DownloadIDs] 开始按 ID 下载 ==="
                    empty_log = "[Popular:DownloadIDs] 当前 folder 没有待下载的 ID，跳过。"
                else:  # rank
                    banner = "=== ID 收集完毕，开始按 ID 下载 ==="
                    empty_log = "[Rank] 没有收集到任何 ID，跳过下载阶段。"
                job.append_log(banner)
                # 把内存里的 pending_ids 同步成 ids_data.json 当前快照（grabber_collect_ids 末尾已写盘）
                try:
                    job.pending_ids = set(str(x) for x in (job.db.load_ids_data() or []))
                except Exception:
                    pass
                if not job.pending_ids:
                    # 没有可下载的 ID，页进度也清零，保持状态干净
                    job.page_total = 0
                    job.page_current = 0
                    job.append_log(empty_log)
                else:
                    # 进入下载阶段：清零页进度，避免 collect 阶段的数字残留
                    job.page_total = 0
                    job.page_current = 0
                    # mode 临时改成 download_ids：让 task_download_ids 用统一的下载路径
                    # （日志标签、并发度、每 20 张刷新都直接复用）
                    prev_mode = job.mode
                    job.mode = "download_ids"
                    try:
                        task_download_ids(job, inline_ids=None)
                    finally:
                        job.mode = prev_mode
            elif mode == "popular_collect_ids" and job.is_running:
                _clear_cursor()
                job.append_log("=== [Popular:Collect] ID 收集完毕（按 ID 下载请用下一阶段） ===")
            elif mode == "collect_ids" and job.is_running:
                _clear_cursor()
                job.append_log("=== [Rank:Collect] ID 收集完毕（按 ID 下载请用下一阶段） ===")

    except Exception as e:
        if job.outcome != "stopped":
            job.outcome = "error"
            job.error_message = str(e)
        job.append_log(f"抓取任务异常中断: {e}")
    finally:
        job.is_running = False
        if job.outcome == "running":
            job.outcome = "completed_with_failures" if job.failed_pages else "completed"
        # 「停止」触发的终止：必须把当前内存里的 ids_data / viewer_data 落盘，
        # 否则用户点停止后立刻关掉 app（或切到下一任务）会丢停止前已收集的 ID / 已下好的图。
        # 正常完成 / 异常终止也跑一遍（幂等，flush 没坏处），保证 worker 退出前一定落盘。
        if job.outcome == "stopped":
            job.append_log("已停止，正在把已收集的 ID / 已下载的图片落盘...")
        job.finalize_on_stop()
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
        # 探活：单次请求不重试，走共享 session（连接池），但不占 Danbooru 限速桶
        resp = http_client.request(
            "GET", url, kind="cdn", retries=0, timeout=5,
            headers=danbooru_api.HEADERS, proxies=proxies,
        )
        if proxies:
            return {"status": "success", "msg": "代理可用（已连通）", "color": "green"}
        else:
            return {"status": "warning", "msg": "直连可用（未使用代理）", "color": "orange"}
    except http_client.PermanentHTTPError as e:
        return {"status": "error", "msg": f"访问异常 ({e.status_code})", "color": "red"}
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


# ==========================================
# Tag 浏览：像 Danbooru 原网页一样按 tag 预览缩略图，勾选后再下载。
# browse_tags 只拉 posts.json 元数据、不落盘；实际下载复用 download_ids 流程
# （前端调 /api/start mode=download_ids）。缩略图由前端 <img> 直接指向
# preview_file_url（直连）或经 /api/proxy_thumb 转发（走代理）。
# ==========================================

# 视频扩展名：这些帖子的 large_file_url **不是图片**。Danbooru 对没有 large 变体的
# 帖子会把 large_file_url 退回 file_url 本体，视频就是 .../original/xx.mp4
# （实测 33 个视频里 32 个如此，第 33 个是三个 url 全空的已删除帖）。
# 前端拿它当缩略图 <img> 既渲染不出来，后端还会把整个视频当"缩略图"拉下来缓存 ——
# 所以单独挑一张静态封面帧给它（见 _browse_video_cover_url）。
_BROWSE_VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv", ".avi"}
# 封面帧变体优先级：360x360 长边正好匹配 Tag 浏览的缩略图档位（_THUMB_SIZE=360），
# 退化时用 180x180。两者对视频都是 jpg。
_BROWSE_COVER_VARIANT_TYPES = ("360x360", "180x180")


def _browse_video_cover_url(post: dict) -> str:
    """给视频挑一张静态封面图 URL。非视频、或挑不到时返回空串。

    数据源是 media_asset.variants（Danbooru 为每个 post 预生成的派生文件）：
    视频的 360x360 / 180x180 变体是 jpg 静态帧，而 720x720 / original 仍是 mp4，
    所以按类型挑中之后还要再确认它不是视频扩展名。
    老数据 / 缺 media_asset 时退回 preview_file_url（180x180 jpg），由调用方兜底。
    """
    ext = "." + (post.get("file_ext") or "").lower()
    if ext not in _BROWSE_VIDEO_EXTS:
        return ""
    media = post.get("media_asset")
    variants = (media or {}).get("variants") if isinstance(media, dict) else None
    if not variants:
        return ""
    by_type = {}
    for v in variants:
        if isinstance(v, dict) and v.get("type") and v.get("url"):
            by_type.setdefault(v["type"], v)
    for t in _BROWSE_COVER_VARIANT_TYPES:
        v = by_type.get(t)
        if not v:
            continue
        # 只要图片变体：视频的 720x720 / original 也是 mp4，不能当封面
        if "." + (v.get("file_ext") or "").lower() not in _BROWSE_VIDEO_EXTS:
            return v["url"]
    return ""


def _slim_post_for_browse(post: dict) -> dict:
    """从 Danbooru posts.json 的单条 post 里挑出前端预览要用的字段。
    已删除 / 无预览图的帖子（file_url 全空）返回 None 由上层过滤。"""
    if not isinstance(post, dict):
        return None
    pid = post.get("id")
    if not pid:
        return None
    preview = post.get("preview_file_url") or ""
    large = post.get("large_file_url") or ""
    full = post.get("file_url") or ""
    # 三个 url 全空一般是被 ban / 需登录的帖子，预览不了也下不了，直接丢弃
    if not (preview or large or full):
        return None
    # 视频的封面帧（非视频恒为空串）：前端 thumbUrl 优先用它，避开"mp4 当缩略图"。
    # variants 里挑不到时退回 preview_file_url（180x180 jpg）—— 仍远好过 mp4 本体。
    cover = ""
    if "." + (post.get("file_ext") or "").lower() in _BROWSE_VIDEO_EXTS:
        cover = _browse_video_cover_url(post) or preview

    artist = ""
    artist_str = post.get("tag_string_artist") or ""
    if artist_str:
        drawers = [t for t in artist_str.split(" ") if t and not t.lower().endswith("(voice_actor)")]
        if drawers:
            artist = " ".join(drawers)

    return {
        "id": str(pid),
        "preview_file_url": preview,
        "large_file_url": large,
        "file_url": full,
        # 视频专用静态封面帧（非视频为空串）。见 _browse_video_cover_url 的注释：
        # 视频的 large_file_url 指向 mp4 本体，缩略图必须改用它。
        "cover_file_url": cover,
        "image_width": post.get("image_width") or 0,
        "image_height": post.get("image_height") or 0,
        "rating": post.get("rating") or "",
        "score": post.get("score") or 0,
        "fav_count": post.get("fav_count") or 0,
        "file_ext": post.get("file_ext") or "",
        "file_size": post.get("file_size") or 0,
        "tag_string_artist": artist,
        "tag_string_character": post.get("tag_string_character") or "",
        "created_at": post.get("created_at") or "",
        # 已下载标记：log.json 里有这个 id 就给前端打点用（不命中就不带这个 key，省一点字节）
        **({"downloaded": True} if str(pid) in log_store else {}),
    }


@app.get("/api/browse_tags")
def browse_tags(tags: str = "", page: int = 1, limit: int = 40):
    """按 tag 查询串拉一页 posts.json 元数据供前端预览，不下载任何图片。
    tags 支持 Danbooru 多 tag 语法；SFW/代理开关自动跟随 danbooru_api 当前状态。"""
    tags = (tags or "").strip()
    if not tags:
        return {"ok": False, "msg": "请填写 tag 查询串", "posts": []}
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 40
    limit = max(1, min(limit, 200))  # Danbooru 单页上限 200

    try:
        raw = danbooru_api.get_posts_by_tags(tags, page, limit=limit)
    except Exception as e:
        return {"ok": False, "msg": f"获取失败: {e}", "posts": []}

    if not isinstance(raw, list):
        return {"ok": False, "msg": "Danbooru 返回格式异常（可能触发限流或 tag 无效）", "posts": []}

    posts = [slim for slim in (_slim_post_for_browse(p) for p in raw) if slim]
    return {
        "ok": True,
        "tags": tags,
        "page": page,
        "limit": limit,
        "count": len(posts),
        "has_more": len(raw) >= limit,  # 拉满一页就假定还有下一页
        "host": danbooru_api.get_host(),
        "posts": posts,
    }


@app.get("/api/browse_rank")
def browse_rank(page: int = 1, limit: int = 40):
    """按 Danbooru order:rank 拉一页 posts.json 元数据供前端预览，不下载任何图片。
    与 /api/browse_tags 同样的 limit 1..200 夹紧；SFW/代理开关自动跟随 danbooru_api 当前状态。"""
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 40
    limit = max(1, min(limit, 200))  # 与 browse_tags 同样的 200 上限

    try:
        raw = danbooru_api.get_posts_by_rank(page=page, limit=limit, timeout=20)
    except Exception as e:
        return {"ok": False, "msg": f"获取失败: {e}", "posts": []}

    if not isinstance(raw, list):
        return {"ok": False, "msg": "Danbooru 返回格式异常（可能触发限流）", "posts": []}

    posts = [slim for slim in (_slim_post_for_browse(p) for p in raw) if slim]
    return {
        "ok": True,
        "page": page,
        "limit": limit,
        "count": len(posts),
        "has_more": len(raw) >= limit,
        "host": danbooru_api.get_host(),
        "posts": posts,
    }


# 只允许转发 Danbooru 自家 CDN 的图片，避免 /api/proxy_thumb 变成开放 SSRF 代理
_PROXY_THUMB_ALLOWED_HOSTS = (
    "donmai.us",
    "cdn.donmai.us",
)

# 在线预览图（Tag 浏览 / 收集ID 预览）的本地磁盘缓存。
# 缩略图一张 ~20-50KB，命中缓存即免网络；超过上限按最久未访问(LRU，用文件 mtime 近似)淘汰。
_BROWSE_THUMB_CACHE_DIR = HOT_PIC_DIR / ".browse_thumb_cache"
# 缩略图源走 large_file_url（720px），Pillow 服务端缩到长边 360px 落盘：单张 ~30-60KB。
# 缓存 500 张约 ~20MB，权衡清晰度（720→360 比 150→360 upscale 清晰得多）和磁盘开销。
_BROWSE_THUMB_MAX = 500
_BROWSE_THUMB_LOCK = threading.Lock()
# 允许缓存落盘的图片扩展名（防止把 html 错误页之类的东西也缓存下来）
_BROWSE_THUMB_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif"}


def _browse_thumb_cache_path(url: str, size: int = 0) -> Path:
    """用 url 的 sha1 + 原扩展名做缓存文件名。扩展名不认识时统一按 .jpg。
    带 size 时：缩放后的版本固定按 .jpg 存（LANCZOS 输出无透明通道，PNG/WebP 全部转 JPEG
    省字节），文件名加 -s<size> 后缀避免跟原图缓存撞名。"""
    from urllib.parse import urlparse
    if size and size > 0:
        digest = hashlib.sha1(f"{url}|size={size}".encode("utf-8")).hexdigest()
        return _BROWSE_THUMB_CACHE_DIR / f"{digest}-s{size}.jpg"
    ext = os.path.splitext(urlparse(url).path)[1].lower()
    if ext not in _BROWSE_THUMB_EXTS:
        ext = ".jpg"
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return _BROWSE_THUMB_CACHE_DIR / (digest + ext)


def _evict_browse_thumb_cache():
    """缓存文件数超过 _BROWSE_THUMB_MAX 时，按 mtime 升序删最旧的，删到上限以内。
    串行执行（外层持锁），避免多请求并发写时重复扫描。"""
    try:
        files = [
            (f, f.stat().st_mtime)
            for f in _BROWSE_THUMB_CACHE_DIR.iterdir()
            if f.is_file() and not f.name.endswith(".tmp")
        ]
    except FileNotFoundError:
        return
    if len(files) <= _BROWSE_THUMB_MAX:
        return
    files.sort(key=lambda x: x[1])  # 最旧的在前
    for f, _ in files[: len(files) - _BROWSE_THUMB_MAX]:
        try:
            f.unlink()
        except OSError:
            pass


@app.get("/api/collected_ids")
def collected_ids(date: str = ""):
    """读取某日期 folder 的 ids_data.json（「仅收集ID」模式的产物），
    返回纯数字 ID 列表供前端在线预览。date 省略 = 今天。"""
    date = (date or "").strip() or _resolve_today()
    try:
        db = DanbooruData(target_date=date)
        raw = db.load_ids_data() or []
    except Exception as e:
        return {"ok": False, "msg": f"读取失败: {e}", "date": date, "ids": [], "count": 0}
    # 只留纯数字串并去重，按数值稳定排序
    seen = set()
    ids = []
    for x in raw:
        s = str(x).strip()
        if s.isdigit() and s not in seen:
            seen.add(s)
            ids.append(s)
    ids.sort(key=lambda s: int(s))
    return {"ok": True, "date": date, "ids": ids, "count": len(ids)}


@app.get("/api/downloaded_ids")
def downloaded_ids(contains: str = ""):
    """诊断 / 调试用：返回全局 log_store 的 post id 集合（字符串形式）。
    ?contains=11897341 可以只查某个 id 是否在内存里（避免把几万条 id 一次返回）。
    之前 Tag 浏览「已下载」不显示时，用这个判断后端 in-memory 状态是否正确同步。"""
    snap = log_store.snapshot()
    if contains:
        s = str(contains).strip()
        return {
            "ok": True,
            "contains": s,
            "in_store": s in snap,
            "total": len(snap),
        }
    # 不带 contains 时只返回总数和前 20 条，避免大 payload
    sample = sorted(snap.keys(), key=lambda x: int(x) if x.isdigit() else 0)[:20]
    return {
        "ok": True,
        "total": len(snap),
        "sample": sample,
    }


@app.get("/api/proxy_thumb")
def proxy_thumb(url: str = "", size: int = 0):
    """把 Danbooru 在线预览图经后端转发给浏览器 <img>，并落盘缓存。
    - 直连模式下浏览器直接连 Danbooru CDN 会被防盗链/网络挡掉，统一走这里最稳。
    - 命中本地缓存即免网络；未命中则拉取后写入缓存并做 LRU 淘汰。
    - 带域名白名单防 SSRF。
    - 可选 ?size=360：拉到大图后 Pillow LANCZOS 缩到长边 size 落盘 JPEG；用于 Tag 浏览想要
      比 Danbooru preview(150) 清晰、比 large(720) 省缓存的场景。GIF 走首帧，其他格式自动
      转 RGB / JPEG。size≤0 或缺省 = 不缩放，原图透传。"""
    url = (url or "").strip()
    if not url:
        return PlainTextResponse("missing url", status_code=400)

    from urllib.parse import urlparse
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return PlainTextResponse("bad scheme", status_code=400)
    host = (parsed.hostname or "").lower()
    if not any(host == h or host.endswith("." + h) for h in _PROXY_THUMB_ALLOWED_HOSTS):
        return PlainTextResponse("host not allowed", status_code=403)

    # 视频本体（.mp4/.webm…）不允许进缩略图管线：Danbooru 对没有 large 变体的帖子会把
    # large_file_url 退回 file_url 本体，前端若直接把它丢进来，这里会把整个视频（动辄
    # 几十 MB）拉下来 —— Pillow 解不开、降级成原图透传，最后以 `.jpg` 名义写进
    # .browse_thumb_cache，既白费带宽又留下"名为 jpg 实为 mp4"的脏缓存（命中后浏览器
    # 解码失败 = 缩略图空白）。正确做法是前端改用 cover_file_url（见 _browse_video_cover_url）。
    # 这里 415 掉，让问题在日志里显形，而不是静默地持续恶化。
    if os.path.splitext(parsed.path)[1].lower() in _BROWSE_VIDEO_EXTS:
        print(f"[browse_thumb] 拒绝视频本体当缩略图: {url}")
        return PlainTextResponse("video is not a thumbnail", status_code=415)

    try:
        size = int(size or 0)
    except (TypeError, ValueError):
        size = 0
    if size < 0:
        size = 0
    # 限制范围，防止有人传 99999 拖死 CPU
    if size > 0:
        size = max(64, min(size, 2048))

    cache_path = _browse_thumb_cache_path(url, size)
    # 1) 命中缓存：更新 mtime（LRU 访问时间）后直接返回
    if cache_path.exists():
        try:
            now = datetime.datetime.now().timestamp()
            os.utime(cache_path, (now, now))
        except OSError:
            pass
        return FileResponse(cache_path, headers={"Cache-Control": "public, max-age=86400"})

    # 2) 未命中：拉取（共享 session 连接池，CDN 类不占限速桶；429/瞬时错允许 2 次重试）
    try:
        r = http_client.request(
            "GET", url,
            kind="cdn", retries=2, timeout=20,
            headers=danbooru_api.HEADERS,
            proxies=danbooru_api.PROXIES,
        )
    except http_client.PermanentHTTPError as e:
        return PlainTextResponse("upstream error", status_code=e.status_code)
    except Exception as e:
        return PlainTextResponse(f"fetch error: {e}", status_code=502)

    content = r.content
    content_type = r.headers.get("Content-Type", "image/jpeg")
    output_bytes = content
    output_type = content_type

    # 2.5) 可选：服务端缩放。失败（Pillow 不支持该格式 / 损坏 / 动画 GIF）时降级到原图透传。
    if size > 0:
        try:
            from PIL import Image
            import io
            with Image.open(io.BytesIO(content)) as im:
                is_animated = getattr(im, "is_animated", False)
                if is_animated:
                    # 动图（GIF/WebP）：保留原图透传，不缩第一帧（避免预览图变静帧）
                    pass
                else:
                    im.thumbnail((size, size), Image.Resampling.LANCZOS)
                    if im.mode in ("RGBA", "LA", "P"):
                        # 透明通道转白底，避免 JPEG 存成黑色
                        bg = Image.new("RGB", im.size, (255, 255, 255))
                        if im.mode == "P":
                            im = im.convert("RGBA")
                        bg.paste(im, mask=im.split()[-1] if im.mode in ("RGBA", "LA") else None)
                        im = bg
                    elif im.mode != "RGB":
                        im = im.convert("RGB")
                    buf = io.BytesIO()
                    im.save(buf, format="JPEG", quality=85, optimize=True)
                    output_bytes = buf.getvalue()
                    output_type = "image/jpeg"
        except Exception as e:
            # 缩放失败不致命：返回原图，仍可缓存到不带 size 后缀的路径
            print(f"[browse_thumb] 缩放失败 {url} (size={size}): {e}")
            output_bytes = content
            output_type = content_type
            # 失败时改用无 size 路径作为 fallback，避免反复尝试
            cache_path = _browse_thumb_cache_path(url, 0)

    # 3) 写入缓存（原子写：先写 .tmp 再 os.replace）+ 触发 LRU 淘汰
    try:
        _BROWSE_THUMB_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(cache_path.suffix + ".tmp")
        with open(tmp_path, "wb") as f:
            f.write(output_bytes)
        os.replace(tmp_path, cache_path)
        with _BROWSE_THUMB_LOCK:
            _evict_browse_thumb_cache()
    except OSError as e:
        # 缓存写失败不影响本次返回，只是下次还得重新拉
        print(f"[browse_thumb] 缓存写入失败 {url}: {e}")

    return Response(
        content=output_bytes,
        media_type=output_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )



@app.post("/api/start")
def start_scraper(req: StartRequest, background_tasks: BackgroundTasks):
    # 先让正在跑的空闲下载轮次收手 —— 它是我们自己起的后台任务，不能拿它把用户的
    # 「开始」挡回去（MAX_CONCURRENT=1，不让位的话用户只会看到「已有 1 个任务在跑」）。
    _yield_idle_jobs()
    if not jobs.can_start():
        active = jobs.list_active()
        return {
            "ok": False,
            "msg": f"已有 {len(active)} 个任务在跑，达到并发上限 {jobs.MAX_CONCURRENT}",
        }

    filter_tags = [t.strip() for t in req.tags.split(',') if t.strip()]
    tag_source = "gelbooru" if (req.tag_source or "").lower() == "gelbooru" else "danbooru"

    # 不同 mode 落到不同的 target_folder：rank/collect_ids 跟随真今天；popular/download_ids
    # 跟随用户指定 target_date；popular_range 用 start_date 做起点，之后 job 自己用
    # switch_target 按日期迭代；tags 算出 tag_xxx 文件夹名。
    # popular_*/popular_range_* 三个新子动作与对应原 mode 共用同一 folder 规则。
    if req.mode == "tags":
        if not (req.tag_query or "").strip():
            return {"ok": False, "msg": "tags 模式需要填写 tag 查询串。"}
        folder_name = sanitize_tag_folder(req.tag_query)
        if not folder_name:
            return {"ok": False, "msg": f"tag 查询串 [{req.tag_query}] 转文件夹名失败。"}
        target_folder = folder_name
        label = f"tags:{tag_source} · {req.tag_query}"
    elif req.mode == "popular_recover":
        # 补全/补齐必须有明确的目标日期 —— 不允许默认今天，否则会把"补 2026-03-07" 错下到今天的目录。
        # 历史上 popular_recover 与 popular 共用「未指定则今天」的兜底，导致前端偶发传空时
        # 静默写到 hot_pic/<today>/，label 写着补齐补全实际却下今天的热门（11.94M 段 ID）。
        # 与下面 recover_popular legacy alias 行为对齐：target_date 必填，缺则报错。
        if not (req.target_date or "").strip():
            return {"ok": False, "msg": "popular_recover 模式需要 target_date（YYYY-MM-DD）。"}
        target_folder = req.target_date
        sub_label = {
            "popular_recover": "popular:recover",
        }[req.mode]
        label = f"{sub_label} · {target_folder}"
    elif req.mode in ("popular", "popular_collect_ids", "popular_download_ids"):
        target_folder = req.target_date or _resolve_today()
        sub_label = {
            "popular": "popular",
            "popular_collect_ids": "popular:collect",
            "popular_download_ids": "popular:download_ids",
        }[req.mode]
        label = f"{sub_label} · {target_folder}"
    elif req.mode in ("popular_range", "popular_range_collect_ids", "popular_range_download_ids"):
        if not req.start_date or not req.end_date:
            return {"ok": False, "msg": "日期范围缺失。"}
        target_folder = req.start_date
        sub_label = {
            "popular_range": "popular_range",
            "popular_range_collect_ids": "popular_range:collect",
            "popular_range_download_ids": "popular_range:download_ids",
        }[req.mode]
        label = f"{sub_label} · {req.start_date}~{req.end_date}"
    elif req.mode == "download_ids":
        target_folder = req.target_date or _resolve_today()
        label = f"download_ids · {target_folder}"
    elif req.mode == "recover_popular":
        if not req.target_date:
            return {"ok": False, "msg": "recover_popular 模式需要 target_date。"}
        target_folder = req.target_date
        label = f"recover_popular · {target_folder}"
    else:
        target_folder = _resolve_today()
        label = f"{req.mode} · {target_folder}"

    # 断盘/路径漂移保护（仅 popular_recover / recover_popular 模式）：
    # 补全必须写到原盘上的原日期目录；如果 library_roots 任何一个都找不到目标日期，
    # 就拒绝启动任务，避免 makedirs 兜底把图静默写到 HOT_PIC_DIR 的空目录里。
    save_dir_override = None
    if req.mode in ("popular_recover", "recover_popular"):
        save_dir_override, root_id = _resolve_save_dir_for_date(target_folder)
        if not save_dir_override:
            # 盘没接 / 日期不在任何图库根目录：返回结构化错误 + 本地回退路径，
            # 前端弹确认框 → 用户点确认后重发 force_local=true → 写到本地 HOT_PIC_DIR
            # 之后再手动复制到原盘做"增量更新"。
            local_path = str(Path(str(HOT_PIC_DIR)) / target_folder)
            if req.force_local:
                # 用户已确认 force_local：直接走本地路径，不再校验
                save_dir_override = local_path
                root_id = "local(HOT_PIC_DIR)"
                label = f"{label} [{root_id}] · 需手动同步"
            else:
                roots_list = ", ".join(r["path"] for r in get_library_roots())
                return {
                    "ok": False,
                    "code": "DRIVE_UNPLUGGED",
                    "msg": (
                        f"目标日期 {target_folder} 在所有图库根目录都找不到。\n"
                        f"已配置的根目录：[{roots_list}]\n"
                        f"可能原因：① 日期拼写错（应该是 YYYY-MM-DD）；② 移动硬盘没接 / 路径不可访问。\n\n"
                        f"是否临时改写到本地？下载完成后手动把图片移动到原盘。"
                    ),
                    "local_path": local_path,
                }
        else:
            # 把 root 标到 label 上，让用户知道任务落到哪块盘
            label = f"{label} [{root_id}]"

    job = _make_job(target_folder, req.mode, filter_tags, label, tag_source=tag_source, download_concurrency=req.download_concurrency, save_dir=save_dir_override, local_only=bool(req.force_local), skip_logged=bool(req.skip_logged), retry_only=bool(req.retry_only))
    job.tag_query = req.tag_query or ""
    job.tag_source = tag_source
    # tag_query/source 此时才就位：从 deck.db 恢复本 scope 持久化的失败页（旧实现重启即丢）
    job.hydrate_failed_pages()
    job.is_running = True
    job.outcome = "running"
    job.error_message = ""
    job.play_event.set()
    # ⚠ 注册必须走闸门（而不是 jobs.add）：开头的 can_start() 早退只是省下构造开销，
    #   真正的互斥在这里 —— 上面那句 _yield_idle_jobs() 与本句之间仍有一段缝，空闲
    #   调度器完全可能刚好又起一轮。被占就再让一次位重试，仍失败才认输报错。
    if not _register_job(job):
        _yield_idle_jobs()
        if not _register_job(job):
            job.is_running = False
            job.outcome = "stopped"
            return {
                "ok": False,
                "msg": f"已有 {len(jobs.list_active())} 个任务在跑，达到并发上限 {jobs.MAX_CONCURRENT}",
            }

    job.thread = threading.Thread(
        target=_run_job,
        args=(job, req.start_page, req.end_page, req.mode, req.target_date,
              req.start_date, req.end_date, req.ids, req.tag_query, tag_source,
              [int(p) for p in (req.pages or [])]),
        daemon=True,
    )
    job.thread.start()

    mode_labels = {
        "rank": "排行抓取", "popular": "Popular热门", "collect_ids": "仅收集ID",
        "download_ids": "按ID下载", "popular_range": "日期范围", "tags": "Tag下载",
        "popular_collect_ids": "热门·仅收集ID", "popular_download_ids": "热门·按ID下载",
        "popular_range_collect_ids": "日期范围·仅收集ID",
        "popular_range_download_ids": "日期范围·按ID下载",
        "popular_recover": "热门·补全/补齐",
        "recover_popular": "补全热门",
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


def _group_failed_ids(failed_ids: dict):
    """把 {ids: folder} 分组成 [{folder, ids:[...]}]，供前端按 folder 一键重试。"""
    by_folder = {}
    for ids, folder in (failed_ids or {}).items():
        by_folder.setdefault(folder or "", []).append(str(ids))
    return [{"folder": folder, "ids": sorted(ids_list)} for folder, ids_list in by_folder.items()]


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
    job.outcome = "stopped"
    job.is_running = False
    job.play_event.set()
    # 真正落盘在 worker 线程的 finally 块里做（finalize_on_stop）：
    # 1) collect 阶段 → pending_ids 同步到 ids_data.json
    # 2) download 阶段 → viewer_data flush 到 viewer_data.json
    # 这里只发停止信号 + 通知用户，避免和 worker 抢写磁盘。
    job.append_log("已强制结束任务，正在等待落盘...")
    return {"msg": "已强制结束任务", "job_id": job.job_id}


@app.post("/api/shutdown")
def shutdown_backend():
    """Electron 退出前的优雅关闭口（替代直接 taskkill /F 导致 Python finally 不执行、
    最后 ≤20 条已下载记录丢失）：

    1. 给所有活跃任务发停止信号（同 /api/stop）；
    2. 给 worker 线程最多 3s 自行收尾（它们本来就在多个检查点轮询 is_running）；
    3. 在本请求线程内同步跑一遍 finalize_on_stop() —— 所有写都走 viewer_lock /
       per-path 文件锁，与 worker 互斥；worker 之后自己的 finalize 只是幂等重放；
    4. deck.db 做一次 TRUNCATE checkpoint（log/stats 本来就逐条已落 WAL），
       返回响应，1s 后 os._exit(0)。uvicorn 句柄在 -c 启动串内不可达，os._exit 最直接。
    """
    active = jobs.list_active()
    finalized = []
    for job in active:
        job.outcome = "stopped"
        job.is_running = False
        job.play_event.set()
    for job in active:
        if job.thread is not None and job.thread.is_alive():
            job.thread.join(timeout=3.0)
    for job in active:
        try:
            job.finalize_on_stop()
        except Exception as e:
            print(f"[shutdown] finalize job {job.job_id} failed: {e}")
        finalized.append(job.job_id)
    try:
        _persist_global_data()
    except Exception as e:
        print(f"[shutdown] persist global data failed: {e}")
    try:
        # 把 WAL 合回 deck.db 主文件再退出，避免长期只靠 -wal 承载历史
        deck_db.checkpoint("TRUNCATE")
    except Exception as e:
        print(f"[shutdown] deck.db checkpoint failed: {e}")

    def _exit():
        # 守护线程里退出：给 uvicorn 1s 把本响应发回 Electron
        os._exit(0)

    threading.Timer(1.0, _exit).start()
    return {"ok": True, "finalized_jobs": finalized}


@app.get("/api/status")
def get_status(job_id: str = ""):
    """返回 primary job（或指定 job_id）的状态。
    没活跃任务时所有关键字段都返回 falsy，前端 sync 看到 is_running=False 自动收尾。"""
    job = _resolve_job(job_id)

    if job is None:
        return {
            "is_running": False,
            "is_stopping": False,
            "is_paused": False,
            "target_folder": "",
            "new_logs": [],
            "new_images": [],
            "failed_pages": [],
            "failed_ids": [],
            "cursor": None,
            "job_id": "",
            "outcome": "idle",
            "error_message": "",
            "progress": {"total": 0, "success": 0, "skip": 0, "fail": 0},
            "page_progress": {"current": 0, "total": 0},
            "jobs": [],
            # 没有 job 时 is_idle 也要在（前端无条件读它，缺字段会让上一次的值残留成 true）
            "is_idle": False,
            # 空闲下载：没有任务时前端仍要能显示它的开关状态 / 下一轮倒计时 / 积压数
            "idle_download": _idle_download_payload(),
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
        # 进度条 3 个数一起快照；外部 success_count / fail_count 在 worker 线程累加，
        # 读取走 viewer_lock 保证看到一致值
        progress_snapshot = {
            "total": int(getattr(job, "total_planned", 0) or 0),
            "success": int(getattr(job, "success_count", 0) or 0),
            "skip": int(getattr(job, "skip_count", 0) or 0),
            "fail": int(getattr(job, "fail_count", 0) or 0),
        }
        # 抓取 ID 阶段的页进度：page_current / page_total 由 collect 循环写入，
        # 进入 download 时清零；前端在 total=0 时不渲染条
        page_progress_snapshot = {
            "current": int(getattr(job, "page_current", 0) or 0),
            "total": int(getattr(job, "page_total", 0) or 0),
            "done": int(getattr(job, "page_done_count", 0) or 0),
        }

    thread_alive = bool(job.thread is not None and job.thread.is_alive())
    # 页断点（deck.db page_cursor）：收 ID 阶段每页落盘，自然跑完/进入下载阶段清掉；
    # 停止后仍在 → 前端据此出「从第 N 页续跑」。PK 点查，轮询开销可忽略。
    try:
        cursor_snapshot = deck_db.cursor_load(
            job.target_folder, getattr(job, "tag_query", "") or "",
            getattr(job, "tag_source", "danbooru") or "danbooru")
    except Exception:
        cursor_snapshot = None
    return {
        "job_id": job.job_id,
        "is_running": job.is_running,
        "is_stopping": not job.is_running and thread_alive,
        # is_paused 属性 = is_running 且 play_event 被清空：用户手动暂停、限流/超时
        # 「原地暂停」、连续永久错 auto-pause 三种挂起都算 —— 前端「继续」按钮据此可用。
        "is_paused": job.is_paused,
        "outcome": job.outcome,
        "error_message": job.error_message,
        # 当前下载实际写入的子目录名（日期 "YYYY-MM-DD" 或 tag 文件夹 "tag_xxx"）。
        # 前端用这个匹配 selectedDate 决定 new_images 该不该追加进当前画廊。
        "target_folder": job.target_folder,
        "new_logs": logs,
        "new_images": new_images,
        # 抓取页失败的页（[{folder, page}]），不 drain，保留到 job 被清；
        # 任务暂停中（用户点「继续」前）也保留，方便用户看「当前有 N 页未抓到」。
        "failed_pages": list(job.failed_pages),
        # 图片下载失败的 id（按 folder 分组 [{folder, ids:[...]}]），不 drain；前端据此弹「按 id 重试」。
        # 这些 id 仍留在对应 folder 的 ids_data.json 里，重试即用 download_ids 模式消费。
        "failed_ids": _group_failed_ids(job.failed_ids),
        # 任务创建时的过滤标签（rank/popular/tags 都会设）。暴露给前端是为了在
        # 「重新入队失败的页」时能拿到原 tags 重建请求，避免用当前 form 的 tag 误命中。
        "filter_tags": list(getattr(job, "filter_tags", []) or []),
        # 本次停止时落盘到 ids_data.json 的条数 / 路径（finalize_on_stop 写入）。
        # 任务结束后被前端 syncStatusOnce 读出来做 toast 文案；任务运行中始终 0。
        "last_saved_ids_count": int(getattr(job, "last_saved_ids_count", 0) or 0),
        "last_ids_data_path": getattr(job, "last_ids_data_path", "") or "",
        # 进度条数据：task_download_ids 入口处把 total_planned 设上并清零 success/fail；
        # 其它模式（rank / popular 单段 / tags）total 一直 = 0，前端不渲染 bar 只显示数字
        "progress": progress_snapshot,
        # 抓取 ID 阶段的页进度（collect 循环里写入，download 阶段清零；total=0 时前端不渲染条）
        "page_progress": page_progress_snapshot,
        "mode": job.mode,
        "tag_query": job.tag_query,
        "tag_source": job.tag_source,
        # 收 ID 阶段的页断点：停止后非空 → 一键「从第 N 页续跑（N–原末页）」
        "cursor": cursor_snapshot,
        # 是不是「空闲下载」轮次：前端据此静默收尾（不弹「任务完成」、不把视图拽到别的日期）
        "is_idle": bool(getattr(job, "is_idle", False)),
        "idle_download": _idle_download_payload(),
        # 给前端将来扩展 "多任务 UI" 用的列表；目前只有 1 个（MAX_CONCURRENT=1）
        "jobs": [
            {
                "job_id": j.job_id,
                "target_folder": j.target_folder,
                "mode": j.mode,
                "tag_source": j.tag_source,
                "label": j.label,
                "is_running": j.is_running,
                "is_idle": bool(getattr(j, "is_idle", False)),
            }
            for j in jobs.list_active()
        ],
    }

@app.get("/api/recovery_state")
def get_recovery_state(folder: str = "", tag_query: str = "", tag_source: str = "danbooru",
                       mode: str = "", target_date: str = ""):
    """任务结束（或重启 App）后查询某 scope 的可恢复状态：

    - cursor：收 ID 阶段中途停止留下的页断点 {mode,page,start_page,end_page,...}，
      前端据此出「从第 N 页续跑（N–原末页）」；自然跑完/已进入下载阶段则为 null。
    - failed_pages：永久错误（400/403/404）跳过的页号（int 列表），前端出
      「重试失败页」走 pages=[...] 定向重试；瞬时错误从不进这里。

    scope = (folder, tag_query, tag_source)，与 failed_pages / page_cursor 表一致。
    folder 可省略，由 mode 推导（前端无需复刻 tag 文件夹命名规则）：tags →
    sanitize_tag_folder(tag_query)；rank/collect_ids → 真今天；popular* → target_date。
    纯 deck.db 读取，不碰网络；job 已离开注册表（30s 保留期外/重启后）也能查到。
    """
    folder = (folder or "").strip()
    tag_query = tag_query or ""
    tag_source = tag_source or "danbooru"
    if not folder:
        if mode == "tags":
            if not tag_query.strip():
                return {"ok": False, "msg": "tags 模式需要 tag_query",
                        "cursor": None, "failed_pages": []}
            folder = sanitize_tag_folder(tag_query)
        elif mode in ("popular", "popular_collect_ids", "popular_recover"):
            folder = (target_date or "").strip()
            if not folder:
                return {"ok": False, "msg": "popular 模式需要 target_date",
                        "cursor": None, "failed_pages": []}
        elif mode in ("rank", "collect_ids"):
            folder = _resolve_today()
        else:
            return {"ok": False, "msg": "缺少 folder 参数（或指定可推导的 mode）",
                    "cursor": None, "failed_pages": []}
    try:
        cursor = deck_db.cursor_load(folder, tag_query=tag_query, tag_source=tag_source)
        failed = [r["page"] for r in
                  deck_db.failed_page_load_scope(folder, tag_query=tag_query, tag_source=tag_source)]
    except Exception as e:
        return {"ok": False, "msg": f"读取恢复状态失败: {e}", "cursor": None, "failed_pages": []}
    return {"ok": True, "folder": folder, "tag_query": tag_query, "tag_source": tag_source,
            "cursor": cursor, "failed_pages": failed}


@app.get("/api/gallery_data")
def get_gallery_data():
    selected_date, available_dates = resolve_selected_date()
    return {
        "latest_images": [],
        "local_images": build_local_image_library(selected_date),
        "selected_date": selected_date,
        "available_dates": available_dates,
        "available_date_folders": get_available_date_folder_details(),
        "available_tags": get_available_tag_folders(),
        "library_roots": get_library_roots_payload(),
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
        "available_date_folders": get_available_date_folder_details(),
        "available_tags": get_available_tag_folders(),
        "library_roots": get_library_roots_payload(),
        # 同上，使用系统日历今天而不是会被下载任务 hijack 的 today_str。
        "today": datetime.datetime.now().strftime("%Y-%m-%d"),
        "requested_date": date_str
    }

@app.get("/api/search_entries")
def api_search_entries(q: str = "", kind: str = "auto", start: str = "",
                       end: str = "", limit: int = 120, offset: int = 0,
                       sort: str = "date"):
    """跨日期本地搜索：在 deck.db 内按角色/作品系列/作者整词匹配，日期闭区间可选。

    character 维度同时匹配 tag_character 与 tag_copyright（作品/系列 tag，如 azur_lane；
    单日画廊搜 azur_lane 靠角色 tag 的 _(系列名) 后缀子串命中，这里用 copyright 整词对齐）。
    只查当前在线的图库根目录；纯离线搜索，不发任何网络请求。
    返回结构与 gallery_data 的 local_images 卡片一致（每项额外带 date，可跨日期）。
    """
    empty = {"ok": False, "items": [], "total": 0, "limit": limit, "offset": offset}
    q = (q or "").strip()
    if not q:
        return {**empty, "msg": "请输入要搜索的角色或作者"}
    if kind not in ("auto", "character", "artist"):
        kind = "auto"
    kinds = {"character", "artist"} if kind == "auto" else {kind}

    start = (start or "").strip()
    end = (end or "").strip()
    for d in (start, end):
        if d:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                return {**empty, "msg": "日期必须是 YYYY-MM-DD"}
            try:
                datetime.datetime.strptime(d, "%Y-%m-%d")
            except ValueError:
                return {**empty, "msg": "日期不是合法日历日期"}
    if start and end and start > end:
        start, end = end, start
    if sort not in ("date", "score", "fav_count"):
        sort = "date"

    token_groups, expanded_tags = _expand_search_tokens(q, kind)
    if not token_groups:
        hint = "中文角色名未在离线字典中找到对应 tag" if kind != "artist" else "作者请使用 Danbooru 英文名"
        return {**empty, "msg": f"没有可搜索的 tag（{hint}）"}

    online_roots = [r for r in get_library_roots() if r["path"].is_dir()]
    offline_roots = [
        {"id": r["id"], "label": r["label"]}
        for r in get_library_roots() if not r["path"].is_dir()
    ]
    roots_by_id = {r["id"]: r for r in online_roots}
    total, rows = deck_db.viewer_search(
        token_groups, kinds,
        folder_start=start or None, folder_end=end or None,
        library_ids=[r["id"] for r in online_roots],
        limit=limit, offset=offset, sort=sort,
    )
    items = _search_rows_to_items(rows, roots_by_id)
    return {
        "ok": True,
        "items": items,
        "total": total,
        "returned": len(items),
        "limit": max(1, min(int(limit or 120), 500)),
        "offset": max(0, int(offset or 0)),
        "kind": kind,
        "sort": sort,
        "matched_kinds": sorted(kinds),
        "tokens": token_groups,
        "expanded_tags": expanded_tags,
        "online_libraries": [{"id": r["id"], "label": r["label"]} for r in online_roots],
        "offline_libraries": offline_roots,
    }

@app.get("/api/library_root_summary")
def get_library_root_summary(date: str = ""):
    """Return per-root counts for one date so same-date libraries are explicit."""
    try:
        datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "msg": "date 必须是 YYYY-MM-DD"}

    media_exts = {
        ".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".avif",
        ".zip", ".mp4", ".webm", ".mov", ".mkv", ".avi"
    }
    result = []
    for root in get_library_roots():
        date_dir = root["path"] / date
        viewer_path = date_dir / "viewer_data.json"
        accessible = False
        media_count = 0
        viewer_count = 0
        try:
            accessible = date_dir.is_dir()
            if accessible:
                media_count = sum(
                    1 for item in date_dir.iterdir()
                    if item.is_file() and item.suffix.lower() in media_exts
                )
                # viewer_count 以 deck.db 为准（先吸收镜像外部改动）；DB 故障时回退 JSON 解析
                try:
                    deck_db.reconcile_viewer(root["id"], date, str(viewer_path))
                    viewer_count = deck_db.viewer_count(root["id"], date)
                except Exception:
                    if viewer_path.is_file():
                        raw = load_json(str(viewer_path), [])
                        viewer_count = len(dedup_viewer_data(raw if isinstance(raw, list) else []))
        except OSError:
            accessible = False
        result.append({
            "id": root["id"],
            "label": root["label"],
            "path": str(root["path"]),
            "is_default": bool(root.get("is_default", False)),
            "accessible": accessible,
            "date_dir": str(date_dir),
            "viewer_path": str(viewer_path),
            "media_count": media_count,
            "viewer_count": viewer_count,
            "has_viewer_data": viewer_path.is_file(),
        })
    return {"ok": True, "date": date, "roots": result}


@app.post("/api/open_local")
def open_local_file(req: OpenLocalRequest):
    try:
        target_path = Path(req.local_path).resolve()
        if not is_path_in_library_roots(target_path):
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

    # 先扫出候选孤立文件名，再用 DB 批量点查 filename→post_id（旧实现先建 52 万行全量
    # 内存 map 再查，单次刷新白白吃 ~30MB；点查语义与 log_pids_for_filenames 完全一致）。
    candidate_names = []
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
        candidate_names.append(name)

    fn_to_pid = deck_db.log_pids_for_filenames(candidate_names) if candidate_names else {}
    orphans = [(name, fn_to_pid[name]) for name in candidate_names if fn_to_pid.get(name)]

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
                "tag_string_meta": post.get('tag_string_meta', ''),
                "rating": post.get('rating', '') or ''
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
        new_rating = post.get("rating", "") or ""
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
        # 旧图往往没有 rating（早期版本没保存），刷新时顺手补进 tags.rating
        if new_rating:
            tags = item.setdefault("tags", {})
            if tags.get("rating") != new_rating:
                tags["rating"] = new_rating
                changed = True
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
    # ⚠ 让空闲下载轮次收手 —— 刷新是用户按下的动作，优先级高于后台补刀。
    #   ⚠⚠ 顺序不能反：**先占位 is_running，再让位**。反过来的话，让位期间
    #   （最多 10s）空闲调度器看到的 is_running 仍是 False，会心安理得地起新一轮，
    #   于是「刷新 + 空闲下载」并存抢网络 —— 而这个占位一落下，tick 的门控②
    #   立刻生效，从根上堵住那条缝。（刷新不起 job、不走 _START_GATE，
    #   它和 job 的互斥就靠这个标志位 + 上面这道让位。）
    _yield_idle_jobs()
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


# ==========================================
# 「空闲下载」：没人用的时候自动吃掉积压的待下载 id
# ==========================================
# 场景：用户白天用「仅收集ID」把几个日期 folder 的 id 收好了，人就走开了。这个调度器
# 在「没有前台任务 + 网络也安静」时，隔一段时间挑一个 folder、下掉一小批（per_round），
# 走的是下载路径，和用户手点「按ID下载」完全同一条链路。
#
# 三条门控缺一不可（缺了就是抢用户带宽 / 抢并发位）：
#   ① 没有任何 job 在跑 —— 注意**暂停中的任务也算在跑**（is_running 仍为 True）；
#   ② 热度刷新线程不在跑 —— 它不占 job 注册表，必须单独问 refresh_state；
#   ③ 网络安静 —— http_client 的活动探针静默超过 min_idle_sec，避免和用户正在做的
#      浏览 / 缩略图代理抢带宽。
# 再加一层「轮次间隔」：每轮跑完才预定下一轮，不会连着刷。
#
# ⚠⚠ 空闲轮次是一条**可被抢占**的 job。没有抢占的话，MAX_CONCURRENT=1 会让用户点
#     「开始」得到「已有 1 个任务在跑」—— 而那个任务是用户自己没排过的后台任务，
#     体验上等于「后台把用户挡在门外」。见 _yield_idle_jobs。

IDLE_DOWNLOAD_META_KEY = "idle_download_cfg"
IDLE_DOWNLOAD_DEFAULTS = {
    "enabled": False,     # 默认关：后台自动联网需要用户点头
    "interval_sec": 600,  # 两轮之间的空档（默认 10 分钟）
    "per_round": 100,     # 每轮最多下多少张（100 张 = 1 个元数据请求）
    "min_idle_sec": 60,   # 网络静默多久才算「空闲」
}
IDLE_DOWNLOAD_TICK = 2.0            # 调度线程的检查周期
IDLE_DOWNLOAD_RETRY_SCAN = 60       # 没有积压时多久重扫一次
IDLE_DOWNLOAD_BACKOFF_FACTOR = 3    # 整轮颗粒无收（典型：断网）时把下一轮推远
IDLE_DOWNLOAD_BACKOFF_MIN_FAIL = 3  # 失败数到这个量级才当「网络真的坏了」，避免 1 张坏图就退避


def _idle_clamp(value, lo, hi, default):
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


class IdleDownloadState:
    """空闲下载的配置 + 运行态。

    配置持久化在 deck.db 的 meta 表（后端重启后仍然有效，不依赖前端）；
    运行态（下一轮时间 / 上一轮结果）只在内存，重启即归零。
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.enabled = IDLE_DOWNLOAD_DEFAULTS["enabled"]
        self.interval_sec = IDLE_DOWNLOAD_DEFAULTS["interval_sec"]
        self.per_round = IDLE_DOWNLOAD_DEFAULTS["per_round"]
        self.min_idle_sec = IDLE_DOWNLOAD_DEFAULTS["min_idle_sec"]
        self.next_run_at = 0.0        # epoch 秒；<= now 才允许开下一轮
        self.rev = 0                  # 配置版本号：每次改配置 +1（见 _idle_run_round 的收尾）
        self.round_seq = 0            # 轮次序号：只增不减，外部据此判断「又跑完了一轮」
        self.last_round = None        # {seq, at, folder, picked, success, skip, fail, outcome}
        self.last_skip_reason = ""    # 上一次没开跑的真实原因（给前端解释「为什么还没动」）
        self.running_folder = ""      # 正在跑的空闲轮次的目标 folder

    def load(self):
        """从 deck.db 读回上次的配置。读不到 / 读坏了都退回默认值，不抛。"""
        try:
            raw = deck_db.meta_get(IDLE_DOWNLOAD_META_KEY)
            cfg = json.loads(raw) if raw else {}
        except Exception:
            cfg = {}
        if not isinstance(cfg, dict):
            cfg = {}
        d = IDLE_DOWNLOAD_DEFAULTS
        with self.lock:
            self.enabled = bool(cfg.get("enabled", d["enabled"]))
            self.interval_sec = _idle_clamp(cfg.get("interval_sec"), 30, 86400, d["interval_sec"])
            self.per_round = _idle_clamp(cfg.get("per_round"), 1, 1000, d["per_round"])
            self.min_idle_sec = _idle_clamp(cfg.get("min_idle_sec"), 0, 3600, d["min_idle_sec"])
        if self.enabled:
            # 重启后不要把「上一轮的时间」也带回来（内存态没持久化）→ 立刻可跑
            self.next_run_at = 0.0

    def save(self):
        with self.lock:
            cfg = {
                "enabled": self.enabled,
                "interval_sec": self.interval_sec,
                "per_round": self.per_round,
                "min_idle_sec": self.min_idle_sec,
            }
        try:
            deck_db.meta_set(IDLE_DOWNLOAD_META_KEY, json.dumps(cfg, ensure_ascii=False))
        except Exception as e:
            print(f"[idle] 配置落库失败（本次运行仍然生效）: {e}")


idle_state = IdleDownloadState()


def _idle_pending_folders():
    """有积压的 folder 列表：[(folder, lib_id, 行数), ...]，日期文件夹在前、新日期优先。

    纯 deck.db 查询 —— 不碰磁盘、不碰网络，所以「盘没接」也能列出积压。
    """
    try:
        grouped = deck_db.queue_counts_grouped()
    except Exception as e:
        print(f"[idle] 读取积压计数失败: {e}")
        return []
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    rows = [(folder, lib_id, int(cnt))
            for (lib_id, folder), cnt in grouped.items() if cnt]
    # 两次稳定排序：先按名字倒序（新日期 / 大字典序在前），再把日期文件夹稳到最前
    rows.sort(key=lambda r: r[0], reverse=True)
    rows.sort(key=lambda r: 0 if date_re.match(r[0]) else 1)
    return rows


def _idle_resolve_dir(lib_id, folder):
    """(lib_id, folder) → 磁盘上的绝对目录；返回 "" 表示这块盘现在不在。

    ⚠ 不能图省事用 _resolve_save_dir_for_date：那只按**日期名**在所有 root 里找，
    同一个日期同时存在于两块盘时（混合图库的常见形态）会挑错，把图写进 lib_id
    对不上的那块盘；这里必须拿 lib_id 精确匹配。
    """
    for root in get_library_roots():
        if root["id"] != lib_id:
            continue
        try:
            candidate = Path(root["path"]) / folder
            if candidate.is_dir():
                return str(candidate)
        except OSError:
            continue
    return ""


def _idle_pick_round():
    """挑出本轮要下的一批：一个 folder + 最多 per_round 个 id（挑不到返回 None）。

    pending 在前、failed 在后，一起消费：
      - pending = 还没下过的积压，是本功能的主目标；
      - failed  = 上一轮瞬时网络失败留下的残留。它们**留在队列里就不会自己消失**
        （永久失败如已删帖在下载路径上直接出队），所以顺手带上，否则它们会一直
        挂在日历角标上，而调度器却说「没有积压」。
    """
    with idle_state.lock:
        per_round = idle_state.per_round
    for folder, lib_id, _count in _idle_pending_folders():
        pending = deck_db.queue_load(lib_id, folder, status=deck_db.QUEUE_PENDING)
        failed = deck_db.queue_load(lib_id, folder, status=deck_db.QUEUE_FAILED)
        ids = (pending + failed)[:per_round]
        if not ids:
            continue
        save_dir = _idle_resolve_dir(lib_id, folder)
        if not save_dir:
            continue   # 盘没接：跳过，绝不在本地空目录里造一份（同 popular_recover 的断盘保护）
        return {"folder": folder, "lib_id": lib_id, "ids": ids, "save_dir": save_dir}
    return None


def _idle_run_round(job, ids):
    """跑一轮空闲下载：复用 _run_job 的全部收尾逻辑（outcome / 落盘 / 注册表延迟清理）。"""
    started_rev = getattr(job, "idle_rev", 0)
    try:
        _run_job(job, 1, 1, "download_ids", job.target_folder, "", "", ids, "", "danbooru", [])
    finally:
        with idle_state.lock:
            idle_state.last_round = {
                # seq 单调递增，外部（前端 / e2e）据此判断「又跑完了一轮」——
                # 别拿 at（秒级时间戳）当标记：同一秒内连跑两轮就分不出来了。
                "seq": getattr(job, "idle_seq", 0),
                "at": datetime.datetime.now().strftime("%H:%M:%S"),
                "folder": job.target_folder,
                "picked": len(ids),
                "success": int(job.success_count),
                "skip": int(job.skip_count),
                "fail": int(job.fail_count),
                "outcome": job.outcome,
            }
            idle_state.running_folder = ""
            # ⚠ 本轮跑着的时候用户可能改过配置（尤其是刚把开关打开）。那一刻
            # set_idle_download 已经把 next_run_at 归零、想让调度器立刻动起来；
            # 这里若无条件回写，就会用「本轮开始时的 interval」把它盖回去，
            # 用户表现为「点了开关，结果要等满一个 interval 才动」。rev 变了就不回写。
            if idle_state.rev == started_rev:
                wait = idle_state.interval_sec
                # 一整轮颗粒无收且失败成片 —— 典型是断网 / 被墙，别每 10 分钟再撞一次墙
                if job.success_count == 0 and job.fail_count >= IDLE_DOWNLOAD_BACKOFF_MIN_FAIL:
                    wait *= IDLE_DOWNLOAD_BACKOFF_FACTOR
                idle_state.next_run_at = time.time() + wait


def _idle_start_round(target):
    job = _make_job(
        target["folder"], "download_ids", [],
        label=f"空闲下载 · {target['folder']}",
        save_dir=target["save_dir"],
        download_concurrency=DOWNLOAD_CONCURRENCY,
    )
    job.is_idle = True
    # 记下起跑时的配置版本：收尾时靠它判断「用户中途改过配置没有」（见 _idle_run_round）
    job.idle_rev = idle_state.rev
    job.is_running = True
    job.outcome = "running"
    job.error_message = ""
    job.play_event.set()
    # ⚠ 走闸门注册（不是 jobs.add）：tick 里「看有没有 active job」到这句之间隔着
    #   _idle_pick_round()（查 deck.db + stat 磁盘目录），用户完全可能在这段缝里点了
    #   「开始」。被占就直接放弃本轮（不重试、不硬挤）—— 用户的动作优先，
    #   下一拍 tick 看到 active 非空自然会让路。
    if not _register_job(job):
        job.is_running = False
        job.outcome = "stopped"
        return None

    # seq 到这一步才推进：它同时是 last_round.seq 的来源，而 last_round 只在本轮
    # 真的跑完后才写。注册被拒时若也推进，外部会看到一个「跳号且永远等不到」的序号。
    idle_state.round_seq += 1
    job.idle_seq = idle_state.round_seq
    job.append_log(
        f"[空闲下载] 本轮处理 {target['folder']} 的 {len(target['ids'])} 个待下载 ID"
        f"（每轮上限 {idle_state.per_round}）"
    )
    job.thread = threading.Thread(
        target=_idle_run_round, args=(job, target["ids"]),
        daemon=True, name=f"idle-dl-{job.job_id}",
    )
    job.thread.start()
    return job


def _yield_idle_jobs(timeout=10.0):
    """前台任务要开跑时，让正在跑的空闲下载立刻收手、把并发位让出来。

    只在「活跃 job 全是空闲轮次」时动手（用户自己的任务永远不动）。
    返回 True 表示确实让过位。_run_job 的 finally 仍会把已下好的图落盘。
    """
    active = jobs.list_active()
    idle_jobs = [j for j in active if getattr(j, "is_idle", False)]
    if not idle_jobs or len(idle_jobs) != len(active):
        return False
    for j in idle_jobs:
        j.append_log("[空闲下载] 前台任务要开始了，本轮提前收手（已下好的图会正常落盘）")
        j.outcome = "stopped"
        j.is_running = False
        j.play_event.set()
    deadline = time.time() + timeout
    while time.time() < deadline and jobs.list_active():
        sleep(0.05)
    return True


def _idle_download_tick():
    """调度线程的一拍：判断门控 → 挑目标 → 起一轮。任何异常都由调用方兜住。"""
    st = idle_state
    with st.lock:
        if not st.enabled:
            st.last_skip_reason = "空闲下载已关闭"
            return
        if st.next_run_at - time.time() > 0:
            # ⚠ 倒计时**不写 reason**。reason 是「上一次真的没能开跑的原因」
            # （有任务在跑 / 网络刚有活动 / 没有积压），每拍都被倒计时覆盖的话，
            # 用户永远只能看到「距下一轮 3 分 12 秒」，真正的原因一闪就没。
            # 倒计时另有 next_run_in 字段，前端自己拼。
            return
    active = jobs.list_active()
    if active:
        label = getattr(active[0], "label", "") or active[0].mode
        with st.lock:
            st.last_skip_reason = f"有任务在跑（{label}）"
        return
    if refresh_state.is_running:
        with st.lock:
            st.last_skip_reason = "热度刷新进行中"
        return
    with st.lock:
        min_idle = st.min_idle_sec
    quiet = http_client.seconds_since_activity()
    if quiet < min_idle:
        with st.lock:
            st.last_skip_reason = f"网络刚有活动（{int(quiet)}s 前，需静默 {min_idle}s）"
        return
    target = _idle_pick_round()
    if not target:
        with st.lock:
            st.last_skip_reason = "没有待下载的积压"
            st.next_run_at = time.time() + IDLE_DOWNLOAD_RETRY_SCAN
        return
    with st.lock:
        st.last_skip_reason = ""
        st.running_folder = target["folder"]
    if _idle_start_round(target) is None:
        # 注册被拒：闸门判定并发位已被占（用户在前一瞬点了「开始」）。
        # 不重试、不硬挤 —— 用户的动作优先；下一拍 tick 自会看到 active 非空而让路。
        with st.lock:
            st.running_folder = ""
            st.last_skip_reason = "刚开始就被前台任务占用了并发位"
            st.next_run_at = time.time() + IDLE_DOWNLOAD_TICK


def _idle_download_loop():
    """常驻调度线程（daemon：随后端进程一起退出，不留孤儿）。"""
    while True:
        try:
            _idle_download_tick()
        except Exception as e:
            try:
                with idle_state.lock:
                    idle_state.last_skip_reason = f"调度异常：{e}"
                    idle_state.running_folder = ""
            except Exception:
                pass
        sleep(IDLE_DOWNLOAD_TICK)


def _idle_download_payload():
    """给 /api/status 与 /api/idle_download 共用的状态快照。

    ⚠ 积压计数只 GROUP BY 一次（queue_counts_grouped），总数和 folder 明细都从这一份
    派生 —— /api/status 每秒被轮询一次，别在这里打两次库。
    """
    try:
        grouped = deck_db.queue_counts_grouped()
    except Exception:
        grouped = {}
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    folders = [(folder, lib_id, int(cnt))
               for (lib_id, folder), cnt in grouped.items() if cnt]
    folders.sort(key=lambda r: r[0], reverse=True)
    folders.sort(key=lambda r: 0 if date_re.match(r[0]) else 1)
    with idle_state.lock:
        return {
            "enabled": idle_state.enabled,
            "interval_sec": idle_state.interval_sec,
            "per_round": idle_state.per_round,
            "min_idle_sec": idle_state.min_idle_sec,
            "next_run_in": max(0, int(idle_state.next_run_at - time.time())),
            "running_folder": idle_state.running_folder,
            "reason": idle_state.last_skip_reason,
            "last_round": idle_state.last_round,
            "pending_total": sum(cnt for _f, _l, cnt in folders),
            "pending_folders": [
                {"folder": f, "count": c} for f, _l, c in folders[:8]
            ],
        }


class IdleDownloadRequest(BaseModel):
    enabled: bool | None = None
    interval_sec: int | None = None
    per_round: int | None = None
    min_idle_sec: int | None = None


@app.get("/api/idle_download")
def get_idle_download():
    """空闲下载配置 + 运行态。前端轮询走 /api/status 的同名字段，这里是手动查 / 断言入口。"""
    return {"ok": True, **_idle_download_payload()}


@app.post("/api/idle_download")
def set_idle_download(req: IdleDownloadRequest):
    """改空闲下载配置。只传想改的字段，返回改完后的完整状态。"""
    with idle_state.lock:
        changed = any(v is not None for v in
                      (req.enabled, req.interval_sec, req.per_round, req.min_idle_sec))
        if changed:
            # 版本号 +1：正在跑的那一轮收尾时看到 rev 变了，就不会用旧 interval 覆盖
            # 这里刚定下的 next_run_at（否则「刚点开开关却要等满一个轮间隔」）。
            idle_state.rev += 1
        if req.enabled is not None:
            idle_state.enabled = bool(req.enabled)
            # 打开的瞬间就把「下一轮」定在现在：用户点一下就能马上看到它动，
            # 不然要盯着「距下一轮还有 10 分」怀疑自己没点成功
            if idle_state.enabled:
                idle_state.next_run_at = 0.0
                # 顺手清掉关闭时留下的那句「空闲下载已关闭」——它会被 tick 覆盖，
                # 但清掉后前端下一帧就能显示真正的原因，不用等一拍
                idle_state.last_skip_reason = ""
            else:
                idle_state.last_skip_reason = "空闲下载已关闭"
        if req.interval_sec is not None:
            idle_state.interval_sec = _idle_clamp(
                req.interval_sec, 30, 86400, idle_state.interval_sec)
        if req.per_round is not None:
            idle_state.per_round = _idle_clamp(
                req.per_round, 1, 1000, idle_state.per_round)
        if req.min_idle_sec is not None:
            idle_state.min_idle_sec = _idle_clamp(
                req.min_idle_sec, 0, 3600, idle_state.min_idle_sec)
    idle_state.save()
    return {"ok": True, **_idle_download_payload()}


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
    new_rating = post.get("rating", "") or ""

    if date:
        try:
            dd = DanbooruData(target_date=date)
            data = dd.load_viewer_data()
            updated = False
            for item in data:
                if _extract_post_id(item.get("post_url", "")) == str(post_id):
                    item["score"] = new_score
                    item["fav_count"] = new_fav
                    if new_rating:
                        item.setdefault("tags", {})["rating"] = new_rating
                    updated = True
                    break
            if updated:
                dd.save_viewer_data(data)
        except Exception as e:
            append_log(f"单图刷新写盘失败 {post_id}: {e}")

    return {"ok": True, "post_id": post_id, "score": new_score, "fav_count": new_fav, "rating": new_rating}


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


def _refresh_visible_by_paths(local_paths: list[str]):
    """Refresh posts for explicit image paths and write each update beside its source file."""
    targets = []
    for raw in local_paths:
        try:
            image_path = Path(raw).resolve()
        except Exception:
            continue
        if not image_path.exists() or not image_path.is_file():
            continue
        if not is_path_in_library_roots(image_path):
            continue
        targets.append(image_path)
    if not targets:
        return {"ok": False, "msg": "local_paths 为空或不在已接管图库内", "updates": []}

    # viewer_cache: key(镜像路径) → (lib_id, folder, DB 重组的条目 list)
    # 权威走 deck.db；先 reconcile 镜像指纹（吸收外部直写）再读行。
    viewer_cache = {}
    unresolved_names = []
    fetch_jobs = []
    updates = []

    def _active_job_for_scope(image_path):
        """只把同一日期、同一图库根目录的刷新合并进下载任务内存。"""
        job = jobs.get_by_folder(image_path.parent.name)
        if job is None:
            return None
        try:
            if Path(job.save_dir).resolve() == image_path.parent.resolve():
                return job
        except OSError:
            pass
        return None

    def _load_scope(image_path):
        parent = image_path.parent
        key = str(parent / "viewer_data.json")
        scope = viewer_cache.get(key)
        if scope is None:
            lib_id = library_id_for_path(str(parent))
            folder = parent.name
            active_job = _active_job_for_scope(image_path)
            viewer_mirror = parent / "viewer_data.json"
            if active_job is not None:
                # 下载任务的内存数据是当前写入源，不能先从旧镜像 reconcile 回 DB。
                with active_job.viewer_lock:
                    data = active_job.viewer_data
            else:
                try:
                    deck_db.reconcile_viewer(lib_id, folder, str(viewer_mirror))
                    data = deck_db.viewer_load_entries(lib_id, folder)
                except Exception as e:
                    append_log(f"viewer DB 读取失败，回退 JSON {key}: {e}")
                    raw = load_json(key, []) if viewer_mirror.exists() else []
                    data = raw if isinstance(raw, list) else []
            scope = {"lib_id": lib_id, "folder": folder, "data": data, "job": active_job}
            viewer_cache[key] = scope
        return scope

    for image_path in targets:
        scope = _load_scope(image_path)
        lib_id, folder, data = scope["lib_id"], scope["folder"], scope["data"]
        post_id = None
        for item in data:
            if item.get("filename") == image_path.name:
                post_id = _extract_post_id(item.get("post_url", ""))
                if post_id:
                    break
        if not post_id:
            unresolved_names.append(image_path.name)
        fetch_jobs.append((image_path, post_id))

    # filename→post_id 只对 viewer 里没查到的少量名字做点查，不建 52 万全量 map
    fn_to_pid_log = deck_db.log_pids_for_filenames(unresolved_names) if unresolved_names else {}
    fetch_jobs = [
        (image_path, post_id or fn_to_pid_log.get(image_path.name))
        for image_path, post_id in fetch_jobs
    ]

    # 批量拉取：一次请求最多 200 个 id（Danbooru `tags=id:1,2,3` 语法，实测见
    # danbooru_api.fetch_data_batch 的注释）。原来逐张打 /posts/{id}.json —— 一批 120 张
    # 就是 120 个请求，在全局令牌桶 1 req/s 下要两分钟；现在 1 个请求。
    unique_ids, seen_ids = [], set()
    for _, post_id in fetch_jobs:
        if post_id and post_id not in seen_ids:
            seen_ids.add(post_id)
            unique_ids.append(post_id)
    post_map = danbooru_api.fetch_data_batch(unique_ids) if unique_ids else {}
    # 没回来的（已删除 / 当前 host 不可见 / 整批失败）留 None，下面统一走「拉取失败」分支
    fetched = [
        (image_path, post_id, post_map.get(str(post_id)))
        for image_path, post_id in fetch_jobs
    ]

    changed_viewers = set()
    for image_path, post_id, post in fetched:
        filename = image_path.name
        if not post_id:
            updates.append({
                "filename": filename,
                "local_path": str(image_path),
                "ok": False,
                "msg": "无法反查 post_id",
            })
            continue
        if not post:
            updates.append({
                "filename": filename,
                "local_path": str(image_path),
                "ok": False,
                "msg": "拉取失败",
            })
            continue

        new_score = post.get("score", 0) or 0
        new_fav = post.get("fav_count", 0) or 0
        tag_artist = post.get("tag_string_artist", "") or ""
        artist_tokens = [s for s in tag_artist.split(" ")
                         if s and not s.lower().endswith("(voice_actor)")]
        artist = " ".join(artist_tokens) if artist_tokens else "未知"
        chars_str = post.get("tag_string_character", "") or ""
        post_url = danbooru_api.post_url(post_id)
        tags_full = {
            "tag_string_general": post.get("tag_string_general", ""),
            "tag_string_character": chars_str,
            "tag_string_copyright": post.get("tag_string_copyright", ""),
            "tag_string_artist": tag_artist,
            "tag_string_meta": post.get("tag_string_meta", ""),
        }

        viewer_path = image_path.parent / "viewer_data.json"
        key = str(viewer_path)
        # scope 在 _load_scope 阶段必然已建缓存（targets 都过了一遍）
        scope = viewer_cache[key]
        lib_id, folder, data = scope["lib_id"], scope["folder"], scope["data"]
        active_job = scope["job"]
        # 网络请求在锁外完成；真正修改任务内存时再持有同一把锁，
        # 避免下载线程在“刷新结果已改内存、尚未写盘”的窗口读取旧快照。
        with (active_job.viewer_lock if active_job is not None else nullcontext()):
            item = None
            for existing in data:
                if existing.get("filename") == filename:
                    item = existing
                    break
            if item is None:
                item = {
                    "filename": filename,
                    "web_url": f"/images/{image_path.parent.name}/{filename}",
                }
                data.append(item)

            item["artist"] = artist
            item["local_path"] = str(image_path)
            item["post_url"] = post_url
            item["score"] = new_score
            item["fav_count"] = new_fav
            merged_tags = item.get("tags") or {}
            merged_tags.update(tags_full)
            item["tags"] = merged_tags
            changed_viewers.add(key)

        updates.append({
            "filename": filename,
            "local_path": str(image_path),
            "ok": True,
            "post_id": str(post_id),
            "post_url": post_url,
            "score": new_score,
            "fav_count": new_fav,
            "artist": artist,
            "characters": _translate_characters_str(chars_str),
            "tags": tags_full,
        })

    for key in changed_viewers:
        scope = viewer_cache[key]
        lib_id, folder, data = scope["lib_id"], scope["folder"], scope["data"]
        active_job = scope["job"]
        if active_job is not None:
            # 与下载线程共用 viewer_lock；刷新完成后立即落盘，但不再绕过任务内存。
            with active_job.viewer_lock:
                cleaned = dedup_viewer_data(active_job.viewer_data)
                active_job.viewer_data[:] = cleaned
                active_job.viewer_keys = {
                    _viewer_item_key(item) for item in cleaned if isinstance(item, dict)
                }
                active_job.db.save_viewer_data(active_job.viewer_data)
        else:
            cleaned = dedup_viewer_data(data)
            # DB 为权威：整 folder 替换后再导出同名镜像（原子写）。
            deck_db.viewer_replace(lib_id, folder, cleaned)
            deck_db.export_viewer_mirror(lib_id, folder, key, items=cleaned)

    return {"ok": True, "updates": updates}


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
    local_paths = [p for p in (req.local_paths or []) if p]
    if local_paths:
        return _refresh_visible_by_paths(local_paths)

    filenames = [fn for fn in (req.filenames or []) if fn]
    if not filenames:
        return {"ok": False, "msg": "filenames 为空", "updates": []}

    # 关键路径：根据 target_folder 找当前正在跑的 job；命中就走共享内存路径，否则建临时 dd
    active_job = jobs.get_by_folder(date_str)
    dd = None if active_job else DanbooruData(target_date=date_str)

    # Step 1: filename -> post_id 反查（只对这批文件名做点查，不建 52 万全量 map）
    fn_to_pid_log = deck_db.log_pids_for_filenames(filenames)

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

    # Step 2: 批量拉取 —— 一次请求最多 200 个 id（Danbooru `tags=id:1,2,3` 语法，
    # 实测见 danbooru_api.fetch_data_batch 的注释）。原来逐张打 /posts/{id}.json，
    # 在全局令牌桶 1 req/s 下每张都要等一秒，一批 120 张就是两分钟。
    fn_to_pid = {
        fn: (fn_to_pid_initial.get(fn) or fn_to_pid_log.get(fn))
        for fn in filenames
    }
    post_map = danbooru_api.fetch_data_batch(
        [pid for pid in fn_to_pid.values() if pid]
    )
    # 统一成 3 元组；拉不到的（已删 / 不可见 / 整批失败）给 None。下面仍保留
    # len(entry)==4 的分支，兼容未来别处塞进来的异常条目。
    fetched = [
        (fn, fn_to_pid[fn], post_map.get(str(fn_to_pid[fn])) if fn_to_pid[fn] else None)
        for fn in filenames
    ]

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
                "tag_string_meta": post.get('tag_string_meta', ''),
                "rating": post.get('rating', '') or ''
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


@app.post("/api/merge_viewer_data")
def merge_viewer_data(req: MergeViewerDataRequest):
    """跨 root 增量合并 viewer_data.json：从 source 增量追加到 target。
    - 用 post_url 做主 key 去重（重复执行安全）
    - 重写合并项的 local_path 到 target 路径
    - web_url 保持相对路径不变（/images/<date>/<file>）
    - dry_run=True 时只统计，不写盘"""
    if not req.date or not req.target_root:
        return {"ok": False, "msg": "date 和 target_root 必填"}

    # 解析 source_root：空 = 默认 hot_pic
    source_root = Path(req.source_root).resolve() if req.source_root else HOT_PIC_DIR.resolve()
    target_root = Path(req.target_root).resolve()

    # 路径校验：必须在 library_roots 列表中（包括 default），防越界
    valid_roots = {str(r["path"]).lower() for r in get_library_roots()}
    if str(source_root).lower() not in valid_roots:
        return {"ok": False, "msg": f"源路径 {source_root} 不在 library_roots 中"}
    if str(target_root).lower() not in valid_roots:
        return {"ok": False, "msg": f"目标路径 {target_root} 不在 library_roots 中"}
    if str(source_root).lower() == str(target_root).lower():
        return {"ok": False, "msg": "源和目标不能是同一个 root"}

    source_dir = source_root / req.date
    target_dir = target_root / req.date

    # 读源 / 目标（deck.db 为权威：先吸收镜像指纹变化再从行重组；目录不存在保持空，
    # 与旧版「镜像文件不存在 = []」的语义一致）
    source_lib = library_id_for_path(str(source_root))
    target_lib = library_id_for_path(str(target_root))
    source_path = source_dir / "viewer_data.json"
    target_path = target_dir / "viewer_data.json"

    def _load_scope(lib_id, day_dir, mirror_path):
        if not day_dir.is_dir():
            return []
        try:
            deck_db.reconcile_viewer(lib_id, day_dir.name, str(mirror_path))
            return deck_db.viewer_load_entries(lib_id, day_dir.name)
        except Exception as e:
            print(f"[deck_db] merge 读取失败，回退 JSON {mirror_path}: {e}")
            raw = load_json(str(mirror_path), []) if mirror_path.exists() else []
            return raw if isinstance(raw, list) else []

    source_items = _load_scope(source_lib, source_dir, source_path)
    target_items = _load_scope(target_lib, target_dir, target_path)

    blocked_move_names = set()
    if req.move_files and source_dir.is_dir() and target_dir.is_dir():
        try:
            target_names = {
                item.name.lower()
                for item in target_dir.iterdir()
                if item.is_file()
            }
            blocked_move_names = {
                item.name.lower()
                for item in source_dir.iterdir()
                if item.is_file()
                and item.suffix.lower() in _MEDIA_EXTS
                and item.name.lower() in target_names
            }
        except OSError as e:
            return {"ok": False, "msg": f"扫描目标图片失败: {e}"}

    # 用现成的 merge_daily_viewer_data：基于 _viewer_item_key(post_url) 去重
    # 1) 先对 target 做 dedup（防御性，目标文件可能历史遗留重复）
    deduped_target = dedup_viewer_data(target_items)
    # 2) 合并：从 source 增量追加到 target
    if req.move_files:
        move_source_names = {
            item.name.lower()
            for item in source_dir.iterdir()
            if item.is_file() and item.suffix.lower() in _MEDIA_EXTS
            and item.name.lower() not in blocked_move_names
        } if source_dir.is_dir() else set()
        merge_source_items = [
            item for item in source_items
            if str(item.get("filename", "")).lower() in move_source_names
        ]
    else:
        merge_source_items = source_items
    # 必须在 merge 之前量 target 基线：merge_daily_viewer_data 对空 target 会复用
    # deduped_target 这个 list 原地 append（旧 dedup 还会把空列表原样返回），
    # 事后再 len() 会得到 672 而不是 0 —— 计数全错、local_path 重写边界也跟着失效。
    target_count_before = len(deduped_target)
    merged = merge_daily_viewer_data(deduped_target, merge_source_items)

    # 3) 重写合并项的 local_path：从 source 路径改写到 target 路径
    # merged 数组前 target_count_before 项是原 target 的（保留不动），
    # 之后的都是从 source 增量来的，需要重写 local_path 到 target 路径。
    final_items = []
    rewritten = 0
    for idx, item in enumerate(merged):
        new_item = dict(item)  # 浅拷贝，避免污染原 source_items
        if idx >= target_count_before:
            # 这一项是从 source 来的，重写 local_path
            fn = new_item.get("filename", "")
            source_file = source_dir / fn if fn else None
            target_file = target_dir / fn if fn else None
            can_point_to_target = (
                bool(fn)
                and (
                    target_file.is_file()
                    or (source_file is not None and source_file.is_file())
                    or not req.move_files
                )
            )
            if can_point_to_target:
                new_item["local_path"] = str(target_dir / fn)
                rewritten += 1
        final_items.append(new_item)

    merged_count = len(final_items) - target_count_before

    move_candidates = []
    move_conflicts = []
    move_missing = []
    if req.move_files:
        if source_dir.is_dir():
            try:
                target_names = {
                    item.name.lower()
                    for item in target_dir.iterdir()
                    if item.is_file()
                } if target_dir.is_dir() else set()
                for item in source_dir.iterdir():
                    if not item.is_file() or item.suffix.lower() not in _MEDIA_EXTS:
                        continue
                    if item.name.lower() in target_names:
                        move_conflicts.append(item.name)
                    else:
                        move_candidates.append(item.name)
            except OSError as e:
                return {"ok": False, "msg": f"扫描源图片失败: {e}"}
        else:
            move_missing = ["源日期目录不存在"]

    if req.dry_run:
        return {
            "ok": True,
            "dry_run": True,
            "source_count": len(source_items),
            "target_count_before": target_count_before,
            "merged_count": merged_count,
            "rewritten_local_path": rewritten,
            "move_files": bool(req.move_files),
            "move_count": len(move_candidates),
            "move_conflict_count": len(move_conflicts),
            "move_missing_count": len(move_missing),
            "move_conflicts": move_conflicts[:30],
            "move_missing": move_missing,
        }

    # 写盘
    if not target_dir.exists():
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return {"ok": False, "msg": f"创建目标目录失败: {e}"}

    moved_files = []
    move_errors = []
    if req.move_files:
        for filename in move_candidates:
            source_file = source_dir / filename
            target_file = target_dir / filename
            try:
                if source_file.exists() and not target_file.exists():
                    shutil.move(str(source_file), str(target_file))
                    moved_files.append(filename)
            except OSError as e:
                move_errors.append({"filename": filename, "error": str(e)})

        # Only point newly merged metadata at the target when the file is
        # actually there. A failed move must not create a broken local_path.
        moved_set = set(moved_files)
        for item in final_items[target_count_before:]:
            filename = item.get("filename", "")
            if filename and (filename in moved_set or (target_dir / filename).is_file()):
                item["local_path"] = str(target_dir / filename)

    try:
        # DB 权威写入 + 同名镜像导出（原子写，indent=2 与旧版一致）
        deck_db.viewer_replace(target_lib, req.date, final_items)
        deck_db.export_viewer_mirror(target_lib, req.date, str(target_path), items=final_items)
    except Exception as e:
        return {"ok": False, "msg": f"写目标 viewer 数据失败: {e}"}

    # Remove successfully moved entries only after target metadata is safely
    # written. If source cleanup fails, duplication is safer than data loss.
    source_cleanup_error = ""
    if req.move_files and moved_files and source_path.exists():
        remaining_source_items = [
            item for item in source_items
            if str(item.get("filename", "")) not in set(moved_files)
        ]
        try:
            remaining_clean = dedup_viewer_data(remaining_source_items)
            deck_db.viewer_replace(source_lib, req.date, remaining_clean)
            deck_db.export_viewer_mirror(source_lib, req.date, str(source_path),
                                         items=remaining_clean)
        except Exception as e:  # sqlite3 / OSError：宁可报错也不能丢清理失败
            source_cleanup_error = str(e)

    return {
        "ok": True,
        "dry_run": False,
        "source_count": len(source_items),
        "target_count_before": target_count_before,
        "merged_count": merged_count,
        "rewritten_local_path": rewritten,
        "target_path": str(target_path),
        "move_files": bool(req.move_files),
        "moved_count": len(moved_files),
        "move_conflict_count": len(move_conflicts),
        "move_error_count": len(move_errors),
        "move_conflicts": move_conflicts[:30],
        "move_errors": move_errors[:30],
        "source_cleanup_error": source_cleanup_error,
    }


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
    """聚合本地图库中所有日期 viewer_data 里「翻译字典查不到」的角色 token。
    返回 {tags: [{tag, post_count, fallback_name}]}，按出现次数倒序。
    计数直接流式扫 deck.db 的 tag_character 列（旧实现逐日 build_library + 翻译富化，
    全量跑要解析上百个 JSON）。"""
    counter = {}
    folder = None
    if date:
        resolved_date, _ = resolve_selected_date(date)
        folder = resolved_date
    for chars_str in deck_db.viewer_iter_tag_values("tag_character", folder=folder):
        for token in (chars_str or "").split():
            token = token.strip()
            if token:
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
    return {"date": folder or "", "tags": pending}


@app.get("/api/character_source/{tag}")
def api_character_source(tag: str):
    """返回 character.json 里的英文描述 + other_names + 已组装好的 manual prompt。
    tag 在 URL 路径上需 URL-encode（前端用 encodeURIComponent）。"""
    source = translator.get_character_source(tag)
    manual_prompt = translator.build_manual_prompt(tag, source)
    translation = translator.get_translation_entry(tag)
    if translation.get("matched_key"):
        manual_prompt += (
            "\n\n【当前字典记录（请重点检查同名角色、作品归属和皮肤差异）】"
            f"\nmatched_key: {translation.get('matched_key', '')}"
            f"\nchinese_name: {translation.get('chinese_name', '')}"
            f"\nsource_hint: {translation.get('source_hint', '')}"
            f"\ntranslated_description_zh: {translation.get('translated_description_zh', '')}"
        )
    return {
        "tag": tag,
        "exists": source.get("exists", False),
        "matched_key": source.get("matched_key", ""),
        "description": source.get("description", ""),
        "other_names": source.get("other_names", []),
        "fallback_name": translator._format_tag(tag),
        "manual_prompt": manual_prompt,
        "translation": translation,
    }


@app.get("/api/character_translations")
def api_character_translations(q: str = "", limit: int = 100):
    """搜索用户可编辑的角色翻译字典；用于同名皮肤和同作品批量排查。"""
    return {"ok": True, "query": q, "items": translator.search_translation_entries(q, limit)}


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
        # 手动修正应立即生效，不再要求用户额外点一次“导入到画廊”。
        translator.import_search_to_custom()
        return {"ok": True, "msg": "已保存并同步到画廊字典"}
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
    try:
        return deck_db.fav_group_reconcile("artist", str(ARTIST_FAVORITES_JSON))
    except Exception as e:
        print(f"Failed to load artist favorites: {e}")
        return {}


def _save_artist_favorites(groups: dict) -> None:
    deck_db.fav_group_save("artist", groups, str(ARTIST_FAVORITES_JSON))


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
    try:
        return deck_db.fav_group_reconcile("character", str(CHARACTER_FAVORITES_JSON))
    except Exception as e:
        print(f"Failed to load character favorites: {e}")
        return {}


def _save_character_favorites(groups: dict) -> None:
    deck_db.fav_group_save("character", groups, str(CHARACTER_FAVORITES_JSON))


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

def _image_fav_key(date: str, filename: str, library_id: str = "default", local_path: str = "") -> str:
    date = (date or "").strip()
    filename = (filename or "").strip()
    library_id = (library_id or "default").strip()
    # Keep existing favorites compatible for the default library.
    if library_id in ("", "default"):
        return f"{date}/{filename}"
    if local_path:
        try:
            path_key = str(Path(local_path).resolve()).replace("\\", "/")
            return f"{library_id}:{path_key}"
        except Exception:
            pass
    return f"{library_id}:{date}/{filename}"


def _load_image_favorites() -> dict:
    try:
        return deck_db.fav_image_reconcile(str(IMAGE_FAVORITES_JSON))
    except Exception as e:
        print(f"Failed to load image favorites: {e}")
        return {}


@app.get("/api/image_favorites")
def api_image_favorites_get():
    """返回 {ok, items: [...], keys: [...]}。items 按 added_at 倒序，keys 给前端快速做 Set 查询。"""
    _load_image_favorites()  # 触发一次镜像指纹对账（外部改动吸收）
    items = deck_db.fav_image_items()
    keys = [it["key"] for it in items]
    return {"ok": True, "items": items, "keys": keys, "count": len(items)}


@app.post("/api/image_favorites/toggle")
def api_image_favorites_toggle(req: ImageFavoriteToggleRequest):
    """切换单张图片的收藏状态，幂等。DB 点 upsert/delete 后导出小镜像。"""
    item = req.item
    if not item.date or not item.filename:
        return {"ok": False, "msg": "date / filename 不能为空"}
    key = _image_fav_key(item.date, item.filename, item.library_id, item.local_path)
    if key in deck_db.fav_image_load_all():
        deck_db.fav_image_delete(key)
        favorited = False
    else:
        import time as _time
        try:
            payload = item.model_dump()  # pydantic v2
        except AttributeError:
            payload = item.dict()
        payload["added_at"] = int(_time.time())
        deck_db.fav_image_upsert(key, payload)
        favorited = True
    deck_db.export_fav_image_mirror(str(IMAGE_FAVORITES_JSON))
    return {"ok": True, "favorited": favorited, "key": key,
            "count": deck_db.fav_image_count()}


@app.post("/api/image_favorites/remove")
def api_image_favorites_remove(req: ImageFavoriteRemoveRequest):
    """按 key 显式移除（收藏页用，避免依赖整条 item）。"""
    removed = deck_db.fav_image_delete(req.key)
    if removed:
        deck_db.export_fav_image_mirror(str(IMAGE_FAVORITES_JSON))
    return {"ok": True, "removed": removed, "count": deck_db.fav_image_count()}

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


@app.post("/api/convert_all_zips")
def api_convert_all_zips(req: ConvertAllZipsRequest):
    """批量把 hot_pic/<date>/ 下所有 .zip 转成 .gif（单 zip 转不动就跳过、不影响其他）；
    overwrite=False（默认）会跳过已有 .gif；True 会强制覆盖。
    返回 ok/总数/成功/跳过/失败/逐条结果，前端可据此刷新 gallery（gallery 用 .gif 优先）。"""
    try:
        from pic_web.main import convert_zip_to_gif
    except Exception as e:
        return {"ok": False, "msg": f"ffmpeg 模块加载失败: {e}"}

    date = (req.date or "").strip()
    try:
        datetime.datetime.strptime(date, "%Y-%m-%d")
    except ValueError:
        return {"ok": False, "msg": f"日期格式不正确: {req.date}"}

    folder = (HOT_PIC_DIR / date).resolve()
    try:
        # 防路径穿越：解析后必须仍在 HOT_PIC_DIR 下
        folder.relative_to(HOT_PIC_DIR.resolve())
    except ValueError:
        return {"ok": False, "msg": "日期路径越界"}
    if not folder.is_dir():
        return {"ok": False, "msg": f"找不到日期文件夹: {date}"}

    zips = sorted(folder.glob("*.zip"))
    results = []
    converted = 0
    skipped = 0
    failed = 0
    for zip_path in zips:
        gif_path = zip_path.with_suffix(".gif")
        if gif_path.exists() and not req.overwrite:
            results.append({"zip": zip_path.name, "gif": gif_path.name, "status": "skipped"})
            skipped += 1
            continue
        try:
            convert_zip_to_gif(zip_path, gif_path)
            results.append({"zip": zip_path.name, "gif": gif_path.name, "status": "ok"})
            converted += 1
        except Exception as e:
            results.append({"zip": zip_path.name, "gif": gif_path.name, "status": "failed", "msg": str(e)})
            failed += 1

    return {
        "ok": True,
        "date": date,
        "total": len(zips),
        "converted": converted,
        "skipped": skipped,
        "failed": failed,
        "results": results,
    }


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
    """在图片同目录的 deck.db viewer 行里按 filename 点查，取出该图元数据
    （角色 / 作品 / 画师 / tags），供构造手动模式提示词用。
    先 reconcile 一次镜像（吸收外部直写），DB 异常才回退 JSON 直读。"""
    parent = image_path.parent
    viewer_json = parent / "viewer_data.json"
    lib_id = library_id_for_path(str(parent))
    folder = parent.name
    target = image_path.name
    try:
        deck_db.reconcile_viewer(lib_id, folder, str(viewer_json))
        rows = deck_db.viewer_find_entries(lib_id, folder, filenames=[target])
        return rows[0] if rows else None
    except Exception:
        if not viewer_json.exists():
            return None
        try:
            with open(viewer_json, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return None
        for entry in data if isinstance(data, list) else []:
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
    # 限制只看已接管图库目录下的图，避免任意路径泄露
    if not is_path_in_library_roots(image_path):
        return {"ok": False, "msg": "路径不在已接管的图片目录下"}

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


# ⚠⚠ 这两句**绝不能**放进下面的 `if __name__ == "__main__":` 里。
# 桌面应用（desktop-app/electron/main.cjs）拉起后端的方式是
#     python -u -c "... uvicorn.run('main:app', host='127.0.0.1', port=18765, ...)"
# 即把 main **当模块导入**，此时 __name__ == "main"，守护块里的代码一行都不执行。
#
# 症状极具迷惑性：面板开关能拨、配置能落库、/api/idle_download 也答得好好的，
# 但调度线程根本不存在 —— 一张图都不会下。最明显的指纹是 reason 恒为空串：
# _idle_download_tick 的**每一条**跳过分支都会写 last_skip_reason，静态对象才空。
# 而 `python main.py` 手跑一切正常，所以「用脚本启动后端」的 e2e 永远抓不到它。
#
# 放在模块尾部 = 「被导入」与「当脚本跑」两种情况都在 import 完成时各执行一次；
# 位置必须在所有路由与 refresh_state / _run_job 定义之后，否则 import 期撞 NameError。
idle_state.load()          # 配置从 deck.db 读回（后端重启后仍然生效，不靠前端推）
threading.Thread(
    target=_idle_download_loop, daemon=True, name="idle-download",
).start()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=18765,
        reload=False,
        access_log=False,
        log_level="warning",
    )
