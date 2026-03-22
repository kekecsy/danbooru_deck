import os
import sys
from pathlib import Path
import requests
from bs4 import BeautifulSoup
from time import sleep
import datetime
import json
import threading
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pic_web.main import app as mosaic_editor_app

# ==========================================
# 1. 爬虫全局配置与初始化
# ==========================================
base_download_dir = './hot_pic'
os.makedirs(base_download_dir, exist_ok=True)
today_str = datetime.datetime.now().strftime('%Y-%m-%d')
save_dir = os.path.join(base_download_dir, today_str)
stats_path = os.path.join(base_download_dir, "artist_stats.json") 
log_path = os.path.join(base_download_dir, "log.json")
status_path = os.path.join(base_download_dir, "status.json")

os.makedirs(save_dir, exist_ok=True)
os.makedirs('./drawer', exist_ok=True)

# 加载全局数据
def load_json(path, default):
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f: return json.load(f)
        except: return default
    return default

log_data = load_json(log_path, {})
artist_stats = load_json(stats_path, {})
daily_viewer_data = load_json(os.path.join(save_dir, "viewer_data.json"), [])

# 初始化排除列表和画师字典
if not os.path.exists('./drawer/txtdata.txt'):
    open('./drawer/txtdata.txt', 'w', encoding='utf-8').close()
if not os.path.exists('./drawer/disk_drawer.json'):
    with open('./drawer/disk_drawer.json', 'w', encoding='utf-8') as f:
        json.dump({"1": [], "2": []}, f, ensure_ascii=False, indent=4)
if not os.path.exists('./drawer/hot_drawer.txt'):
    open('./drawer/hot_drawer.txt', 'w', encoding='utf-8').close()

with open('./drawer/txtdata.txt', 'r', encoding='utf-8') as f:
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

def save_global_data():
    temp_path = log_path + ".tmp"
    with open(temp_path, 'w', encoding='utf-8') as f:
        json.dump(log_data, f, ensure_ascii=False, indent=4)
    os.replace(temp_path, log_path)
    with open(stats_path, 'w', encoding='utf-8') as f:
        json.dump(artist_stats, f, ensure_ascii=False, indent=4)

def save_viewer_data(viewer_data):
    with open(os.path.join(save_dir, "viewer_data.json"), 'w', encoding='utf-8') as f:
        json.dump(viewer_data, f, ensure_ascii=False, indent=4)

def fetch_data_with_retry(ids, retries=5, delay=3):
    url = f'https://danbooru.donmai.us/posts/{ids}.json'
    attempt = 0
    while attempt < retries:
        try:
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            return response.json()
        except Exception as e:
            attempt += 1
            append_log(f"请求ID {ids} 失败 ({attempt}/{retries}): {e}")
            sleep(delay)
    return None

# ==========================================
# 2. FastAPI 后端与状态管理
# ==========================================
app = FastAPI()
app.mount("/images", StaticFiles(directory="hot_pic"), name="images")
app.mount("/mosaic", mosaic_editor_app)


class ScraperState:
    def __init__(self):
        self.is_running = False
        self.play_event = threading.Event()
        self.play_event.set()
        self.logs = []
        self.filter_tags = []
        self.sent_image_count = 0

state = ScraperState()

def append_log(msg):
    print(msg)  # 控制台也打印一份
    state.logs.append(msg)

class StartRequest(BaseModel):
    start_page: int
    end_page: int
    tags: str

# ==========================================
# 3. 核心爬虫逻辑 (融入了打断检测)
# ==========================================
def grabber(all_drawer, page_num, filter_tags):
    def download_image(url, folder):
        if not url: return None
        filename = url.split('/')[-1]
        filepath = os.path.join(folder, filename)

        if os.path.exists(filepath):
            append_log(f"文件已存在: {filename}")
            return filename

        try:
            state.play_event.wait() # 【关键点】下载前检查是否暂停
            if not state.is_running: return None

            append_log(f"正在下载: {filename} ...")
            r = requests.get(url, timeout=20)
            if r.status_code == 200:
                with open(filepath, 'wb') as f:
                    for chunk in r.iter_content(1024):
                        f.write(chunk)
                append_log(f"下载完成: {filename}")
                return filename
            else:
                append_log(f"下载失败 (状态码 {r.status_code}): {url}")
                return None
        except Exception as e:
            append_log(f"下载出错: {e}")
            return None

    global log_data, artist_stats, daily_viewer_data
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    state.play_event.wait() # 【关键点】获取页面前检查是否暂停
    if not state.is_running: return [], page_need_update

    try:
        append_log(f"正在获取第 {page_num} 页...")
        r = requests.get(f'https://danbooru.donmai.us/posts?d=1&page={page_num}&tags=order%3Arank', timeout=15)
        r.raise_for_status()
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    soup = BeautifulSoup(r.content, "html.parser")
    articles = soup.find_all('article')
    data_ids = [article.get('data-id') for article in articles]

    for ids in data_ids:
        if not state.is_running: break # 任务终止时跳出
        state.play_event.wait() # 【关键点】处理每个 ID 前检查是否暂停

        if not ids: continue
        if ids in log_data: continue

        test = fetch_data_with_retry(ids)
        if test:
            # 动态过滤 Tag
            if any(tag in test.get('tag_string', '') for tag in filter_tags):
                append_log(f"跳过 ID {ids}，包含过滤标签。")
                continue

            artist = ""
            if 'tag_string_artist' in test:
                drawer_list = test['tag_string_artist'].split(' ')
                drawer_list = [s for s in drawer_list if not s.lower().endswith("(voice_actor)")]
                if len(drawer_list) >= 1:
                    artist = ' '.join(drawer_list)

            image_url = test.get('file_url') or test.get('large_file_url')
            saved_filename = None

            if image_url:
                saved_filename = download_image(image_url, save_dir)
                if saved_filename:
                    log_data[ids] = image_url
                    sleep(1)
                else:
                    append_log(f"跳过 ID {ids}，下载失败。")
                    continue
            else:
                continue

            if artist:
                artist_stats[artist] = artist_stats.get(artist, 0) + 1
                if artist in all_drawer:
                    f_name = get_folder_name(artist)
                    disk_key = folder_to_disk.get(f_name, "2")
                    page_need_update[disk_key].append(artist)
                else:
                    new_hot_artists.append(artist)

            if saved_filename and artist:
                post_url = f"https://danbooru.donmai.us/posts/{ids}"
                web_url = f"/images/{today_str}/{saved_filename}"
                daily_viewer_data.append({
                    "artist": artist,
                    "filename": saved_filename,
                    "local_path": os.path.join(save_dir, saved_filename),
                    "post_url": post_url,
                    "web_url": web_url,
                    "tags": {
                        "tag_string_general": test.get('tag_string_general', ''),
                        "tag_string_character": test.get('tag_string_character', ''),
                        "tag_string_copyright": test.get('tag_string_copyright', ''),
                        "tag_string_artist": test.get('tag_string_artist', '')
                    }
                })

    save_global_data()
    save_viewer_data(daily_viewer_data)
    return new_hot_artists, page_need_update


