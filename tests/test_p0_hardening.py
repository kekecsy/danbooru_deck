# tests/test_p0_hardening.py
# P0 加固的离线回归测试（无外部网络依赖；HTTP 部分用本地 mock server）。
# 运行（仓库根目录）：.venv/Scripts/python.exe tests/test_p0_hardening.py
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# 脚本在 tests/ 子目录里运行，需把仓库根目录放进 sys.path 才能 import 项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 指向临时数据目录（必须在 import 项目模块之前设置）
_TMP_DATA = tempfile.mkdtemp(prefix="danbooru_p0_")
os.environ["DANBOORU_DECK_DATA_DIR"] = _TMP_DATA

import http_client
from my_utils import atomic_write_json, read_json_atomic
from danbooru_data import DanbooruData

FAILURES = []


def check(name, cond, detail=""):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail and not cond else ""))
    if not cond:
        FAILURES.append(name)


# ---------------- 1. 令牌桶 ----------------
def test_rate_limiter():
    rl = http_client.RateLimiter(rate_per_sec=20, burst=3)
    t0 = time.monotonic()
    for _ in range(3):
        rl.acquire()
    burst_elapsed = time.monotonic() - t0
    check("令牌桶 burst 3 即时放行", burst_elapsed < 0.05, f"{burst_elapsed:.3f}s")

    t0 = time.monotonic()
    rl.acquire()
    wait = time.monotonic() - t0
    check("令牌桶第 4 个请求等待 ~1/rate", 0.03 <= wait <= 0.30, f"waited {wait:.3f}s")


# ---------------- 2. Retry-After 解析 ----------------
class _FakeHeaders:
    def __init__(self, mapping):
        self._m = mapping

    def get(self, key):
        return self._m.get(key)


class _FakeResp:
    def __init__(self, mapping):
        self.headers = _FakeHeaders(mapping)


def test_retry_after():
    d = http_client._retry_after_delay(_FakeResp({"Retry-After": "3"}))
    check("Retry-After 秒数解析", d is not None and abs(d - 3) < 0.01, str(d))

    future = datetime.now(timezone.utc) + timedelta(seconds=2)
    import email.utils
    http_date = email.utils.format_datetime(future, usegmt=True)
    d = http_client._retry_after_delay(_FakeResp({"retry-after": http_date}))
    # 下限放宽：从生成未来时间到解析之间可能被调度延迟吃掉零点几秒
    check("Retry-After HTTP-date 解析", d is not None and 0.8 <= d <= 2.5, str(d))

    d = http_client._retry_after_delay(_FakeResp({}))
    check("Retry-After 缺失返回 None", d is None)

    d = http_client._retry_after_delay(_FakeResp({"Retry-After": "9999"}))
    check("Retry-After clamp 60s", d == http_client.RETRY_AFTER_CAP, str(d))


# ---------------- 3. 并发原子写 ----------------
def test_atomic_writes():
    target = os.path.join(_TMP_DATA, "atomic_test.json")
    stop = threading.Event()
    errors = []

    def writer(seed):
        try:
            for i in range(10):
                atomic_write_json(target, {"seed": seed, "i": i, "data": list(range(20))})
        except Exception as e:  # noqa
            errors.append(e)

    def reader():
        try:
            while not stop.is_set():
                data = read_json_atomic(target, {})
                if data:
                    assert isinstance(data, dict) and "seed" in data
        except Exception as e:  # noqa
            errors.append(e)

    threads = [threading.Thread(target=writer, args=(s,)) for s in range(10)]
    readers = [threading.Thread(target=reader) for _ in range(4)]
    for t in readers:
        t.start()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    stop.set()
    for t in readers:
        t.join()

    final = json.load(open(target, encoding="utf-8"))
    leftovers = [f for f in os.listdir(_TMP_DATA) if f.startswith("atomic_test.json.")]
    check("100 次并发原子写无异常", not errors, str(errors[:2]))
    check("最终文件内容合法", isinstance(final, dict) and "i" in final)
    check("无残留 .tmp 文件", not leftovers, str(leftovers))


