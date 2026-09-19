# my_utils.py
import json
import os
import re
import threading
import time
from requests.utils import get_environ_proxies

def get_proxies_for_url(url):
    proxies = get_environ_proxies(url)
    if 'https' in proxies and proxies['https'].startswith('https://'):
        proxies['https'] = proxies['https'].replace('https://', 'http://', 1)
    return proxies


def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return default
    return default


def _viewer_item_key(item):
    """统一去重 key：优先 post_url，其次 (filename, web_url)。"""
    post_url = item.get("post_url")
    if post_url:
        return ("post", post_url)
    return ("fn", item.get("filename"), item.get("web_url"))


def dedup_viewer_data(items):
    """对 daily_viewer_data 列表去重，保留首次出现的条目。"""
    if not items:
        # 必须返回新列表：旧写法直接返回入参空列表，调用方若之后 append
        # （merge_daily_viewer_data 就是），会反向污染调用方手里的"原列表"
        return []
    seen = set()
    deduped = []
    for item in items:
        if not isinstance(item, dict):
            continue
        key = _viewer_item_key(item)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def merge_daily_viewer_data(current_items, snapshot_items):
    merged = dedup_viewer_data(current_items)
    known = {_viewer_item_key(item) for item in merged}
    for item in snapshot_items or []:
        if not isinstance(item, dict):
            continue
        key = _viewer_item_key(item)
        if key not in known:
            merged.append(item)
            known.add(key)
    return merged


# ---------------- Tag 文件夹命名 ----------------
# 把 Danbooru tag 查询串转成文件系统安全的文件夹名。
# - 统一加 "tag_" 前缀，避免和 YYYY-MM-DD 日期文件夹冲突，前端也能据此区分
# - 空格转 "__"，冒号（rating:safe）转 "__c__"（unambiguous marker，避免和合法的 "-" 混淆）
# - Windows 非法字符直接去掉
# - 限制 80 字符长度，避免 NTFS 路径过长被截断
_TAG_FOLDER_PREFIX = "tag_"
_TAG_SPACE_MARK = "__"
_TAG_COLON_MARK = "__c__"
_INVALID_FS_CHARS_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')

def sanitize_tag_folder(tag_query: str) -> str:
    """tag 查询串 -> 'tag_xxx' 文件夹名；空串返回空串。"""
    s = (tag_query or "").strip()
    if not s:
        return ""
    # 顺序很重要：先把冒号换成专用标记，再去除非法字符（_INVALID_FS_CHARS_RE 也会匹配 ":"，
    # 但此时已经被替换掉了），最后把空白合并成空格标记
    s = s.replace(":", _TAG_COLON_MARK)
    s = _INVALID_FS_CHARS_RE.sub("", s)
    s = re.sub(r"\s+", _TAG_SPACE_MARK, s)
    s = s.strip(". ")
    if not s:
        return ""
    folder = f"{_TAG_FOLDER_PREFIX}{s}"
    return folder[:80]


def is_tag_folder(folder_name: str) -> bool:
    return isinstance(folder_name, str) and folder_name.startswith(_TAG_FOLDER_PREFIX)


def tag_folder_display(folder_name: str) -> str:
    """把 'tag_hatsune_miku__rating__c__safe' 还原成可读的 'hatsune_miku rating:safe'。"""
    if not is_tag_folder(folder_name):
        return folder_name
    body = folder_name[len(_TAG_FOLDER_PREFIX):]
    body = body.replace(_TAG_COLON_MARK, ":")
    body = body.replace(_TAG_SPACE_MARK, " ")
    return body


# ---------------- 进程级 per-path 锁 + 统一原子写 ----------------
# 所有模块（danbooru_data / main.py 的 LogStore、StatsStore、收藏保存等）共享同一张
# 「按文件绝对路径分配的可重入锁」表，对同一个 JSON 的读 / 写在整个进程内串行。
#
# 解决的并发问题：
#   1) 旧实现临时名固定为 "<path>.tmp"，两个写线程同时写同一个 .tmp 互相覆盖，
#      第二个 os.replace 还可能因 .tmp 已被前一个消费而抛 FileNotFoundError；
#   2) Windows 上当一个线程正打开该文件读时，另一个线程的 os.replace 会抛
#      PermissionError([WinError 5] 拒绝访问)（杀软 / 索引器 / Electron 兜底读句柄）。
# 后端是单进程 uvicorn（无 --workers），threading 锁即可，无需跨进程文件锁。
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS = {}


def file_lock_for(path):
    """返回某个文件路径对应的进程级可重入锁（同一路径恒返回同一把）。"""
    key = os.path.abspath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _atomic_replace(src, dst, attempts=5, base_delay=0.05):
    """os.replace 在 Windows 上偶发 PermissionError（杀软 / 索引器 / 外部进程刚好持有句柄，
    例如 Electron 兜底读取或 caption 元数据查询），短暂重试几次即可恢复；POSIX 下通常一次成功。"""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (i + 1))


def read_json_atomic(path, default=None):
    """在 per-path 锁下读取 JSON；文件不存在或损坏时返回 default。
    与 atomic_write_json 共用同一把锁，读期间不会有别的线程在做 os.replace。"""
    if default is None:
        default = {}
    with file_lock_for(path):
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return default
        return default


def atomic_write_json(path, data, indent=4):
    """全进程统一的原子 JSON 写：per-path RLock 串行 + 唯一临时名 + fsync +
    os.replace 原子替换 + Windows PermissionError 重试 + finally 清理残留临时文件。"""
    with file_lock_for(path):
        # 唯一临时名（带 pid + 线程号 + 单调计数）：即便将来有调用方绕过锁，
        # 多个写也不会撞同一个 .tmp。
        temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.{next(_TMP_COUNTER)}.tmp"
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
                f.flush()
                os.fsync(f.fileno())
            _atomic_replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


# 同一线程内连续两次写也保证临时名不同（可重入锁下 tid 相同）。
_TMP_COUNTER = iter(range(2 ** 31))
