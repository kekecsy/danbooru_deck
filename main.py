import os
import re
import sys
from pathlib import Path
from time import sleep
import datetime
import json
import threading
from fastapi import FastAPI, BackgroundTasks
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from fastapi.staticfiles import StaticFiles
from my_utils import (
    clear_runtime_snapshot,
    load_json,
    merge_daily_viewer_data,
    save_runtime_snapshot,
    get_proxies_for_url
)
import danbooru_api
from danbooru_data import DanbooruData

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from pic_web.main import app as mosaic_editor_app

# ==========================================
# 1. 爬虫全局配置与初始化
# ==========================================
db_data = DanbooruData()
base_download_dir = db_data.base_dir
today_str = db_data.today_str
save_dir = db_data.save_dir

runtime_snapshot_path = os.path.join(save_dir, "_runtime_snapshot.json")
runtime_snapshot = load_json(runtime_snapshot_path, {})
daily_viewer_data = db_data.load_viewer_data()

if runtime_snapshot:
    db_data.log_data.update(runtime_snapshot.get("log_data", {}))
    db_data.artist_stats.update(runtime_snapshot.get("artist_stats", {}))
    daily_viewer_data = merge_daily_viewer_data(
        daily_viewer_data,
        runtime_snapshot.get("daily_viewer_data", [])
    )
    db_data.save_global_data()
    db_data.save_viewer_data(daily_viewer_data)
    clear_runtime_snapshot(runtime_snapshot_path)


def get_available_date_folders():
    date_pattern = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    folders = []
    for item in Path(base_download_dir).iterdir():
        if not item.is_dir() or not date_pattern.match(item.name):
            continue
        try:
            datetime.datetime.strptime(item.name, "%Y-%m-%d")
        except ValueError:
            continue
        folders.append(item.name)
    return sorted(folders, reverse=True)

def resolve_selected_date(requested_date=None):
    available_dates = get_available_date_folders()
    if requested_date:
        try:
            datetime.datetime.strptime(requested_date, "%Y-%m-%d")
            if requested_date in available_dates:
                return requested_date, available_dates
        except ValueError:
            pass

    if today_str in available_dates:
        return today_str, available_dates
    if available_dates:
        return available_dates[0], available_dates
    return today_str, available_dates

