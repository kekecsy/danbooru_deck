import os
import sys
import json
import argparse
import datetime
import requests
from time import sleep
from proxy import get_proxies_for_url

# --- 配置（复用原脚本）---
base_download_dir = './hot_pic'
os.makedirs(base_download_dir, exist_ok=True)
today_str = datetime.datetime.now().strftime('%Y-%m-%d')
save_dir = os.path.join(base_download_dir, today_str)
stats_path = os.path.join(base_download_dir, "artist_stats.json")
log_path = os.path.join(base_download_dir, "log.json")
status_path = os.path.join(base_download_dir, "status.json")

proxies = get_proxies_for_url("https://danbooru.donmai.us")

# --- 辅助函数（从原脚本复制并调整）---
def fetch_data_with_retry(post_id, retries=5, delay=3):
    """获取单个 post 的 JSON 数据"""
    url = f'https://danbooru.donmai.us/posts/{post_id}.json'
    for attempt in range(retries):
        try:
            response = requests.get(url, timeout=10, proxies=proxies)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            print(f"请求ID {post_id} 失败 ({attempt+1}/{retries}): {e}")
            sleep(delay)
    return None

def download_image(url, folder):
    """下载图片到指定文件夹，返回文件名，若已存在则返回 None"""
    if not url:
        return None
    filename = url.split('/')[-1]
    filepath = os.path.join(folder, filename)
    if os.path.exists(filepath):
        print(f"文件已存在，跳过: {filename}")
        return None
    try:
        print(f"正在下载: {filename} ...")
        r = requests.get(url, timeout=20, proxies=proxies)
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                for chunk in r.iter_content(1024):
                    f.write(chunk)
            print(f"下载完成: {filename}")
            return filename
        else:
            print(f"下载失败 (状态码 {r.status_code}): {url}")
            return None
    except Exception as e:
        print(f"下载出错: {e}")
        return None

def load_json_safe(path, default=None):
    """安全加载 JSON 文件，若不存在或格式错误则返回默认值"""
    if default is None:
        default = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except json.JSONDecodeError:
            print(f"警告: {path} 格式错误，将使用空数据")
            return default
    return default

def save_json_safe(path, data):
    """原子保存 JSON 文件"""
    temp_path = path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=4)
    os.replace(temp_path, path)

# --- 主下载逻辑 ---
def download_by_ids(post_ids, filter_tags=None):
    """
    下载指定的 post ID 列表，并更新全局统计和当天 viewer 数据
    :param post_ids: 可迭代的 post ID 列表（字符串或整数）
    :param filter_tags: 可选，需要过滤的标签列表，若图片包含其中任一标签则跳过
    """
    # 确保当天目录存在
    os.makedirs(save_dir, exist_ok=True)

    # 加载现有数据
    log_data = load_json_safe(log_path)
    artist_stats = load_json_safe(stats_path)
    viewer_data_path = os.path.join(save_dir, "viewer_data.json")
    daily_viewer_data = load_json_safe(viewer_data_path, default=[])

    # 用于去重：避免同一 ID 重复处理（但 log_data 已记录）
    # 同时收集新添加的条目，最后统一保存
    new_log_entries = {}
    new_artist_counts = {}
    new_viewer_entries = []

    for pid in post_ids:
        pid_str = str(pid)  # 确保是字符串
        if pid_str in log_data:
            print(f"ID {pid_str} 已存在于 log.json，跳过")
            continue

        print(f"正在处理 ID: {pid_str}")
        post_data = fetch_data_with_retry(pid_str)
        if not post_data:
            print(f"ID {pid_str} 获取数据失败，跳过")
            continue

        # 可选过滤标签
        if filter_tags:
            tag_string = post_data.get('tag_string', '')
            if any(tag in tag_string for tag in filter_tags):
                print(f"ID {pid_str} 包含过滤标签，跳过")
                continue

        # 获取图片 URL
        image_url = post_data.get('file_url') or post_data.get('large_file_url')
        if not image_url:
            print(f"ID {pid_str} 无有效图片 URL，跳过")
            continue

        # 下载图片
        saved_filename = download_image(image_url, save_dir)
        if not saved_filename:
            print(f"ID {pid_str} 下载失败，跳过")
            continue

        # 提取画师名
        artist = ""
        if 'tag_string_artist' in post_data:
            artist_list = post_data['tag_string_artist'].split()
            # 过滤掉 voice_actor 标签（与原脚本一致）
            artist_list = [a for a in artist_list if not a.lower().endswith("(voice_actor)")]
            if artist_list:
                artist = ' '.join(artist_list)

        # 更新日志
        new_log_entries[pid_str] = image_url

        # 更新画师统计
        if artist:
            new_artist_counts[artist] = new_artist_counts.get(artist, 0) + 1

        # 构建 viewer 条目
        post_url = f"https://danbooru.donmai.us/posts/{pid_str}"
        viewer_entry = {
            "artist": artist,
            "filename": saved_filename,
            "local_path": os.path.join(save_dir, saved_filename),
            "post_url": post_url,
            "tags": {
                "tag_string_general": post_data.get('tag_string_general', ''),
                "tag_string_character": post_data.get('tag_string_character', ''),
                "tag_string_copyright": post_data.get('tag_string_copyright', ''),
                "tag_string_artist": post_data.get('tag_string_artist', ''),
                "tag_string_meta": post_data.get('tag_string_meta', '')
            }
        }
        new_viewer_entries.append(viewer_entry)

        # 打印进度
        print(f"ID {pid_str} 处理完成")

    # 合并并保存数据
    if new_log_entries:
        log_data.update(new_log_entries)
        save_json_safe(log_path, log_data)
        print(f"已更新 log.json，新增 {len(new_log_entries)} 条记录")

    if new_artist_counts:
        for artist, count in new_artist_counts.items():
            artist_stats[artist] = artist_stats.get(artist, 0) + count
        save_json_safe(stats_path, artist_stats)
        print(f"已更新 artist_stats.json，新增 {len(new_artist_counts)} 位画师记录")

    if new_viewer_entries:
        daily_viewer_data.extend(new_viewer_entries)
        save_json_safe(viewer_data_path, daily_viewer_data)
        print(f"已更新 {viewer_data_path}，新增 {len(new_viewer_entries)} 条记录")

    print("所有指定 ID 处理完毕。")

# --- 命令行入口 ---
def main():
    parser = argparse.ArgumentParser(description="根据 ID 列表下载 Danbooru 图片并更新统计")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--ids', nargs='+', help="空格分隔的 ID 列表，如 123456 789012")
    group.add_argument('--file', help="包含 ID 列表的文本文件，每行一个 ID")
    parser.add_argument('--filter', nargs='+', default=['furry', 'futanari'],
                        help="过滤标签，图片包含任一标签则跳过，默认 ['furry','futanari']")
    parser.add_argument('--no-filter', action='store_true',
                        help="禁用过滤标签，下载所有图片")
    args = parser.parse_args()

    # 获取 ID 列表
    if args.ids:
        post_ids = args.ids
    else:  # args.file
        if not os.path.exists(args.file):
            print(f"错误：文件 {args.file} 不存在")
            sys.exit(1)
        with open(args.file, 'r', encoding='utf-8') as f:
            post_ids = [line.strip() for line in f if line.strip()]

    if not post_ids:
        print("没有有效的 ID，退出")
        return

    # 过滤标签设置
    filter_tags = None if args.no_filter else args.filter

    # 执行下载
    download_by_ids(post_ids, filter_tags)

if __name__ == "__main__":
    main()