# ---------------- 4. DanbooruData 惰性加载 ----------------
def test_lazy_data():
    # 先放一个小 log.json / artist_stats.json
    os.makedirs(os.path.join(_TMP_DATA, "hot_pic"), exist_ok=True)
    with open(os.path.join(_TMP_DATA, "hot_pic", "log.json"), "w", encoding="utf-8") as f:
        json.dump({"100": "http://example.com/a.jpg"}, f)
    with open(os.path.join(_TMP_DATA, "hot_pic", "artist_stats.json"), "w", encoding="utf-8") as f:
        json.dump({"artist_x": 3}, f)

    import deck_db
    t0 = time.monotonic()
    db = DanbooruData("__p0_lazy__")
    elapsed = time.monotonic() - t0
    # 冷构造含 mkdir / txtdata 读取，Windows 上略超 100ms 正常；关键是不解析 48MB JSON、
    # 也不连 SQLite（首次访问映射字段时才连库并一次性引导旧 JSON）
    check("DanbooruData() 构造 <250ms（不解析 log/stats、不连库）", elapsed < 0.25,
          f"{elapsed*1000:.1f}ms")
    t0 = time.monotonic()
    DanbooruData("__p0_lazy2__")
    check("热构造 <100ms", (time.monotonic() - t0) < 0.1)
    check("惰性字段未加载（私有哨兵为 None）", db._log_data is None and db._artist_stats is None)

    # 首次访问触发 deck.db 连接 + 旧 JSON 一次性引导（小数据，毫秒级）
    check("log_data 首次访问从 DB 取（含引导导入）",
          db.log_data.get("100") == "http://example.com/a.jpg")
    check("artist_stats 首次访问从 DB 取", db.artist_stats.get("artist_x") == 3)
    check("引导标志已置位", deck_db.meta_get("imported_log_stats") == "1")
    check("viewer/ids 域引导标志已置位", deck_db.meta_get("imported_viewer_ids") == "1")

    # 赋值即时落 SQLite；save_global_data 是 no-op，旧 JSON 不被改动也不需要
    db.log_data["101"] = "http://example.com/b.jpg"
    db.save_global_data()
    check("新写入即时落库且旧数据不丢",
          deck_db.log_get("100", "").endswith("a.jpg") and deck_db.log_get("101") ==
          "http://example.com/b.jpg")
    legacy = read_json_atomic(db.log_path, {})
    check("save_global_data 不再重写 log.json", set(legacy.keys()) == {"100"})

    # setter 兼容性（个别脚本直接赋值）：upsert 合并，不删未知键（防误清空）
    db.log_data = {"k": "v"}
    check("log_data setter 为合并语义",
          db.log_data.get("k") == "v" and "100" in db.log_data and "101" in db.log_data)


# ---------------- 5. 本地 mock server：重试 / 永久错 ----------------
class _MockState:
    def __init__(self):
        self.ra_hits = 0
        self.misdirect_hits = 0


def _make_handler(state):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/retry-after":
                state.ra_hits += 1
                if state.ra_hits == 1:
                    self.send_response(429)
                    self.send_header("Retry-After", "1")
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"ok": true}')
            elif self.path == "/misdirect":
                state.misdirect_hits += 1
                if state.misdirect_hits == 1:
                    self.send_response(421)
                    self.end_headers()
                    return
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            elif self.path == "/missing":
                self.send_response(404)
                self.end_headers()
            else:
                self.send_response(500)
                self.end_headers()

    return Handler


