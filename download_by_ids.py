import os
import sys
import argparse
from time import sleep
import danbooru_api
from danbooru_data import DanbooruData
import json

# --- 主下载逻辑 ---
def download_by_ids(post_ids, filter_tags=None):
    """
    下载指定的 post ID 列表，并更新全局统计和当天 viewer 数据
    :param post_ids: 可迭代的 post ID 列表（字符串或整数）
    :param filter_tags: 可选，需要过滤的标签列表，若图片包含其中任一标签则跳过
    """
    db_data = DanbooruData()
    daily_viewer_data = db_data.load_viewer_data()

    # 用于去重：避免同一 ID 重复处理（但 log_data 已记录）
    # 同时收集新添加的条目，最后统一保存
    new_log_entries = {}
    new_artist_counts = {}
    new_viewer_entries = []

    for pid in post_ids:
        pid_str = str(pid)  # 确保是字符串
        if pid_str in db_data.log_data:
            print(f"ID {pid_str} 已存在于 log.json，跳过")
            continue

        print(f"正在处理 ID: {pid_str}")
        post_data = danbooru_api.fetch_data_with_retry(pid_str)
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
        saved_filename = danbooru_api.download_image(image_url, db_data.save_dir)
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
        post_url = danbooru_api.post_url(pid_str)
        viewer_entry = {
            "artist": artist,
            "filename": saved_filename,
            "local_path": os.path.join(db_data.save_dir, saved_filename),
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
        db_data.log_data.update(new_log_entries)
        print(f"已更新 log.json，新增 {len(new_log_entries)} 条记录")

    if new_artist_counts:
        for artist, count in new_artist_counts.items():
            db_data.artist_stats[artist] = db_data.artist_stats.get(artist, 0) + count
        print(f"已更新 artist_stats.json，新增 {len(new_artist_counts)} 位画师记录")

    if new_viewer_entries:
        daily_viewer_data.extend(new_viewer_entries)
        db_data.save_viewer_data(daily_viewer_data)
        print(f"已更新 viewer_data.json，新增 {len(new_viewer_entries)} 条记录")

    db_data.save_global_data()
    print("所有指定 ID 处理完毕。")

# --- 命令行入口 ---
def main():
    # 默认json为当天的文件，方便直接使用
    db_data = DanbooruData()
    default_json = os.path.join(db_data.save_dir, "ids_data.json")
    
    parser = argparse.ArgumentParser(description="根据 ID 列表下载 Danbooru 图片并更新统计")
    group = parser.add_mutually_exclusive_group()  # 注意：这里去掉 required=True
    group.add_argument('--ids', nargs='+', help="空格分隔的 ID 列表，如 123456 789012")
    group.add_argument('--file', help="包含 ID 列表的文本文件，每行一个 ID")
    group.add_argument('--json', help="包含 ID 列表的 JSON 文件，格式如 [123, 456, 789]")
    parser.add_argument('--filter', nargs='+', default=['furry', 'futanari'],
                        help="过滤标签，图片包含任一标签则跳过，默认 ['furry','futanari']")
    parser.add_argument('--no-filter', action='store_true',
                        help="禁用过滤标签，下载所有图片")
    args = parser.parse_args()

    # 获取 ID 列表
    post_ids = None
    
    if args.ids:
        post_ids = args.ids
    elif args.file:
        if not os.path.exists(args.file):
            print(f"错误：文件 {args.file} 不存在")
            sys.exit(1)
        with open(args.file, 'r', encoding='utf-8') as f:
            post_ids = [line.strip() for line in f if line.strip()]
    elif args.json:
        json_path = args.json
        if not os.path.exists(json_path):
            print(f"错误：JSON 文件 {json_path} 不存在")
            sys.exit(1)
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, list):
                print(f"错误：JSON 文件 {json_path} 必须包含一个列表")
                sys.exit(1)
            post_ids = [str(item) for item in data]
            print(f"从 JSON 文件读取到 {len(post_ids)} 个 ID")
        except json.JSONDecodeError as e:
            print(f"错误：JSON 文件 {json_path} 格式错误: {e}")
            sys.exit(1)
    else:
        # 没有任何参数时，尝试使用默认的当天 JSON 文件
        if os.path.exists(default_json):
            print(f"未指定输入源，自动使用默认 JSON 文件: {default_json}")
            try:
                with open(default_json, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                if not isinstance(data, list):
                    print(f"错误：默认 JSON 文件 {default_json} 必须包含一个列表")
                    sys.exit(1)
                post_ids = [str(item) for item in data]
                print(f"从默认 JSON 文件读取到 {len(post_ids)} 个 ID")
            except json.JSONDecodeError as e:
                print(f"错误：默认 JSON 文件 {default_json} 格式错误: {e}")
                sys.exit(1)
        else:
            print("错误：未指定 --ids / --file / --json，且默认的当天 JSON 文件不存在")
            print(f"期望的默认路径: {default_json}")
            sys.exit(1)

    if not post_ids:
        print("没有有效的 ID，退出")
        return

    # 过滤标签设置
    filter_tags = None if args.no_filter else args.filter

    # 执行下载
    download_by_ids(post_ids, filter_tags)

if __name__ == "__main__":
    main()