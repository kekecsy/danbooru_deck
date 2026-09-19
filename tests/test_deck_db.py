# tests/test_deck_db.py
# P1 SQLite 迁移（deck.db）的离线回归测试：无外部网络依赖，全部在临时数据目录里跑。
# 运行（仓库根目录）：.venv/Scripts/python.exe tests/test_deck_db.py
#
# 覆盖六类用例（对应计划阶段 7）：
#   ① 小全集导入器幂等：bootstrap 两遍（done 早退）+ --force 语义重跑，行数不变
#   ② 镜像 ↔ DB 等价：viewer/ids 编解码还原、适配器 save/load 往返、failed 状态镜像语义
#   ③ 两个以上独立 python 进程并发 stats 自增 / queue_add / log 写
#      （旧 JSON「启动快照整盘覆盖」丢计数 bug 的回归测试）
#   ④ viewer 两套唯一键：post_url 重复 vs 同 filename 不同 web_url vs post_url='#'
#   ⑤ reconcile：手工改镜像后 load 立即可见新行/删除行；指纹未变不重复吸收
#   ⑥ 回滚演练：export-all → 删库 → 从导出 JSON 重新引导，内容一致、JSON 路径全可读
#   外加：两个进程同时对一份全新 JSON 首连（bootstrap 跨进程锁竞争）。
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

# Windows 控制台默认 GBK，输出含 ↔ 等符号会 UnicodeEncodeError；统一切 UTF-8
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# 脚本在 tests/ 子目录里运行，需把仓库根目录放进 sys.path 才能 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 指向临时数据目录（必须在 import 项目模块之前设置；runtime_paths 在 import 期读 env）
_TMP_DATA = tempfile.mkdtemp(prefix="danbooru_db_test_")
_TMP_RACE = tempfile.mkdtemp(prefix="danbooru_db_race_")
os.environ["DANBOORU_DECK_DATA_DIR"] = _TMP_DATA

import deck_db
from my_utils import atomic_write_json, dedup_viewer_data
from danbooru_data import DanbooruData

# 仓库根（tests/ 的上一级）：子进程 worker 以它为 cwd 才能 import 项目模块
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


# ------------------------------------------------------------ 小全集 fixture ----
DAY1 = "2026-09-01"
DAY2 = "2026-09-02"


def _tags(general="1girl", character="hatsune_miku", copyright_="vocaloid",
          artist="artist_a", **extra):
    tags = {
        "tag_string_general": general,
        "tag_string_character": character,
        "tag_string_copyright": copyright_,
        "tag_string_artist": artist,
    }
    tags.update(extra)
    return tags


def _entry(fname, post, web, *, artist="artist_a", score=10, fav=1, tags=None, **extra_top):
    item = {
        "artist": artist,
        "filename": fname,
        "local_path": f"hot_pic/{DAY1}/{fname}",
        "post_url": post,
        "score": score,
        "fav_count": fav,
        "tags": tags or _tags(),
    }
    if web is not None:
        item["web_url"] = web
    item.update(extra_top)
    return item


def _canon(items):
    """viewer 条目集合的规范化比较形式（顺序无关，键排序后比较）。"""
    return sorted(
        json.dumps(it, ensure_ascii=False, sort_keys=True)
        for it in dedup_viewer_data(items)
    )


def build_fixture(data_dir):
    """在空数据目录里布置一套「小全集」：log/stats + 两天 viewer/ids + drawer + 收藏。"""
    hot = os.path.join(data_dir, "hot_pic")
    d1 = os.path.join(hot, DAY1)
    d2 = os.path.join(hot, DAY2)
    drawer = os.path.join(data_dir, "drawer")
    os.makedirs(d1, exist_ok=True)
    os.makedirs(d2, exist_ok=True)
    os.makedirs(drawer, exist_ok=True)

    # log：10 条 pid→url；stats：5 位画师
    atomic_write_json(os.path.join(hot, "log.json"),
                      {str(1000 + i): f"https://cdn.donmai.us/original/{i:02d}/a{i}.jpg"
                       for i in range(10)})
    atomic_write_json(os.path.join(hot, "artist_stats.json"),
                      {"artist_a": 12, "artist_b": 7, "artist_c": 3, "artist_d": 1,
                       "artist_e": 0})

    # day1：3 条，含一个老格式条目（无 web_url 键）、一个带 rating/md5/meta/杂键的富条目
    day1_items = [
        _entry("a01.jpg", "https://danbooru.donmai.us/posts/1001",
               "https://cdn.donmai.us/original/a01.jpg"),
        _entry("a02.jpg", "https://danbooru.donmai.us/posts/1002",
               None,  # 2026-03 前的老条目没有 web_url 键，NULL=键缺失，必须逐字节保真
               tags=_tags(character="", copyright_="original")),
        _entry("a03.jpg", "https://danbooru.donmai.us/posts/1003",
               "https://cdn.donmai.us/original/a03.jpg",
               score=0,
               tags=_tags(general="solo long_hair",
                          tag_string_meta="highres", tag_string="tagme",
                          rating="q", md5="deadbeef",
                          tag_string_weird_unknown="保活"),
               file_ext="jpg",  # entry 顶层未知键 → entry_extra 往返
               ),
    ]
    atomic_write_json(os.path.join(d1, "viewer_data.json"), day1_items)
    atomic_write_json(os.path.join(d1, "ids_data.json"), ["1001", "1002", "1003"])

    # day2：2 条，其中一条 post_url='#'（走 filename+web_url 唯一键分支）
    day2_items = [
        _entry("b01.jpg", "https://danbooru.donmai.us/posts/2001",
               "https://cdn.donmai.us/original/b01.jpg"),
        _entry("b02.jpg", "#", "https://cdn.donmai.us/original/b02.jpg"),
    ]
    day2_items[1]["local_path"] = f"hot_pic/{DAY2}/b02.jpg"
    atomic_write_json(os.path.join(d2, "viewer_data.json"), day2_items)
    atomic_write_json(os.path.join(d2, "ids_data.json"), ["2001", "2002"])

    # drawer 四件套
    with open(os.path.join(drawer, "txtdata.txt"), "w", encoding="utf-8") as f:
        f.write("artist_a\nartist_b")
    atomic_write_json(os.path.join(drawer, "disk_drawer.json"),
                      {"1": ["artist_a"], "2": ["artist_b"]})
    with open(os.path.join(drawer, "hot_drawer.txt"), "w", encoding="utf-8") as f:
        f.write("artist_a\nartist_c")
    atomic_write_json(os.path.join(drawer, "need_update.json"),
                      {"1": ["artist_x"], "2": ["artist_y"]})

    # 三类收藏
    atomic_write_json(os.path.join(data_dir, "artist_favorites.json"),
                      {"group1": ["artist_a", "artist_b"]})
    atomic_write_json(os.path.join(data_dir, "character_favorites.json"),
                      {"default": ["hatsune_miku"]})
    atomic_write_json(os.path.join(data_dir, "image_favorites.json"), {
        f"{DAY1}/a01.jpg": {
            "added_at": 1700000000, "date": DAY1, "filename": "a01.jpg",
            "library_id": "default", "caption_en": "a girl",
        },
        f"{DAY2}/b02.jpg": {
            "added_at": 1700000001, "date": DAY2, "filename": "b02.jpg",
            "library_id": "default",
        },
    })
    return day1_items, day2_items