def test_http_retry():
    state = _MockState()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _make_handler(state))
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        t0 = time.monotonic()
        r = http_client.request("GET", base + "/retry-after", kind="json", retries=3,
                                timeout=10, proxies={}, base_delay=1)
        elapsed = time.monotonic() - t0
        check("429 + Retry-After:1 重试成功", r.status_code == 200 and r.json()["ok"])
        check("实际等待了 Retry-After 的 ~1s", 0.9 <= elapsed <= 3.0, f"{elapsed:.2f}s")
        check("只请求了 2 次", state.ra_hits == 2, str(state.ra_hits))

        r = http_client.request("GET", base + "/misdirect", kind="cdn", retries=3,
                                timeout=10, proxies={}, base_delay=1)
        check("421 瞬时错误自动重试成功", r.status_code == 200 and state.misdirect_hits == 2)

        t0 = time.monotonic()
        try:
            http_client.request("GET", base + "/missing", kind="json", retries=3,
                                timeout=10, proxies={}, base_delay=1)
            raised = False
        except http_client.PermanentHTTPError as e:
            raised = e.status_code == 404
        check("404 立即抛 PermanentHTTPError 不重试", raised)
        check("404 路径零退避等待", time.monotonic() - t0 < 0.5)

        # 网络异常：绑定后立即关闭的端口会立刻回 RST（不能用端口 1 —— Windows 上
        # 它不回 RST，curl 每次要干等 2s 连接超时）
        import socket
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        dead_port = s.getsockname()[1]
        s.close()
        def one_run(retries):
            t0 = time.monotonic()
            try:
                http_client.request("GET", f"http://127.0.0.1:{dead_port}/x", kind="cdn",
                                    retries=retries, timeout=3, proxies={},
                                    base_delay=0.05, max_delay=0.05)
                return False, time.monotonic() - t0
            except Exception:
                return True, time.monotonic() - t0

        # 本机有代理虚拟网卡时，到关闭端口的 SYN 可能被丢包（curl 每次 ~2s 连接超时），
        # 所以不看绝对耗时，只比较「多一次重试」新增的时间 ≈ 一次连接 + 0.05s 抖动退避。
        net_raised, t_single = one_run(0)
        _, t_double = one_run(1)
        check("网络异常重试耗尽后抛出", net_raised)
        check("退避抖动生效（两次重试的增量合理）", 0 < t_double - t_single < t_single + 1.0,
              f"{t_single:.2f}s -> {t_double:.2f}s")
    finally:
        server.shutdown()


