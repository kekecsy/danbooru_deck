# danbooru_hot.py
import os
import json
import datetime
from time import sleep
import danbooru_api
from danbooru_data import DanbooruData

def grabber(db_data, page_num, log_callback=None, filter_tags=['furry', 'futanari']):
    def custom_print(msg):
        print(msg)
        if log_callback: log_callback(msg)

    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    
    daily_viewer_data = db_data.load_viewer_data()

    # 1. 直接获取 JSON 列表，一次性拿 20 条数据
    try:
        custom_print(f"正在获取第 {page_num} 页数据 (JSON)...")
        posts = danbooru_api.get_posts_by_rank(page_num)
    except Exception as e:
        custom_print(f"获取 JSON 失败: {e}")
        return [], {"1": [], "2": []}

    # 2. 遍历数据
    for post in posts:
        post_id = str(post.get('id'))
        if not post_id or post_id in db_data.log_data:
            continue

        # 检查过滤标签
        tag_string = post.get('tag_string', '')
        if any(tag in tag_string for tag in filter_tags):
            custom_print(f"跳过 ID {post_id} (包含过滤标签)")
            continue

        # 获取画师信息
        artist_tags = post.get('tag_string_artist', '').split(' ')
        artist_list = [a for a in artist_tags if a and not a.lower().endswith("(voice_actor)")]
        artist = ' '.join(artist_list) if artist_list else "unknown_artist"

        # 下载图片
        image_url = post.get('file_url') or post.get('large_file_url')
        if not image_url:
            continue

        saved_filename = danbooru_api.download_image(image_url, db_data.save_dir, custom_print)
        if saved_filename:
            db_data.log_data[post_id] = image_url
            
            # 统计画师
            if artist != "unknown_artist":
                db_data.artist_stats[artist] = db_data.artist_stats.get(artist, 0) + 1
                if artist in db_data.all_drawer:
                    disk_key = db_data.get_disk_key(artist)
                    page_need_update[disk_key].append(artist)
                else:
                    new_hot_artists.append(artist)

            # 存入 Viewer 数据
            daily_viewer_data.append({
                "artist": artist,
                "filename": saved_filename,
                "local_path": os.path.join(db_data.save_dir, saved_filename),
                "post_url": f"https://danbooru.donmai.us/posts/{post_id}",
                "tags": {
                    "tag_string_general": post.get('tag_string_general', ''),
                    "tag_string_character": post.get('tag_string_character', ''),
                    "tag_string_copyright": post.get('tag_string_copyright', ''),
                    "tag_string_artist": post.get('tag_string_artist', ''),
                    "tag_string_meta": post.get('tag_string_meta', '')
                }
            })
            sleep(1.5) # 稍微慢一点，保护 IP

    db_data.save_global_data()
    db_data.save_viewer_data(daily_viewer_data)
    return new_hot_artists, page_need_update


def run(page_start=1, page_end=16):
    db_data = DanbooruData()
    n = page_start

    output = db_data.load_hot_drawer()
    nu_sets = db_data.load_need_update()

    while n <= page_end:
        print(f"--- 处理第 {n} 页 ---")
        o, n_u_dict = grabber(db_data, n)
        
        # 更新列表
        output = list(set(output + o) - db_data.all_drawer)
        for k in ["1", "2"]:
            nu_sets[k].update(n_u_dict[k])
        
        # 保存文件
        db_data.save_hot_drawer(list(set(output)))
        db_data.save_need_update(nu_sets)
            
        n += 1

    print("所有页面处理完毕。")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1) 
    parser.add_argument("--end", type=int, default=16)
    args = parser.parse_args()

    run(args.start, args.end)
