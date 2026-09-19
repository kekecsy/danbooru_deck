import os
import json
import datetime
from collections.abc import MutableMapping

import deck_db
from library_config import library_id_for_path
from my_utils import (
    atomic_write_json,
    dedup_viewer_data,
    read_json_atomic,
)
from runtime_paths import DRAWER_DIR, HOT_PIC_DIR, ensure_user_directories

# per-path 锁表与原子写已统一到 my_utils（收藏保存等小 JSON 仍共用同一张表）。
# log.json / artist_stats.json 已迁入 deck.db，下面的 _DB*Mapping 是无状态视图，
# 旧 CLI 脚本（db_data.log_data[pid]=url; save_global_data()）无需任何改动。


class _DBLogMapping(MutableMapping):
    """post_log 表的 dict 视图：点查/点写即时落库，不做全量缓存。"""

    def __getitem__(self, key):
        value = deck_db.log_get(str(key), None)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        deck_db.log_record(str(key), value)

    def __delitem__(self, key):
        if not deck_db.log_delete(str(key)):
            raise KeyError(key)

    def __contains__(self, key):
        return deck_db.log_has(str(key))

    def __iter__(self):
        return iter(deck_db.log_all_ids())

    def __len__(self):
        return deck_db.log_count()

    def get(self, key, default=None):
        # 覆盖默认实现，避免「存在但 cdn_url 为空串」与「行不存在」语义打架
        return deck_db.log_get(str(key), default)

    def update(self, other=(), **kwargs):
        data = dict(other, **kwargs) if kwargs else dict(other)
        if data:
            deck_db.log_bulk_upsert({str(k): v for k, v in data.items()})