# ---------------- 6. DownloadJob：viewer_keys + pending 降频 ----------------
def test_job_dedup_and_pending():
    import main
    db = DanbooruData("__p0_job__")
    job = main.DownloadJob(
        job_id="p0test", target_folder="__p0_job__", mode="rank", label="p0",
        save_dir=db.save_dir, db=db,
    )
    post = {"post_url": "https://example.com/posts/1", "score": 5}
    job.append_viewer_entry("1", "artist_a", "1.jpg", post)
    job.append_viewer_entry("1", "artist_a", "1.jpg", post)  # 同 post_url
    check("viewer_keys O(1) 判重", len(job.viewer_data) == 1 and len(job.viewer_keys) == 1)

    # 不同 post_url 但同 filename/web_url：视为两条（与 dedup_viewer_data 的 key 规则一致）
    job.append_viewer_entry("2", "artist_b", "2.jpg",
                            {"post_url": "https://example.com/posts/2"})
    check("不同 post_url 不误杀", len(job.viewer_data) == 2)

    # 模拟从盘上加载（load_viewer_data 会 dedup），索引仍能正确重建
    db.save_viewer_data(job.viewer_data)
    reloaded = db.load_viewer_data()
    job2 = main.DownloadJob(
        job_id="p0test2", target_folder="__p0_job__", mode="rank", label="p0",
        save_dir=db.save_dir, db=db, viewer_data=reloaded,
    )
    check("重建任务的 viewer_keys 与数据一致", len(job2.viewer_keys) == 2)
    job2.append_viewer_entry("2", "artist_b", "2.jpg",
                             {"post_url": "https://example.com/posts/2"})
    check("重建后追加判重仍生效", len(job2.viewer_data) == 2)

    ids_path = os.path.join(db.save_dir, "ids_data.json")
    import deck_db
    lib, folder = db._db_scope()
    job.queue_pending_id("999")
    check("queue_pending_id 立即落盘（崩溃安全）",
          read_json_atomic(ids_path, []) == ["999"])
    check("queue_pending_id 后 dl_queue 立即有 pending 行",
          deck_db.queue_load(lib, folder) == ["999"] and
          deck_db.queue_load(lib, folder, status="failed") == [])
    job.resolve_pending_id("999")
    check("resolve_pending_id 不立即写盘", read_json_atomic(ids_path, []) == ["999"])
    check("resolve_pending_id 已立即删 DB 行", deck_db.queue_count(lib, folder) == 0)
    job.flush_pending_ids()
    check("flush_pending_ids 落盘移除增量", read_json_atomic(ids_path, None) == [])
    job.flush_pending_ids()  # 不 dirty 时应无异常

    # 图级失败持久化：新任务 hydrate 后 failed 横幅与队列状态都能恢复
    job.queue_pending_id("777")
    job.record_failed_id("777")
    check("record_failed_id 后行标记 failed 且仍在队列里",
          deck_db.queue_load(lib, folder, status="failed") == ["777"] and
          deck_db.queue_load(lib, folder) == ["777"])
    job_h = main.DownloadJob(
        job_id="p0test-h", target_folder="__p0_job__", mode="rank", label="h",
        save_dir=db.save_dir, db=db,
    )
    check("重启 hydrate：failed id 同时进 pending_ids 与 failed_ids",
          "777" in job_h.pending_ids and job_h.failed_ids.get("777") == "__p0_job__")
    job_h.resolve_pending_id("777")
    job_h.flush_pending_ids()
    check("失败 id 重试成功后彻底消失", deck_db.queue_count(lib, folder) == 0)

    # 页级失败持久化：record 落库去重、hydrate 按 scope 恢复、clear 内存+DB 双清
    fp_entry = {"folder": "__p0_job__", "page": 5}
    job.record_failed_page(5)
    job.record_failed_page(5)  # 内存去重
    check("record_failed_page 内存去重且落库",
          job.failed_pages == [fp_entry] and
          deck_db.failed_page_load_scope("__p0_job__", "", "danbooru") == [fp_entry])
    job_t = main.DownloadJob(
        job_id="p0test-t", target_folder="__p0_job__", mode="tags", label="t",
        save_dir=db.save_dir, db=db, tag_query="blue_eyes",
    )
    job_t.hydrate_failed_pages()
    check("失败页按 tag_query scope 隔离", job_t.failed_pages == [])
    job_h2 = main.DownloadJob(
        job_id="p0test-h2", target_folder="__p0_job__", mode="rank", label="h2",
        save_dir=db.save_dir, db=db,
    )
    job_h2.hydrate_failed_pages()  # 模拟 start 端点在 tag_query/source 就位后的恢复
    check("重启 hydrate_failed_pages 恢复失败页", job_h2.failed_pages == [fp_entry])
    job_h2.clear_failed_page(5)
    check("clear_failed_page 内存+DB 双清",
          job_h2.failed_pages == [] and
          deck_db.failed_page_load_scope("__p0_job__", "", "danbooru") == [])

    # viewer 行式存储往返：标准键 + 可选 tag 键 + entry 顶层杂键都不丢
    rich_post = {
        "post_url": "https://example.com/posts/9", "score": 11, "fav_count": 22,
        "tag_string_general": "1girl solo", "tag_string_character": "hatsune_miku",
        "tag_string_copyright": "vocaloid", "tag_string_artist": "artist_a",
        "tag_string_meta": "highres", "rating": "g", "md5": "abc123",
    }
    job.append_viewer_entry("9", "artist_a", "9.jpg", rich_post)
    job.viewer_data[0]["custom_note"] = {"k": "v"}  # 未知顶层键
    db.save_viewer_data(job.viewer_data)
    reloaded2 = db.load_viewer_data()
    check("viewer DB 往返条目全等（含 rating/md5/meta/未知键）",
          reloaded2 == job.viewer_data, str(reloaded2[:1]))
    check("viewer 镜像文件与 DB 重组一致",
          read_json_atomic(os.path.join(db.save_dir, "viewer_data.json"), []) == reloaded2)

    shutil.rmtree(db.save_dir, ignore_errors=True)