# ------------------------------------------------------------ ① 导入幂等 ----
def test_import_idempotent(day1_items, day2_items):
    section("① 导入器幂等（bootstrap ×2 + force）")
    r1 = deck_db.bootstrap_legacy(rename_bak=False)  # 单测里保留 JSON，后续用例还要读
    check("首次 bootstrap 成功", r1.get("ok") is True and not r1.get("skipped"),
          str(r1)[:200])
    check("import_state=done", deck_db.meta_get("import_state") == "done")
    check("schema_version=1", deck_db.meta_get("schema_version") == "1")

    counts = (deck_db.log_count(), deck_db.stats_count(),
              deck_db.viewer_count("default", DAY1), deck_db.viewer_count("default", DAY2),
              deck_db.queue_count("default", DAY1), deck_db.queue_count("default", DAY2),
              deck_db.fav_image_count())
    check("行数：log 10 / stats 5 / viewer 3+2 / queue 3+2 / fav_image 2",
          counts == (10, 5, 3, 2, 3, 2, 2), str(counts))

    r2 = deck_db.bootstrap_legacy(rename_bak=False)
    check("第二次 bootstrap 走 done 早退", r2.get("ok") and r2.get("skipped"), str(r2)[:200])

    deck_db.bootstrap_legacy(force=True, rename_bak=False)
    counts2 = (deck_db.log_count(), deck_db.stats_count(),
               deck_db.viewer_count("default", DAY1), deck_db.viewer_count("default", DAY2),
               deck_db.queue_count("default", DAY1), deck_db.queue_count("default", DAY2),
               deck_db.fav_image_count())
    check("force 重跑后行数不变", counts2 == counts, f"{counts} vs {counts2}")

    report = deck_db.validate_legacy_import()
    check("全量只读校验通过（JSON 全在）", report["ok"],
          "; ".join(report["errors"])[:300])


# ①b：app 首启自动引导（只导入不改名）→ CLI --import 纯改名收尾（子进程，全新目录）
_AUTO_RENAME_WORKER = r"""
import json, os
import deck_db
hot = os.path.join(os.environ["DANBOORU_DECK_DATA_DIR"], "hot_pic")
log_json = os.path.join(hot, "log.json")
stats_json = os.path.join(hot, "artist_stats.json")

# 阶段 1：任何首次 DB 访问触发的自动引导——只导入/校验，绝不改名
deck_db.get_conn()
phase1 = {
    "state": deck_db.meta_get("import_state"),
    "flag": deck_db.meta_get("legacy_rename_pending"),
    "log_json_exists": os.path.exists(log_json),
    "stats_json_exists": os.path.exists(stats_json),
    "log_count": deck_db.log_count(),
}
# 阶段 2：显式 CLI `python deck_db.py --import`（默认 rename_bak=True）做纯改名收尾
r2 = deck_db.bootstrap_legacy()
phase2 = {
    "result": r2,
    "state": deck_db.meta_get("import_state"),
    "flag": deck_db.meta_get("legacy_rename_pending"),
    "log_json_exists": os.path.exists(log_json),
    "log_bak_exists": os.path.exists(log_json + ".bak"),
    "stats_bak_exists": os.path.exists(stats_json + ".bak"),
    "log_count": deck_db.log_count(),
}
# 阶段 3：再跑一次 --import：没有挂起标志 → skipped，不报错
phase3 = deck_db.bootstrap_legacy()
print(json.dumps({"phase1": phase1, "phase2": phase2, "phase3": phase3}))
"""


