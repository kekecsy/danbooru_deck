import os
import json
import time
import datetime
import threading

from my_utils import dedup_viewer_data
from runtime_paths import DRAWER_DIR, HOT_PIC_DIR, ensure_user_directories

# 进程级「按文件路径分配的可重入锁」表：所有 DanbooruData 实例共享同一张表，
# 于是对同一个 JSON（尤其是 viewer_data.json）的「读 / 写」在整个进程内串行。
#
# 解决的并发 bug：刷新热度（/api/refresh_visible「本页/范围/全部」或后台 _run_refresh_scores）
# 与点开图片触发的单图刷新（refreshSinglePost → 同 endpoint）会各自 new 一个 DanbooruData，
# 彼此没有共享锁，于是在磁盘上撞车：
#   1) 旧实现临时名固定为 "<path>.tmp"，两个写线程同时写同一个 .tmp 互相覆盖，
#      第二个 os.replace 还可能因 .tmp 已被前一个消费而抛 FileNotFoundError；
#   2) Windows 上当一个线程正打开该文件读时，另一个线程的 os.replace 会抛
#      PermissionError([WinError 5] 拒绝访问)。
# 后端是单进程 uvicorn（无 --workers），threading 锁即可，无需跨进程文件锁。
_PATH_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS = {}


def _lock_for(path):
    """返回某个文件路径对应的进程级可重入锁（同一路径恒返回同一把）。"""
    key = os.path.abspath(path)
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _atomic_replace(src, dst, attempts=5, base_delay=0.05):
    """os.replace 在 Windows 上偶发 PermissionError（杀软 / 索引器 / 外部进程刚好持有句柄，
    例如 Electron 兜底读取或 caption 元数据查询），短暂重试几次即可恢复；POSIX 下通常一次成功。"""
    for i in range(attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if i == attempts - 1:
                raise
            time.sleep(base_delay * (i + 1))


class DanbooruData:
    def __init__(self, target_date=None):
        ensure_user_directories()
        self.base_dir = str(HOT_PIC_DIR)
        self.drawer_dir = str(DRAWER_DIR)
        self.today_str = target_date if target_date else datetime.datetime.now().strftime('%Y-%m-%d')
        self.save_dir = os.path.join(self.base_dir, self.today_str)
        
        self.stats_path = os.path.join(self.base_dir, "artist_stats.json")
        self.log_path = os.path.join(self.base_dir, "log.json")
        self.status_path = os.path.join(self.base_dir, "status.json")
        
        self.txtdata_path = os.path.join(self.drawer_dir, "txtdata.txt")
        self.disk_drawer_path = os.path.join(self.drawer_dir, "disk_drawer.json")
        self.hot_drawer_path = os.path.join(self.drawer_dir, "hot_drawer.txt")
        self.need_update_path = os.path.join(self.drawer_dir, "need_update.json")
        
        self._init_directories()
        
        self.log_data = self._load_json(self.log_path, {})
        self.artist_stats = self._load_json(self.stats_path, {})
        
        # Load drawer data
        with open(self.txtdata_path, 'r', encoding='utf-8') as f:
            self.txtdata1 = f.read().split('\n')
        self.disk_drawer = self._load_json(self.disk_drawer_path, {"1": [], "2": []})
        self.txtdata2 = self.disk_drawer.get("1", []) + self.disk_drawer.get("2", [])
        self.all_drawer = set(self.txtdata1 + self.txtdata2)
        
        # Build folder_to_disk dictionary
        self.folder_to_disk = {}
        for k, v in self.disk_drawer.items():
            for folder in v:
                self.folder_to_disk[folder] = k

    def _init_directories(self):
        os.makedirs(self.save_dir, exist_ok=True)
        os.makedirs(self.drawer_dir, exist_ok=True)
        
        if not os.path.exists(self.txtdata_path):
            with open(self.txtdata_path, 'w', encoding='utf-8') as f:
                f.write('')
        if not os.path.exists(self.disk_drawer_path):
            with open(self.disk_drawer_path, 'w', encoding='utf-8') as f:
                json.dump({"1": [], "2": []}, f, ensure_ascii=False, indent=4)
        if not os.path.exists(self.hot_drawer_path):
            with open(self.hot_drawer_path, 'w', encoding='utf-8') as f:
                f.write('')

    def _load_json(self, path, default=None):
        if default is None:
            default = {}
        # 与 _save_json 共用同一把 per-path 锁：读期间不会有别的线程在做 os.replace，
        # 既避免 Windows 上「替换正被打开读取的文件」抛 PermissionError，也读不到半截文件。
        with _lock_for(path):
            if os.path.exists(path):
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        return json.load(f)
                except json.JSONDecodeError:
                    return default
            return default

    def _save_json(self, path, data):
        # 全程持有 per-path 锁：保证同一文件的写彼此串行、且不与读重叠。
        with _lock_for(path):
            # 唯一临时名（带 pid + 线程号）：即便将来有调用方绕过锁，多个写也不会撞同一个 .tmp。
            temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
            try:
                with open(temp_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=4)
                _atomic_replace(temp_path, path)
            finally:
                # 写失败（json 序列化异常 / 重试仍失败）时清掉残留临时文件，别污染目录。
                if os.path.exists(temp_path):
                    try:
                        os.remove(temp_path)
                    except OSError:
                        pass

    def save_global_data(self):
        self._save_json(self.log_path, self.log_data)
        self._save_json(self.stats_path, self.artist_stats)

    def load_viewer_data(self):
        items = self._load_json(os.path.join(self.save_dir, "viewer_data.json"), [])
        if not isinstance(items, list):
            return []
        return dedup_viewer_data(items)

    def save_viewer_data(self, data):
        self._save_json(os.path.join(self.save_dir, "viewer_data.json"), dedup_viewer_data(data))

    def load_ids_data(self):
        return self._load_json(os.path.join(self.save_dir, "ids_data.json"), [])
        
    def save_ids_data(self, data):
        self._save_json(os.path.join(self.save_dir, "ids_data.json"), data)

    def write_status(self, state, page=None):
        data = {
            "state": state,
            "page": page,
            "time": datetime.datetime.now().isoformat()
        }
        self._save_json(self.status_path, data)

    def get_folder_name(self, name):
        return (name.replace(":", "%3A").replace("/", "%2F").replace("!", "_")
                .replace("?", "_").replace("<", "_").replace(">", "_").rstrip('.'))

    def get_disk_key(self, artist_name, default="2"):
        f_name = self.get_folder_name(artist_name)
        return self.folder_to_disk.get(f_name, default)

    def load_need_update(self):
        temp_nu = self._load_json(self.need_update_path, {"1": [], "2": []})
        return {"1": set(temp_nu.get("1", [])), "2": set(temp_nu.get("2", []))}

    def save_need_update(self, nu_sets):
        final_nu = {k: sorted(list(v)) for k, v in nu_sets.items()}
        self._save_json(self.need_update_path, final_nu)
        
    def load_hot_drawer(self):
        with open(self.hot_drawer_path, 'r', encoding='utf-8') as f:
            return f.read().split('\n')
            
    def save_hot_drawer(self, output_list):
        with open(self.hot_drawer_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(output_list))
