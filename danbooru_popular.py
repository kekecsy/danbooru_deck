# danbooru_hot.py
import os
from time import sleep
import datetime
import json
import danbooru_api
from danbooru_data import DanbooruData

# 如果需要固定日期，修改这个变量
TODAY_STR = "2026-05-10"


def get_frequency_level(count):
    if count >= 10: return "High (高频)"
    elif count >= 4: return "Mid (中频)"
    else: return "Low (低频)"

def grabber(db_data, page_num, log_callback=None, filter_tags=['furry','futanari']):
    def custom_print(msg):
            print(msg) # 控制台依然显示
            if log_callback:
                log_callback(msg) # 发送给 GUI

    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    daily_viewer_data = db_data.load_viewer_data()
    
    try:
        custom_print(f"正在获取第 {page_num} 页...")
        posts = danbooru_api.get_popular_posts(db_data.today_str, page_num)
    except Exception as e:
        custom_print(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    for test in posts:
        ids = str(test.get('id'))
        if not ids: continue
        if ids in db_data.log_data: continue

        if test:

            if any(tag in test.get('tag_string', '') for tag in filter_tags):
                custom_print(f"跳过 ID {ids}，包含过滤标签。")
                continue  # ⬅️ 包含过滤标签，跳过这个 post

            artist = ""
            if 'tag_string_artist' in test:
                drawer_list = test['tag_string_artist'].split(' ')
                drawer_list = [s for s in drawer_list if not s.lower().endswith("(voice_actor)")]
                
                if len(drawer_list) >= 1:
                    artist = ' '.join(drawer_list)

            image_url = test.get('file_url') or test.get('large_file_url')
            saved_filename = None

            if image_url:
                saved_filename = danbooru_api.download_image(image_url, db_data.save_dir, custom_print)
                if saved_filename:
                    db_data.log_data[ids] = image_url
                    sleep(1)
                else:
                    custom_print(f"跳过 ID {ids}，下载失败。")
                    continue  # ⬅️ 下载失败，直接跳过这个 post
            else:
                continue

            if artist:
                # 更新历史计数
                db_data.artist_stats[artist] = db_data.artist_stats.get(artist, 0) + 1
                
                # 你的原有逻辑：判断是否在库
                if artist in db_data.all_drawer:
                    disk_key = db_data.get_disk_key(artist)
                    page_need_update[disk_key].append(artist)
                else:
                    new_hot_artists.append(artist)

            if saved_filename and artist:
                post_url = f"https://danbooru.donmai.us/posts/{ids}"
                daily_viewer_data.append({
                    "artist": artist,
                    "filename": saved_filename,
                    "local_path": os.path.join(db_data.save_dir, saved_filename),
                    "post_url": post_url,
                    "tags": {
                        "tag_string_general": test.get('tag_string_general', ''),
                        "tag_string_character": test.get('tag_string_character', ''),
                        "tag_string_copyright": test.get('tag_string_copyright', ''),
                        "tag_string_artist": test.get('tag_string_artist', ''),
                        "tag_string_meta": test.get('tag_string_meta', '')
                    }
                })

    db_data.save_global_data()
    db_data.save_viewer_data(daily_viewer_data) # 实时保存 Viewer 数据，防止中断
    return new_hot_artists, page_need_update

def run(page_start=1, page_end=50):
    db_data = DanbooruData(TODAY_STR)
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
    parser.add_argument("--end", type=int, default=35)
    args = parser.parse_args()

    run(args.start, args.end)
