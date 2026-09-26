# deck_db.py
# Danbooru Deck 的 SQLite 元数据层：log/stats/viewer/下载队列/drawer/收藏全部入库，
# JSON 文件降级为「DB 派生镜像」（Electron 兜底直读 / 外置盘自包含用）。
#
# 设计约束：
#   - 只用标准库 sqlite3，不加依赖；Electron 侧不装任何原生模块，Node 不碰 deck.db。
#   - 每进程一个连接（check_same_thread=False）+ 进程级 RLock 串行化所有写；
#     跨进程（uvicorn 服务端 vs CLI 脚本）靠 WAL + busy_timeout，
#     互踩从语义上消除：所有写都是 upsert / 原子增量，绝不做「读全量→整盘覆盖」。
#   - post_id 一律 TEXT（兼容 "gelbooru:123" 之类带站点前缀的 id）。
import argparse
import json
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from runtime_paths import DECK_DB_PATH, ensure_user_directories

SCHEMA_VERSION = 1

# ---------------------------------------------------------------- DDL --------
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta(
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- log.json 的替代：成员去重点查 / cached_cdn_url / filename→post_id 反查
CREATE TABLE IF NOT EXISTS post_log(
    post_id    TEXT PRIMARY KEY,
    cdn_url    TEXT,
    filename   TEXT,
    updated_at REAL
);
CREATE INDEX IF NOT EXISTS idx_post_log_filename
    ON post_log(filename) WHERE filename <> '';

-- viewer_data.json 的行式存储；自增 id 即列表顺序，画廊 reversed = ORDER BY id DESC
CREATE TABLE IF NOT EXISTS viewer_entries(
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    library_id    TEXT NOT NULL DEFAULT 'default',
    folder        TEXT NOT NULL,          -- 日期目录名（YYYY-MM-DD 或 tag_ 子目录场景预留）
    artist        TEXT,
    filename      TEXT NOT NULL,
    local_path    TEXT,
    post_url      TEXT,
    web_url       TEXT,
    score         INTEGER DEFAULT 0,
    fav_count     INTEGER DEFAULT 0,
    tag_general   TEXT,
    tag_character TEXT,
    tag_copyright TEXT,
    tag_artist    TEXT,
    tag_meta      TEXT,
    tag_string_raw TEXT,
    rating        TEXT,
    md5           TEXT,
    tags_extra    TEXT,   -- tags dict 已知键以外的键，JSON 原样保留
    entry_extra   TEXT,   -- entry 顶层已知字段以外的键，JSON 原样保留
    created_at    REAL,
    updated_at    REAL
);
-- 两套唯一键与 my_utils._viewer_item_key / dedup_viewer_data 严格等价：
--   有正常 post_url（非空、非 '#'）→ 按 (lib, folder, post_url) 去重
CREATE UNIQUE INDEX IF NOT EXISTS ux_viewer_post
    ON viewer_entries(library_id, folder, post_url)
    WHERE post_url IS NOT NULL AND post_url <> '' AND post_url <> '#';
--   无 post_url（缺失/空串/'#'）→ 按 (lib, folder, filename, web_url) 去重
CREATE UNIQUE INDEX IF NOT EXISTS ux_viewer_fn
    ON viewer_entries(library_id, folder, filename, web_url)
    WHERE post_url IS NULL OR post_url = '' OR post_url = '#';
CREATE INDEX IF NOT EXISTS idx_viewer_folder
    ON viewer_entries(library_id, folder);

-- 持久下载队列：pending/failed；成功或永久失败直接 DELETE 行
CREATE TABLE IF NOT EXISTS dl_queue(
    library_id TEXT NOT NULL,
    folder     TEXT NOT NULL,
    post_id    TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending',  -- pending / failed
    updated_at REAL,
    PRIMARY KEY(library_id, folder, post_id)
);
CREATE INDEX IF NOT EXISTS idx_queue_folder
    ON dl_queue(library_id, folder, status);

-- 现状纯内存的失败页记录，顺带持久化（任务恢复时可 hydrate 到内存）
CREATE TABLE IF NOT EXISTS failed_pages(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    folder     TEXT NOT NULL,
    page       INTEGER,
    tag_query  TEXT,
    tag_source TEXT,
    created_at REAL,
    UNIQUE(folder, page, tag_query, tag_source)
);

-- 页级抓取断点：用户在收 ID 阶段停止任务时记录「下一个该抓的页」，
-- 前端据此提供「从第 N 页续跑」一键入口。正常跑完 / 进入下载阶段会清掉。
-- scope 与 failed_pages 完全一致（folder + tag_query + tag_source）。
CREATE TABLE IF NOT EXISTS page_cursor(
    folder       TEXT NOT NULL,
    tag_query    TEXT NOT NULL DEFAULT '',
    tag_source   TEXT NOT NULL DEFAULT 'danbooru',
    mode         TEXT,
    page_current INTEGER,
    start_page   INTEGER,
    end_page     INTEGER,
    updated_at   REAL,
    PRIMARY KEY(folder, tag_query, tag_source)
);

-- artist_stats.json 的替代：increment 全部走原子 UPSERT
CREATE TABLE IF NOT EXISTS artist_stats(
    artist     TEXT PRIMARY KEY,
    count      INTEGER NOT NULL DEFAULT 0,
    updated_at REAL
);

-- drawer 四件套
CREATE TABLE IF NOT EXISTS drawer_disk(
    grp  TEXT NOT NULL,
    name TEXT NOT NULL,
    pos  INTEGER NOT NULL,
    PRIMARY KEY(grp, name)
);
CREATE TABLE IF NOT EXISTS drawer_txt(
    name TEXT PRIMARY KEY,
    pos  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS drawer_hot(
    name TEXT PRIMARY KEY,
    pos  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS drawer_need_update(
    grp  TEXT NOT NULL,
    name TEXT NOT NULL,
    PRIMARY KEY(grp, name)
);

-- 收藏三类；artist/character 用 (grp,name/pos) 保持列表顺序
CREATE TABLE IF NOT EXISTS fav_artist(
    grp  TEXT NOT NULL,
    name TEXT NOT NULL,
    pos  INTEGER NOT NULL,
    PRIMARY KEY(grp, name)
);
CREATE TABLE IF NOT EXISTS fav_character(
    grp   TEXT NOT NULL,
    token TEXT NOT NULL,
    pos   INTEGER NOT NULL,
    PRIMARY KEY(grp, token)
);
CREATE TABLE IF NOT EXISTS fav_image(
    fav_key    TEXT PRIMARY KEY,   -- date/filename | libid:abs/path | libid:date/filename
    added_at   INTEGER,
    date       TEXT,
    filename   TEXT,
    library_id TEXT,
    payload    TEXT                -- 完整 item JSON，GET 时原样反序列化
);
CREATE INDEX IF NOT EXISTS idx_fav_image_added ON fav_image(added_at DESC);

-- 镜像文件对账：viewer_data.json / ids_data.json 的 mtime_ns+size 指纹
CREATE TABLE IF NOT EXISTS folder_sync(
    library_id TEXT NOT NULL,
    folder     TEXT NOT NULL,
    kind       TEXT NOT NULL,      -- 'viewer' / 'ids'
    mtime_ns   INTEGER,
    size       INTEGER,
    rows       INTEGER,
    updated_at REAL,
    PRIMARY KEY(library_id, folder, kind)
);
"""

# ------------------------------------------------------------------ 连接 ------
_LOCK = threading.RLock()
# RLock：get_conn 包装器持锁后调用 bootstrap_legacy，后者自身也要进同一把锁（同线程重入）
_BOOTSTRAP_LOCK = threading.RLock()
_conn = None
_tx_depth = 0
# 库消费者（main.py / CLI 经 DanbooruData）首次连接时自动从旧 JSON 引导；
# deck_db.py 自己的 --status 之类检查命令会关掉它，避免「看一眼状态」触发长导入。
AUTO_BOOTSTRAP = True
_bootstrapped = False
_in_bootstrap = False


def _now() -> float:
    return time.time()


def _open_conn() -> sqlite3.Connection:
    global _conn
    with _LOCK:
        if _conn is not None:
            return _conn
        ensure_user_directories()
        conn = sqlite3.connect(
            str(DECK_DB_PATH),
            check_same_thread=False,
            isolation_level=None,   # 自动提交关闭，事务边界全部显式管理
            timeout=5.0,            # 等价 busy_timeout=5000，跨进程写冲突时等待
        )
        conn.row_factory = sqlite3.Row
        # busy_timeout 必须最先设：下面的 journal_mode 切换需要独占锁，与其他进程
        # 并发首连时会立刻 SQLITE_BUSY（默认 0ms 不等待）。
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-131072")  # ~128MB 页缓存
        # WAL：读写不互斥；NORMAL 下已提交事务在崩溃/os._exit 后不丢
        # （WAL 帧写入即落盘，只有最后若干秒的 checkpoint 可能需要重放）。
        # 注意：对已有连接占用的库执行 journal_mode 切换可能撞 busy，
        # busy_timeout 之外再做有限重试（WAL 模式会持久化在 DB 头里，正常只切一次）。
        for attempt in range(50):
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]
                if str(mode).lower() == "wal":
                    break
            except sqlite3.OperationalError:
                if attempt == 49:
                    raise
            time.sleep(0.1)
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA_SQL)
        _conn = conn
        return _conn


def get_conn() -> sqlite3.Connection:
    """进程内唯一连接。首次调用建目录、设 PRAGMA、建表，并同步引导旧 JSON。"""
    global _bootstrapped, _in_bootstrap
    if _conn is None:
        _open_conn()
    if AUTO_BOOTSTRAP and not _bootstrapped and not _in_bootstrap:
        with _BOOTSTRAP_LOCK:
            if not _bootstrapped and not _in_bootstrap:
                _in_bootstrap = True
                try:
                    # 注意不能调 bootstrap_legacy()：它顶部有 nested 重入守卫，
                    # 而本帧已经把 _in_bootstrap 置 True——直接调会被当成「嵌套调用」
                    # 立即空返回（= 自动引导静默什么都不导，空库上线）。
                    # rename_bak=False：app 首启的自动引导只导入/校验，**不改名**，
                    # log.json/artist_stats.json 原样留作离线兜底；改名收尾只由显式
                    # CLI `python deck_db.py --import` 完成（meta 里挂 pending 标志）。
                    _bootstrap_locked(rename_bak=False)
                except Exception as exc:
                    # 旧 JSON 损坏等情况下不阻断启动：以空库继续，用户可修文件后 --import 重跑。
                    print(f"[deck_db] 旧数据引导失败，本次以空库启动: {exc}")
                finally:
                    _in_bootstrap = False
                    _bootstrapped = True
    return _conn


@contextmanager
def transaction():
    """显式写事务（BEGIN IMMEDIATE 立刻拿写锁，避免升级死锁）。

    同线程可重入：外层 BEGIN/COMMIT，内层用 SAVEPOINT；异常时内层回滚到 savepoint、
    外层整体 ROLLBACK。所有调用方都在进程级 RLock 内，sqlite 连接跨线程使用已由
    check_same_thread=False 放开。
    """
    global _tx_depth
    conn = get_conn()
    with _LOCK:
        if _tx_depth == 0:
            conn.execute("BEGIN IMMEDIATE")
            savepoint = None
        else:
            savepoint = f"deck_sp_{_tx_depth}"
            conn.execute(f"SAVEPOINT {savepoint}")
        _tx_depth += 1
        try:
            yield conn
        except Exception:
            if savepoint is not None:
                conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                conn.execute("ROLLBACK")
            _tx_depth -= 1
            raise
        else:
            if savepoint is not None:
                conn.execute(f"RELEASE SAVEPOINT {savepoint}")
            else:
                conn.execute("COMMIT")
            _tx_depth -= 1


def query_all(sql, params=()):
    """只读小帮手：拿锁→执行→在锁内把行全部取成 list[Row]，避免跨线程交错。"""
    with _LOCK:
        cur = get_conn().execute(sql, params)
        return cur.fetchall()


def query_one(sql, params=()):
    with _LOCK:
        return get_conn().execute(sql, params).fetchone()


def execute(sql, params=()):
    """自动提交的单条写（内部隐式短事务）；批量写请用 transaction()。"""
    with _LOCK:
        return get_conn().execute(sql, params)


def checkpoint(mode: str = "PASSIVE"):
    """WAL checkpoint。常态 PASSIVE；关停前用 TRUNCATE 把 WAL 合回主库并截断。"""
    with _LOCK:
        get_conn().execute(f"PRAGMA wal_checkpoint({mode})")


# ------------------------------------------------------------------ meta ------
def meta_get(key: str, default=None):
    row = query_one("SELECT value FROM meta WHERE key=?", (key,))
    return row["value"] if row is not None else default


def meta_set(key: str, value):
    with transaction():
        get_conn().execute(
            "INSERT INTO meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


# ============================================================
# post_log（替代 log.json：成员去重 / CDN URL 缓存 / filename→pid 反查）
# ============================================================
def _filename_from_url(url) -> str:
    if not url:
        return ""
    return str(url).split("/")[-1].split("?")[0]


def log_has(post_id) -> bool:
    return query_one("SELECT 1 FROM post_log WHERE post_id=?", (str(post_id),)) is not None


def log_get(post_id, default=None):
    row = query_one("SELECT cdn_url FROM post_log WHERE post_id=?", (str(post_id),))
    return default if row is None else row["cdn_url"]


def log_record(post_id, url, ts: float = None):
    """单条 upsert（下载成功路径调用，立即持久，替代旧内存 dict + 全量落盘）。"""
    with transaction():
        get_conn().execute(
            "INSERT INTO post_log(post_id,cdn_url,filename,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(post_id) DO UPDATE SET cdn_url=excluded.cdn_url, "
            "filename=excluded.filename, updated_at=excluded.updated_at",
            (str(post_id), url or "", _filename_from_url(url), ts or _now()),
        )


def log_delete(post_id) -> bool:
    with transaction():
        cur = get_conn().execute("DELETE FROM post_log WHERE post_id=?", (str(post_id),))
        return cur.rowcount > 0


def log_bulk_upsert(mapping, ts: float = None):
    """合并一批 pid→url（CLI log_data.update(...) 路径），单事务。"""
    if not mapping:
        return
    ts = ts or _now()
    rows = [(str(k), v or "", _filename_from_url(v), ts) for k, v in mapping.items()]
    with transaction():
        conn = get_conn()
        for i in range(0, len(rows), 2000):
            conn.executemany(
                "INSERT INTO post_log(post_id,cdn_url,filename,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(post_id) DO UPDATE SET cdn_url=excluded.cdn_url, "
                "filename=excluded.filename, updated_at=excluded.updated_at",
                rows[i:i + 2000],
            )


def log_replace_all(mapping, progress_cb=None, label: str = "log.json"):
    """整表替换（仅导入器用）。521k 行放一个事务，分批 executemany 省解析开销。"""
    ts = _now()
    items = list(mapping.items())
    with transaction():
        conn = get_conn()
        conn.execute("DELETE FROM post_log")
        for i in range(0, len(items), 5000):
            chunk = items[i:i + 5000]
            conn.executemany(
                "INSERT INTO post_log(post_id,cdn_url,filename,updated_at) VALUES(?,?,?,?)",
                [(str(k), v or "", _filename_from_url(v), ts) for k, v in chunk],
            )
            if progress_cb:
                progress_cb(label, min(i + len(chunk), len(items)), len(items))


def log_count() -> int:
    return query_one("SELECT COUNT(*) FROM post_log")[0]


def log_all_ids():
    return [r[0] for r in query_all("SELECT post_id FROM post_log")]


def log_snapshot_urls() -> dict:
    return {r["post_id"]: r["cdn_url"] for r in query_all("SELECT post_id,cdn_url FROM post_log")}


def log_pids_for_filenames(filenames) -> dict:
    """批量反查 filename→post_id（orphan backfill 用）。

    filename 列非唯一（历史上不同 pid 可能同文件名），取 rowid 最大（最近写入）的一条，
    与旧 dict 构造时后者覆盖前者的行为一致。"""
    names = [n for n in dict.fromkeys(filenames) if n]
    result = {}
    if not names:
        return result
    with _LOCK:
        conn = get_conn()
        for i in range(0, len(names), 500):
            chunk = names[i:i + 500]
            placeholders = ",".join("?" * len(chunk))
            for fn, pid in conn.execute(
                f"SELECT filename, post_id FROM post_log "
                f"WHERE filename IN ({placeholders}) AND filename <> '' ORDER BY rowid",
                chunk,
            ):
                result[fn] = pid  # 后者覆盖
    return result


def log_filename_to_id_map() -> dict:
    """全量 filename→pid（兼容旧 LogStore 方法；调用方建议改批量点查）。"""
    result = {}
    for fn, pid in query_all(
        "SELECT filename, post_id FROM post_log WHERE filename <> '' ORDER BY rowid"
    ):
        result[fn] = pid
    return result


# ============================================================
# artist_stats（替代 artist_stats.json；计数一律原子 UPSERT）
# ============================================================
def stats_inc(artist, n: int = 1):
    with transaction():
        get_conn().execute(
            "INSERT INTO artist_stats(artist,count,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(artist) DO UPDATE SET count=count+excluded.count, "
            "updated_at=excluded.updated_at",
            (artist, int(n), _now()),
        )


def stats_bulk_add(mapping):
    """按增量合并（StatsStore.bulk_merge 语义；非整数 value 跳过）。"""
    rows = []
    for k, v in (mapping or {}).items():
        try:
            inc = int(v or 0)
        except (TypeError, ValueError):
            continue
        if inc:
            rows.append((k, inc, _now()))
    if not rows:
        return
    with transaction():
        conn = get_conn()
        conn.executemany(
            "INSERT INTO artist_stats(artist,count,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(artist) DO UPDATE SET count=count+excluded.count, "
            "updated_at=excluded.updated_at",
            rows,
        )


def stats_apply_relative(artist, value: int, baseline=None):
    """「读旧值→写绝对值」风格（CLI: stats[k]=stats.get(k,0)+n）的安全落点。

    增量相对**调用方读到的基线 baseline** 计算（value-baseline），在写事务内应用到
    DB 当前值上：两个进程都读到 0、各 +1 时，两个 +1 都会生效，不再互相覆盖。
    baseline 缺失（从没读过直接赋值）时退化为相对当前 DB 值，与旧 JSON 行为一致。
    极端情况：A 读到 0、B 已把值推到 5 后 A 想写 10（A 观察到一批 +10），会加成 15
    （宁多勿丢，且只在跨进程同画师同时写入时出现）；count 不允许变负。
    服务端自身走 stats_inc 的纯原子路径，没有这个窗口。"""
    value = int(value)
    with transaction():
        conn = get_conn()
        row = conn.execute("SELECT count FROM artist_stats WHERE artist=?", (artist,)).fetchone()
        current = row["count"] if row is not None else 0
        base = current if baseline is None else int(baseline)
        new_value = current + (value - base)
        if new_value < 0:
            new_value = 0
        if new_value == current and row is not None:
            return
        conn.execute(
            "INSERT INTO artist_stats(artist,count,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(artist) DO UPDATE SET count=excluded.count, updated_at=excluded.updated_at",
            (artist, new_value, _now()),
        )


def stats_merge_absolute(mapping):
    """setter 批量合并：给得到精确值、不删未知键（测试 / 个别脚本赋值用）。"""
    rows = []
    for k, v in (mapping or {}).items():
        try:
            rows.append((k, max(0, int(v or 0)), _now()))
        except (TypeError, ValueError):
            continue
    if not rows:
        return
    with transaction():
        get_conn().executemany(
            "INSERT INTO artist_stats(artist,count,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(artist) DO UPDATE SET count=excluded.count, updated_at=excluded.updated_at",
            rows,
        )


def stats_replace_all(mapping, progress_cb=None, label: str = "artist_stats.json"):
    items = [(k, max(0, int(v or 0))) for k, v in (mapping or {}).items()]
    with transaction():
        conn = get_conn()
        conn.execute("DELETE FROM artist_stats")
        for i in range(0, len(items), 5000):
            chunk = items[i:i + 5000]
            conn.executemany(
                "INSERT INTO artist_stats(artist,count,updated_at) VALUES(?,?,?)",
                [(k, c, _now()) for k, c in chunk],
            )
            if progress_cb:
                progress_cb(label, min(i + len(chunk), len(items)), len(items))


def stats_delete(artist) -> bool:
    with transaction():
        cur = get_conn().execute("DELETE FROM artist_stats WHERE artist=?", (artist,))
        return cur.rowcount > 0


def stats_count() -> int:
    return query_one("SELECT COUNT(*) FROM artist_stats")[0]


def stats_get(artist, default=0):
    row = query_one("SELECT count FROM artist_stats WHERE artist=?", (artist,))
    return default if row is None else row["count"]


def stats_snapshot() -> dict:
    return {r["artist"]: r["count"] for r in query_all("SELECT artist,count FROM artist_stats")}


# ============================================================
# viewer_entries（替代每个日期目录的 viewer_data.json）
# ============================================================
# entry 顶层已知列；其它键原样塞进 entry_extra（历史数据 / merge 带入的杂键不丢）
VIEWER_KNOWN_ENTRY_KEYS = (
    "artist", "filename", "local_path", "post_url", "web_url", "score",
    "fav_count", "tags",
)
# tags dict 的已知键：4 个基础键（旧实现恒写，哪怕空串）+ meta（真值才写）
# + 原始串/rating/md5；其它进 tags_extra
VIEWER_TAG_COLUMNS = (
    ("tag_general", "tag_string_general"),
    ("tag_character", "tag_string_character"),
    ("tag_copyright", "tag_string_copyright"),
    ("tag_artist", "tag_string_artist"),
)
VIEWER_EXTRA_TAG_KEYS = ("tag_string_meta", "tag_string", "rating", "md5")
VIEWER_COLUMNS = (
    "library_id", "folder", "artist", "filename", "local_path", "post_url",
    "web_url", "score", "fav_count", "tag_general", "tag_character",
    "tag_copyright", "tag_artist", "tag_meta", "tag_string_raw", "rating",
    "md5", "tags_extra", "entry_extra",
)
VIEWER_INSERT_SQL = (
    "INSERT INTO viewer_entries(library_id,folder,artist,filename,local_path,post_url,"
    "web_url,score,fav_count,tag_general,tag_character,tag_copyright,tag_artist,"
    "tag_meta,tag_string_raw,rating,md5,tags_extra,entry_extra) "
    "VALUES (" + ",".join("?" * len(VIEWER_COLUMNS)) + ")"
)


def _entry_to_row(item: dict, library_id: str, folder: str):
    """viewer entry dict → 插入参数 tuple（未知键收进两个 extra JSON）。"""
    raw_tags = item.get("tags")
    tags = raw_tags if isinstance(raw_tags, dict) else {}
    row = [library_id, folder]
    row.append(item.get("artist"))
    # filename 是 NOT NULL；正常条目恒有，极端脏数据兜底成空串（走 fn 唯一索引）
    row.append(item.get("filename") or "")
    row.append(item.get("local_path"))
    row.append(item.get("post_url"))
    row.append(item.get("web_url"))
    try:
        row.append(int(item.get("score") or 0))
    except (TypeError, ValueError):
        row.append(0)
    try:
        row.append(int(item.get("fav_count") or 0))
    except (TypeError, ValueError):
        row.append(0)
    for _col, tag_key in VIEWER_TAG_COLUMNS:
        row.append(tags.get(tag_key, ""))
    # 可选键用 NULL 表示「原 dict 没这个键」、空串保留（refresh 路径会写
    # tag_string_meta/rating 的空串键，重组必须逐字节还原，不能吞掉键）
    row.append(tags.get("tag_string_meta"))
    row.append(tags.get("tag_string"))
    row.append(tags.get("rating"))
    row.append(tags.get("md5"))
    extra_tags = {k: v for k, v in tags.items()
                  if k not in {k2 for _, k2 in VIEWER_TAG_COLUMNS} and
                  k not in VIEWER_EXTRA_TAG_KEYS}
    extra_entry = {k: v for k, v in item.items() if k not in VIEWER_KNOWN_ENTRY_KEYS}
    row.append(json.dumps(extra_tags, ensure_ascii=False) if extra_tags else None)
    row.append(json.dumps(extra_entry, ensure_ascii=False) if extra_entry else None)
    return tuple(row)


def _row_to_entry(r) -> dict:
    """DB 行 → viewer entry dict，结构与旧 JSON 条目严格一致（rating/md5 在 tags 内）。"""
    tags = {}
    # 4 个基础键旧实现恒写（哪怕空串），重组也恒给，保证画廊/merge 行为不变
    for col, tag_key in VIEWER_TAG_COLUMNS:
        tags[tag_key] = r[col] or ""
    # meta/原始串/rating/md5：键存在即还原（空串也保留），NULL 才表示原 dict 没有
    if r["tag_meta"] is not None:
        tags["tag_string_meta"] = r["tag_meta"]
    if r["tag_string_raw"] is not None:
        tags["tag_string"] = r["tag_string_raw"]
    if r["rating"] is not None:
        tags["rating"] = r["rating"]

    if r["md5"] is not None:
        tags["md5"] = r["md5"]
    if r["tags_extra"]:
        try:
            tags.update(json.loads(r["tags_extra"]))
        except (ValueError, TypeError):
            pass
    entry = {
        "artist": r["artist"],
        "filename": r["filename"],
        "local_path": r["local_path"],
        "post_url": r["post_url"],
        "score": r["score"] or 0,
        "fav_count": r["fav_count"] or 0,
        "tags": tags,
    }
    # 极老数据（2026-03 前）有 1934 条没有 web_url 键：NULL = 键不存在，逐字节保真；
    # 画廊有 `item.get("web_url") or /images/...` 兜底，不影响显示
    if r["web_url"] is not None:
        entry["web_url"] = r["web_url"]
    if r["entry_extra"]:
        try:
            extra = json.loads(r["entry_extra"])
            if isinstance(extra, dict):
                # 未知键放在标准键之后还原
                merged = dict(extra)
                merged.update(entry)
                entry = merged
        except (ValueError, TypeError):
            pass
    return entry


def viewer_load_entries(library_id: str, folder: str) -> list:
    """按 id 升序（= 旧 JSON 文件顺序；画廊自己 reversed）重组整个 folder。"""
    rows = query_all(
        "SELECT * FROM viewer_entries WHERE library_id=? AND folder=? ORDER BY id",
        (library_id, folder),
    )
    return [_row_to_entry(r) for r in rows]


def viewer_count(library_id: str, folder: str) -> int:
    return query_one(
        "SELECT COUNT(*) FROM viewer_entries WHERE library_id=? AND folder=?",
        (library_id, folder),
    )[0]


def viewer_folder_counts(library_id=None) -> dict:
    """{(library_id, folder): count}，日历/root 概览一次取完。"""
    if library_id is None:
        rows = query_all("SELECT library_id, folder, COUNT(*) c FROM viewer_entries GROUP BY 1,2")
    else:
        rows = query_all(
            "SELECT library_id, folder, COUNT(*) c FROM viewer_entries WHERE library_id=? GROUP BY 1,2",
            (library_id,),
        )
    return {(r["library_id"], r["folder"]): r["c"] for r in rows}


def viewer_replace(library_id: str, folder: str, items: list, progress_cb=None):
    """整 folder 行集替换（flush / merge / refresh / 导入器用），单事务保序。

    items 假定已去重（适配器会再过一遍 dedup_viewer_data）。与旧 JSON 全量覆写同语义，
    但作用域只有这一个 folder，不碰别的日期/根。"""
    from my_utils import dedup_viewer_data
    items = dedup_viewer_data(items)
    skipped = 0
    with transaction():
        conn = get_conn()
        conn.execute(
            "DELETE FROM viewer_entries WHERE library_id=? AND folder=?",
            (library_id, folder),
        )
        rows = [_entry_to_row(it, library_id, folder) for it in items if isinstance(it, dict)]
        for i in range(0, len(rows), 1000):
            chunk = rows[i:i + 1000]
            try:
                conn.executemany(VIEWER_INSERT_SQL, chunk)
            except sqlite3.IntegrityError:
                # 理论上 dedup_viewer_data 已按同规则去重；脏数据（如多条 post_url='#'）
                # 兜底：逐行插入，冲突的跳过，不能让整 folder 导入失败
                for row in chunk:
                    try:
                        conn.execute(VIEWER_INSERT_SQL, row)
                    except sqlite3.IntegrityError:
                        skipped += 1
        if progress_cb:
            progress_cb(f"viewer:{library_id}/{folder}", len(rows), len(rows))
    if skipped:
        print(f"[deck_db] 警告：viewer {library_id}/{folder} 有 {skipped} 条因唯一键冲突被跳过")
    return len(items)


def viewer_iter_tag_values(column: str, folder: str = None):
    """扫某 tag 列的原始空格分隔串（untranslated_characters 聚合用），流式返回。
    folder 非空时限单日期/tag 目录（跨所有 library root）。"""
    assert column in {"tag_general", "tag_character", "tag_copyright", "tag_artist",
                      "tag_meta", "tag_string_raw"}
    sql = f"SELECT {column} v FROM viewer_entries WHERE {column} IS NOT NULL AND {column} <> ''"
    params = ()
    if folder is not None:
        sql += " AND folder=?"
        params = (folder,)
    with _LOCK:
        cur = get_conn().execute(sql, params)
        while True:
            batch = cur.fetchmany(1000)
            if not batch:
                break
            for r in batch:
                yield r["v"]


_ISO_DATE_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def _escape_like_token(token: str) -> str:
    """整词匹配用的 LIKE 转义：tag 里的下划线在 LIKE 中是单字符通配，必须转义。"""
    return token.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def viewer_search(token_groups, kinds, folder_start=None, folder_end=None,
                  library_ids=None, limit=200, offset=0, sort="date"):
    """跨日期的整词 tag 搜索。只查 deck.db，不碰磁盘/网络。

    token_groups: [[tag, ...], ...] —— 组间 AND、组内 OR（支持中文角色名展开成多个候选 tag）。
    kinds: 要匹配的标签类别集合，取值 'character' / 'artist'。
           'character' 同时匹配 tag_character 与 tag_copyright（作品/系列 tag，如 azur_lane）：
           单日画廊子串搜索靠角色 tag 的 _(系列名) 后缀命中系列名，跨日期搜索要保持同等体验。
    folder_start/folder_end: 可选的 YYYY-MM-DD 闭区间（字典序即日期序）。
    library_ids: 仅在这些库内搜索（主进程只传当前在线的 root）。
    返回 (total, rows)：rows 为原始 sqlite Row（含 library_id/folder 上下文，供主进程归一化）。
    """
    groups = [[str(t).strip().lower() for t in group if str(t).strip()] for group in token_groups]
    groups = [g for g in groups if g]
    kinds = set(kinds) & {"character", "artist"}
    if not groups or not kinds:
        return 0, []
    limit = max(1, min(int(limit or 200), 500))
    offset = max(0, int(offset or 0))
    order_by = {
        "date": "folder DESC, id DESC",
        "score": "score DESC, folder DESC, id DESC",
        "fav_count": "fav_count DESC, folder DESC, id DESC",
    }.get(sort, "folder DESC, id DESC")

    where = ["folder GLOB ?"]
    params = [_ISO_DATE_GLOB]
    if library_ids:
        where.append("library_id IN (" + ",".join("?" * len(library_ids)) + ")")
        params.extend(library_ids)
    if folder_start:
        where.append("folder >= ?")
        params.append(folder_start)
    if folder_end:
        where.append("folder <= ?")
        params.append(folder_end)

    # 每组生成一个 (角色整词匹配 OR 作品系列整词匹配 OR 作者整词匹配) 子句；tag 以空格分词存储，
    # 前后补空格后 LIKE '% tag %' 做整词边界，ESCAPE 让 tag 里的 _ 不被当通配。
    for group in groups:
        branches = []
        for token in group:
            word = f"% {_escape_like_token(token)} %"
            if "character" in kinds:
                # tag_character 之外把 tag_copyright（作品/系列，如 azur_lane、genshin_impact）
                # 也纳入：画廊单日搜索的 azur_lane 命中实际来自角色 tag 的 _(系列名) 后缀，
                # 跨日期整词匹配只有搜 copyright 列才能对齐，还能多覆盖只打了系列 tag 的群像图。
                branches.append(
                    "(' '||IFNULL(tag_character,'')||' ' LIKE ? ESCAPE '\\')")
                params.append(word)
                branches.append(
                    "(' '||IFNULL(tag_copyright,'')||' ' LIKE ? ESCAPE '\\')")
                params.append(word)
            if "artist" in kinds:
                # 注意结尾两层 ) ：内层闭合 OR 右半的 (' '||…)，外层闭合 (LOWER(…) OR …)
                branches.append(
                    "(LOWER(IFNULL(artist,''))=? OR "
                    "(' '||IFNULL(tag_artist,'')||' ' LIKE ? ESCAPE '\\'))")
                params.extend([token, word])
        where.append("(" + " OR ".join(branches) + ")")

    where_sql = " AND ".join(where)
    with _LOCK:
        conn = get_conn()
        total = conn.execute(
            f"SELECT COUNT(*) FROM viewer_entries WHERE {where_sql}", params
        ).fetchone()[0]
        rows = conn.execute(
            f"SELECT * FROM viewer_entries WHERE {where_sql} "
            f"ORDER BY {order_by} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    return total, rows


def viewer_find_entries(library_id: str, folder: str, filenames=None,
                        post_urls=None, limit_per_filename=1):
    """单 folder 内按 filename / post_url 点查（caption meta / 单项刷新用）。"""
    clauses, params = ["library_id=?", "folder=?"], [library_id, folder]
    if filenames is not None:
        clauses.append("filename IN (" + ",".join("?" * len(filenames)) + ")")
        params.extend(filenames)
    if post_urls is not None:
        clauses.append("post_url IN (" + ",".join("?" * len(post_urls)) + ")")
        params.extend(post_urls)
    rows = query_all(
        f"SELECT * FROM viewer_entries WHERE {' AND '.join(clauses)} ORDER BY id",
        params,
    )
    return [_row_to_entry(r) for r in rows]


# ============================================================
# dl_queue（持久下载队列，替代 ids_data.json 的权威存储）
# ============================================================
QUEUE_PENDING = "pending"
QUEUE_FAILED = "failed"


def queue_add(library_id: str, folder: str, post_id, status: str = QUEUE_PENDING):
    """入队即提交（崩溃安全契约）。重新排队旧 failed 行时状态翻回 pending。"""
    with transaction():
        get_conn().execute(
            "INSERT INTO dl_queue(library_id,folder,post_id,status,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(library_id,folder,post_id) DO UPDATE SET status=excluded.status, "
            "updated_at=excluded.updated_at",
            (library_id, folder, str(post_id), status, _now()),
        )


def queue_remove(library_id: str, folder: str, post_id):
    with transaction():
        get_conn().execute(
            "DELETE FROM dl_queue WHERE library_id=? AND folder=? AND post_id=?",
            (library_id, folder, str(post_id)),
        )


def queue_mark_status(library_id: str, folder: str, post_id, status: str):
    with transaction():
        get_conn().execute(
            "INSERT INTO dl_queue(library_id,folder,post_id,status,updated_at) VALUES(?,?,?,?,?) "
            "ON CONFLICT(library_id,folder,post_id) DO UPDATE SET status=excluded.status, "
            "updated_at=excluded.updated_at",
            (library_id, folder, str(post_id), status, _now()),
        )


def queue_load(library_id: str, folder: str, status=None) -> list:
    """取 folder 队列 id（post_id 字典序，与旧 sorted(set) 镜像一致）。"""
    sql = "SELECT post_id FROM dl_queue WHERE library_id=? AND folder=?"
    params = [library_id, folder]
    if status:
        sql += " AND status=?"
        params.append(status)
    sql += " ORDER BY post_id"
    return [r["post_id"] for r in query_all(sql, params)]


def queue_count(library_id: str, folder: str, status=None) -> int:
    sql = "SELECT COUNT(*) FROM dl_queue WHERE library_id=? AND folder=?"
    params = [library_id, folder]
    if status:
        sql += " AND status=?"
        params.append(status)
    return query_one(sql, params)[0]


def queue_counts_grouped() -> dict:
    """日历 pending 角标：{(library_id, folder): 全部 pending+failed 行数}，一次取完。"""
    rows = query_all(
        "SELECT library_id, folder, COUNT(*) c FROM dl_queue GROUP BY library_id, folder"
    )
    return {(r["library_id"], r["folder"]): r["c"] for r in rows}


def queue_replace(library_id: str, folder: str, ids, status: str = QUEUE_PENDING):
    """用 ids 全量替换 folder 的 pending 集合（旧 save_ids_data 语义）。

    保留细节：已标记 failed 且仍在列表里的行不动（ON CONFLICT DO NOTHING）；
    不在列表里的行（已完成）删除。"""
    ids = [str(x) for x in (ids or [])]
    with transaction():
        conn = get_conn()
        # TEMP 表承载本次全集：NOT IN 不随 id 数量产生上万绑定变量
        # （SQLITE_MAX_VARIABLE_NUMBER 现代版本也只有 32766，大收集集会超）。
        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _q_replace(pid TEXT PRIMARY KEY)")
        conn.execute("DELETE FROM _q_replace")
        conn.executemany("INSERT INTO _q_replace(pid) VALUES(?)",
                         [(pid,) for pid in dict.fromkeys(ids)])
        conn.execute(
            "DELETE FROM dl_queue WHERE library_id=? AND folder=? AND post_id NOT IN "
            "(SELECT pid FROM _q_replace)",
            (library_id, folder),
        )
        if ids:
            now = _now()
            # ON CONFLICT DO NOTHING：已存在的 failed 行（id 仍在全集里）状态不动
            conn.executemany(
                "INSERT INTO dl_queue(library_id,folder,post_id,status,updated_at) "
                "VALUES(?,?,?,?,?) ON CONFLICT(library_id,folder,post_id) DO NOTHING",
                [(library_id, folder, pid, status, now) for pid in dict.fromkeys(ids)],
            )
        conn.execute("DROP TABLE _q_replace")


# ============================================================
# 镜像导出 + folder_sync 对账（viewer_data.json / ids_data.json 降级为派生镜像）
# ============================================================
def _stat_fingerprint(path) -> tuple:
    st = os.stat(path)
    return st.st_mtime_ns, st.st_size


def _set_sync(library_id: str, folder: str, kind: str, fingerprint: tuple, rows: int,
              conn=None):
    sql = ("INSERT INTO folder_sync(library_id,folder,kind,mtime_ns,size,rows,updated_at) "
           "VALUES(?,?,?,?,?,?,?) ON CONFLICT(library_id,folder,kind) DO UPDATE SET "
           "mtime_ns=excluded.mtime_ns,size=excluded.size,rows=excluded.rows,"
           "updated_at=excluded.updated_at")
    params = (library_id, folder, kind, fingerprint[0], fingerprint[1], rows, _now())
    if conn is not None:
        conn.execute(sql, params)
    else:
        with transaction():
            get_conn().execute(sql, params)


def export_viewer_mirror(library_id: str, folder: str, dest_path: str, items=None) -> int:
    """DB → viewer_data.json 镜像，返回条数。原子写，成功后登记 folder_sync 指纹。"""
    from my_utils import atomic_write_json, dedup_viewer_data
    items = viewer_load_entries(library_id, folder) if items is None else dedup_viewer_data(items)
    atomic_write_json(dest_path, items)
    _set_sync(library_id, folder, "viewer", _stat_fingerprint(dest_path), len(items))
    return len(items)


def export_ids_mirror(library_id: str, folder: str, dest_path: str) -> int:
    """DB（pending+failed）→ ids_data.json 镜像。"""
    from my_utils import atomic_write_json
    ids = queue_load(library_id, folder)
    atomic_write_json(dest_path, ids)
    _set_sync(library_id, folder, "ids", _stat_fingerprint(dest_path), len(ids))
    return len(ids)


def reconcile_viewer(library_id: str, folder: str, mirror_path: str) -> bool:
    """镜像文件被外部改动（他机同步 / CLI 直写 / 手工编辑）时，按文件 replace 进 DB。

    判据是 mtime_ns+size 与 folder_sync 不等（不用「更新」，防时钟回拨）。幂等。
    返回是否发生了吸收。"""
    from my_utils import dedup_viewer_data, load_json
    if not os.path.exists(mirror_path):
        return False
    fingerprint = _stat_fingerprint(mirror_path)
    row = query_one(
        "SELECT mtime_ns,size FROM folder_sync WHERE library_id=? AND folder=? AND kind='viewer'",
        (library_id, folder),
    )
    if row is not None and (row["mtime_ns"], row["size"]) == fingerprint:
        return False
    f = _open_legacy_text(mirror_path)
    if f is None:
        # stat 之后、读取前被改名/删除：本轮不吸收，绝不能拿空列表清表
        return False
    with f:
        try:
            items = json.load(f)
        except ValueError:
            items = []
    if not isinstance(items, list):
        items = []
    items = dedup_viewer_data(items)
    viewer_replace(library_id, folder, items)
    _set_sync(library_id, folder, "viewer", fingerprint, len(items))
    return True


def reconcile_ids(library_id: str, folder: str, mirror_path: str) -> bool:
    from my_utils import load_json
    if not os.path.exists(mirror_path):
        return False
    fingerprint = _stat_fingerprint(mirror_path)
    row = query_one(
        "SELECT mtime_ns,size FROM folder_sync WHERE library_id=? AND folder=? AND kind='ids'",
        (library_id, folder),
    )
    if row is not None and (row["mtime_ns"], row["size"]) == fingerprint:
        return False
    f = _open_legacy_text(mirror_path)
    if f is None:
        return False
    with f:
        try:
            items = json.load(f)
        except ValueError:
            items = []
    if not isinstance(items, list):
        items = []
    ids = [str(x) for x in items]
    queue_replace(library_id, folder, ids)
    _set_sync(library_id, folder, "ids", fingerprint, len(ids))
    return True


# 浸泡期双读比对的已校验指纹缓存：(lib, folder, mtime_ns, size) → 是否一致。
# 画廊请求很频繁，全量规范化比对一个 folder 只能做一次；镜像指纹变了（=被重写）才再比。
_DUALREAD_CHECKED = set()


def viewer_compare_with_json(library_id: str, folder: str, mirror_items: list,
                             mirror_path: str = None) -> str:
    """浸泡期双读比对：DB 重组结果 vs 直接解析 JSON，返回差异描述（无差异返回 ""）。

    每个 (folder, 镜像指纹) 进程内只实际比对一次；调用方应先 reconcile_viewer。"""
    fingerprint = None
    if mirror_path and os.path.exists(mirror_path):
        fingerprint = (library_id, folder) + _stat_fingerprint(mirror_path)
        if fingerprint in _DUALREAD_CHECKED:
            return ""
    from my_utils import dedup_viewer_data
    db_items = viewer_load_entries(library_id, folder)
    def canon(items):
        return sorted(
            json.dumps(it, ensure_ascii=False, sort_keys=True)
            for it in dedup_viewer_data(items) if isinstance(it, dict)
        )
    a, b = canon(db_items), canon(mirror_items)
    if a == b:
        if fingerprint is not None:
            _DUALREAD_CHECKED.add(fingerprint)  # 一致才缓存；差异每次都报（浸泡信号）
        return ""
    only_db = len(set(a) - set(b))
    only_json = len(set(b) - set(a))
    return f"folder {library_id}/{folder}: DB {len(a)} 条 vs JSON {len(b)} 条，"\
           f"仅 DB 有 {only_db}，仅 JSON 有 {only_json}"


# ============================================================
# drawer 四件套（txtdata / disk_drawer / hot_drawer / need_update）
# ============================================================
def drawer_txt_replace(names):
    """txtdata.txt 全量替换（画师名全集，pos 保留文件行序，重复也保留）。"""
    names = [str(x) for x in (names or [])]
    with transaction():
        conn = get_conn()
        conn.execute("DELETE FROM drawer_txt")
        conn.executemany("INSERT INTO drawer_txt(name,pos) VALUES(?,?)",
                         [(n, i) for i, n in enumerate(names)])


def drawer_txt_load():
    return [r["name"] for r in query_all("SELECT name FROM drawer_txt ORDER BY pos")]


def drawer_disk_replace(groups):
    """disk_drawer.json 替换：{grp: [name, ...]}，未知 grp 原样保留。"""
    groups = groups or {}
    with transaction():
        conn = get_conn()
        conn.execute("DELETE FROM drawer_disk")
        rows = []
        for grp in sorted(groups.keys()):
            for pos, name in enumerate(groups.get(grp) or []):
                rows.append((str(grp), str(name), pos))
        conn.executemany("INSERT INTO drawer_disk(grp,name,pos) VALUES(?,?,?)", rows)


def drawer_disk_load():
    out = {}
    for r in query_all("SELECT grp,name FROM drawer_disk ORDER BY grp,pos"):
        out.setdefault(r["grp"], []).append(r["name"])
    return out


def drawer_hot_replace(names):
    names = [str(x) for x in (names or [])]
    with transaction():
        conn = get_conn()
        conn.execute("DELETE FROM drawer_hot")
        conn.executemany("INSERT INTO drawer_hot(name,pos) VALUES(?,?)",
                         [(n, i) for i, n in enumerate(names)])


def drawer_hot_load():
    return [r["name"] for r in query_all("SELECT name FROM drawer_hot ORDER BY pos")]


def drawer_need_update_replace(groups):
    groups = groups or {}
    with transaction():
        conn = get_conn()
        conn.execute("DELETE FROM drawer_need_update")
        rows = []
        for grp in sorted(groups.keys()):
            for name in groups.get(grp) or []:
                rows.append((str(grp), str(name)))
        conn.executemany("INSERT OR IGNORE INTO drawer_need_update(grp,name) VALUES(?,?)", rows)


def drawer_need_update_load():
    out = {}
    for r in query_all("SELECT grp,name FROM drawer_need_update ORDER BY grp,name"):
        out.setdefault(r["grp"], []).append(r["name"])
    return out


# ---------------- 全局文件指纹（drawer / favorites 镜像对账用，存 meta 表）----
def _sync_mark(sync_name: str, fingerprint: tuple):
    meta_set(f"sync_file:{sync_name}", json.dumps(list(fingerprint)))


def _sync_changed(sync_name: str, path: str) -> tuple:
    """返回 (是否变化, 当前指纹)。文件不存在按未变化处理（调用方自行判存在）。"""
    if not os.path.exists(path):
        return False, None
    fingerprint = _stat_fingerprint(path)
    raw = meta_get(f"sync_file:{sync_name}")
    try:
        old = tuple(json.loads(raw)) if raw else None
    except (ValueError, TypeError):
        old = None
    return old != fingerprint, fingerprint


def _atomic_write_text(path: str, text: str):
    """drawer 的 .txt 镜像用：与 atomic_write_json 同一把 per-path 锁 + 唯一临时名。"""
    from my_utils import file_lock_for
    with file_lock_for(path):
        temp_path = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass


def drawer_reconcile_static(drawer_dir: str):
    """DanbooruData 构造时调：txtdata.txt / disk_drawer.json 是用户手工维护文件，
    指纹变了就重新吸收（首次运行吸收空种子）。"""
    txt_path = os.path.join(drawer_dir, "txtdata.txt")
    changed, fp = _sync_changed("drawer_txt", txt_path)
    if changed:
        f = _open_legacy_text(txt_path)
        if f is not None:
            with f:
                drawer_txt_replace(f.read().split("\n"))
            _sync_mark("drawer_txt", fp)
    disk_path = os.path.join(drawer_dir, "disk_drawer.json")
    changed, fp = _sync_changed("drawer_disk", disk_path)
    if changed:
        f = _open_legacy_text(disk_path)
        if f is not None:
            with f:
                try:
                    data = json.load(f)
                except ValueError:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            drawer_disk_replace(data)
            _sync_mark("drawer_disk", fp)


def drawer_hot_reconcile(path: str):
    changed, fp = _sync_changed("drawer_hot", path)
    if not changed:
        return
    f = _open_legacy_text(path)
    if f is None:
        return
    with f:
        drawer_hot_replace(f.read().split("\n"))
    _sync_mark("drawer_hot", fp)


def drawer_need_update_reconcile(path: str):
    changed, fp = _sync_changed("drawer_need_update", path)
    if not changed:
        return
    f = _open_legacy_text(path)
    if f is None:
        return
    with f:
        try:
            data = json.load(f)
        except ValueError:
            data = {}
    if not isinstance(data, dict):
        data = {}
    drawer_need_update_replace({k: list(v) if isinstance(v, (list, set, tuple)) else []
                                for k, v in data.items()})
    _sync_mark("drawer_need_update", fp)


def export_hot_drawer_mirror(path: str, names) -> None:
    text = "\n".join(str(x) for x in (names or []))
    _atomic_write_text(path, text)
    _sync_mark("drawer_hot", _stat_fingerprint(path))


def export_need_update_mirror(path: str, groups) -> None:
    from my_utils import atomic_write_json
    atomic_write_json(path, {str(k): [str(x) for x in v] for k, v in (groups or {}).items()})
    _sync_mark("drawer_need_update", _stat_fingerprint(path))


# ============================================================
# 三类收藏（artist / character 分组列表；image 单键 payload）
# ============================================================
# kind → (表名, 成员列名)：character 表的列叫 token，artist 表叫 name
_FAV_KINDS = {
    "artist": ("fav_artist", "name"),
    "character": ("fav_character", "token"),
}


def fav_group_replace(kind: str, groups: dict):
    table, col = _FAV_KINDS[kind]
    groups = groups or {}
    with transaction():
        conn = get_conn()
        conn.execute(f"DELETE FROM {table}")
        rows = []
        for grp, names in groups.items():
            for pos, name in enumerate(names or []):
                rows.append((str(grp), str(name), pos))
        conn.executemany(f"INSERT INTO {table}(grp,{col},pos) VALUES(?,?,?)", rows)


def fav_group_load(kind: str) -> dict:
    table, col = _FAV_KINDS[kind]
    out = {}
    for r in query_all(f"SELECT grp,{col} AS member FROM {table} ORDER BY grp,pos"):
        out.setdefault(r["grp"], []).append(r["member"])
    return out


def export_fav_group_mirror(path: str, groups: dict) -> None:
    from my_utils import atomic_write_json
    atomic_write_json(path, groups or {}, indent=2)


def fav_image_upsert(key: str, payload: dict):
    payload = payload if isinstance(payload, dict) else {}
    with transaction():
        get_conn().execute(
            "INSERT INTO fav_image(fav_key,added_at,date,filename,library_id,payload) "
            "VALUES(?,?,?,?,?,?) ON CONFLICT(fav_key) DO UPDATE SET "
            "added_at=excluded.added_at,date=excluded.date,filename=excluded.filename,"
            "library_id=excluded.library_id,payload=excluded.payload",
            (key, int(payload.get("added_at") or 0), payload.get("date") or "",
             payload.get("filename") or "", payload.get("library_id") or "default",
             json.dumps(payload, ensure_ascii=False)),
        )


def fav_image_delete(key: str) -> bool:
    with transaction():
        cur = get_conn().execute("DELETE FROM fav_image WHERE fav_key=?", (key,))
        return cur.rowcount > 0


def fav_image_load_all() -> dict:
    return {r["fav_key"]: json.loads(r["payload"])
            for r in query_all("SELECT fav_key,payload FROM fav_image")}


def fav_image_count() -> int:
    return query_one("SELECT COUNT(*) FROM fav_image")[0]


def fav_image_items():
    """GET 用：payload 反序列化后带 key，按 added_at 倒序。"""
    items = []
    for r in query_all("SELECT fav_key,payload FROM fav_image ORDER BY added_at DESC"):
        try:
            payload = json.loads(r["payload"])
        except (ValueError, TypeError):
            continue
        items.append({"key": r["fav_key"], **payload})
    return items


def export_fav_image_mirror(path: str, data: dict = None) -> None:
    from my_utils import atomic_write_json
    if data is None:
        data = fav_image_load_all()
    atomic_write_json(path, data, indent=2)
    _sync_mark("fav_image", _stat_fingerprint(path))


def fav_group_reconcile(kind: str, path: str) -> dict:
    """外部改动（他机同步 / 手工编辑）吸收后返回当前分组。"""
    changed, fp = _sync_changed(f"fav_{kind}", path)
    if changed:
        f = _open_legacy_text(path)
        if f is not None:
            with f:
                try:
                    data = json.load(f)
                except ValueError:
                    data = {}
            cleaned = _clean_group_lists(data if isinstance(data, dict) else {})
            fav_group_replace(kind, cleaned)
            _sync_mark(f"fav_{kind}", fp)
    return fav_group_load(kind)


def fav_group_save(kind: str, groups: dict, path: str) -> None:
    """DB 替换 + 镜像导出 + 指纹登记（收藏 POST 唯一写入路径）。"""
    fav_group_replace(kind, groups or {})
    export_fav_group_mirror(path, groups or {})
    _sync_mark(f"fav_{kind}", _stat_fingerprint(path))


def fav_image_reconcile(path: str) -> dict:
    changed, fp = _sync_changed("fav_image", path)
    if changed:
        f = _open_legacy_text(path)
        if f is not None:
            with f:
                try:
                    data = json.load(f)
                except ValueError:
                    data = {}
            if isinstance(data, dict):
                with transaction():
                    get_conn().execute("DELETE FROM fav_image")
                    for key, payload in data.items():
                        if isinstance(payload, dict):
                            get_conn().execute(
                                "INSERT INTO fav_image(fav_key,added_at,date,filename,library_id,payload) "
                                "VALUES(?,?,?,?,?,?)",
                                (str(key), int(payload.get("added_at") or 0), payload.get("date") or "",
                                 payload.get("filename") or "", payload.get("library_id") or "default",
                                 json.dumps(payload, ensure_ascii=False)),
                            )
            _sync_mark("fav_image", fp)
    return fav_image_load_all()


# ============================================================
# failed_pages（失败页持久化，跨进程重启 hydrate）
# ============================================================
def failed_page_add(folder, page, tag_query="", tag_source="danbooru"):
    with transaction():
        get_conn().execute(
            "INSERT OR IGNORE INTO failed_pages(folder,page,tag_query,tag_source,created_at) "
            "VALUES(?,?,?,?,?)",
            (folder, int(page), tag_query or "", tag_source or "danbooru", _now()),
        )


def failed_page_remove(folder, page, tag_query="", tag_source="danbooru"):
    with transaction():
        get_conn().execute(
            "DELETE FROM failed_pages WHERE folder=? AND page=? AND tag_query=? AND tag_source=?",
            (folder, int(page), tag_query or "", tag_source or "danbooru"),
        )


def failed_page_load_scope(folder, tag_query="", tag_source="danbooru"):
    rows = query_all(
        "SELECT folder,page FROM failed_pages WHERE folder=? AND tag_query=? AND tag_source=? "
        "ORDER BY page",
        (folder, tag_query or "", tag_source or "danbooru"),
    )
    return [{"folder": r["folder"], "page": r["page"]} for r in rows]


# ============================================================
# page_cursor（收 ID 阶段的断点，供「从第 N 页续跑」一键恢复）
# ============================================================
def cursor_save(folder, mode, page, start_page, end_page,
                tag_query="", tag_source="danbooru"):
    with transaction():
        get_conn().execute(
            "INSERT INTO page_cursor(folder,tag_query,tag_source,mode,page_current,"
            "start_page,end_page,updated_at) VALUES(?,?,?,?,?,?,?,?) "
            "ON CONFLICT(folder,tag_query,tag_source) DO UPDATE SET "
            "mode=excluded.mode,page_current=excluded.page_current,"
            "start_page=excluded.start_page,end_page=excluded.end_page,"
            "updated_at=excluded.updated_at",
            (folder, tag_query or "", tag_source or "danbooru", mode,
             int(page), int(start_page), int(end_page), _now()),
        )


def cursor_load(folder, tag_query="", tag_source="danbooru"):
    row = query_one(
        "SELECT folder,mode,page_current,start_page,end_page,updated_at "
        "FROM page_cursor WHERE folder=? AND tag_query=? AND tag_source=?",
        (folder, tag_query or "", tag_source or "danbooru"),
    )
    if not row:
        return None
    return {
        "folder": row["folder"],
        "mode": row["mode"],
        "page": row["page_current"],
        "start_page": row["start_page"],
        "end_page": row["end_page"],
        "updated_at": row["updated_at"],
    }


def cursor_clear(folder, tag_query="", tag_source="danbooru"):
    with transaction():
        get_conn().execute(
            "DELETE FROM page_cursor WHERE folder=? AND tag_query=? AND tag_source=?",
            (folder, tag_query or "", tag_source or "danbooru"),
        )


# ============================================================
# 旧 JSON 引导导入（按域幂等；meta 标志位分阶段解锁）
# ============================================================
_META_LOG_STATS = "imported_log_stats"
_META_VIEWER_IDS = "imported_viewer_ids"
_META_DRAWER = "imported_drawer"
_META_FAVORITES = "imported_favorites"
# viewer/ids 单 folder 导入规模（行数）超过它才打一行进度，避免 55+ 目录刷屏
_PROGRESS_VIEWER_MIN_ROWS = 2000


def _print_progress(label, done, total):
    if total and (done == total or done % 50000 < 5000):
        print(f"[deck_db] 导入 {label}: {done}/{total}")


def _open_legacy_text(path, *, retries: int = 50, delay: float = 0.1):
    """读旧 JSON/txt 的占用安全 open()。

    Windows 上另一进程正 os.replace 改名/原子覆写该文件的几十毫秒内，并发
    CreateFile 会 PermissionError [Errno 13]（双开 app 同时首启时实测必现）——
    重试；文件在此期间消失（已改名）返回 None，调用方按「源缺失，跳过」处理。"""
    last_exc = None
    for _ in range(retries):
        try:
            return open(path, "r", encoding="utf-8")
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            last_exc = exc
            if not os.path.exists(path):
                return None
            time.sleep(delay)
    if not os.path.exists(path):
        return None
    raise last_exc


def _load_legacy_json(path, default):
    """占用安全的 json.load：文件缺失/损坏返回 default。"""
    f = _open_legacy_text(path)
    if f is None:
        return default
    try:
        with f:
            return json.load(f)
    except (ValueError, OSError):
        return default


def import_legacy_log_stats(progress_cb=_print_progress):
    """log.json / artist_stats.json → post_log / artist_stats。

    只在 meta 标志未置位时由 bootstrap 自动跑；手动 --import 可强制重跑。
    导入成功后本阶段**不改名、不删 JSON**（最终全量校验通过才在阶段 6 改名 .bak）。"""
    from runtime_paths import HOT_PIC_DIR
    log_path = HOT_PIC_DIR / "log.json"
    stats_path = HOT_PIC_DIR / "artist_stats.json"
    # 注意：源文件缺失时只跳过、绝不清表——校验通过后 log.json 会被改名 .bak，
    # 此后若 --force 重跑导入，清表会把整库 521k 行抹掉。
    f = _open_legacy_text(log_path)
    if f is not None:
        print(f"[deck_db] 首次启用数据库，正在导入 {log_path}（一次性，请稍候）…")
        with f:
            mapping = json.load(f)
        log_replace_all(mapping, progress_cb=progress_cb)
        print(f"[deck_db] log.json 导入完成：{len(mapping)} 条")
    f = _open_legacy_text(stats_path)
    if f is not None:
        with f:
            mapping = json.load(f) or {}
        stats_replace_all(mapping, progress_cb=progress_cb)
        print(f"[deck_db] artist_stats.json 导入完成：{len(mapping)} 位画师")
    meta_set(_META_LOG_STATS, "1")


def import_legacy_viewer_ids(progress_cb=_print_progress):
    """遍历所有当前在线 library root 的一级子目录，把 viewer_data.json / ids_data.json
    吸收进 viewer_entries / dl_queue，并登记 folder_sync 指纹（不重写镜像文件）。

    - 离线外置盘跳过，日后访问该 root 时由 reconcile_viewer/reconcile_ids 按需吸收；
    - 幂等：viewer_replace/queue_replace 都是 folder 作用域全量替换；
    - 不改名、不删 JSON（最终校验通过后阶段 6 才处理 log/stats 的 .bak，镜像本就保留）。"""
    from library_config import get_library_roots

    n_viewer_files = n_ids_files = n_viewer_rows = n_ids_rows = n_folders = 0
    for root in get_library_roots():
        lib_id = root["id"]
        root_path = root["path"]
        if not root_path.exists() or not root_path.is_dir():
            print(f"[deck_db] 根 {lib_id}（{root_path}）离线，跳过 viewer/ids 导入")
            continue
        for sub in sorted(root_path.iterdir(), key=lambda p: p.name.lower()):
            if not sub.is_dir():
                continue
            folder = sub.name
            viewer_file = sub / "viewer_data.json"
            ids_file = sub / "ids_data.json"
            if not viewer_file.exists() and not ids_file.exists():
                continue
            n_folders += 1
            if viewer_file.exists():
                # 占用安全读：双开首启时另一进程可能正在原子覆写该镜像
                items = _load_legacy_json(str(viewer_file), [])
                if not isinstance(items, list):
                    items = []
                count = viewer_replace(lib_id, folder, items)
                _set_sync(lib_id, folder, "viewer", _stat_fingerprint(str(viewer_file)), count)
                n_viewer_files += 1
                n_viewer_rows += count
                if count >= _PROGRESS_VIEWER_MIN_ROWS:
                    print(f"[deck_db] 导入 viewer {lib_id}/{folder}: {count} 条")
            if ids_file.exists():
                raw_ids = _load_legacy_json(str(ids_file), [])
                ids = [str(x) for x in raw_ids] if isinstance(raw_ids, list) else []
                queue_replace(lib_id, folder, ids)
                _set_sync(lib_id, folder, "ids", _stat_fingerprint(str(ids_file)), len(ids))
                n_ids_files += 1
                n_ids_rows += len(ids)
    print(f"[deck_db] viewer/ids 导入完成：{n_folders} 个目录，viewer {n_viewer_rows} 条/"
          f"{n_viewer_files} 文件，队列 {n_ids_rows} 条/{n_ids_files} 文件")
    meta_set(_META_VIEWER_IDS, "1")


def _clean_group_lists(data: dict) -> dict:
    """artist/character 收藏加载时的同款规范化：strip + 保序去重，丢弃非 str 项。"""
    out = {}
    for grp, names in (data or {}).items():
        if not isinstance(grp, str) or not isinstance(names, list):
            continue
        seen, cleaned = set(), []
        for item in names:
            if not isinstance(item, str):
                continue
            name = item.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            cleaned.append(name)
        out[grp] = cleaned
    return out


def import_legacy_drawer():
    """drawer 四件套首次入库（用户手工维护文件；不重写镜像，指纹记为当前文件）。"""
    from runtime_paths import DRAWER_DIR

    drawer_dir = str(DRAWER_DIR)
    txt_path = os.path.join(drawer_dir, "txtdata.txt")
    f = _open_legacy_text(txt_path)
    if f is not None:
        with f:
            drawer_txt_replace(f.read().split("\n"))
        _sync_mark("drawer_txt", _stat_fingerprint(txt_path))
    disk_path = os.path.join(drawer_dir, "disk_drawer.json")
    if os.path.exists(disk_path):
        data = _load_legacy_json(disk_path, {})
        if isinstance(data, dict):
            drawer_disk_replace(data)
        _sync_mark("drawer_disk", _stat_fingerprint(disk_path))
    hot_path = os.path.join(drawer_dir, "hot_drawer.txt")
    f = _open_legacy_text(hot_path)
    if f is not None:
        with f:
            drawer_hot_replace(f.read().split("\n"))
        _sync_mark("drawer_hot", _stat_fingerprint(hot_path))
    nu_path = os.path.join(drawer_dir, "need_update.json")
    if os.path.exists(nu_path):
        data = _load_legacy_json(nu_path, {})
        if isinstance(data, dict):
            drawer_need_update_replace(data)
        _sync_mark("drawer_need_update", _stat_fingerprint(nu_path))
    n = (len(drawer_txt_load()), drawer_disk_load(),
         len(drawer_hot_load()), drawer_need_update_load())
    print(f"[deck_db] drawer 导入完成：txtdata {n[0]}，disk {sum(len(v) for v in n[1].values())}，"
          f"hot {n[2]}，need_update {sum(len(v) for v in n[3].values())}")
    meta_set(_META_DRAWER, "1")


def import_legacy_favorites():
    """三类收藏首次入库；图片收藏 payload 原样 JSON 保留，GET 形状不变。"""
    from runtime_paths import DATA_DIR

    artist_path = DATA_DIR / "artist_favorites.json"
    char_path = DATA_DIR / "character_favorites.json"
    image_path = DATA_DIR / "image_favorites.json"
    # 与 log/stats 同理：源文件缺失只跳过、不清表（收藏镜像永不改名，但 --force
    # 重跑时用户可能正把镜像临时移走；缺文件不能被解释成「清空收藏」）。
    if artist_path.exists():
        a_data = _load_legacy_json(str(artist_path), {})
        a_clean = _clean_group_lists(a_data if isinstance(a_data, dict) else {})
        fav_group_replace("artist", a_clean)
    else:
        a_clean = {}
    if char_path.exists():
        c_data = _load_legacy_json(str(char_path), {})
        c_clean = _clean_group_lists(c_data if isinstance(c_data, dict) else {})
        fav_group_replace("character", c_clean)
    else:
        c_clean = {}
    n_image = 0
    if image_path.exists():
        i_data = _load_legacy_json(str(image_path), {})
        if isinstance(i_data, dict):
            with transaction():
                get_conn().execute("DELETE FROM fav_image")
                for key, payload in i_data.items():
                    if isinstance(payload, dict):
                        get_conn().execute(
                            "INSERT INTO fav_image(fav_key,added_at,date,filename,library_id,payload) "
                            "VALUES(?,?,?,?,?,?) ON CONFLICT(fav_key) DO UPDATE SET "
                            "added_at=excluded.added_at,date=excluded.date,filename=excluded.filename,"
                            "library_id=excluded.library_id,payload=excluded.payload",
                            (str(key), int(payload.get("added_at") or 0), payload.get("date") or "",
                             payload.get("filename") or "", payload.get("library_id") or "default",
                             json.dumps(payload, ensure_ascii=False)),
                        )
                        n_image += 1
    # 指纹登记在导入值上：首次 GET 的 reconcile 不会把清洗后的分组又用原始 JSON 覆盖回去
    if artist_path.exists():
        _sync_mark("fav_artist", _stat_fingerprint(str(artist_path)))
    if char_path.exists():
        _sync_mark("fav_character", _stat_fingerprint(str(char_path)))
    if image_path.exists():
        _sync_mark("fav_image", _stat_fingerprint(str(image_path)))
    print(f"[deck_db] 收藏导入完成：artist {sum(len(v) for v in a_clean.values())}，"
          f"character {sum(len(v) for v in c_clean.values())}，image {n_image}")
    meta_set(_META_FAVORITES, "1")


def _bootstrap_domain(meta_key, importer, force: bool):
    """单域引导：已置位跳过；撞上另一进程的写锁就轮询本域标志位（最多 300s）。"""
    if not force and meta_get(meta_key) == "1":
        return
    try:
        importer()
    except sqlite3.OperationalError as exc:
        if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
            raise
        deadline = _now() + 300
        while _now() < deadline:
            time.sleep(1)
            if meta_get(meta_key) == "1":
                return
        raise TimeoutError(f"等待另一进程的 deck_db 导入超时（300s，域 {meta_key}）")


_META_STATE = "import_state"
_META_SCHEMA = "schema_version"
_META_LAST_VALIDATION = "last_validation"
# 自动引导（app 首启）只导入不改名时挂此标志；显式 CLI --import 据此做纯改名收尾。
# 与 import_state=rename_pending 的区别：后者是 CLI 改名撞占用、下次启动整体过一遍；
# 本标志下 state 已是 done，启动 0.2s 早退，不会反复校验 48MB 的 log.json。
_META_RENAME_PENDING = "legacy_rename_pending"
_IMPORT_DOMAINS = (
    (_META_DRAWER, import_legacy_drawer),
    (_META_FAVORITES, import_legacy_favorites),
    (_META_LOG_STATS, import_legacy_log_stats),
    (_META_VIEWER_IDS, import_legacy_viewer_ids),
)


def _sample_positions(n: int, k: int) -> list:
    """确定性等距抽样（不用 random，便于复查/回归）：n<=k 时取全集。"""
    if n <= k:
        return list(range(n))
    return [int(i * n / k) for i in range(k)]


def validate_legacy_import(kv_sample: int = 200, viewer_sample: int = 20) -> dict:
    """只读校验：DB 内容 vs 现存旧 JSON，返回 {ok, checks, errors}。不写任何东西。

    - log/stats：计数相等 + 等距抽样键值逐字相等；
    - viewer：每个在线 root 的每个镜像文件计数相等 + 首/末条 + 抽样条目 dict 全等
      （覆盖 tags 拆列还原的编解码正确性）；
    - ids：去重排序集合相等；
    - drawer/收藏：计数 + 顺序/集合语义比对，图片收藏抽样 payload 全等。
    缺失的源文件视为「该域已改名/已迁移」自动跳过，不报错。"""
    from runtime_paths import HOT_PIC_DIR, DATA_DIR, DRAWER_DIR
    from library_config import get_library_roots
    from my_utils import dedup_viewer_data, _viewer_item_key
    # 所有源文件读取都走占用安全 helper：另一进程可能正 os.replace 改名/覆写该文件
    # （双开首启竞争），裸 open() 在 Windows 上会 PermissionError。

    checks, errors = [], []

    def good(name, detail=""):
        checks.append({"name": name, "ok": True, "detail": detail})

    def bad(name, detail):
        checks.append({"name": name, "ok": False, "detail": detail})
        errors.append(f"{name}: {detail}")

    # ---- log.json ----
    log_path = HOT_PIC_DIR / "log.json"
    f = _open_legacy_text(log_path)
    if f is not None:
        with f:
            mapping = json.load(f)
        n_db = log_count()
        if n_db != len(mapping):
            bad("log 计数", f"DB {n_db} vs JSON {len(mapping)}")
        else:
            keys = list(mapping)
            mism = [k for k in (keys[i] for i in _sample_positions(len(keys), kv_sample))
                    if log_get(str(k), None) != (mapping[k] or "")]
            if mism:
                bad("log 抽样", f"{len(mism)}/{min(len(keys), kv_sample)} 条不一致，例: {mism[:3]}")
            else:
                good("log", f"{n_db} 条，抽样 {min(len(keys), kv_sample)} 一致")

    # ---- artist_stats.json ----
    stats_path = HOT_PIC_DIR / "artist_stats.json"
    f = _open_legacy_text(stats_path)
    if f is not None:
        with f:
            mapping = json.load(f) or {}
        n_db = stats_count()
        if n_db != len(mapping):
            bad("stats 计数", f"DB {n_db} vs JSON {len(mapping)}")
        else:
            keys = list(mapping)
            mism = [k for k in (keys[i] for i in _sample_positions(len(keys), kv_sample))
                    if stats_get(k, 0) != int(mapping[k] or 0)]
            if mism:
                bad("stats 抽样", f"{len(mism)} 条不一致，例: {mism[:3]}")
            else:
                good("stats", f"{n_db} 位画师，抽样一致")

    # ---- viewer / ids：只校验当前在线 root（离线盘日后 reconcile 吸收）----
    n_folders = n_rows = 0
    for root in get_library_roots():
        lib_id, root_path = root["id"], root["path"]
        if not root_path.exists() or not root_path.is_dir():
            continue
        for sub in sorted(root_path.iterdir(), key=lambda p: p.name.lower()):
            if not sub.is_dir():
                continue
            folder = sub.name
            viewer_file = sub / "viewer_data.json"
            ids_file = sub / "ids_data.json"
            if viewer_file.exists():
                n_folders += 1
                raw = _load_legacy_json(str(viewer_file), [])
                json_items = dedup_viewer_data(raw if isinstance(raw, list) else [])
                db_items = viewer_load_entries(lib_id, folder)
                n_rows += len(json_items)
                if len(db_items) != len(json_items):
                    bad(f"viewer {lib_id}/{folder} 计数",
                        f"DB {len(db_items)} vs JSON {len(json_items)}")
                    continue
                if json_items:
                    by_key = {_viewer_item_key(it): it for it in db_items}
                    probe_idx = sorted({0, len(json_items) - 1,
                                        * _sample_positions(len(json_items), viewer_sample)})
                    mism = 0
                    for i in probe_idx:
                        src = json_items[i]
                        got = by_key.get(_viewer_item_key(src))
                        if got != src:
                            mism += 1
                    if mism:
                        bad(f"viewer {lib_id}/{folder} 抽样", f"{mism}/{len(probe_idx)} 条不一致")
            if ids_file.exists():
                raw = _load_legacy_json(str(ids_file), [])
                json_ids = sorted({str(x) for x in raw} if isinstance(raw, list) else set())
                db_ids = queue_load(lib_id, folder)  # pending+failed 全集
                if db_ids != json_ids:
                    bad(f"ids {lib_id}/{folder}", f"DB {len(db_ids)} vs JSON {len(json_ids)}")
    good("viewer/ids", f"{n_folders} 个在线目录、{n_rows} 条 viewer 参与校验")

    # ---- drawer 四件套 ----
    drawer_dir = str(DRAWER_DIR)
    txt_path = os.path.join(drawer_dir, "txtdata.txt")
    f_txt = _open_legacy_text(txt_path)
    if f_txt is not None:
        with f_txt:
            json_txt = f_txt.read().split("\n")
        db_txt = drawer_txt_load()
        if db_txt != json_txt:
            bad("drawer txtdata", f"DB {len(db_txt)} 行 vs 文件 {len(json_txt)} 行")
        else:
            good("drawer txtdata", f"{len(db_txt)} 行")
    disk_path = os.path.join(drawer_dir, "disk_drawer.json")
    if os.path.exists(disk_path):
        raw = _load_legacy_json(disk_path, {})
        json_groups = {k: list(v) for k, v in (raw or {}).items() if isinstance(v, list) and v}
        if drawer_disk_load() != json_groups:
            bad("drawer disk", "分组/顺序不一致")
        else:
            good("drawer disk", f"{sum(len(v) for v in json_groups.values())} 项")
    hot_path = os.path.join(drawer_dir, "hot_drawer.txt")
    f_hot = _open_legacy_text(hot_path)
    if f_hot is not None:
        with f_hot:
            json_hot = f_hot.read().split("\n")
        if drawer_hot_load() != json_hot:
            bad("drawer hot", f"DB {len(drawer_hot_load())} 行 vs 文件 {len(json_hot)} 行")
        else:
            good("drawer hot", f"{len(json_hot)} 行")
    nu_path = os.path.join(drawer_dir, "need_update.json")
    if os.path.exists(nu_path):
        raw = _load_legacy_json(nu_path, {})
        json_nu = {k: {str(x) for x in v} for k, v in (raw or {}).items()
                   if isinstance(v, list) and v}
        db_nu = {k: set(v) for k, v in drawer_need_update_load().items() if v}
        if db_nu != json_nu:
            bad("drawer need_update", "分组集合不一致")
        else:
            good("drawer need_update", f"{sum(len(v) for v in json_nu.values())} 项")

    # ---- 三类收藏 ----
    for kind, file_name in (("artist", "artist_favorites.json"),
                            ("character", "character_favorites.json")):
        path = DATA_DIR / file_name
        if not path.exists():
            continue
        raw = _load_legacy_json(str(path), {})
        cleaned = _clean_group_lists(raw if isinstance(raw, dict) else {})
        if fav_group_load(kind) != cleaned:
            bad(f"收藏 {kind}", "分组/顺序不一致")
        else:
            good(f"收藏 {kind}", f"{sum(len(v) for v in cleaned.values())} 项")
    image_path = DATA_DIR / "image_favorites.json"
    if image_path.exists():
        raw = _load_legacy_json(str(image_path), {})
        json_payloads = {str(k): v for k, v in (raw or {}).items()
                         if isinstance(v, dict)} if isinstance(raw, dict) else {}
        db_payloads = fav_image_load_all()
        if len(db_payloads) != len(json_payloads):
            bad("收藏 image 计数", f"DB {len(db_payloads)} vs JSON {len(json_payloads)}")
        else:
            keys = list(json_payloads)
            mism = [k for k in (keys[i] for i in _sample_positions(len(keys), kv_sample))
                    if db_payloads.get(k) != json_payloads[k]]
            if mism:
                bad("收藏 image 抽样", f"{len(mism)} 条 payload 不一致，例: {mism[:3]}")
            else:
                good("收藏 image", f"{len(keys)} 项，抽样一致")

    return {"ok": not errors, "checks": checks, "errors": errors}


def _rename_to_bak(path) -> str | None:
    """log.json → log.json.bak；.bak 已存在则 .bak2/.bak3 递增，绝不覆盖既有备份。

    Windows 上另一进程可能正 open() 着源文件读（validate/导入器），此时 os.replace
    抛 PermissionError——有限重试（5s）；仍失败则把异常交给状态机转 rename_pending。"""
    p = Path(path)
    if not p.exists():
        return None
    last_exc = None
    for attempt in range(50):
        candidate = p.with_name(p.name + ".bak")
        i = 2
        while candidate.exists():
            candidate = p.with_name(f"{p.name}.bak{i}")
            i += 1
        try:
            os.replace(p, candidate)
            return str(candidate)
        except PermissionError as exc:
            # 另一进程的读句柄 / 杀软扫描占用；它读完就释放，等 0.1s 再试
            last_exc = exc
            time.sleep(0.1)
    raise last_exc


_META_LEADER = "import_leader"
_LEADER_STALE_SECS = 600      # leader 超过 10 分钟没动静 = 崩在导入中途，可接管
_LEADER_WAIT_SECS = 300       # 等待另一进程导入的上限（真实库导入预算 20-60s）


def _acquire_import_leader(force: bool) -> bool:
    """跨进程导入领导权。WAL 下 BEGIN IMMEDIATE 把写者串行化，因此「检查+插入」在
    同一写事务里是原子的：双开 app 同时首启时恰好一个拿到、一个等待。

    force（--import --force）无条件接管；现存 leader 超过 _LEADER_STALE_SECS（上个进程
    被强杀在导入中途）也可接管。返回本进程是否成为 leader。"""
    token = json.dumps({"pid": os.getpid(), "started": time.time()})
    with transaction():
        conn = get_conn()
        row = conn.execute("SELECT value FROM meta WHERE key=?", (_META_LEADER,)).fetchone()
        mine = force or row is None
        if not mine:
            try:
                started = float(json.loads(row["value"]).get("started", 0))
                mine = time.time() - started > _LEADER_STALE_SECS
            except (ValueError, TypeError, AttributeError):
                mine = True  # leader 行损坏：接管
        if mine:
            conn.execute(
                "INSERT INTO meta(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_META_LEADER, token),
            )
        return mine


def _release_import_leader() -> None:
    try:
        execute("DELETE FROM meta WHERE key=?", (_META_LEADER,))
    except Exception:
        pass


def _wait_import_leader() -> str:
    """另一进程正在导入：轮询终态。返回观察到的 import_state（可能仍是 importing——
    超时或 leader 消失，调用方选择接管或直接上线）。"""
    deadline = time.time() + _LEADER_WAIT_SECS
    state = meta_get(_META_STATE)
    while time.time() < deadline:
        time.sleep(0.25)
        state = meta_get(_META_STATE)
        if state in ("done", "rename_pending", "validate_failed", "incomplete"):
            return state
        if meta_get(_META_LEADER) is None:
            # leader 已释放但状态未到终态（崩溃）：让调用方决定是否接管
            return state or "absent"
    return state or "importing"


def _finalize_log_stats_renames():
    """校验通过后的收尾改名。返回 (renamed, rename_failed)。leader 路径与等待方
    对 rename_pending 的补改名共用。"""
    from runtime_paths import HOT_PIC_DIR
    renamed, rename_failed = [], []
    for name in ("log.json", "artist_stats.json"):
        try:
            bak = _rename_to_bak(HOT_PIC_DIR / name)
        except PermissionError as exc:
            # Windows：另一个进程正 open() 着该文件（双开 app 的首启竞争）。
            # 数据此时已 100% 入库并通过校验，只剩改名这个表面动作。
            rename_failed.append(name)
            print(f"[deck_db] {name} 改名 .bak 暂时失败（文件被占用：{exc}）；"
                  f"数据已全部入库，下次启动自动重试改名")
            continue
        if bak:
            renamed.append(bak)
            print(f"[deck_db] 校验通过，{name} 已改名为 {os.path.basename(bak)}"
                  f"（需要回滚用 --export-all-legacy-json 重建）")
    return renamed, rename_failed


def _finalize_pending_legacy_rename() -> dict | None:
    """state=done 但自动引导当初只导入没改名（legacy_rename_pending=1）时，
    CLI --import 走这条纯改名收尾：不重跑导入、不重跑 48MB 校验，启动快路径不变。
    没有挂起标志返回 None（调用方按普通 skipped 处理）。"""
    if meta_get(_META_RENAME_PENDING) != "1":
        return None
    if not _acquire_import_leader(force=False):
        state = _wait_import_leader()
        return {"ok": state in ("done", "rename_pending"), "waited": True, "state": state}
    try:
        renamed, rename_failed = _finalize_log_stats_renames()
        if rename_failed:
            # 文件仍被占用：标志留着，下次 --import 再试
            return {"ok": True, "renamed": renamed, "rename_pending": rename_failed}
        try:
            execute("DELETE FROM meta WHERE key=?", (_META_RENAME_PENDING,))
        except Exception:
            pass
        return {"ok": True, "renamed": renamed, "finalized": True}
    finally:
        _release_import_leader()


def bootstrap_legacy(force: bool = False, *, rename_bak: bool = True) -> dict:
    """CLI/外部直调入口（`python deck_db.py --import`）。

    状态机（meta.import_state）：absent/importing → 导四域 → validate →
    done（schema_version=1，ANALYZE + TRUNCATE checkpoint）。校验不过置 validate_failed，
    JSON 原样保留，下次启动只重跑校验（不重复导入），修好文件后 --import --force 重导；
    Windows 改名撞占用置 rename_pending，下次启动自动重试（数据已可用）。
    另一个进程可能正在导入（521k 行持写锁十几秒）：各域独立门控、独立等待。
    自动引导（get_conn 钩子）直接调 _bootstrap_locked，不经本入口。"""
    global _in_bootstrap
    # 重入守卫：导入进行中别的线程再调本函数，直接短路。
    if _in_bootstrap:
        return {"ok": True, "nested": True}
    # 关键：先置位再做任何 SQL。否则第一条 meta_get→get_conn 的自动引导钩子会带默认
    # 参数（rename_bak=True）抢先把整套状态机跑完，调用方的 rename_bak=False 被吞掉。
    _in_bootstrap = True
    try:
        if not force and meta_get(_META_STATE) == "done":
            # 自动引导当初只导入没改名？默认的 CLI --import 在这做纯改名收尾，不重跑导入；
            # 显式 rename_bak=False（测试/特殊调用）则严格早退。
            if rename_bak:
                finalized = _finalize_pending_legacy_rename()
                if finalized is not None:
                    return finalized
            return {"ok": True, "skipped": True}
        return _bootstrap_locked(force=force, rename_bak=rename_bak)
    finally:
        _in_bootstrap = False


def _bootstrap_locked(force: bool = False, *, rename_bak: bool = True) -> dict:
    """真正的导入状态机。get_conn() 自动引导与 CLI 直调都收敛到这里。

    调用方保证进入前已置 _in_bootstrap=True（本函数执行期间的嵌套 get_conn 因此不会
    再触发自动引导），本函数只负责 _bootstrapped。

    双进程同时首启时由 meta.import_leader 选主：leader 跑全套导入/校验/改名，
    waiter 轮询终态后直接上线（绝不在半成品上跑 validate 把状态写成 validate_failed）。"""
    global _bootstrapped
    with _BOOTSTRAP_LOCK:
        if not force and meta_get(_META_STATE) == "done":
            _bootstrapped = True
            return {"ok": True, "skipped": True}

        is_leader = False
        for _attempt in range(2):
            is_leader = _acquire_import_leader(force)
            if is_leader:
                break
            state = _wait_import_leader()
            if state == "done":
                _bootstrapped = True
                return {"ok": True, "waited": True}
            if state in ("validate_failed", "incomplete"):
                # leader 导入/校验失败：DB 里已有部分数据，本进程不重复跑状态机
                _bootstrapped = True
                return {"ok": False, "waited": True, "state": state}
            # rename_pending / leader 崩溃 / 等待超时：再试一次接管（导入幂等）
        try:
            if not is_leader:
                # 二次接管仍拿不到写锁（busy_timeout 耗尽）：不阻断启动，数据按现状上线，
                # 下次启动自愈。
                print("[deck_db] 等待另一进程导入持锁过久，本次以现有数据库状态启动；"
                      "下次启动会自动完成引导")
                return {"ok": True, "deferred": True}

            # ---- 本进程是 leader ----
            # rename_pending 重入：域标志已置位会秒跳，validate 只核现存文件，
            # 然后补改名，正常收敛到 done。
            meta_set(_META_STATE, "importing")

            for meta_key, importer in _IMPORT_DOMAINS:
                _bootstrap_domain(meta_key, importer, force)
            if not all(meta_get(k) == "1" for k, _ in _IMPORT_DOMAINS):
                meta_set(_META_STATE, "incomplete")
                return {"ok": False, "reason": "部分导入域未完成"}

            report = validate_legacy_import()
            try:
                meta_set(_META_LAST_VALIDATION, json.dumps(report, ensure_ascii=False)[:20000])
            except Exception:
                pass
            if not report["ok"]:
                meta_set(_META_STATE, "validate_failed")
                for err in report["errors"][:20]:
                    print(f"[deck_db] 校验失败：{err}")
                print("[deck_db] 旧 JSON 与数据库不一致，已保留全部 JSON 不改名；"
                      "修复文件后运行 python deck_db.py --import --force 重新导入")
                return {"ok": False, "report": report}

            meta_set(_META_SCHEMA, "1")
            renamed, rename_failed = [], []
            if rename_bak:
                renamed, rename_failed = _finalize_log_stats_renames()
            else:
                # 自动引导路径：导入/校验已完成，但按契约保留 log.json/artist_stats.json
                # 不改名（离线兜底 / tests/test_p0_hardening.py 契约）。挂标志，CLI --import 时纯改名收尾。
                from runtime_paths import HOT_PIC_DIR
                pending = [name for name in ("log.json", "artist_stats.json")
                           if (HOT_PIC_DIR / name).exists()]
                if pending:
                    meta_set(_META_RENAME_PENDING, "1")
                    print("[deck_db] 旧 JSON 已全部入库并通过校验；保留原文件未改名。"
                          "确认无误后运行 python deck_db.py --import 收尾改名（回滚用 "
                          "--export-all-legacy-json）")
            if rename_failed:
                # 不进 done：下次启动状态机重跑（域标志已置位会秒跳，validate 再过一遍）
                # 重试改名；本次返回 ok=True——数据完整可用，不阻断启动。
                meta_set(_META_STATE, "rename_pending")
                return {"ok": True, "renamed": renamed, "rename_pending": rename_failed}
            meta_set(_META_STATE, "done")
            try:
                get_conn().execute("ANALYZE")
            except Exception as exc:
                print(f"[deck_db] ANALYZE 失败（不影响使用）: {exc}")
            try:
                checkpoint("TRUNCATE")  # 双开首启时可能撞独占锁，失败无妨，WAL 自愈
            except Exception as exc:
                print(f"[deck_db] checkpoint 失败（不影响使用，下次启动自动恢复）: {exc}")
            return {"ok": True, "renamed": renamed}
        finally:
            # _in_bootstrap 由调用方（get_conn 钩子 / bootstrap_legacy）复位
            _bootstrapped = True
            if is_leader:
                _release_import_leader()


def reset_database():
    """--rebuild：关闭连接并删除 deck.db*，下次连接按空库全新导入。"""
    global _conn, _tx_depth, _bootstrapped, _in_bootstrap
    with _LOCK:
        if _conn is not None:
            try:
                _conn.close()
            except Exception:
                pass
            _conn = None
        _tx_depth = 0
        _bootstrapped = False
        _in_bootstrap = False
        for suffix in ("", "-wal", "-shm"):
            p = str(DECK_DB_PATH) + suffix
            if os.path.exists(p):
                os.remove(p)
                print(f"[deck_db] 已删除 {p}")


def export_all_legacy_json() -> dict:
    """回滚工具：把 DB 全量导回旧 JSON 世界。

    log/stats + drawer 四件 + 三类收藏写到 DATA_DIR；viewer/ids 镜像按库里出现过的
    (library, folder) 导回**当前在线** root 的对应目录（离线盘跳过并报告）。
    已存在的 JSON 会被原子覆盖（建议先手工备份）。"""
    from runtime_paths import HOT_PIC_DIR, DATA_DIR, DRAWER_DIR
    from my_utils import atomic_write_json
    from library_config import get_library_roots

    summary = {"viewer": 0, "ids": 0, "offline_roots": []}

    log_path = HOT_PIC_DIR / "log.json"
    atomic_write_json(str(log_path), log_snapshot_urls())
    summary["log"] = log_count()
    stats_path = HOT_PIC_DIR / "artist_stats.json"
    atomic_write_json(str(stats_path), stats_snapshot())
    summary["stats"] = stats_count()

    roots = {r["id"]: r for r in get_library_roots()}
    pairs = query_all(
        "SELECT DISTINCT library_id, folder FROM viewer_entries "
        "UNION SELECT DISTINCT library_id, folder FROM dl_queue"
    )
    for r in pairs:
        lib_id, folder = r["library_id"], r["folder"]
        root = roots.get(lib_id)
        if root is None or not root["path"].exists() or not root["path"].is_dir():
            summary["offline_roots"].append(f"{lib_id}/{folder}")
            continue
        day_dir = root["path"] / folder
        day_dir.mkdir(parents=True, exist_ok=True)
        if viewer_count(lib_id, folder):
            export_viewer_mirror(lib_id, folder, str(day_dir / "viewer_data.json"))
            summary["viewer"] += 1
        if queue_count(lib_id, folder):
            export_ids_mirror(lib_id, folder, str(day_dir / "ids_data.json"))
            summary["ids"] += 1

    drawer_dir = str(DRAWER_DIR)
    _atomic_write_text(os.path.join(drawer_dir, "txtdata.txt"),
                       "\n".join(drawer_txt_load()))
    disk_groups = drawer_disk_load()
    disk_groups.setdefault("1", [])
    disk_groups.setdefault("2", [])
    atomic_write_json(os.path.join(drawer_dir, "disk_drawer.json"), disk_groups)
    export_hot_drawer_mirror(os.path.join(drawer_dir, "hot_drawer.txt"), drawer_hot_load())
    export_need_update_mirror(os.path.join(drawer_dir, "need_update.json"),
                              drawer_need_update_load())

    export_fav_group_mirror(str(DATA_DIR / "artist_favorites.json"), fav_group_load("artist"))
    export_fav_group_mirror(str(DATA_DIR / "character_favorites.json"),
                            fav_group_load("character"))
    export_fav_image_mirror(str(DATA_DIR / "image_favorites.json"))
    summary["favorites"] = (
        sum(len(v) for v in fav_group_load("artist").values()),
        sum(len(v) for v in fav_group_load("character").values()),
        fav_image_count(),
    )
    return summary


def db_status() -> dict:
    """--status 用：版本、迁移状态、各表行数、WAL 大小。"""
    conn = get_conn()
    tables = (
        "post_log", "viewer_entries", "dl_queue", "failed_pages", "page_cursor",
        "artist_stats",
        "drawer_disk", "drawer_txt", "drawer_hot", "drawer_need_update",
        "fav_artist", "fav_character", "fav_image", "folder_sync",
    )
    counts = {}
    with _LOCK:
        for name in tables:
            counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
    wal_path = str(DECK_DB_PATH) + "-wal"
    return {
        "db_path": str(DECK_DB_PATH),
        "schema_version": meta_get("schema_version"),
        "import_state": meta_get("import_state"),
        "legacy_rename_pending": meta_get(_META_RENAME_PENDING) == "1",
        "counts": counts,
        "wal_bytes": os.path.getsize(wal_path) if os.path.exists(wal_path) else 0,
    }


# ------------------------------------------------------------------ CLI -------
def _main(argv=None):
    global AUTO_BOOTSTRAP
    parser = argparse.ArgumentParser(description="Danbooru Deck SQLite 元数据库工具")
    parser.add_argument("--status", action="store_true", help="打印库状态与各表行数（不触发导入）")
    parser.add_argument("--checkpoint", action="store_true", help="TRUNCATE checkpoint 后退出")
    parser.add_argument("--import", dest="do_import", action="store_true",
                        help="从旧 JSON 引导导入（幂等，已导入的域自动跳过；校验通过后 log/stats 改名 .bak）")
    parser.add_argument("--force", action="store_true", help="配合 --import 强制重跑已完成的域")
    parser.add_argument("--rebuild", action="store_true",
                        help="删除 deck.db* 后全新导入（旧 JSON 必须还在；等价回滚后重建）")
    parser.add_argument("--dry-run", dest="dry_run", action="store_true",
                        help="只读校验：DB vs 现存旧 JSON 的计数与抽样一致性报告，不写库不改名")
    parser.add_argument("--export-all-legacy-json", dest="export_json", action="store_true",
                        help="回滚用：把 DB 全量导回 log/stats/viewer/ids/drawer/收藏 JSON")
    args = parser.parse_args(argv)

    did_something = False
    if args.status:
        AUTO_BOOTSTRAP = False  # 检查状态不做长导入
        print(json.dumps(db_status(), ensure_ascii=False, indent=2))
        did_something = True
    if args.dry_run:
        AUTO_BOOTSTRAP = False  # 纯只读，不借 get_conn 触发自动导入
        get_conn()  # 建表/连库即可（不导数据）
        report = validate_legacy_import()
        for c in report["checks"]:
            print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['name']} {c['detail']}")
        print("=" * 60)
        print("校验通过" if report["ok"] else f"校验失败 {len(report['errors'])} 项")
        did_something = True
        if not report["ok"]:
            raise SystemExit(1)
    if args.rebuild:
        reset_database()
        result = bootstrap_legacy(force=True)
        print(json.dumps({"bootstrap": result, "status": db_status()},
                         ensure_ascii=False, indent=2))
        did_something = True
    if args.do_import:
        result = bootstrap_legacy(force=args.force)
        print(json.dumps({"bootstrap": result, "status": db_status()},
                         ensure_ascii=False, indent=2))
        did_something = True
    if args.export_json:
        summary = export_all_legacy_json()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        did_something = True
    if args.checkpoint:
        checkpoint("TRUNCATE")
        print("checkpoint TRUNCATE done")
        did_something = True
    if not did_something:
        parser.print_help()


if __name__ == "__main__":
    _main()