def test_auto_import_then_cli_rename():
    section("①b 自动引导只导入不改名 → CLI --import 纯改名收尾")
    d = tempfile.mkdtemp(prefix="danbooru_db_auto_")
    try:
        build_fixture(d)
        env = os.environ.copy()
        env["DANBOORU_DECK_DATA_DIR"] = d
        p = subprocess.run([sys.executable, "-c", _AUTO_RENAME_WORKER], cwd=REPO_ROOT, env=env,
                           capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=180)
        if p.returncode != 0:
            check("子进程退出码 0", False, p.stderr[-600:])
            return
        line = next((ln for ln in p.stdout.splitlines() if ln.strip().startswith("{")), "")
        try:
            out = json.loads(line)
        except ValueError:
            check("子进程输出可解析", False, p.stdout[-300:])
            return
        p1, p2, p3 = out["phase1"], out["phase2"], out["phase3"]
        check("自动引导后 state=done", p1["state"] == "done", str(p1))
        check("自动引导后挂起标志=1", p1["flag"] == "1", str(p1))
        check("自动引导不改名（log/stats JSON 都在）",
              p1["log_json_exists"] and p1["stats_json_exists"], str(p1))
        check("自动引导数据已入库（log 10 条）", p1["log_count"] == 10, str(p1))
        check("CLI --import 改名收尾成功",
              p2["result"].get("ok") and p2["result"].get("finalized")
              and len(p2["result"].get("renamed", [])) == 2, str(p2["result"]))
        check("收尾后 state=done 且标志清除",
              p2["state"] == "done" and p2["flag"] is None, str(p2))
        check("收尾后 JSON 消失、.bak 存在、数据仍在",
              not p2["log_json_exists"] and p2["log_bak_exists"]
              and p2["stats_bak_exists"] and p2["log_count"] == 10, str(p2))
        check("再次 --import 走 skipped",
              p3.get("ok") and p3.get("skipped"), str(p3))
    finally:
        shutil.rmtree(d, ignore_errors=True)


# ------------------------------------------------- ② 镜像 ↔ DB 等价/适配器 ----
def test_mirror_equivalence(day1_items, day2_items):
    section("② 镜像 ↔ DB 等价 + DanbooruData 适配器往返")
    # 导入器不重写镜像：DB 重组结果必须与原始 fixture JSON 逐条 dict 相等
    db_day1 = deck_db.viewer_load_entries("default", DAY1)
    check("viewer day1 编解码还原（含无 web_url/空串/rating/md5/杂键）",
          _canon(db_day1) == _canon(day1_items))
    # 无 web_url 的老条目必须真的没有这个键（NULL=键缺失），且空串不丢
    old = next(it for it in db_day1 if it["filename"] == "a02.jpg")
    check("老条目 web_url 键不存在", "web_url" not in old)
    check("空 character 串保留", old["tags"]["tag_string_character"] == "")
    rich = next(it for it in db_day1 if it["filename"] == "a03.jpg")
    check("富条目 rating/md5/meta/未知 tag/未知 entry 键全保留",
          rich["tags"].get("rating") == "q" and rich["tags"].get("md5") == "deadbeef"
          and rich["tags"].get("tag_string_meta") == "highres"
          and rich["tags"].get("tag_string_weird_unknown") == "保活"
          and rich.get("file_ext") == "jpg"
          and "tagme" == rich["tags"].get("tag_string"),
          json.dumps(rich, ensure_ascii=False)[:300])

    db_day2 = deck_db.viewer_load_entries("default", DAY2)
    check("viewer day2 编解码还原（含 post_url='#'）",
          _canon(db_day2) == _canon(day2_items))

    with open(os.path.join(_TMP_DATA, "hot_pic", DAY1, "ids_data.json"),
              "r", encoding="utf-8") as f:
        mirror_ids = json.load(f)
    check("ids 镜像 == DB（排序集合）",
          deck_db.queue_load("default", DAY1) == sorted(set(mirror_ids)))

    # DanbooruData 适配器：viewer save/load 往返 + 镜像同步导出
    dd = DanbooruData(DAY1)
    items = dd.load_viewer_data()
    new_item = _entry("a04.jpg", "https://danbooru.donmai.us/posts/1004",
                      "https://cdn.donmai.us/original/a04.jpg")
    dd.save_viewer_data(items + [new_item])
    check("save_viewer_data 后 DB 4 条", deck_db.viewer_count("default", DAY1) == 4)
    with open(dd._viewer_mirror_path, "r", encoding="utf-8") as f:
        mirror = json.load(f)
    check("save_viewer_data 后镜像也是 4 条且等价",
          len(mirror) == 4 and _canon(mirror) == _canon(items + [new_item]))
    # 新实例（模拟重启）：指纹一致不吸收，读到 4 条
    dd2 = DanbooruData(DAY1)
    check("新实例 load_viewer_data 读到 4 条", len(dd2.load_viewer_data()) == 4)

    # ids 队列：failed 状态留在全集镜像里（旧契约「失败 id 留在文件」），
    # 且 failed 行即使仍出现在 save 的全集中也不被翻回 pending
    dd.save_ids_data(["7000", "7001", "1001"])
    dd.queue_mark_failed_id("7000")
    dd.queue_mark_failed_id("1001")
    dd.save_ids_data(["7000", "7001", "1001"])  # 全集覆写不得重置 failed 状态
    dd.queue_export_mirror()
    all_ids = deck_db.queue_load("default", DAY1)
    pending = deck_db.queue_load("default", DAY1, status=deck_db.QUEUE_PENDING)
    failed = deck_db.queue_load("default", DAY1, status=deck_db.QUEUE_FAILED)
    check("queue failed/pending 状态正确（failed 行在全集覆写后保持）",
          sorted(failed) == ["1001", "7000"] and pending == ["7001"] and len(all_ids) == 3,
          f"failed={failed} pending={pending} all={all_ids}")
    with open(dd._ids_mirror_path, "r", encoding="utf-8") as f:
        m_ids = json.load(f)
    check("ids 镜像 = pending+failed 排序全集", m_ids == all_ids == ["1001", "7000", "7001"])

    # drawer 适配器往返（need_update / hot_drawer 镜像）
    dd.save_need_update({"1": {"artist_x", "artist_z"}, "2": {"artist_y"}})
    nu = dd.load_need_update()
    check("load_need_update 恒为 {'1','2'} 两个 set 键且内容往返",
          set(nu.keys()) == {"1", "2"} and nu["1"] == {"artist_x", "artist_z"}
          and nu["2"] == {"artist_y"}, str(nu))
    dd.save_hot_drawer(["artist_c", "artist_a", "artist_q"])
    check("hot_drawer 往返保序", dd.load_hot_drawer() == ["artist_c", "artist_a", "artist_q"])


