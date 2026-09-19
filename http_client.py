# http_client.py
"""共享 HTTP 基础设施（P0 加固，参考 monbooru/monloader 的稳健请求思路）。

三件事：
1. 线程局部 curl_cffi Session 池 —— 连接复用（keep-alive），代理 / SFW 切换时
   通过 generation 计数让所有线程下次取到全新 session；
2. 令牌桶限速器 RateLimiter —— 只约束 Danbooru JSON API（匿名 1 req/s），
   图片 CDN 下载与缩略图代理不经过桶，保持原有每线程 sleep(1) 节奏；
3. 统一 request() —— Retry-After（秒数 / HTTP-date）、421 Misdirected Request、
   指数退避 + 抖动、永久错误立即抛 PermanentHTTPError。

对外只暴露 request()/get_session()/reset_sessions()/RateLimiter/PermanentHTTPError，
danbooru_api / gelbooru_api / main.py 的直连点全部走这里。
"""
import email.utils
import json
import os
import random
import threading
import time

from curl_cffi import requests

from runtime_paths import DATA_DIR, RESOURCE_DIR

IMPERSONATE = "chrome120"

# 永久错误：重试没有意义（已删帖 / 无权限 / 非法请求）。
DEFAULT_PERMANENT_STATUS = frozenset({403, 404, 410, 451})
# 列表端点额外把 400（非法 tag 查询）视为永久错误：main 的翻页静默重试不必再白等。
LIST_PERMANENT_STATUS = frozenset({400, 403, 404, 410, 451})
# 值得退避重试的状态码。421 在 donmai.us 经 Cloudflare/CDN 切换时偶发，重试即恢复。
TRANSIENT_STATUS = frozenset({421, 429, 500, 502, 503, 504})

RETRY_AFTER_CAP = 60.0  # 无论 Retry-After 给多大，单次最多等 60s


class PermanentHTTPError(Exception):
    """HTTP 永久错误（见 DEFAULT_PERMANENT_STATUS）—— 立即失败，绝不重试。"""

    def __init__(self, status_code, url=""):
        super().__init__(f"HTTP {status_code} 永久错误: {url}")
        self.status_code = int(status_code)
        self.url = url


# ---------------- 线程局部 Session 池 ----------------

_local = threading.local()
_generation = 0


def reset_sessions():
    """让本进程所有线程缓存的 Session 失效（代理切换 / host 切换后调用）。

    其他线程里已经缓存的旧 session 不会被强行 close（拿不到别的线程的 local），
    但它们下次取用会发现 generation 过期而自行 close 并重建。
    """
    global _generation
    _generation += 1


def get_session():
    """返回当前线程专属的 curl_cffi Session（chrome120 指纹，连接池复用）。"""
    global _generation
    gen = _generation
    sess = getattr(_local, "session", None)
    if sess is None or getattr(_local, "gen", -1) != gen:
        if sess is not None:
            try:
                sess.close()
            except Exception:
                pass
        sess = requests.Session(impersonate=IMPERSONATE)
        _local.session = sess
        _local.gen = gen
    return sess


# ---------------- 令牌桶 ----------------

class RateLimiter:
    """经典令牌桶：按 rate_per_sec 匀速补充，最多攒 burst 个令牌应对突发。

    线程安全；acquire() 在额度不足时阻塞到下一个令牌就绪。
    5 个 refresh worker 可以瞬间拿光 burst，之后按 1/s 排队。
    """

    def __init__(self, rate_per_sec, burst):
        self.rate = float(rate_per_sec)
        self.capacity = float(burst)
        self.tokens = float(burst)
        self.updated = time.monotonic()
        self.lock = threading.Lock()

    def acquire(self):
        while True:
            with self.lock:
                now = time.monotonic()
                self.tokens = min(
                    self.capacity,
                    self.tokens + (now - self.updated) * self.rate,
                )
                self.updated = now
                if self.tokens >= 1.0:
                    self.tokens -= 1.0
                    return
                wait = (1.0 - self.tokens) / self.rate
            # 不持锁睡眠；醒来后重新结算（多线程同时被放醒也只有一个能拿到令牌）
            time.sleep(wait)


