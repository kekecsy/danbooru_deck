import os
import re
import sys
from pathlib import Path
from time import sleep
import datetime
import json
import threading
import concurrent.futures
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
from translator import translator

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
            tags_dict = item.get("tags") or {}
            characters_str = tags_dict.get("tag_string_character", "")
            
            translated_chars = []
            for c in characters_str.split():
                c = c.strip()
                if c:
                    info = translator.get_tag_info(c)
                    chinese_name = info.get("chinese_name") or translator._format_tag(c)
                    hint = info.get("source_hint", "")
                    alias = translator.get_source_hint_alias(hint) if hint else ""
                    
                    # 组合成一个包含搜索元数据的字符串，前端显示时会截取
                    meta = chinese_name
                    if hint: meta += f" [{hint}]"
                    if alias: meta += f" [{alias}]"
                    translated_chars.append(meta)

            library.append({
                "artist": item.get("artist") or "未知",
                "filename": filename,
                "local_path": item.get("local_path") or os.path.join(base_download_dir, day_folder, filename),
                "post_url": item.get("post_url") or "#",
                "web_url": web_url,
                "tags": tags_dict,
                "characters": translated_chars,
                "score": item.get("score", 0) or 0,
                "fav_count": item.get("fav_count", 0) or 0
            })

    if current_day_dir.exists():
        for image_path in sorted(current_day_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not image_path.is_file():
                continue
            suffix = image_path.suffix.lower()
            if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                continue
            # 跳过已有对应 zip 的 gif（属于已转换的动画），避免重复显示
            if suffix == ".gif" and image_path.with_suffix(".zip").exists():
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
                "tags": {},
                "characters": []
            })

    return library



# ==========================================
# 2. FastAPI 后端与状态管理
# ==========================================
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
    mode: str = "rank"  # rank | collect_ids | download_ids | popular | popular_range
    target_date: str = ""  # popular 模式用，可指定日期
    start_date: str = ""   # popular_range
    end_date: str = ""     # popular_range

class OpenLocalRequest(BaseModel):
    local_path: str

class TranslationImportRequest(BaseModel):
    translations: dict

class ConvertLocalZipRequest(BaseModel):
    local_path: str

# ==========================================
# 3. 核心爬虫逻辑 (融入了打断检测)
# ==========================================
def _ensure_today(db_data_inst, target_date=None):
    """如果跨天或指定了目标日期，更新全局变量"""
    global daily_viewer_data, today_str, save_dir, runtime_snapshot_path
    current_day = target_date if target_date else datetime.datetime.now().strftime('%Y-%m-%d')
    
    if db_data_inst.today_str != current_day:
        db_data_inst.__init__(current_day)
        
    if today_str != current_day:
        today_str = db_data_inst.today_str
        save_dir = db_data_inst.save_dir
        runtime_snapshot_path = os.path.join(save_dir, "_runtime_snapshot.json")
        daily_viewer_data = db_data_inst.load_viewer_data()

def _process_post(post, db_data_inst, filter_tags, do_download=True):
    """处理单个 post：过滤、提取画师、可选下载。返回 (ids, artist, saved_filename) 或 None"""
    global daily_viewer_data
    ids = str(post.get('id'))
    if not ids or ids in db_data_inst.log_data:
        return None

    tag_string = post.get('tag_string', '')
    if any(tag in tag_string for tag in filter_tags):
        append_log(f"跳过 ID {ids}，包含过滤标签。")
        return None

    artist = ""
    if 'tag_string_artist' in post:
        drawer_list = post['tag_string_artist'].split(' ')
        drawer_list = [s for s in drawer_list if not s.lower().endswith("(voice_actor)")]
        if drawer_list:
            artist = ' '.join(drawer_list)

    saved_filename = None
    if do_download:
        image_url = post.get('file_url') or post.get('large_file_url')
        if not image_url:
            return None

        state.play_event.wait()
        if not state.is_running:
            return None

        saved_filename = danbooru_api.download_image(image_url, save_dir, append_log)
        if saved_filename:
            db_data_inst.log_data[ids] = image_url
            save_runtime_snapshot(db_data_inst.log_data, db_data_inst.artist_stats, daily_viewer_data, runtime_snapshot_path)
            sleep(1)
        else:
            append_log(f"跳过 ID {ids}，下载失败。")
            return None

    return ids, artist, saved_filename