# ------------------------------------------- ④ viewer 两套唯一键冲突语义 ----
def test_viewer_unique_keys():
    section("④ viewer 两套唯一键（post_url 去重 / filename+web_url 去重 / '#'）")
    dd = DanbooruData(DAY2)

    def replace_and_filenames(items):
        dd.save_viewer_data(items)
        rows = deck_db.viewer_load_entries("default", DAY2)
        return len(rows), [it["filename"] for it in rows]

    # A：正常 post_url 重复（哪怕 filename 不同）→ 按 post 去重，保留首条
    n, names = replace_and_filenames([
        _entry("fA1.jpg", "https://danbooru.donmai.us/posts/U1", "https://x/fA1.jpg"),
        _entry("fA2.jpg", "https://danbooru.donmai.us/posts/U1", "https://x/fA2.jpg"),
    ])
    check("A: 同 post_url 两条 → 1 行（保留首条）", n == 1 and names == ["fA1.jpg"], str(names))

    # B：无 post_url，同 filename 不同 web_url → fn 键含 web_url，2 行
    n, names = replace_and_filenames([
        _entry("fC.jpg", None, "https://x/1.jpg"),
        _entry("fC.jpg", None, "https://x/2.jpg"),
    ])
    check("B: 无 post_url + 同 filename 不同 web_url → 2 行", n == 2, str(n))

    # C：post_url='#' + 同 filename 同 web_url → 1 行
    n, names = replace_and_filenames([
        _entry("fD.jpg", "#", "https://x/3.jpg"),
        _entry("fD.jpg", "#", "https://x/3.jpg"),
    ])
    check("C: post_url='#' 完全同键 → 1 行", n == 1, str(n))

    # D：post_url 不同但 filename/web_url 都相同 → post 分支，2 行
    n, names = replace_and_filenames([
        _entry("fE.jpg", "https://danbooru.donmai.us/posts/U2", "https://x/4.jpg"),
        _entry("fE.jpg", "https://danbooru.donmai.us/posts/U3", "https://x/4.jpg"),
    ])
    check("D: 不同 post_url + 同 filename/web_url → 2 行", n == 2, str(n))


# ------------------------------------------------- ⑤ 镜像外部改动 reconcile ----
def test_reconcile():
    section("⑤ reconcile：手工改镜像后 load 可见")
    dd = DanbooruData(DAY2)
    # 当前 day2 是用例④的 D 组（2 行）；手工往镜像追加一条
    mirror = dd._viewer_mirror_path
    with open(mirror, "r", encoding="utf-8") as f:
        items = json.load(f)
    extra = _entry("fF.jpg", "https://danbooru.donmai.us/posts/U4", "https://x/5.jpg")
    extra["local_path"] = f"hot_pic/{DAY2}/fF.jpg"
    time.sleep(0.02)
    atomic_write_json(mirror, items + [extra])
    loaded = dd.load_viewer_data()
    check("镜像手工追加后 load 出现新行",
          len(loaded) == 3 and any(it["filename"] == "fF.jpg" for it in loaded),
          f"{len(loaded)} 行")
    check("DB 也被吸收为 3 行", deck_db.viewer_count("default", DAY2) == 3)
    # 指纹已登记：同样内容再 load 不重复吸收
    check("指纹一致时 reconcile 返回 False",
          deck_db.reconcile_viewer("default", DAY2, mirror) is False)

    # 手工把镜像改成只剩 1 条（外置盘他机删除场景）：DB 按镜像 replace
    time.sleep(0.02)
    atomic_write_json(mirror, [items[0]])
    loaded = dd.load_viewer_data()
    check("镜像手工缩短后 DB 跟随替换为 1 行",
          len(loaded) == 1 and loaded[0]["filename"] == "fE.jpg",
          f"{[it['filename'] for it in loaded]}")

    # ids：手工加一个 id，load 可见
    ids_mirror = dd._ids_mirror_path
    with open(ids_mirror, "r", encoding="utf-8") as f:
        ids = json.load(f)
    time.sleep(0.02)
    atomic_write_json(ids_mirror, sorted(set(ids) | {"55555"}))
    got_ids = dd.load_ids_data()
    check("ids 镜像手工追加后 load 可见", "55555" in got_ids, str(got_ids))
    check("ids 指纹一致时不重复吸收",
          deck_db.reconcile_ids("default", DAY2, ids_mirror) is False)