class _DBStatsMapping(MutableMapping):
    """artist_stats 表的 dict 视图。

    stats[k]=stats.get(k,0)+n 这种旧 JSON 时代的读改写：get 时记下本进程看到的
    基线，赋值时把「目标值-基线」的增量原子应用到 DB 最新值上，CLI 与服务端并发、
    两个 CLI 互相并发都不会整盘互踩或丢自增（详见 deck_db.stats_apply_relative）。"""

    def __init__(self):
        self._baseline = {}

    def __getitem__(self, key):
        value = deck_db.stats_get(key, None)
        if value is None:
            raise KeyError(key)
        self._baseline[key] = value
        return value

    def __setitem__(self, key, value):
        baseline = self._baseline.pop(key, None)
        deck_db.stats_apply_relative(key, int(value), baseline=baseline)

    def __delitem__(self, key):
        self._baseline.pop(key, None)
        if not deck_db.stats_delete(key):
            raise KeyError(key)

    def __contains__(self, key):
        return deck_db.stats_get(key, None) is not None

    def __iter__(self):
        return iter(deck_db.stats_snapshot())

    def __len__(self):
        return deck_db.stats_count()

    def get(self, key, default=0):
        value = deck_db.stats_get(key, default)
        # get 是 CLI 读改写的「读」，无论键是否存在都记下基线（缺省=0）
        self._baseline[key] = value
        return value


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

        # log.json（~49MB）/ artist_stats.json（~2MB）已迁入 deck.db：
        # 视图惰性构造（首次访问才连库，构造 DanbooruData 依旧零大 IO），
        # 主进程 LogStore/StatsStore 与 CLI 脚本都打同一份 post_log/artist_stats。
        self._log_data = None
        self._artist_stats = None
        # (library_id, folder) 惰性缓存：_make_job 构造后还可能覆写 save_dir/base_dir，
        # 所以不能在 __init__ 算；一个实例不会中途切换 folder（switch_target 是换新实例）。
        self._db_scope_cache = None

        # Load drawer data —— txtdata / disk_drawer 是用户手工维护文件：
        # 先按 mtime+size 指纹把外部改动吸收进 deck.db，再从 DB 读（DB 为权威缓存）。
        deck_db.drawer_reconcile_static(self.drawer_dir)
        self.txtdata1 = deck_db.drawer_txt_load()
        self.disk_drawer = deck_db.drawer_disk_load()
        self.txtdata2 = self.disk_drawer.get("1", []) + self.disk_drawer.get("2", [])
        self.all_drawer = set(self.txtdata1 + self.txtdata2)

        # Build folder_to_disk dictionary
        self.folder_to_disk = {}
        for k, v in self.disk_drawer.items():
            for folder in v:
                self.folder_to_disk[folder] = k

    @property
    def log_data(self):
        """post_log 的惰性 dict 视图：点查/点写即时落 SQLite。"""
        if self._log_data is None:
            self._log_data = _DBLogMapping()
        return self._log_data

    @log_data.setter
    def log_data(self, value):
        """兼容旧的整体赋值：按 upsert 合并，不删除未知键（防误清空）。"""
        if self._log_data is None:
            self._log_data = _DBLogMapping()
        deck_db.log_bulk_upsert(value or {})

    @property
    def artist_stats(self):
        """artist_stats 的惰性 dict 视图，语义同 log_data。"""
        if self._artist_stats is None:
            self._artist_stats = _DBStatsMapping()
        return self._artist_stats

    @artist_stats.setter
    def artist_stats(self, value):
        if self._artist_stats is None:
            self._artist_stats = _DBStatsMapping()
        deck_db.stats_merge_absolute(value or {})

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
        return read_json_atomic(path, default)

    def _save_json(self, path, data):
        atomic_write_json(path, data)

    def save_global_data(self):
        # 历史职责：把 log.json / artist_stats.json 全量落盘。迁移 SQLite 后每次
        # 赋值都已即时入库，JSON 不再常态导出（回滚用 `python deck_db.py --export-all-legacy-json`）。
        # 保留空实现，让 danbooru_hot / collect_ids 等 CLI 的调用点零改动。
        return

    # ---------- viewer_data / ids_data：deck.db 为权威，JSON 为派生镜像 ----------
    def _db_scope(self):
        """(library_id, folder)：按 base_dir 反查图库根，folder = save_dir 末级目录名。"""
        if self._db_scope_cache is None:
            lib_id = library_id_for_path(self.base_dir)
            folder = os.path.basename(os.path.normpath(self.save_dir))
            self._db_scope_cache = (lib_id, folder)
        return self._db_scope_cache

    @property
    def _viewer_mirror_path(self):
        return os.path.join(self.save_dir, "viewer_data.json")

    @property
    def _ids_mirror_path(self):
        return os.path.join(self.save_dir, "ids_data.json")

    def load_viewer_data(self):
        lib_id, folder = self._db_scope()
        # 镜像可能被 CLI / 他机同步改过：先按 mtime+size 指纹吸收，再从 DB 重组
        deck_db.reconcile_viewer(lib_id, folder, self._viewer_mirror_path)
        return deck_db.viewer_load_entries(lib_id, folder)

    def save_viewer_data(self, data):
        lib_id, folder = self._db_scope()
        items = dedup_viewer_data(data)
        deck_db.viewer_replace(lib_id, folder, items)
        # DB commit 后再导出镜像：Electron 离线兜底 / 外置盘自包含读取不受影响
        deck_db.export_viewer_mirror(lib_id, folder, self._viewer_mirror_path, items=items)

    def load_ids_data(self):
        lib_id, folder = self._db_scope()
        deck_db.reconcile_ids(lib_id, folder, self._ids_mirror_path)
        return deck_db.queue_load(lib_id, folder)

    def save_ids_data(self, data):
        lib_id, folder = self._db_scope()
        ids = [str(x) for x in (data or [])]
        # 旧 save_ids_data 是「pending 全集覆写」语义：failed 行保留（见 queue_replace）
        deck_db.queue_replace(lib_id, folder, ids)
        deck_db.export_ids_mirror(lib_id, folder, self._ids_mirror_path)

    # ---- DownloadJob 用的队列点操作（入队即提交即镜像，崩溃安全）----
    def queue_add_id(self, post_id):
        lib_id, folder = self._db_scope()
        deck_db.queue_add(lib_id, folder, str(post_id))
        deck_db.export_ids_mirror(lib_id, folder, self._ids_mirror_path)

    def queue_resolve_id(self, post_id):
        """成功/永久失败：删 DB 行；镜像不立即写，沿用 P0 的 dirty 批量节奏。"""
        lib_id, folder = self._db_scope()
        deck_db.queue_remove(lib_id, folder, str(post_id))

    def queue_mark_failed_id(self, post_id):
        lib_id, folder = self._db_scope()
        deck_db.queue_mark_status(lib_id, folder, str(post_id), deck_db.QUEUE_FAILED)

    def queue_load_failed_ids(self):
        lib_id, folder = self._db_scope()
        return deck_db.queue_load(lib_id, folder, status=deck_db.QUEUE_FAILED)

    def queue_export_mirror(self):
        lib_id, folder = self._db_scope()
        deck_db.export_ids_mirror(lib_id, folder, self._ids_mirror_path)

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
        # 镜像被 danbooru_most_view 等外部脚本直改时，按指纹吸收后进 DB 权威读。
        # 契约与旧 JSON 版逐字对齐：永远返回且只返回 "1"/"2" 两个 set 键
        # （所有调用点直接 nu_sets[k].update(...)，缺键会 KeyError；未知 grp 丢弃）。
        deck_db.drawer_need_update_reconcile(self.need_update_path)
        groups = deck_db.drawer_need_update_load()
        return {
            "1": set(groups.get("1", [])),
            "2": set(groups.get("2", [])),
        }

    def save_need_update(self, nu_sets):
        final_nu = {k: sorted(list(v)) for k, v in nu_sets.items()}
        deck_db.drawer_need_update_replace(final_nu)
        deck_db.export_need_update_mirror(self.need_update_path, final_nu)

    def load_hot_drawer(self):
        deck_db.drawer_hot_reconcile(self.hot_drawer_path)
        return deck_db.drawer_hot_load()

    def save_hot_drawer(self, output_list):
        deck_db.drawer_hot_replace(output_list)
        deck_db.export_hot_drawer_mirror(self.hot_drawer_path, output_list)
