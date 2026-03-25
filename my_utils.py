# my_utils.py
import json
import os
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
    with open(runtime_snapshot_path, 'w', encoding='utf-8') as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=4)


def clear_runtime_snapshot(runtime_snapshot_path):
    if os.path.exists(runtime_snapshot_path):
        os.remove(runtime_snapshot_path)