def build_local_image_library(selected_date=None):
    library = []
    resolved_date, _ = resolve_selected_date(selected_date)
    current_day_dir = Path(base_download_dir) / resolved_date
    viewer_files = [current_day_dir / "viewer_data.json"]
    known_paths = set()

    for viewer_file in viewer_files:
        if not viewer_file.exists():
            continue
        day_folder = viewer_file.parent.name
        items = load_json(str(viewer_file), [])
        for item in reversed(items):
            filename = item.get("filename")
            web_url = item.get("web_url")
            if not filename:
                continue
            if not web_url:
                web_url = f"/images/{day_folder}/{filename}"
            local_key = os.path.join(day_folder, filename).replace("\\", "/")
            known_paths.add(local_key)
            library.append({
                "artist": item.get("artist") or "未知",
                "filename": filename,
                "local_path": item.get("local_path") or os.path.join(base_download_dir, day_folder, filename),
                "post_url": item.get("post_url") or "#",
                "web_url": web_url,
                "tags": item.get("tags") or {}
            })

    if current_day_dir.exists():
        for image_path in sorted(current_day_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not image_path.is_file():
                continue
            if image_path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                continue
            local_key = f"{current_day_dir.name}/{image_path.name}"
            if local_key in known_paths:
                continue
            library.append({
                "artist": "未知",
                "filename": image_path.name,
                "local_path": str(image_path),
                "post_url": "#",
                "web_url": f"/images/{current_day_dir.name}/{image_path.name}",
                "tags": {}
            })

    return library



# ==========================================
# 2. FastAPI 后端与状态管理
# ==========================================
app = FastAPI()
app.mount("/images", StaticFiles(directory="hot_pic"), name="images")
app.mount("/static", StaticFiles(directory="static"), name="static")
app.mount("/mosaic", mosaic_editor_app)


class ScraperState:
    def __init__(self):
        self.is_running = False
        self.play_event = threading.Event()
        self.play_event.set()
        self.logs = []
        self.filter_tags = []
        self.sent_image_count = len(daily_viewer_data)

state = ScraperState()
scraper_thread = None

def append_log(msg):
    text = str(msg)
    try:
        print(text)  # 控制台也打印一份
    except UnicodeEncodeError:
        safe_text = text.encode("gbk", errors="replace").decode("gbk")
        print(safe_text)
    state.logs.append(text)
    state.logs = state.logs[-500:]


def get_today_save_dir():
    current_day = datetime.datetime.now().strftime('%Y-%m-%d')
    target_dir = os.path.join(base_download_dir, current_day)
    os.makedirs(target_dir, exist_ok=True)
    return current_day, target_dir, os.path.join(target_dir, "_runtime_snapshot.json")

class StartRequest(BaseModel):
    start_page: int
    end_page: int
    tags: str

class OpenLocalRequest(BaseModel):
    local_path: str

# ==========================================
# 3. 核心爬虫逻辑 (融入了打断检测)
# ==========================================
def grabber(db_data, page_num, filter_tags):
    global daily_viewer_data, today_str, save_dir, runtime_snapshot_path
    
    current_day = datetime.datetime.now().strftime('%Y-%m-%d')
    if db_data.today_str != current_day:
        # 如果运行跨天，更新全局 db_data 实例指向新的一天
        db_data.__init__(current_day)
        daily_viewer_data = db_data.load_viewer_data()
        today_str = db_data.today_str
        save_dir = db_data.save_dir
        runtime_snapshot_path = os.path.join(save_dir, "_runtime_snapshot.json")

    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    state.play_event.wait() # 获取页面前检查是否暂停
    if not state.is_running: return [], page_need_update

    try:
        append_log(f"正在获取第 {page_num} 页...")
        posts = danbooru_api.get_posts_by_rank(page_num)
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)
        return [], {"1": [], "2": []}

    for post in posts:
        if not state.is_running: break # 任务终止时跳出
        state.play_event.wait() # 处理每个 ID 前检查是否暂停

        ids = str(post.get('id'))
        if not ids or ids in db_data.log_data:
            continue

        tag_string = post.get('tag_string', '')
        if any(tag in tag_string for tag in filter_tags):
            append_log(f"跳过 ID {ids}，包含过滤标签。")
            continue

        artist = ""
        if 'tag_string_artist' in post:
            drawer_list = post['tag_string_artist'].split(' ')
            drawer_list = [s for s in drawer_list if not s.lower().endswith("(voice_actor)")]
            if len(drawer_list) >= 1:
                artist = ' '.join(drawer_list)

        image_url = post.get('file_url') or post.get('large_file_url')
        if not image_url:
            continue

        state.play_event.wait() # 下载前检查是否暂停
        if not state.is_running: break

        saved_filename = danbooru_api.download_image(image_url, save_dir, append_log)
        if saved_filename:
            db_data.log_data[ids] = image_url
            save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)
            sleep(1)
        else:
            append_log(f"跳过 ID {ids}，下载失败。")
            continue

        if artist:
            db_data.artist_stats[artist] = db_data.artist_stats.get(artist, 0) + 1
            if artist in db_data.all_drawer:
                disk_key = db_data.get_disk_key(artist)
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
                    "tag_string_general": post.get('tag_string_general', ''),
                    "tag_string_character": post.get('tag_string_character', ''),
                    "tag_string_copyright": post.get('tag_string_copyright', ''),
                    "tag_string_artist": post.get('tag_string_artist', '')
                }
            })
            save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)

    db_data.save_global_data()
    db_data.save_viewer_data(daily_viewer_data)
    clear_runtime_snapshot(runtime_snapshot_path)
    return new_hot_artists, page_need_update


