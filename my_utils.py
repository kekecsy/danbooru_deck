# my_utils.py
import json
import os
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


def merge_daily_viewer_data(current_items, snapshot_items):
    merged = list(current_items)
    known = {
        (item.get("filename"), item.get("web_url"))
        for item in current_items
    }
    for item in snapshot_items:
        key = (item.get("filename"), item.get("web_url"))
        if key not in known:
            merged.append(item)
            known.add(key)
    return merged


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
        "daily_viewer_data": daily_viewer_data
    }
    temp_path = runtime_snapshot_path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=4)
    os.replace(temp_path, runtime_snapshot_path)


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