def _load_rate_config():
    """从 env_config.json（DATA_DIR 优先、源码目录兜底）/ 环境变量读限速配置。

    缺省 Danbooru 匿名限制 1 req/s、burst 5。全部为可选项，不配也能跑。
    """
    rate, burst = 1.0, 5.0
    for base in (DATA_DIR, RESOURCE_DIR):
        cfg_path = os.path.join(str(base), "env_config.json")
        if os.path.exists(cfg_path):
            try:
                with open(cfg_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                if isinstance(cfg, dict):
                    rate = float(cfg.get("danbooru_rate_per_sec", rate))
                    burst = float(cfg.get("danbooru_rate_burst", burst))
                break
            except Exception:
                pass
    try:
        rate = float(os.environ.get("DANBOORU_RATE_PER_SEC", rate))
        burst = float(os.environ.get("DANBOORU_RATE_BURST", burst))
    except (TypeError, ValueError):
        pass
    # 防御：0 / 负数会让令牌桶除零或永不放行
    rate = max(rate, 0.05)
    burst = max(burst, 1.0)
    return rate, burst


_RATE, _BURST = _load_rate_config()
# Danbooru JSON API 全进程共享一个桶（所有 worker 线程合起来 1 req/s）。
danbooru_limiter = RateLimiter(_RATE, _BURST)


# ---------------- 退避 / Retry-After ----------------

def _retry_after_delay(response, cap=RETRY_AFTER_CAP):
    """解析 Retry-After：既支持 delta-seconds，也支持 HTTP-date。无法解析返回 None。"""
    try:
        val = response.headers.get("Retry-After") or response.headers.get("retry-after")
    except Exception:
        return None
    if not val:
        return None
    val = val.strip()
    try:
        return min(cap, max(0.0, float(val)))
    except ValueError:
        pass
    try:
        dt = email.utils.parsedate_to_datetime(val)
        if dt is not None:
            return min(cap, max(0.0, dt.timestamp() - time.time()))
    except (TypeError, ValueError, OSError):
        pass
    return None


def _backoff_delay(attempt, base_delay, max_delay):
    """attempt 为已失败次数（1 起）：base, 2base, 4base... 上限 30s，±25% 抖动。"""
    delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
    return delay * random.uniform(0.75, 1.25)


# ---------------- 统一请求入口 ----------------

def request(method, url, *, kind="json", retries=5, timeout=20,
            permanent=None, headers=None, params=None, data=None,
            proxies=None, limiter=None, base_delay=1.0, max_delay=30.0):
    """发起 HTTP 请求并按统一策略重试。

    kind:
      - "json": JSON API 请求，调用方传了 limiter 则先过令牌桶；
      - "cdn":  图片 / 代理等大字节请求，永不过限速器。
    永久状态码 -> 立即抛 PermanentHTTPError（不消耗重试次数）。
    瞬时状态码（见 TRANSIENT_STATUS）与网络异常 -> Retry-After 优先，否则指数退避；
    JSON 请求遇到其余非 2xx 状态码（如 400）按永久错误处理（除非调用方扩大 permanent），
    CDN 请求遇到其余状态码按瞬时处理（保持 download_image 旧契约）。
    重试耗尽：瞬时状态码走 raise_for_status 抛出，网络异常抛出最后一次异常。
    返回 curl_cffi Response（status 2xx）。
    """
    if kind == "cdn":
        limiter = None
    permanent_set = DEFAULT_PERMANENT_STATUS if permanent is None else frozenset(permanent)
    attempt = 0
    while True:
        if limiter is not None:
            limiter.acquire()
        attempt += 1
        try:
            resp = get_session().request(
                method,
                url,
                params=params,
                headers=headers,
                data=data,
                proxies=proxies,
                timeout=timeout,
            )
        except Exception:
            if attempt > retries:
                raise
            time.sleep(_backoff_delay(attempt, base_delay, max_delay))
            continue

        status = resp.status_code
        if 200 <= status < 300:
            return resp
        if status in permanent_set:
            raise PermanentHTTPError(status, url)
        if status in TRANSIENT_STATUS:
            if attempt > retries:
                resp.raise_for_status()
            delay = _retry_after_delay(resp)
            if delay is None:
                delay = _backoff_delay(attempt, base_delay, max_delay)
            time.sleep(delay)
            continue
        # 其余状态码：JSON 类请求视为永久（400 非法查询等），CDN 类视为瞬时重试。
        if kind == "json":
            raise PermanentHTTPError(status, url)
        if attempt > retries:
            resp.raise_for_status()
        time.sleep(_backoff_delay(attempt, base_delay, max_delay))