def scraper_task(start_page, end_page):
    global scraper_thread
    try:
        output = db_data.load_hot_drawer()
        nu_sets = db_data.load_need_update()

        n = start_page
        append_log(f"开始抓取，从第 {start_page} 页到第 {end_page} 页")
        append_log(f"当前过滤 Tags: {state.filter_tags}")

        while n <= end_page:
            if not state.is_running: 
                append_log("任务已被强制终止。")
                break
                
            state.play_event.wait() # 等待暂停恢复
            
            append_log(f"--- 正在处理大页码 第 {n} 页 ---")
            o, n_u_dict = grabber(db_data, n, state.filter_tags)
            
            output = list(set(output + o) - db_data.all_drawer)
            for k in ["1", "2"]:
                nu_sets[k].update(n_u_dict[k])
            
            db_data.save_hot_drawer(list(set(output)))
            db_data.save_need_update(nu_sets)
                
            n += 1
    except Exception as e:
        save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)
        append_log(f"抓取任务异常中断，已写入临时快照: {e}")
    finally:
        state.is_running = False
        scraper_thread = None
        append_log("所有页面处理完毕或已结束。")

# ==========================================
# 4. API 路由定义
# ==========================================
@app.get("/api/proxy_check")
def check_proxy():
    url = "https://danbooru.donmai.us"
    proxies = danbooru_api.PROXIES
    try:
        resp = danbooru_api.requests.get(url, timeout=5, headers=danbooru_api.HEADERS, proxies=proxies, impersonate="chrome120")
        if resp.status_code == 200:
            if proxies:
                return {"status": "success", "msg": "代理可用（已连通）", "color": "green"}
            else:
                return {"status": "warning", "msg": "直连可用（未使用代理）", "color": "orange"}
        else:
            return {"status": "error", "msg": f"访问异常 ({resp.status_code})", "color": "red"}
    except Exception as e:
        return {"status": "error", "msg": f"无法访问: {str(e)}", "color": "red"}

@app.post("/api/start")
def start_scraper(req: StartRequest, background_tasks: BackgroundTasks):
    global scraper_thread
    if state.is_running:
        return {"msg": "爬虫已经在运行中"}
    
    state.filter_tags = [t.strip() for t in req.tags.split(',') if t.strip()]
    state.is_running = True
    state.play_event.set()
    state.logs = []
    state.sent_image_count = len(daily_viewer_data)

    scraper_thread = threading.Thread(
        target=scraper_task,
        args=(req.start_page, req.end_page),
        daemon=True
    )
    scraper_thread.start()
    return {"msg": "任务已启动"}

@app.post("/api/pause")
def pause_scraper():
    state.play_event.clear()
    append_log("任务已暂停（正在等待当前动作完成）...")
    return {"msg": "已暂停"}

@app.post("/api/resume")
def resume_scraper():
    state.play_event.set()
    append_log("任务已恢复。")
    return {"msg": "已恢复"}

@app.post("/api/stop")
def stop_scraper():
    state.is_running = False
    state.play_event.set()
    save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)
    append_log("已强制结束任务，当前进度已写入临时快照。")
    return {"msg": "已强制结束任务"}

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

@app.get("/api/gallery_data")
def get_gallery_data():
    selected_date, available_dates = resolve_selected_date()
    return {
        "latest_images": [],
        "local_images": build_local_image_library(selected_date),
        "selected_date": selected_date,
        "available_dates": available_dates,
        "today": today_str
    }

@app.get("/api/gallery_data/{date_str}")
def get_gallery_data_by_date(date_str: str):
    selected_date, available_dates = resolve_selected_date(date_str)
    return {
        "latest_images": [],
        "local_images": build_local_image_library(selected_date),
        "selected_date": selected_date,
        "available_dates": available_dates,
        "today": today_str,
        "requested_date": date_str
    }

@app.post("/api/open_local")
def open_local_file(req: OpenLocalRequest):
    try:
        target_path = Path(req.local_path).resolve()
        base_path = Path(base_download_dir).resolve()
        if not str(target_path).startswith(str(base_path)):
            return {"ok": False, "msg": "路径不在允许范围内"}
        if not target_path.exists():
            return {"ok": False, "msg": "文件不存在"}
        if hasattr(os, "startfile"):
            os.startfile(str(target_path))
            return {"ok": True, "msg": "已打开本地文件"}
        return {"ok": False, "msg": "当前系统不支持直接打开本地文件"}
    except Exception as e:
        return {"ok": False, "msg": f"打开失败: {e}"}

@app.get("/")
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>找不到 index.html</h1>")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