def _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists):
    """更新画师统计并归类"""
    if not artist:
        return
    db_data_inst.artist_stats[artist] = db_data_inst.artist_stats.get(artist, 0) + 1
    if artist in db_data_inst.all_drawer:
        disk_key = db_data_inst.get_disk_key(artist)
        page_need_update[disk_key].append(artist)
    else:
        new_hot_artists.append(artist)

def _append_viewer(ids, artist, saved_filename, post):
    """往 daily_viewer_data 追加一条记录"""
    global daily_viewer_data
    if not (saved_filename and artist):
        return
    post_url = f"https://danbooru.donmai.us/posts/{ids}"
    web_url = f"/images/{today_str}/{saved_filename}"
    daily_viewer_data.append({
        "artist": artist,
        "filename": saved_filename,
        "local_path": os.path.join(save_dir, saved_filename),
        "post_url": post_url,
        "web_url": web_url,
        "score": post.get('score', 0) or 0,
        "fav_count": post.get('fav_count', 0) or 0,
        "tags": {
            "tag_string_general": post.get('tag_string_general', ''),
            "tag_string_character": post.get('tag_string_character', ''),
            "tag_string_copyright": post.get('tag_string_copyright', ''),
            "tag_string_artist": post.get('tag_string_artist', '')
        }
    })
    save_runtime_snapshot(db_data.log_data, db_data.artist_stats, daily_viewer_data, runtime_snapshot_path)

# --- mode: rank (原有默认模式) ---
def grabber_rank(db_data_inst, page_num, filter_tags):
    global daily_viewer_data
    _ensure_today(db_data_inst)
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    state.play_event.wait()
    if not state.is_running:
        return [], page_need_update

    try:
        append_log(f"[Rank] 正在获取第 {page_num} 页...")
        posts = danbooru_api.get_posts_by_rank(page_num)
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        save_runtime_snapshot(db_data_inst.log_data, db_data_inst.artist_stats, daily_viewer_data, runtime_snapshot_path)
        return [], {"1": [], "2": []}

    for post in posts:
        if not state.is_running:
            break
        state.play_event.wait()
        result = _process_post(post, db_data_inst, filter_tags, do_download=True)
        if not result:
            continue
        ids, artist, saved_filename = result
        _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists)
        _append_viewer(ids, artist, saved_filename, post)

    db_data_inst.save_global_data()
    db_data_inst.save_viewer_data(daily_viewer_data)
    clear_runtime_snapshot(runtime_snapshot_path)
    return new_hot_artists, page_need_update

# --- mode: popular (按日期热门) ---
def grabber_popular(db_data_inst, page_num, filter_tags, target_date):
    global daily_viewer_data
    _ensure_today(db_data_inst, target_date)
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []

    state.play_event.wait()
    if not state.is_running:
        return [], page_need_update

    try:
        append_log(f"[Popular] 正在获取 {target_date} 第 {page_num} 页...")
        posts = danbooru_api.get_popular_posts(target_date, page_num)
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    for post in posts:
        if not state.is_running:
            break
        state.play_event.wait()
        result = _process_post(post, db_data_inst, filter_tags, do_download=True)
        if not result:
            continue
        ids, artist, saved_filename = result
        _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists)
        _append_viewer(ids, artist, saved_filename, post)

    db_data_inst.save_global_data()
    db_data_inst.save_viewer_data(daily_viewer_data)
    return new_hot_artists, page_need_update

# --- mode: collect_ids (只收集 ID 不下载) ---
def grabber_collect_ids(db_data_inst, page_num, filter_tags):
    _ensure_today(db_data_inst)
    page_need_update = {"1": [], "2": []}
    new_hot_artists = []
    daily_ids_data = db_data_inst.load_ids_data()

    state.play_event.wait()
    if not state.is_running:
        return [], page_need_update

    try:
        append_log(f"[CollectIDs] 正在获取第 {page_num} 页...")
        posts = danbooru_api.get_posts_by_rank(page_num)
    except Exception as e:
        append_log(f"获取页面失败: {e}")
        return [], {"1": [], "2": []}

    for post in posts:
        if not state.is_running:
            break
        state.play_event.wait()
        result = _process_post(post, db_data_inst, filter_tags, do_download=False)
        if not result:
            continue
        ids, artist, _ = result
        _update_artist_stats(db_data_inst, artist, page_need_update, new_hot_artists)
        if artist:
            daily_ids_data.append(ids)

    db_data_inst.save_global_data()
    daily_ids_data = list(set(daily_ids_data))
    db_data_inst.save_ids_data(daily_ids_data)
    append_log(f"[CollectIDs] 当前已收集 {len(daily_ids_data)} 个 ID")
    return new_hot_artists, page_need_update