# --------------------- ③ 跨进程并发：stats 自增 / queue_add / log 点写 --------
_CONCURRENCY_WORKER = r"""
import sys
from danbooru_data import DanbooruData
idx = int(sys.argv[1]); n = int(sys.argv[2]); artist = sys.argv[3]
d = DanbooruData("2026-09-01")
for j in range(n):
    st = d.artist_stats
    st[artist] = st.get(artist, 0) + 1            # 旧 JSON 时代：读快照→整盘覆盖，必丢计数
    d.log_data[f"log-{idx}-{j}"] = f"http://x/{idx}/{j}.jpg"
    d.queue_add_id(f"p{idx}-{j:04d}")
print("worker ok")
"""


def test_cross_process_concurrency():
    section("③ 跨进程并发（3 进程 × 15 轮 stats/queue/log）")
    n_proc, n_each, artist = 3, 15, "concurrent_artist"
    procs = []
    for i in range(n_proc):
        p = subprocess.Popen(
            [sys.executable, "-c", _CONCURRENCY_WORKER, str(i), str(n_each), artist],
            cwd=REPO_ROOT, env=os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        procs.append(p)
    bad = []
    for i, p in enumerate(procs):
        out, err = p.communicate(timeout=180)
        if p.returncode != 0:
            bad.append(f"worker {i} rc={p.returncode}\n{err[-800:]}")
    check("3 个并发子进程全部退出码 0", not bad, "\n".join(bad))

    expected = n_proc * n_each
    check(f"stats 自增无一丢失（{n_proc}×{n_each}={expected}）",
          deck_db.stats_get(artist) == expected,
          f"DB={deck_db.stats_get(artist)}")
    # 每进程的 log 键互不覆盖
    missing = [f"log-{i}-{j}" for i in range(n_proc) for j in range(n_each)
               if deck_db.log_get(f"log-{i}-{j}", None) != f"http://x/{i}/{j}.jpg"]
    check("log 点写 45 键全部可见且 URL 正确", not missing, str(missing[:5]))

    q = deck_db.queue_load("default", DAY1)
    expected_ids = {f"p{i}-{j:04d}" for i in range(n_proc) for j in range(n_each)}
    check("queue 45 个新 id 全部在库（与原有 failed/pending 共存）",
          expected_ids <= set(q), f"缺 {len(expected_ids - set(q))} 个")

    # 镜像最后由本进程按 DB 全量重导一次，必须逐字等于 DB 全集（派生镜像可再同步）
    dd = DanbooruData(DAY1)
    dd.queue_export_mirror()
    with open(dd._ids_mirror_path, "r", encoding="utf-8") as f:
        mirror_ids = json.load(f)
    check("父进程重导后 ids 镜像 == DB 全集", mirror_ids == q,
          f"mirror {len(mirror_ids)} vs db {len(q)}")


# --------------------- 跨进程 bootstrap 竞争（全新库，两个进程同时首连） -------
_RACE_WORKER = r"""
import json
import deck_db
deck_db.get_conn()  # AUTO_BOOTSTRAP：两进程同时从同一份 JSON 导入
print(json.dumps({
    "state": deck_db.meta_get("import_state"),
    "log": deck_db.log_count(),
    "stats": deck_db.stats_count(),
    "viewer1": deck_db.viewer_count("default", "2026-09-01"),
}))
"""


def test_bootstrap_race():
    section("附加：跨进程 bootstrap 竞争（全新临时库，2 进程同时首连）")
    build_fixture(_TMP_RACE)
    race_env = os.environ.copy()
    race_env["DANBOORU_DECK_DATA_DIR"] = _TMP_RACE
    procs = [
        subprocess.Popen([sys.executable, "-c", _RACE_WORKER], cwd=REPO_ROOT, env=race_env,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, encoding="utf-8", errors="replace")
        for _ in range(2)
    ]
    results, bad = [], []
    for i, p in enumerate(procs):
        out, err = p.communicate(timeout=180)
        if p.returncode != 0:
            bad.append(f"race worker {i} rc={p.returncode}\n{err[-800:]}")
            continue
        line = next((ln for ln in out.splitlines() if ln.strip().startswith("{")), "")
        try:
            results.append(json.loads(line))
        except ValueError:
            bad.append(f"race worker {i} 输出无法解析: {out[-300:]}")
    check("两个首连进程退出码 0", not bad, "\n".join(bad))
    check("两进程都收敛到 import_state=done",
          len(results) == 2 and all(r.get("state") == "done" for r in results),
          str(results))
    check("两进程看到的行数都等于 fixture（log 10 / viewer day1 3）",
          all(r.get("log") == 10 and r.get("viewer1") == 3 and r.get("stats") == 5
              for r in results),
          str(results))


# ------------------------------------------------- ⑥ 回滚演练 ----------------
def test_rollback_drill():
    section("⑥ 回滚演练：export → 删库 → 从 JSON 重新引导")
    hot = os.path.join(_TMP_DATA, "hot_pic")
    log_json = os.path.join(hot, "log.json")
    stats_json = os.path.join(hot, "artist_stats.json")

    # 模拟真实迁移后的现场：log/stats JSON 已改名消失，库持续接收新写入
    for p in (log_json, stats_json):
        if os.path.exists(p):
            os.remove(p)
    deck_db.stats_inc("rollback_artist", 3)
    deck_db.log_record("rb-1", "http://rb/1.jpg")
    dd1 = DanbooruData(DAY1)
    items1 = dd1.load_viewer_data()
    rb_item = _entry("a99.jpg", "https://danbooru.donmai.us/posts/9999",
                     "https://cdn.donmai.us/original/a99.jpg")
    dd1.save_viewer_data(items1 + [rb_item])
    dd1.queue_add_id("90001")

    # 导出前快照（回滚后必须全部回来）
    pre = {
        "log": deck_db.log_snapshot_urls(),
        "stats": deck_db.stats_snapshot(),
        "viewer1": _canon(deck_db.viewer_load_entries("default", DAY1)),
        "viewer2": _canon(deck_db.viewer_load_entries("default", DAY2)),
        "ids1": set(deck_db.queue_load("default", DAY1)),
        "ids2": set(deck_db.queue_load("default", DAY2)),
        "hot": deck_db.drawer_hot_load(),
        "txt": deck_db.drawer_txt_load(),
        "disk": deck_db.drawer_disk_load(),
        "nu": deck_db.drawer_need_update_load(),
        "fav_img": deck_db.fav_image_load_all(),
        "fav_artist": deck_db.fav_group_load("artist"),
    }

    summary = deck_db.export_all_legacy_json()
    check("export 重建 log.json/artist_stats.json",
          os.path.exists(log_json) and os.path.exists(stats_json), str(summary))
    check("export 的 viewer/ids 镜像文件数 >= 2",
          summary.get("viewer", 0) >= 2 and summary.get("ids", 0) >= 2, str(summary))
    # 导出内容 == DB 快照
    with open(log_json, "r", encoding="utf-8") as f:
        exported_log = json.load(f)
    with open(stats_json, "r", encoding="utf-8") as f:
        exported_stats = json.load(f)
    check("导出 log/stats 内容与 DB 一致",
          exported_log == pre["log"] and exported_stats == pre["stats"])

    # 删库（= 用户回滚动作），再从导出的 JSON 全新引导
    deck_db.reset_database()
    r = deck_db.bootstrap_legacy(force=True, rename_bak=False)
    check("删库后重新 bootstrap 成功", r.get("ok"), str(r)[:200])
    check("重导后 import_state=done", deck_db.meta_get("import_state") == "done")

    check("重导 log 一致", deck_db.log_snapshot_urls() == pre["log"])
    check("重导 stats 一致", deck_db.stats_snapshot() == pre["stats"])
    check("重导 viewer day1 一致",
          _canon(deck_db.viewer_load_entries("default", DAY1)) == pre["viewer1"])
    check("重导 viewer day2 一致",
          _canon(deck_db.viewer_load_entries("default", DAY2)) == pre["viewer2"])
    # 注意：JSON 镜像不编码 pending/failed 状态，回滚后状态统一为 pending——
    # 契约只保证 id 集合不丢（与旧 ids_data.json 信息含量一致）。
    check("重导 queue id 集合一致（状态不持久是镜像本身的信息上限）",
          set(deck_db.queue_load("default", DAY1)) == pre["ids1"]
          and set(deck_db.queue_load("default", DAY2)) == pre["ids2"])
    check("重导 drawer 四件一致",
          deck_db.drawer_hot_load() == pre["hot"]
          and deck_db.drawer_txt_load() == pre["txt"]
          and deck_db.drawer_disk_load() == pre["disk"]
          and deck_db.drawer_need_update_load() == pre["nu"])
    check("重导收藏一致",
          deck_db.fav_image_load_all() == pre["fav_img"]
          and deck_db.fav_group_load("artist") == pre["fav_artist"])

    # “JSON 路径全可读”：不连库，直接 open/json.load 全部导出文件
    json_paths = [
        log_json, stats_json,
        os.path.join(hot, DAY1, "viewer_data.json"),
        os.path.join(hot, DAY2, "viewer_data.json"),
        os.path.join(hot, DAY1, "ids_data.json"),
        os.path.join(hot, DAY2, "ids_data.json"),
        os.path.join(_TMP_DATA, "drawer", "disk_drawer.json"),
        os.path.join(_TMP_DATA, "drawer", "need_update.json"),
        os.path.join(_TMP_DATA, "artist_favorites.json"),
        os.path.join(_TMP_DATA, "character_favorites.json"),
        os.path.join(_TMP_DATA, "image_favorites.json"),
    ]
    unreadable = []
    for p in json_paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                json.load(f)
        except Exception as exc:
            unreadable.append(f"{p}: {exc}")
    txt_paths = [
        os.path.join(_TMP_DATA, "drawer", "txtdata.txt"),
        os.path.join(_TMP_DATA, "drawer", "hot_drawer.txt"),
    ]
    for p in txt_paths:
        try:
            with open(p, "r", encoding="utf-8") as f:
                f.read()
        except Exception as exc:
            unreadable.append(f"{p}: {exc}")
    check("回滚后全部旧 JSON 路径可直接读（Electron 离线兜底可用）", not unreadable,
          "; ".join(unreadable))

    report = deck_db.validate_legacy_import()
    check("回滚重导后只读校验通过", report["ok"], "; ".join(report["errors"])[:300])
    deck_db.checkpoint("TRUNCATE")


def test_page_cursor_and_failed_scope():
    """⑦ 断点续跑底座：page_cursor upsert/隔离/清理 + failed_pages 作用域点查。"""
    section("⑦ page_cursor 断点与 failed_pages 作用域")
    f1, f2 = "2026-09-10", "tag_hatsune__miku"

    # 全新 scope：没有游标 / 失败页
    check("无游标时 cursor_load 返回 None", deck_db.cursor_load(f1) is None)
    check("无失败页时 scope 为空", deck_db.failed_page_load_scope(f1) == [])

    # 第 10 页撞限流停止：游标落在 10，原范围 1-50 保留
    deck_db.cursor_save(f1, "rank", 10, 1, 50)
    c = deck_db.cursor_load(f1)
    check("cursor_save/load 往返（page=10, range=1-50, mode=rank）",
          c == {"folder": f1, "mode": "rank", "page": 10,
                "start_page": 1, "end_page": 50, "updated_at": c["updated_at"]},
          str(c))

    # 续跑到第 32 页又停：同 scope upsert，不新增行
    deck_db.cursor_save(f1, "rank", 32, 1, 50)
    c2 = deck_db.cursor_load(f1)
    check("同 scope 再保存为 upsert（page 更新为 32，仍只有一行）",
          c2 and c2["page"] == 32 and c2["end_page"] == 50, str(c2))

    # 永久错误跳过的页落 failed_pages，与游标共存、作用域隔离
    deck_db.failed_page_add(f1, 40)
    deck_db.failed_page_add(f1, 40)  # INSERT OR IGNORE 幂等
    deck_db.failed_page_add(f1, 41)
    scope1 = [r["page"] for r in deck_db.failed_page_load_scope(f1)]
    check("failed_pages 记录 + 幂等 + 按页排序", scope1 == [40, 41], str(scope1))
    check("另一 folder 看不到本 scope 的失败页",
          deck_db.failed_page_load_scope("2026-09-11") == [])

    # tag 任务：folder+tag_query+tag_source 组成独立 scope
    deck_db.cursor_save(f2, "tags", 5, 1, 20, tag_query="hatsune_miku")
    deck_db.failed_page_add(f2, 7, tag_query="hatsune_miku")
    check("tag scope 游标独立",
          (deck_db.cursor_load(f2, tag_query="hatsune_miku") or {}).get("page") == 5)
    check("tag scope 失败页独立",
          [r["page"] for r in deck_db.failed_page_load_scope(f2, tag_query="hatsune_miku")] == [7])
    check("不同 tag_query 不串 scope",
          deck_db.cursor_load(f2, tag_query="kagamine_rin") is None
          and deck_db.failed_page_load_scope(f2, tag_query="kagamine_rin") == [])

    # 失败页重试成功后单页清除；任务自然结束游标清除，失败页记录保留供 UI 收口
    deck_db.failed_page_remove(f1, 40)
    check("单页清除后只剩 41",
          [r["page"] for r in deck_db.failed_page_load_scope(f1)] == [41])
    deck_db.cursor_clear(f1)
    check("cursor_clear 后游标消失", deck_db.cursor_load(f1) is None)
    check("清游标不影响 failed_pages",
          [r["page"] for r in deck_db.failed_page_load_scope(f1)] == [41])


def test_viewer_search():
    """⑧ 跨日期搜索：角色列 + 作品系列列(copyright) 整词匹配、作者精确匹配、区间/分库过滤。"""
    section("⑧ viewer_search 跨日期整词搜索")
    d1, d2, d3 = "2026-09-17", "2026-09-18", "2026-09-19"
    # 碧蓝航线图：角色 tag 带 _(azur_lane) 后缀，copyright 整词为 azur_lane
    azur_char = _entry("az1.jpg", "https://x/101", "w101", artist="artist_x",
                       tags=_tags(character="vicksburg_(azur_lane)", copyright_="azur_lane"))
    # 群像图：没打角色 tag，只有系列 tag —— 单日画廊的后缀子串搜不到它，跨日期 copyright 命中能覆盖
    azur_group = _entry("az2.jpg", "https://x/102", "w102", artist="artist_y",
                        tags=_tags(general="multiple_girls", character="", copyright_="azur_lane"))
    # 无关图：初音 / vocaloid
    miku = _entry("miku.jpg", "https://x/103", "w103", artist="artist_a",
                  tags=_tags(character="hatsune_miku", copyright_="vocaloid"))
    deck_db.viewer_replace("default", d1, [azur_char, azur_group, miku])
    deck_db.viewer_replace("default", d2, [azur_char])
    # 外置库同一天也有一张：library_ids 过滤必须生效
    deck_db.viewer_replace("lib2", d1, [azur_group])
    deck_db.viewer_replace("default", d3, [miku])

    # 主进程恒传在线 root 的 id 列表，测试同样显式限定，避免各用例互相串库
    def names(token_groups, kinds, libs=("default",), **kw):
        total, rows = deck_db.viewer_search(token_groups, kinds, library_ids=list(libs), **kw)
        return total, {r["filename"] for r in rows}

    # 系列名搜角色维度：后缀角色图 + 仅系列 tag 群像图都命中（三天 default 库共 3 条：d1×2 + d2×1）
    total, rows = names([["azur_lane"]], {"character"})
    check("azur_lane 走 tag_copyright 整词命中（不靠角色后缀子串）",
          total == 3 and rows == {"az1.jpg", "az2.jpg"}, f"total={total} rows={rows}")
    # 纯作者维度不碰 copyright：azur_lane 是系列不是作者
    total, _ = names([["azur_lane"]], {"artist"})
    check("作者维度搜 azur_lane 为 0", total == 0, str(total))
    # 整词边界不退化：'azur' 不能子串命中 azur_lane
    total, _ = names([["azur"]], {"character"})
    check("整词边界：azur 不命中 azur_lane", total == 0, str(total))
    # 作者精确匹配（artist 列等值 + tag_artist 整词）
    total, rows = names([["artist_x"]], {"artist"})
    check("作者 tag_artist 整词命中", total == 2 and rows == {"az1.jpg"}, f"total={total} rows={rows}")
    # 组间 AND：系列 azur_lane + 作者 artist_x，只有 d1/d2 的 az1
    total, rows = names([["azur_lane"], ["artist_x"]], {"character", "artist"})
    check("组间 AND（系列 ∩ 作者）", total == 2 and rows == {"az1.jpg"}, f"total={total} rows={rows}")
    # 组内 OR（中文角色名多候选展开的形状）
    total, _ = names([["vicksburg_(azur_lane)", "ghost_tag"]], {"character"})
    check("组内 OR 命中存在的候选 tag", total == 2, str(total))
    # 日期闭区间：只看 d2~d3 → d2 的 az1、d3 的 miku（miku 不命中），只剩 1
    total, rows = names([["azur_lane"]], {"character"}, folder_start=d2, folder_end=d3)
    check("folder 闭区间过滤", total == 1 and rows == {"az1.jpg"}, f"total={total} rows={rows}")
    # 分库过滤：只给 lib2 时 default 库行不参与
    total, rows = names([["azur_lane"]], {"character"}, libs=("lib2",))
    check("library_ids 限定外置库", total == 1 and rows == {"az2.jpg"}, f"total={total} rows={rows}")


def race_stress(rounds: int):
    """跨进程首启竞争压力测试：每轮全新临时目录 + 2 进程同时首连。

    曾经必现的失败：loser 在 winner 改名 log.json 的窗口里裸 open 撞 PermissionError，
    状态永久卡 importing。leader 选举 + 占用安全读取后应每轮双 done。"""
    print(f"race stress: {rounds} rounds × 2 进程同时首连")
    failures = 0
    base_env = os.environ.copy()
    for rnd in range(rounds):
        d = tempfile.mkdtemp(prefix=f"danbooru_race_s{rnd}_")
        try:
            build_fixture(d)
            env = base_env.copy()
            env["DANBOORU_DECK_DATA_DIR"] = d
            procs = [
                subprocess.Popen([sys.executable, "-c", _RACE_WORKER], cwd=REPO_ROOT, env=env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, encoding="utf-8", errors="replace")
                for _ in range(2)
            ]
            results, errs = [], []
            for i, p in enumerate(procs):
                out, err = p.communicate(timeout=180)
                if p.returncode != 0:
                    errs.append(f"worker{i} rc={p.returncode}: {err[-400:]}")
                    continue
                line = next((ln for ln in out.splitlines() if ln.strip().startswith("{")), "")
                try:
                    results.append(json.loads(line))
                except ValueError:
                    errs.append(f"worker{i} bad output: {out[-200:]} / {err[-200:]}")
            ok = (not errs and len(results) == 2
                  and all(r.get("state") in ("done", "rename_pending") for r in results)
                  and all(r.get("log") == 10 and r.get("viewer1") == 3 and r.get("stats") == 5
                          for r in results))
            if ok:
                print(f"  round {rnd + 1:2d}/{rounds} OK  {[r['state'] for r in results]}")
            else:
                failures += 1
                print(f"  round {rnd + 1:2d}/{rounds} FAIL  results={results} errs={errs}")
                print(f"    保留现场目录: {d}")
                continue
        finally:
            if not failures:
                shutil.rmtree(d, ignore_errors=True)
    if failures:
        print(f"RACE STRESS FAILED: {failures}/{rounds}")
        sys.exit(1)
    print(f"RACE STRESS PASSED: {rounds} rounds")


def main_run():
    print(f"临时数据目录: {_TMP_DATA}")
    print(f"竞争测试目录: {_TMP_RACE}")
    day1_items, day2_items = build_fixture(_TMP_DATA)
    try:
        test_import_idempotent(day1_items, day2_items)
        test_auto_import_then_cli_rename()
        test_mirror_equivalence(day1_items, day2_items)
        test_viewer_unique_keys()
        test_reconcile()
        test_cross_process_concurrency()
        test_bootstrap_race()
        test_rollback_drill()
        test_page_cursor_and_failed_scope()
        test_viewer_search()
    finally:
        deck_db.checkpoint("TRUNCATE")
    print("\n" + "=" * 70)
    if FAILURES:
        print(f"FAILED {len(FAILURES)}: {FAILURES}")
        print(f"临时目录保留以便排查: {_TMP_DATA} / {_TMP_RACE}")
        sys.exit(1)
    print("ALL DECK.DB CHECKS PASSED")
    if os.environ.get("KEEP_TMP"):
        print(f"临时目录保留（KEEP_TMP）: {_TMP_DATA} / {_TMP_RACE}")
    else:
        for d in (_TMP_DATA, _TMP_RACE):
            shutil.rmtree(d, ignore_errors=True)
        print("临时目录已清理")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--race-stress":
        n = int(sys.argv[2]) if len(sys.argv) >= 3 else 10
        race_stress(n)
    else:
        main_run()
