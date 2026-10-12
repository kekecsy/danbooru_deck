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

4. 网络活动探针 —— seconds_since_activity() 供 main.py 的「空闲下载」判断网络是否安静。
   刻意挂在 request() 这个唯一入口上，而不是让每个调用方自己上报：列表请求 / 元数据 /
   CDN 下载 / 缩略图代理全都经过这里，一处记录即全局有效。

5. ⚠ 可取消（2026-10）—— CancelToken / RequestCancelled / close_quietly()。
   没有它的时候「停止」是**假的**：停信号只改一个布尔量，而 worker 一旦进了
   download_image 就卡在 socket 上，要等满「retries × timeout + 退避」才回来
   （旧参数下最坏 4×30 + 21 ≈ 141 秒），期间 UI 一直写着「正在停止…」。
   掐掉的是 ①还没开始的尝试与退避等待（cancel_check）、②正在传的那一次（读循环
   逐块检查 + 读完/放弃时 close_quietly 收尾）。
   ⚠ **没有**「取消线程去掐在途连接」这条路：试过，两版都无效，理由见 CancelToken。
"""
import email.utils
import json
import os
import random
import threading
import time
from contextlib import contextmanager

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


class RequestCancelled(Exception):
    """请求被**主动取消**（用户点了「停止」）。

    ⚠ 必须与「网络失败」严格区分：上层捕获它要**静默收手** ——
    不计失败、**不写 failed_ids**。混同的后果是「用户每点一次停止，
    在途的那几张图就被误报成『下载失败可重试』」，点得越多失败表越长。
    """


# CDN 大字节请求（图片本体）的超时。
# ⚠ **stream 模式下 curl_cffi 的 timeout 不是「总超时」**（见其 requests/utils.py）：
#     tuple → CONNECTTIMEOUT_MS=connect，同时 LOW_SPEED_LIMIT=1 / LOW_SPEED_TIME=connect+read
#     裸数字 → CONNECTTIMEOUT_MS=timeout，LOW_SPEED_TIME=timeout
#   即「连接最慢 connect 秒」+「吞吐低于 1 B/s 持续 connect+read 秒就中断」。
#   用元组是为了把「连接卡住」与「传输卡住」分开管。
# ⚠ 这两个值同时是**「停止」在最坏情况下的上界**：数据在流动时，取消一置位、
#   下一个 chunk 到达就会被放弃（≈瞬间）；只有 socket **一个字节都不来**时才退化成
#   「等 curl 的低速率关口」。所以别把它们放大 —— 5 秒内 <1 B/s 对一条活着的链路
#   几乎不可能发生（TCP 会重传），但对一条已经死掉的连接是确定的。
#   ⚠ 顺带修掉旧行为：裸 30 是**总超时**，30MB 的图在慢链路上会在第 30 秒被砍掉。
CDN_CONNECT_TIMEOUT = 10
CDN_STALL_TIMEOUT = 5
CDN_TIMEOUT = (CDN_CONNECT_TIMEOUT, CDN_STALL_TIMEOUT)


def close_quietly(resp):
    """把流式响应关掉（读完 / 中途放弃都要调）。不抛异常。

    ⚠ **这个收尾只能在「读它的那个线程」里做。** curl_cffi 的 `Response.close()`
    内部会置 `quit_now` → `stream_task.result()` 等传输线程收工 → `curl_easy_cleanup`；
    读取线程被异常唤醒后也会走同一段收尾，两边撞在一起就是两次 cleanup。
    所以「取消」那一侧只置标记，收尾一律留给读者（见下）。

    ⚠ 非流式响应上它是空操作（curl_cffi 内部早退），可以无条件调。
    """
    try:
        resp.close()
    except Exception:
        pass


class CancelToken:
    """一次「可取消的网络活动」的令牌 —— 每个 job 一个。

    `cancel()` 置位之后：
      1. `request()` 不再发新尝试，退避 / Retry-After 的 sleep 立刻中断；
      2. 下载的读循环在每个 chunk 之后查一次，被取消就抛 RequestCancelled。

    ⚠⚠ 这里**刻意只做一个标记**，别再加「从取消线程去掐响应」那套：
        试过两条路，实测都无效 ——
        · `resp.close()`：会和读线程被异常唤醒后的收尾撞车（两次 curl_easy_cleanup）；
        · 只置 curl_cffi 的 `quit_now`：它只在 **write 回调**里被读到，而 write 回调
          **只在有数据到达时**才被调用 —— 所以它永远快不过「下一个 chunk 到达」，
          对「一个字节都不来的僵死连接」更是完全没有作用（那种情况只能等 curl
          自己的 LOW_SPEED 关口）。三个场景（503 退避 / 慢速吐字节 / 彻底僵死）
          各测一遍，加与不加耗时和连接数**完全一样**，所以不留这段死代码。

    ⚠ 为什么不是全进程一个：`/api/stop?job_id=` 只该停**这一个**任务。
      空闲下载轮次与用户任务可能同时在跑，全局中断会把另一个也一起打断。
    """

    def __init__(self, label=""):
        self.label = label
        self._ev = threading.Event()

    def cancelled(self) -> bool:
        return self._ev.is_set()

    def cancel(self):
        """置位。可重复调用（幂等）。"""
        self._ev.set()


def _sleep_cancellable(seconds, cancel_check, step=0.1):
    """可被打断的 sleep：退避等待不该让「停止」陪着一起等。

    用 0.1s 的步长轮询，而不是「一次 sleep 完再看」—— 退避上限 30s、
    Retry-After 上限 60s，一次睡完的话停止最多要等 1 分钟。
    """
    if cancel_check is None:
        time.sleep(seconds)
        return
    deadline = time.monotonic() + seconds
    while True:
        if cancel_check():
            raise RequestCancelled("等待重试期间被取消")
        remain = deadline - time.monotonic()
        if remain <= 0:
            return
        time.sleep(min(step, remain))


# ---- 线程局部「当前任务令牌」 ------------------------------------------------
# `_fetch_page_or_pause(lambda: api.get_posts_by_tags(...))` 这类闭包在 main.py 里
# 遍地都是（列表抓取 / 元数据预取 / 缩略图…），把 cancel_check 一层层穿进 api 层
# 要改一大片签名；而「这个线程正在替哪个 job 干活」本来就是**线程的属性**。
# 于是：job 的主线程进来时绑一次，request() 在调用方没显式传 cancel_check 时用它兜底。
# ⚠ 只覆盖任务自己的线程。ThreadPoolExecutor 的工作线程是新线程、拿不到绑定 ——
#   下载路径必须**显式**把 token 传进 download_image（main.py 三处都传了）。
_TLS = threading.local()


@contextmanager
def bind_cancel_token(token):
    prev = getattr(_TLS, "token", None)
    _TLS.token = token
    try:
        yield token
    finally:
        _TLS.token = prev


def _ambient_cancel_check():
    token = getattr(_TLS, "token", None)
    return token.cancelled if token is not None else None


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


# ---------------- 网络活动探针 ----------------
# 「空闲下载」需要知道「最近有没有人在用网」。monotonic 不受系统时间调整影响；
# 初值取 -1e9 而非 0：Windows 上 monotonic() 是「开机以来秒数」，刚开机时它可能只有
# 几百秒，用 0 当初值会让「从没发过请求」被算成「1 分钟前刚发过」→ 白等一轮。
_last_activity = time.monotonic() - 1e9


def note_activity():
    """记录一次真实的网络活动（在 request() 里每次实际发出 HTTP 前调用）。"""
    global _last_activity
    _last_activity = time.monotonic()


def seconds_since_activity():
    """距上一次真实网络请求过了多少秒。从未请求过 → 一个很大的数。"""
    return time.monotonic() - _last_activity


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
            proxies=None, limiter=None, base_delay=1.0, max_delay=30.0,
            stream=False, cancel_check=None):
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

    stream=True: 交给 curl_cffi 的流式模式，**响应体由调用方自己读**
        （`iter_content()` 或 `.content`）—— 图片本体走这条路才有被取消的可能，
        因为非流式模式下响应体在 `session.request()` 内部就读完了，
        调用方拿不到句柄、也就没有任何东西可以中止它。
    cancel_check: 返回真表示「已取消」的可调用对象（通常传 `token.cancelled`）。
        每次尝试之前、以及退避/Retry-After 等待期间都会查一次；
        查到就抛 RequestCancelled。
        ⚠ 它**管不了「已经卡在 socket read 上的那一次」** —— 那一层得靠调用方在
        自己的读循环里逐个 chunk 复查（见 danbooru_api.download_image），
        因为没有别的线程能安全地替它掐掉连接（理由见 CancelToken）。
        ⚠ 不传时用当前线程绑定的任务令牌兜底（见 bind_cancel_token）——
        这条兜底覆盖的是遍地都是的 `lambda: api.get_xxx(...)` 闭包。
    """
    if kind == "cdn":
        limiter = None
    if cancel_check is None:
        # 调用方没显式给 → 用当前线程绑定的那个任务令牌兜底（见 bind_cancel_token）。
        cancel_check = _ambient_cancel_check()
    permanent_set = DEFAULT_PERMANENT_STATUS if permanent is None else frozenset(permanent)
    attempt = 0
    while True:
        if cancel_check is not None and cancel_check():
            raise RequestCancelled(f"请求已取消: {url}")
        if limiter is not None:
            limiter.acquire()
        attempt += 1
        note_activity()
        try:
            resp = get_session().request(
                method,
                url,
                params=params,
                headers=headers,
                data=data,
                proxies=proxies,
                timeout=timeout,
                stream=stream,
            )
        except Exception:
            # 被取消时抛出去的异常和「网络错误」长得一样，先按取消判定，
            # 否则停止瞬间会白睡一次退避、还会把它当成一次真实失败往上抛。
            if cancel_check is not None and cancel_check():
                raise RequestCancelled(f"请求已取消: {url}")
            if attempt > retries:
                raise
            _sleep_cancellable(_backoff_delay(attempt, base_delay, max_delay), cancel_check)
            continue

        status = resp.status_code
        if 200 <= status < 300:
            return resp
        if status in permanent_set:
            close_quietly(resp)
            raise PermanentHTTPError(status, url)
        if status in TRANSIENT_STATUS:
            if attempt > retries:
                close_quietly(resp)
                resp.raise_for_status()
            delay = _retry_after_delay(resp)
            if delay is None:
                delay = _backoff_delay(attempt, base_delay, max_delay)
            close_quietly(resp)
            _sleep_cancellable(delay, cancel_check)
            continue
        # 其余状态码：JSON 类请求视为永久（400 非法查询等），CDN 类视为瞬时重试。
        if kind == "json":
            close_quietly(resp)
            raise PermanentHTTPError(status, url)
        if attempt > retries:
            close_quietly(resp)
            resp.raise_for_status()
        close_quietly(resp)
        _sleep_cancellable(_backoff_delay(attempt, base_delay, max_delay), cancel_check)
