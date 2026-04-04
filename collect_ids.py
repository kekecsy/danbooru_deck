# danbooru_hot.py
import os
import requests
from bs4 import BeautifulSoup
from time import sleep
import datetime
import json
from proxy import get_proxies_for_url
# --- 配置区 ---
base_download_dir = './hot_pic'
os.makedirs(base_download_dir, exist_ok=True)
today_str = datetime.datetime.now().strftime('%Y-%m-%d')
save_dir = os.path.join(base_download_dir, today_str)
stats_path = os.path.join(base_download_dir, "artist_stats.json") # 统计画师频率的文件
log_path = os.path.join(base_download_dir, "log.json")
status_path = os.path.join(base_download_dir, "status.json")

proxies = get_proxies_for_url("https://danbooru.donmai.us")

# --- 配置结束 ---

if not os.path.exists(save_dir):
    os.makedirs(save_dir)

# 加载或初始化全局 Log
if os.path.exists(log_path):
    try:
        with open(log_path, 'r', encoding='utf-8') as f:
            log_data = json.load(f)
    except json.JSONDecodeError:
        log_data = {}
else:
    log_data = {}

# 加载或初始化画师频率统计
if os.path.exists(stats_path):
    try:
        with open(stats_path, 'r', encoding='utf-8') as f:
            artist_stats = json.load(f)
    except:
        artist_stats = {}
else:
    artist_stats = {}

# 准备给 Viewer 使用的数据列表
daily_ids_data = []
if os.path.exists(os.path.join(save_dir, "ids_data.json")):
    try:
        with open(os.path.join(save_dir, "ids_data.json"), 'r', encoding='utf-8') as f:
            daily_ids_data = json.load(f)
    except:
        daily_ids_data = []

def check_proxy(self):
    url = "https://danbooru.donmai.us"
    try:
        resp = requests.get(
            url,
            timeout=5,                 # 防止卡死 UI
            allow_redirects=True,
            proxies=proxies           # 使用代理设置
        )

        if resp.status_code == 200:
            proxies = requests.utils.get_environ_proxies(url)
            if proxies:
                self.proxy_label.setText("状态: 代理可用（已连通 Danbooru）")
                self.proxy_label.setStyleSheet("color: #4CAF50")
            else:
                self.proxy_label.setText("状态: 直连可用（未使用代理）")
                self.proxy_label.setStyleSheet("color: #FF9800")
        else:
            self.proxy_label.setText(f"状态: 访问异常 ({resp.status_code})")
            self.proxy_label.setStyleSheet("color: #F44336")

    except requests.exceptions.ProxyError:
        self.proxy_label.setText("状态: 代理错误")
        self.proxy_label.setStyleSheet("color: #F44336")

    except requests.exceptions.Timeout:
        self.proxy_label.setText("状态: 连接超时")
        self.proxy_label.setStyleSheet("color: #F44336")

    except requests.exceptions.RequestException:
        self.proxy_label.setText("状态: 无法访问 Danbooru")
        self.proxy_label.setStyleSheet("color: #F44336")


def write_status(state, page=None):
    with open(status_path, 'w', encoding='utf-8') as f:
        json.dump({
            "state": state,      # running / done / error
            "page": page,        # 当前页
            "time": datetime.datetime.now().isoformat()
        }, f, ensure_ascii=False, indent=2)


def get_frequency_level(count):
    if count >= 10: return "High (高频)"
    elif count >= 4: return "Mid (中频)"
    else: return "Low (低频)"

def save_global_data():
    """保存全局log和统计数据"""
    # 保存 log
    temp_path = log_path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=4)
    os.replace(temp_path, log_path)
    
    # 保存画师统计
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(artist_stats, f, ensure_ascii=False, indent=4)

def save_ids_data(ids_data):
    """保存当天给GUI读取的数据"""
    viewer_json_path = os.path.join(save_dir, "ids_data.json")
    with open(viewer_json_path, 'w', encoding='utf-8') as f:
        json.dump(ids_data, f, ensure_ascii=False, indent=4)



def fetch_data_with_retry(ids, retries=5, delay=3):
    url = f'https://danbooru.donmai.us/posts/{ids}.json'
    attempt = 0
    while attempt < retries:
        try:
            response = requests.get(url, timeout=10, proxies=proxies)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            attempt += 1
            print(f"请求ID {ids} 失败 ({attempt}/{retries}): {e}")
            sleep(delay)
    return None

# --- 加载排除列表逻辑 (保持不变) ---
os.makedirs('./drawer', exist_ok=True)
# 这里我们将两个来源的画师列表合并成一个 set，方便后续检查，如果不存在则创建空列表

if not os.path.exists('./drawer/txtdata.txt'):
    with open('./drawer/txtdata.txt', 'w', encoding='utf-8') as f:
        f.write('')  # 创建一个空文件
