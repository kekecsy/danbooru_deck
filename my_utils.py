# my_utils.py
import json
import os
import re
import time
from requests.utils import get_environ_proxies
import exiftool
import sys
import subprocess
import tempfile
import exiftool

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
        return items if items is not None else []
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


def save_global_data(log_data, artist_stats, log_path, stats_path):
    temp_path = log_path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=4)
    os.replace(temp_path, log_path)
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(artist_stats, f, ensure_ascii=False, indent=4)


def save_viewer_data(viewer_data, save_dir):
    with open(os.path.join(save_dir, "viewer_data.json"), 'w', encoding='utf-8') as f:
        json.dump(viewer_data, f, ensure_ascii=False, indent=4)


def save_runtime_snapshot(log_data, artist_stats, daily_viewer_data, runtime_snapshot_path):
    snapshot = {
        "log_data": log_data,
        "artist_stats": artist_stats,
        "daily_viewer_data": dedup_viewer_data(daily_viewer_data)
    }
    temp_path = runtime_snapshot_path + ".tmp"
    try:
        with open(temp_path, 'w', encoding='utf-8') as f:
            json.dump(snapshot, f, ensure_ascii=False, indent=4)
    except PermissionError as e:
        # 临时文件本身被占用（罕见，比如杀软扫描中），跳过这一次写入
        print(f"[snapshot] 写入临时快照失败，已跳过: {e}")
        return

    # Windows 下若用户用记事本/编辑器打开了快照文件，os.replace 会触发 WinError 5。
    # 不应让爬虫任务因此整体崩溃 —— 重试几次，仍失败则丢弃这一次快照，下次再写。
    last_err = None
    for attempt in range(5):
        try:
            os.replace(temp_path, runtime_snapshot_path)
            return
        except PermissionError as e:
            last_err = e
            time.sleep(0.2 * (attempt + 1))
    # 5 次都失败，清理 .tmp 并放弃这次快照
    try:
        os.remove(temp_path)
    except OSError:
        pass
    print(f"[snapshot] 快照文件被占用，已跳过本次写入: {last_err}")


def clear_runtime_snapshot(runtime_snapshot_path):
    if not os.path.exists(runtime_snapshot_path):
        return
    try:
        os.remove(runtime_snapshot_path)
    except PermissionError:
        pass

def add_extra_info_to_img(img_path, extra_info):
    exiftool_path = r"./exiftool/exiftool-13.54_64/exiftool(-k).exe"
    with exiftool.ExifTool(exiftool_path) as et:
        # 构建参数列表，对应 exiftool -Artist="作者名" -Copyright="版权信息" -overwrite_original 图片路径
        args = [
            f"-Artist={extra_info.get('artist')}",
            f"-Copyright={extra_info.get('urls')}",
            "-overwrite_original",
            img_path
        ]
        # 执行命令
        et.execute(*args)
        print(f"成功为 {img_path} 添加元数据")


if __name__ == "__main__":
    # 测试 add_extra_info_to_img
    test_img_path = r"C:\Users\27147\Desktop\4a4d4cb0cdb4ecc121b077c18716623e.jpg"
    test_extra_info = {
        "artist": "AAA",
        "urls": "BBB.jpg"
    }
    add_extra_info_to_img(test_img_path, test_extra_info)