# --- mode: download_ids (从已收集的 IDs 批量下载) ---
def task_download_ids(db_data_inst, filter_tags):
    global daily_viewer_data
    _ensure_today(db_data_inst)
    daily_viewer_data = db_data_inst.load_viewer_data()
    ids_data = db_data_inst.load_ids_data()

    if not ids_data:
        append_log("[DownloadIDs] 没有已收集的 ID，请先用「仅收集ID」模式收集。")
        return

    append_log(f"[DownloadIDs] 开始下载，共 {len(ids_data)} 个 ID")
    success_count = 0

    for pid_str in ids_data:
        if not state.is_running:
            append_log("任务已被强制终止。")
            break
        state.play_event.wait()

        if pid_str in db_data_inst.log_data:
            continue

        append_log(f"[DownloadIDs] 正在处理 ID: {pid_str}")
        post_data = danbooru_api.fetch_data_with_retry(pid_str)
        if not post_data:
            append_log(f"ID {pid_str} 获取数据失败，跳过")
            continue

        if filter_tags:
            tag_string = post_data.get('tag_string', '')
            if any(tag in tag_string for tag in filter_tags):
                append_log(f"跳过 ID {pid_str}，包含过滤标签。")
                continue

        image_url = post_data.get('file_url') or post_data.get('large_file_url')
        if not image_url:
            continue

        state.play_event.wait()
        if not state.is_running:
            break

        saved_filename = danbooru_api.download_image(image_url, save_dir, append_log)
        if not saved_filename:
            append_log(f"ID {pid_str} 下载失败，跳过")
            continue

        db_data_inst.log_data[pid_str] = image_url
        success_count += 1

        artist = ""
        if 'tag_string_artist' in post_data:
            artist_list = post_data['tag_string_artist'].split()
            artist_list = [a for a in artist_list if not a.lower().endswith("(voice_actor)")]
            if artist_list:
                artist = ' '.join(artist_list)

        if artist:
            db_data_inst.artist_stats[artist] = db_data_inst.artist_stats.get(artist, 0) + 1

        _append_viewer(pid_str, artist, saved_filename, post_data)
        save_runtime_snapshot(db_data_inst.log_data, db_data_inst.artist_stats, daily_viewer_data, runtime_snapshot_path)

    db_data_inst.save_global_data()
    db_data_inst.save_viewer_data(daily_viewer_data)
    clear_runtime_snapshot(runtime_snapshot_path)
    append_log(f"[DownloadIDs] 完成，成功下载 {success_count} 张图片。")