# ---------------- 7. _fetch_page_or_pause：永久错快失败 + auto-pause ----------------
def test_autopause():
    import main
    import danbooru_api

    PAGE_TOTAL_ATTEMPTS = main.PAGE_FETCH_SILENT_RETRIES + 1  # 1 次初次 + N 次静默重试

    db = DanbooruData("__p0_ap__")
    job = main.DownloadJob(
        job_id="p0ap", target_folder="__p0_ap__", mode="rank", label="ap",
        save_dir=db.save_dir, db=db,
    )
    job.is_running = True

    def perm400():
        raise danbooru_api.PermanentHTTPError(400, "https://x/posts.json")

    # 前 4 页永久失败：立即返回 []，每页 fetch_fn 只调一次（旧逻辑每页浪费 ~9s）
    t0 = time.monotonic()
    for page in range(1, 5):
        r = main._fetch_page_or_pause(perm400, job, f"第{page}页", page)
        check(f"永久错第 {page} 页立即返回 []", r == [])
    perm_elapsed = time.monotonic() - t0
    check("4 页永久错零退避（旧逻辑约浪费 36s）", perm_elapsed < 3.0, f"{perm_elapsed:.2f}s")
    check("永久错全部记入 failed_pages", len(job.failed_pages) == 4, str(job.failed_pages))
    check("连续失败计数=4", job.consecutive_page_failures == 4,
          str(job.consecutive_page_failures))

    # 第 5 页永久失败 → auto-pause：阻塞等用户决策（放线程里跑）
    result_box = {}
    t = threading.Thread(
        target=lambda: result_box.setdefault("r", main._fetch_page_or_pause(perm400, job, "第5页", 5))
    )
    t.start()
    paused = False
    for _ in range(100):
        if any("自动暂停" in m for m in job.logs):
            paused = True
            break
        time.sleep(0.05)
    check("连续 5 页永久错触发 auto-pause", paused)
    check("auto-pause 时 play_event 被 clear", not job.play_event.is_set())
    check("第 5 页也记入 failed_pages", len(job.failed_pages) == 5)
    # 模拟用户点「停止」
    job.is_running = False
    job.play_event.set()
    t.join(timeout=5)
    check("停止后返回 None（grabber 收尾）", result_box.get("r") is None and not t.is_alive())

    # auto-pause 后用户点「继续」路径（再攒 5 页永久错，第 5 页放行）
    job2 = main.DownloadJob(
        job_id="p0ap2", target_folder="__p0_ap__", mode="rank", label="ap2",
        save_dir=db.save_dir, db=db,
    )
    job2.is_running = True
    for page in range(1, 5):
        check(f"job2 第{page}页永久错跳过",
              main._fetch_page_or_pause(perm400, job2, f"第{page}页", page) == [])
    box2 = {}
    t2 = threading.Thread(
        target=lambda: box2.setdefault("r", main._fetch_page_or_pause(perm400, job2, "第5页", 5))
    )
    t2.start()
    for _ in range(100):
        if any("自动暂停" in m for m in job2.logs):
            break
        time.sleep(0.05)
    job2.play_event.set()  # 用户点「继续」，is_running 保持 True
    t2.join(timeout=5)
    check("继续后返回 [] 且计数清零",
          box2.get("r") == [] and not t2.is_alive() and job2.consecutive_page_failures == 0)

    # 瞬时错误：静默重试耗尽后「原地暂停」——不记失败页、不跳页、不动连续计数；
    # 用户点继续后重新抓同一页，点停止则收尾返回 None（页由断点续跑/重试管）。
    job3 = main.DownloadJob(
        job_id="p0ap3", target_folder="__p0_ap__", mode="rank", label="ap3",
        save_dir=db.save_dir, db=db,
    )
    job3.is_running = True
    calls = {"n": 0}

    def transient_then_ok():
        # 第一轮 1 次初次 + 3 次静默重试全失败；用户继续后的新一轮成功
        calls["n"] += 1
        if calls["n"] <= PAGE_TOTAL_ATTEMPTS:
            raise ConnectionError("boom")
        return [{"id": 7}]

    old_backoff = main.PAGE_FETCH_BACKOFF
    main.PAGE_FETCH_BACKOFF = (0, 0, 0)  # 测试不等待 1+3+5s 退避
    try:
        box3 = {}
        t3 = threading.Thread(
            target=lambda: box3.setdefault(
                "r", main._fetch_page_or_pause(transient_then_ok, job3, "第1页", 1))
        )
        t3.start()
        paused3 = False
        for _ in range(100):
            if any("原地暂停" in m for m in job3.logs):
                paused3 = True
                break
            time.sleep(0.05)
        check("瞬时错误静默重试耗尽后原地暂停", paused3)
        check("原地暂停时 play_event 被 clear", not job3.play_event.is_set())
        check("瞬时失败不记 failed_pages", job3.failed_pages == [], str(job3.failed_pages))
        check("瞬时失败不动连续计数", job3.consecutive_page_failures == 0)
        check("暂停前共尝试 1+3=4 次", calls["n"] == PAGE_TOTAL_ATTEMPTS, str(calls))
        check("暂停期间线程仍阻塞在本页", t3.is_alive())
        job3.play_event.set()  # 用户等限流冷却后点「继续」
        t3.join(timeout=5)
        check("继续后重新抓同一页并成功返回",
              box3.get("r") == [{"id": 7}] and calls["n"] == PAGE_TOTAL_ATTEMPTS + 1
              and not t3.is_alive(),
              f"{box3.get('r')} calls={calls['n']}")

        # 原地暂停时点「停止」→ 返回 None 收尾，仍然不记 failed_pages（不允许跳页）
        job4 = main.DownloadJob(
            job_id="p0ap4", target_folder="__p0_ap__", mode="rank", label="ap4",
            save_dir=db.save_dir, db=db,
        )
        job4.is_running = True

        def always_boom():
            raise ConnectionError("boom")

        box4 = {}
        t4 = threading.Thread(
            target=lambda: box4.setdefault(
                "r", main._fetch_page_or_pause(always_boom, job4, "第10页", 10))
        )
        t4.start()
        for _ in range(100):
            if any("原地暂停" in m for m in job4.logs):
                break
            time.sleep(0.05)
        job4.is_running = False
        job4.play_event.set()
        t4.join(timeout=5)
        check("原地暂停时点停止返回 None", box4.get("r") is None and not t4.is_alive())
        check("停止路径仍未记 failed_pages（零跳过）", job4.failed_pages == [],
              str(job4.failed_pages))
    finally:
        main.PAGE_FETCH_BACKOFF = old_backoff

    shutil.rmtree(db.save_dir, ignore_errors=True)


def main_run():
    test_rate_limiter()
    test_retry_after()
    test_atomic_writes()
    test_lazy_data()
    test_http_retry()
    test_job_dedup_and_pending()
    test_autopause()
    print()
    if FAILURES:
        print(f"FAILED {len(FAILURES)}: {FAILURES}")
        shutil.rmtree(_TMP_DATA, ignore_errors=True)
        sys.exit(1)
    print("ALL P0 CHECKS PASSED")
    shutil.rmtree(_TMP_DATA, ignore_errors=True)


if __name__ == "__main__":
    main_run()
