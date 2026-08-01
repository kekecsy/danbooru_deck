# 当前网不是很好的时候用于收集ids，等网好了再去用download_by_ids.py下载
import danbooru_api
from danbooru_data import DanbooruData

def grabber(db_data, page_num, log_callback=None, filter_tags=['furry','futanari','guro']):
    def custom_print(msg):
            print(msg) # 控制台依然显示
            if log_callback:
                log_callback(msg) # 发送给 GUI

    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    daily_ids_data = db_data.load_ids_data()
    try:
        custom_print(f"正在获取第 {page_num} 页...")
        posts = danbooru_api.get_posts_by_rank(page_num)
    except Exception as e:
        custom_print(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    for test in posts:
        ids = str(test.get('id'))
        if not ids: continue

        if ids in db_data.log_data:
            continue

        if test:

            if any(tag in test.get('tag_string', '') for tag in filter_tags):
                custom_print(f"跳过 ID {ids}，包含过滤标签。")
                continue

            artist = ""
            if 'tag_string_artist' in test:
                drawer_list = test['tag_string_artist'].split(' ')
                drawer_list = [s for s in drawer_list if not s.lower().endswith("(voice_actor)")]
                
                if len(drawer_list) >= 1:
                    artist = ' '.join(drawer_list)

            if artist:
                db_data.artist_stats[artist] = db_data.artist_stats.get(artist, 0) + 1
                
                if artist in db_data.all_drawer:
                    disk_key = db_data.get_disk_key(artist)
                    page_need_update[disk_key].append(artist)
                else:
                    new_hot_artists.append(artist)

            if artist:
                daily_ids_data.append(ids)

    db_data.save_global_data()
    daily_ids_data = list(set(daily_ids_data)) # 去重
    db_data.save_ids_data(daily_ids_data) # 实时保存 Viewer 数据，防止中断
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