def scraper_task(start_page, end_page, mode="rank", target_date="", start_date="", end_date=""):
    global scraper_thread
    try:
        if mode == "download_ids":
            task_download_ids(db_data, state.filter_tags)
        elif mode == "popular_range":
            if not start_date or not end_date:
                append_log("日期范围缺失。")
                return
            s_dt = datetime.datetime.strptime(start_date, "%Y-%m-%d")
            e_dt = datetime.datetime.strptime(end_date, "%Y-%m-%d")
            if s_dt > e_dt: s_dt, e_dt = e_dt, s_dt
            
            curr_dt = e_dt
            while curr_dt >= s_dt:
                if not state.is_running: break
                pop_date = curr_dt.strftime("%Y-%m-%d")
                append_log(f"=== 开始抓取日期: {pop_date} ===")
                pop_db = DanbooruData(pop_date)
                
                output = pop_db.load_hot_drawer()
                nu_sets = pop_db.load_need_update()
                n = start_page
                
                while n <= end_page:
                    if not state.is_running: break
                    state.play_event.wait()
                    append_log(f"--- 正在处理 {pop_date} 第 {n} 页 ---")
                    o, n_u_dict = grabber_popular(pop_db, n, state.filter_tags, pop_date)
                    output = list(set(output + o) - pop_db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    
                    pop_db.save_hot_drawer(list(set(output)))
                    pop_db.save_need_update(nu_sets)
                    n += 1
                
                curr_dt -= datetime.timedelta(days=1)
        else:
            output = db_data.load_hot_drawer()
            nu_sets = db_data.load_need_update()

            n = start_page
            mode_label = {"rank": "Rank", "popular": "Popular", "collect_ids": "CollectIDs"}.get(mode, mode)
            append_log(f"开始抓取 [{mode_label}]，从第 {start_page} 页到第 {end_page} 页")
            append_log(f"当前过滤 Tags: {state.filter_tags}")

            while n <= end_page:
                if not state.is_running: 
                    append_log("任务已被强制终止。")
                    break
                state.play_event.wait()
                append_log(f"--- 正在处理第 {n} 页 ---")

                if mode == "popular":
                    pop_date = target_date or db_data.today_str
                    pop_db = DanbooruData(pop_date)
                    o, n_u_dict = grabber_popular(pop_db, n, state.filter_tags, pop_date)
                    
                    output = list(set(output + o) - pop_db.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    
                    pop_db.save_hot_drawer(list(set(output)))
                    pop_db.save_need_update(nu_sets)
                elif mode == "collect_ids":
                    o, n_u_dict = grabber_collect_ids(db_data, n, state.filter_tags)
                    output = list(set(output + o) - db_data.all_drawer)
                    for k in ["1", "2"]:
                        nu_sets[k].update(n_u_dict[k])
                    db_data.save_hot_drawer(list(set(output)))
                    db_data.save_need_update(nu_sets)
                else:
                    o, n_u_dict = grabber_rank(db_data, n, state.filter_tags)
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
        args=(req.start_page, req.end_page, req.mode, req.target_date, req.start_date, req.end_date),
        daemon=True
    )
    scraper_thread.start()
    mode_labels = {"rank": "排行抓取", "popular": "Popular热门", "collect_ids": "仅收集ID", "download_ids": "按ID下载", "popular_range": "日期范围热门"}
    return {"msg": f"{mode_labels.get(req.mode, '任务')}已启动"}

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


# ---------------- 热度刷新 (score / fav_count) ----------------

class RefreshState:
    def __init__(self):
        self.is_running = False
        self.date_str = ""
        self.done = 0
        self.total = 0
        self.error = ""
        self.recent = []  # 待推送给前端的增量更新 [{post_id, score, fav_count}]
        self.lock = threading.Lock()

refresh_state = RefreshState()
refresh_thread = None


def _extract_post_id(post_url: str) -> str:
    if not post_url:
        return ""
    tail = post_url.rstrip('/').rsplit('/', 1)[-1]
    return tail if tail.isdigit() else ""


def _run_refresh_scores():
    """全量刷新所有日期的 score / fav_count，使用线程池并发。"""
    MAX_WORKERS = 5
    PER_TASK_SLEEP = 0.3  # 每个 worker 单次任务后的限速

    def _task(date_str, post_id, item):
        if not refresh_state.is_running:
            return None
        try:
            post = danbooru_api.fetch_data_with_retry(int(post_id))
        except Exception:
            post = None
        if not post:
            sleep(PER_TASK_SLEEP)
            return None
        new_score = post.get("score", 0) or 0
        new_fav = post.get("fav_count", 0) or 0
        changed = item.get("score") != new_score or item.get("fav_count") != new_fav
        if changed:
            item["score"] = new_score
            item["fav_count"] = new_fav
            with refresh_state.lock:
                refresh_state.recent.append({
                    "post_id": post_id,
                    "score": new_score,
                    "fav_count": new_fav
                })
        sleep(PER_TASK_SLEEP)
        return date_str if changed else None

    try:
        # 1. 列出所有有 viewer_data.json 的日期
        date_dirs = []
        if os.path.isdir(base_download_dir):
            for name in sorted(os.listdir(base_download_dir)):
                full = os.path.join(base_download_dir, name, "viewer_data.json")
                if os.path.isfile(full):
                    date_dirs.append(name)

        # 2. 加载每个日期的数据并构建任务表
        loaded = {}  # date_str -> (DanbooruData, list, save_lock)
        all_tasks = []
        for date_str in date_dirs:
            dd = DanbooruData(target_date=date_str)
            data = dd.load_viewer_data()
            loaded[date_str] = (dd, data, threading.Lock())
            for item in data:
                pid = _extract_post_id(item.get("post_url", ""))
                if pid:
                    all_tasks.append((date_str, pid, item))

        with refresh_state.lock:
            refresh_state.total = len(all_tasks)
            refresh_state.done = 0
            refresh_state.recent = []
        append_log(f"开始全量刷新: {len(date_dirs)} 个日期, {len(all_tasks)} 条记录, {MAX_WORKERS} 线程")

        # 3. 线程池并发拉取
        date_change_count = {d: 0 for d in date_dirs}
        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {executor.submit(_task, d, pid, item): d for d, pid, item in all_tasks}
            for fut in concurrent.futures.as_completed(futures):
                if not refresh_state.is_running:
                    # 标记停止后让剩余 future 自然完成，但不再增加进度（保持准确性）
                    pass
                changed_date = None
                try:
                    changed_date = fut.result()
                except Exception:
                    pass
                with refresh_state.lock:
                    refresh_state.done += 1
                if changed_date:
                    date_change_count[changed_date] = date_change_count.get(changed_date, 0) + 1
                    # 该日期累积 20 条变更落盘一次
                    if date_change_count[changed_date] % 20 == 0:
                        dd, data, lock = loaded[changed_date]
                        with lock:
                            dd.save_viewer_data(data)

        # 4. 全部完成后把每个日期都落盘一遍
        for date_str, (dd, data, lock) in loaded.items():
            with lock:
                dd.save_viewer_data(data)

        total_changed = sum(date_change_count.values())
        append_log(f"全量刷新完成: {refresh_state.done}/{refresh_state.total} 条已查询，{total_changed} 条数值变化")
    except Exception as e:
        with refresh_state.lock:
            refresh_state.error = str(e)
        append_log(f"刷新任务异常: {e}")
    finally:
        with refresh_state.lock:
            refresh_state.is_running = False


@app.post("/api/refresh_scores")
def refresh_scores_start():
    """全量刷新所有日期的 score / fav_count（多线程并发）。"""
    global refresh_thread
    with refresh_state.lock:
        if refresh_state.is_running:
            return {"ok": False, "msg": "已有刷新任务在运行"}
        refresh_state.is_running = True
        refresh_state.date_str = "ALL"
        refresh_state.done = 0
        refresh_state.total = 0
        refresh_state.error = ""
        refresh_state.recent = []
    refresh_thread = threading.Thread(target=_run_refresh_scores, daemon=True)
    refresh_thread.start()
    return {"ok": True, "msg": "已开始全量刷新热度"}


@app.post("/api/refresh_scores_stop")
def refresh_scores_stop():
    with refresh_state.lock:
        refresh_state.is_running = False
    return {"ok": True}


@app.get("/api/refresh_scores_status")
def refresh_scores_status():
    """返回当前刷新进度并清空 recent 增量（前端调用一次即取走，避免重复应用）。"""
    with refresh_state.lock:
        recent = list(refresh_state.recent)
        refresh_state.recent = []
        return {
            "is_running": refresh_state.is_running,
            "date_str": refresh_state.date_str,
            "done": refresh_state.done,
            "total": refresh_state.total,
            "error": refresh_state.error,
            "recent": recent
        }


@app.get("/api/refresh_score/{post_id}")
def refresh_single_score(post_id: int, date: str = ""):
    """单个 post 同步刷新，写回对应日期的 viewer_data.json。"""
    try:
        post = danbooru_api.fetch_data_with_retry(post_id)
    except Exception as e:
        return {"ok": False, "msg": f"拉取失败: {e}"}
    if not post:
        return {"ok": False, "msg": "拉取失败"}
    new_score = post.get("score", 0) or 0
    new_fav = post.get("fav_count", 0) or 0

    if date:
        try:
            dd = DanbooruData(target_date=date)
            data = dd.load_viewer_data()
            updated = False
            for item in data:
                if _extract_post_id(item.get("post_url", "")) == str(post_id):
                    item["score"] = new_score
                    item["fav_count"] = new_fav
                    updated = True
                    break
            if updated:
                dd.save_viewer_data(data)
        except Exception as e:
            append_log(f"单图刷新写盘失败 {post_id}: {e}")

    return {"ok": True, "post_id": post_id, "score": new_score, "fav_count": new_fav}


@app.get("/")
def read_root():
    if os.path.exists("index.html"):
        with open("index.html", "r", encoding="utf-8") as f:
            return HTMLResponse(content=f.read())
    return HTMLResponse(content="<h1>找不到 index.html</h1>")

@app.post("/api/import_translation")
def api_import_translation(req: TranslationImportRequest):
    try:
        translator.update_custom_dict(req.translations)
        return {"ok": True, "msg": "导入成功"}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

@app.post("/api/convert_local_zip")
def api_convert_local_zip(req: ConvertLocalZipRequest):
    try:
        from pic_web.main import convert_zip_to_gif
        target_path = Path(req.local_path).resolve()
        if not target_path.exists() or target_path.suffix.lower() != '.zip':
            return {"ok": False, "msg": "无效的 ZIP 文件路径"}
        
        output_path = target_path.with_suffix('.gif')
        if not output_path.exists():
            convert_zip_to_gif(target_path, output_path)
            
        return {"ok": True, "gif_path": str(output_path)}
    except Exception as e:
        return {"ok": False, "msg": str(e)}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=False)