def scraper_task(start_page, end_page):
    try:
        with open('./drawer/hot_drawer.txt', 'r', encoding='utf-8') as f:
            output = f.read().split('\n')
            output = [x for x in output if x] # 清理空行
    except:
        output = []

    need_update_json_path = './drawer/need_update.json'
    if os.path.exists(need_update_json_path):
        with open(need_update_json_path, 'r', encoding='utf-8') as f:
            temp_nu = json.load(f)
            nu_sets = {"1": set(temp_nu.get("1", [])), "2": set(temp_nu.get("2", []))}
    else:
        nu_sets = {"1": set(), "2": set()}

    n = start_page
    append_log(f"▶ 开始抓取，从第 {start_page} 页到第 {end_page} 页")
    append_log(f"▶ 当前过滤 Tags: {state.filter_tags}")

    while n <= end_page:
        if not state.is_running: 
            append_log("⏹ 任务已被强制终止。")
            break
            
        state.play_event.wait() # 等待暂停恢复
        
        append_log(f"--- 正在处理大页码 第 {n} 页 ---")
        o, n_u_dict = grabber(all_drawer, n, state.filter_tags)
        
        output = list(set(output + o) - all_drawer)
        for k in ["1", "2"]:
            nu_sets[k].update(n_u_dict[k])
        
        with open('./drawer/hot_drawer.txt', 'w', encoding='utf-8') as f:
            f.write('\n'.join(list(set(output))))
            
        final_nu = {k: sorted(list(v)) for k, v in nu_sets.items()}
        with open(need_update_json_path, 'w', encoding='utf-8') as f:
            json.dump(final_nu, f, ensure_ascii=False, indent=4)
            
        n += 1

    state.is_running = False
    append_log("✅ 所有页面处理完毕或已结束。")

# ==========================================
# 4. API 路由定义
# ==========================================
@app.get("/api/proxy_check")
def check_proxy():
    url = "https://danbooru.donmai.us"
    try:
        resp = requests.get(url, timeout=5, allow_redirects=True)
        if resp.status_code == 200:
            proxies = requests.utils.get_environ_proxies(url)
            if proxies:
                return {"status": "success", "msg": "代理可用（已连通）", "color": "green"}
            else:
                return {"status": "warning", "msg": "直连可用（未使用代理）", "color": "orange"}
        else:
            return {"status": "error", "msg": f"访问异常 ({resp.status_code})", "color": "red"}
    except requests.exceptions.ProxyError:
        return {"status": "error", "msg": "代理错误", "color": "red"}
    except requests.exceptions.Timeout:
        return {"status": "error", "msg": "连接超时", "color": "red"}
    except Exception as e:
        return {"status": "error", "msg": f"无法访问: {str(e)}", "color": "red"}

@app.post("/api/start")
def start_scraper(req: StartRequest, background_tasks: BackgroundTasks):
    if state.is_running:
        return {"msg": "爬虫已经在运行中"}
    
    state.filter_tags = [t.strip() for t in req.tags.split(',') if t.strip()]
    state.is_running = True
    state.play_event.set()
    state.logs = [] 
    
    background_tasks.add_task(scraper_task, req.start_page, req.end_page)
    return {"msg": "任务已启动"}

@app.post("/api/pause")
def pause_scraper():
    state.play_event.clear()
    append_log("\n🔴 任务已暂停（正在等待当前动作完成）...")
    return {"msg": "已暂停"}

@app.post("/api/resume")
def resume_scraper():
    state.play_event.set()
    append_log("\n🟢 任务已恢复...")
    return {"msg": "已恢复"}

@app.get("/api/status")
def get_status():
    global daily_viewer_data
    logs = state.logs.copy()
    state.logs.clear()
    
    # 获取新增的图片数据
    new_images = []
    current_count = len(daily_viewer_data)
    if current_count > state.sent_image_count:
        # 只切片取出新增加的部分
        new_images = daily_viewer_data[state.sent_image_count : current_count]
        state.sent_image_count = current_count

    return {
        "is_running": state.is_running,
        "is_paused": not state.play_event.is_set() if state.is_running else False,
        "new_logs": logs,
        "new_images": new_images  # 发送给前端渲染
    }

@app.get("/")
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>找不到 index.html</h1>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