if not os.path.exists('./drawer/disk_drawer.json'):
    with open('./drawer/disk_drawer.json', 'w', encoding='utf-8') as f:
        json.dump({"1": [], "2": []}, f, ensure_ascii=False, indent=4)
with open('./drawer/txtdata.txt') as f:
    txtdata1 = f.read().split('\n')
with open('./drawer/disk_drawer.json', 'r', encoding='utf-8') as f:
    disk_drawer = json.load(f)
txtdata2 = disk_drawer.get("1", []) + disk_drawer.get("2", [])
all_drawer = set(txtdata1 + txtdata2)

folder_to_disk = {}
for k, v in disk_drawer.items():
    for folder in v: folder_to_disk[folder] = k

def get_folder_name(name):
    return (name.replace(":", "%3A").replace("/", "%2F").replace("!", "_")
            .replace("?", "_").replace("<", "_").replace(">", "_").rstrip('.'))

def grabber(all_drawer, page_num,log_callback=None, filter_tags=['furry','futanari']):
    def custom_print(msg):
            print(msg) # 控制台依然显示
            if log_callback:
                log_callback(msg) # 发送给 GUI

    def download_image(url, folder):
        if not url: return None
        filename = url.split('/')[-1]
        filepath = os.path.join(folder, filename)

        if os.path.exists(filepath):
            custom_print(f"文件已存在: {filename}")
            return filename # 返回文件名供记录

        try:
            custom_print(f"正在下载: {filename} ...")
            r = requests.get(url, timeout=20, proxies=proxies)
            if r.status_code == 200:
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(1024):
                        f.write(chunk)
                custom_print(f"下载完成: {filename}")
                return filename
            else:
                custom_print(f"下载失败 (状态码 {r.status_code}): {url}")
                return None
        except Exception as e:
            custom_print(f"下载出错: {e}")
            return None

    global log_data, artist_stats, daily_ids_data
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    try:
        custom_print(f"正在获取第 {page_num} 页...")
        r = requests.get(f'https://danbooru.donmai.us/posts?d=1&page={page_num}&tags=order%3Arank', timeout=15, proxies=proxies)
        r.raise_for_status()
    except Exception as e:
        custom_print(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    soup = BeautifulSoup(r.content, "html.parser")
    articles = soup.find_all('article')
    data_ids = [article.get('data-id') for article in articles]

    for ids in data_ids:
        if not ids: continue

        if ids in log_data:
            continue

        test = fetch_data_with_retry(ids)
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

            if artist:
                artist_stats[artist] = artist_stats.get(artist, 0) + 1
                
                # 你的原有逻辑：判断是否在库
                if artist in all_drawer:
                    f_name = get_folder_name(artist)
                    disk_key = folder_to_disk.get(f_name, "2")
                    page_need_update[disk_key].append(artist)
                else:
                    new_hot_artists.append(artist)

            if artist:
                daily_ids_data.append(ids)

    save_global_data()
    daily_ids_data = list(set(daily_ids_data)) # 去重
    save_ids_data(daily_ids_data) # 实时保存 Viewer 数据，防止中断
    return new_hot_artists, page_need_update



def run(page_start=1, page_end=16):
    n = page_start

    os.makedirs('./drawer', exist_ok=True)
    if not os.path.exists('./drawer/hot_drawer.txt'):
        with open('./drawer/hot_drawer.txt', 'w', encoding='utf-8') as f:
            f.write('')  # 创建一个空文件
    with open('./drawer/hot_drawer.txt','r', encoding='utf-8') as f:
        output = f.read().split('\n')
    need_update_json_path = './drawer/need_update.json'
    if os.path.exists(need_update_json_path):
        with open(need_update_json_path, 'r', encoding='utf-8') as f:
            temp_nu = json.load(f)
            nu_sets = {"1": set(temp_nu.get("1", [])), "2": set(temp_nu.get("2", []))}
    else:
        nu_sets = {"1": set(), "2": set()}


    while n <= page_end:
        print(f"--- 处理第 {n} 页 ---")
        o, n_u_dict = grabber(all_drawer, n)
        
        # 更新列表
        output = list(set(output + o) - all_drawer)
        for k in ["1", "2"]:
            nu_sets[k].update(n_u_dict[k])
        
        # 保存文件
        with open('./drawer/hot_drawer.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(list(set(output))))
            
        final_nu = {k: sorted(list(v)) for k, v in nu_sets.items()}
        with open(need_update_json_path, 'w', encoding='utf-8') as f:
            json.dump(final_nu, f, ensure_ascii=False, indent=4)
            
        n += 1

    print("所有页面处理完毕。")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", type=int, default=1)
    parser.add_argument("--end", type=int, default=16)
    args = parser.parse_args()

    run(args.start, args.end)
