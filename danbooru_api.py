import os
import socket
import urllib.parse
from time import sleep
from curl_cffi import requests
from my_utils import get_proxies_for_url

COOKIES = "_danbooru2_session=dzlzD2gYvCdxOQfBzhHUpXyPWZdvd8kXWARK6n0KdX4VDPgzGj9sLOyHfTrMVFdFpaJLHAP3LfMiptyeQeiNGE1yNM8tY7IGQXtYV2u8aFKglH7khCVW8FVqurTZSNR25VdDBgoTBDzu9p/gTeTrazCCWzLRPlg7hglvs6F6Xmfd7VVN/Mb+HbA5y7HAykGYk9kXDOTbE5s/HTOvPZd3hT6t/WcVUL8VlEW0nv1aiJt2h0byWwJBgBDGIvPgTebOWaH+xlRuqaHPhU0BmTEP+MtffzowVs9EQBUaO6LCky5e+fYQmcxXl68ANSAF/DQmp1EqppEU/TDW86rPMwoLCJZmIfC+XaAe5Z+PnwLV+DeOrBVtWdYWa8klazul6KqGHX8W6Z7WYMOoB9LpDfCzVnyDXOqCA+w2wbM2GuAUI7uH3A3Nuc/Z73esVF/qhN3CyImze/KOleoApwSRSjMXeb6oSpks1MvFGvN2lADMAtibu3cEfpC0glc+0YieVxc2J8TTQFVAhhSb+PYzohNdDR533ubSH5fikI2D8hiZmR1WSl1gWTol2eDkohCDmi5S+tqGOE1em0ZzJ/lpdfBhoJcwMYMA7dWp--YLXy4wFzNb8Zce8g--/4vINxtQJS/LT/9J9QoqKA=="
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"


class TransientImageError(Exception):
    """图片下载因瞬时网络错误（超时/连接/429/5xx）重试耗尽后抛出，
    上层据此把该页记入「失败页」供用户一键重试；永久错误不会抛此异常。"""
    pass


class PermanentPostError(Exception):
    """拉取 post 元数据时遇到永久错误（403/404/410/451 —— 帖子已删 / 已删号 / 隐私）。
    上层据此把 id 从待下载队列移除（不写 failed_ids），避免「重试」按钮每次都列出同一个已删图。
    区别于 TransientImageError：本类重试无意义，瞬时错误才会重试。"""
    def __init__(self, post_id, status_code):
        super().__init__(f"post {post_id} 永久不可用 (HTTP {status_code})")
        self.post_id = str(post_id)
        self.status_code = int(status_code)


# ---------------- SFW 开关 ----------------
# 默认走 safebooru（无 R-18 内容）；前端通过 /api/set_safe_mode 切换。
# 所有需要拼 URL 的地方都通过 get_host()/post_url() 读，确保切换即时生效。
HOST_SAFE = "safebooru.donmai.us"
HOST_FULL = "danbooru.donmai.us"
_HOST = HOST_SAFE

def get_host():
    return _HOST

def set_safe_mode(safe: bool):
    global _HOST, HEADERS
    _HOST = HOST_SAFE if safe else HOST_FULL
    HEADERS["Referer"] = f"https://{_HOST}/posts"

def post_url(post_id):
    return f"https://{_HOST}/posts/{post_id}"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Cookie": COOKIES,
    "Referer": f"https://{_HOST}/posts",
    "Accept": "application/json"
}


def _proxy_alive(proxies, timeout=1.5):
    """TCP-探测代理端口是否可连接。proxies 为空时按 True 处理。"""
    if not proxies:
        return True
    proxy_url = proxies.get("https") or proxies.get("http")
    if not proxy_url:
        return True
    parsed = urllib.parse.urlparse(proxy_url)
    host, port = parsed.hostname, parsed.port
    if not host or not port:
        return True
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


# 启动时按当前 _HOST 探测一次代理；切换 SFW 时如果 host 改了一般也不影响（同一 donmai.us 域）
_RAW_PROXIES = get_proxies_for_url(f"https://{_HOST}")
if _RAW_PROXIES and not _proxy_alive(_RAW_PROXIES):
    # 环境变量里写了代理但端口没人监听（典型场景：Clash/v2ray 没开），
    # 用户大概率本身可以直连 —— 直接清空 PROXIES，否则 curl_cffi 会卡死在
    # "Failed to connect to 127.0.0.1 port 7897" 然后整个抓图任务失败。
    print(f"[proxy] 检测到环境变量代理 {_RAW_PROXIES} 不可达，自动改用直连。如需走代理请先启动代理软件再重启后端。")
    PROXIES = {}
else:
    PROXIES = _RAW_PROXIES

# 国内访问 Danbooru 常用的本地代理端口（与 caption.py 默认一致）。
# 仅当用户手动切到「走代理」、但环境/系统又没配代理时作为兜底。
DEFAULT_PROXY = "http://127.0.0.1:7897"


def set_proxy_mode(use_proxy: bool):
    """运行时切换代理模式，立即影响后续所有 danbooru_api 请求。

    解决「开着代理启动后端 → PROXIES 定格 → 关掉代理软件后下载仍走死代理报错」：
    - use_proxy=False：清空 PROXIES，强制直连。
    - use_proxy=True：实时重新读取 env/Windows 注册表里的系统代理；读不到就兜底
      DEFAULT_PROXY。返回里带 alive 探测结果，前端据此提示端口是否可达。
    """
    global PROXIES
    if not use_proxy:
        PROXIES = {}
        return {"use_proxy": False, "proxies": {}, "alive": True}
    proxies = get_proxies_for_url(f"https://{_HOST}")
    if not proxies:
        proxies = {"http": DEFAULT_PROXY, "https": DEFAULT_PROXY}
    PROXIES = proxies
    return {"use_proxy": True, "proxies": proxies, "alive": _proxy_alive(proxies)}


def get_proxy_state():
    """当前代理状态：有非空 PROXIES 即视为「走代理」。"""
    return {"use_proxy": bool(PROXIES), "proxies": PROXIES, "alive": _proxy_alive(PROXIES)}

def check_proxy_simple():
    url = f"https://{_HOST}"
    try:
        resp = requests.get(url, timeout=10, proxies=PROXIES, headers=HEADERS, impersonate="chrome120")
        return resp.status_code == 200
    except:
        return False

def get_posts_by_rank(page, limit=20, timeout=20):
    """按 order:rank 拉一页 posts.json 给前端预览/收集，不下载图片。
    limit 默认 20（Danbooru 默认页大小），与旧调用方行为一致；
    新增的 /api/browse_rank 端点会在 1..200 之间透传覆盖。"""
    params = {
        "d": "1",
        "page": page,
        "limit": limit,
        "tags": "order:rank"
    }
    r = requests.get(
        f"https://{_HOST}/posts.json",
        params=params,
        headers=HEADERS,
        proxies=PROXIES,
        impersonate="chrome120",
        timeout=timeout
    )
    r.raise_for_status()
    return r.json()

def get_popular_posts(date_str, page, scale="day", timeout=20):
    params = {
        "date": date_str,
        "page": page,
        "scale": scale
    }
    r = requests.get(
        f"https://{_HOST}/explore/posts/popular.json",
        params=params,
        headers=HEADERS,
        proxies=PROXIES,
        impersonate="chrome120",
        timeout=timeout
    )
    r.raise_for_status()
    return r.json()


def get_posts_by_tags(tags, page, limit=20, timeout=20):
    """按 tag 查询串拉 posts.json。tags 支持 Danbooru 的多 tag 语法
    （空格分隔的 AND 查询，如 'hatsune_miku rating:safe -comic'）。
    走 _HOST 抽象，SFW/full 开关自动生效。"""
    params = {
        "page": page,
        "tags": tags,
        "limit": limit,
    }
    r = requests.get(
        f"https://{_HOST}/posts.json",
        params=params,
        headers=HEADERS,
        proxies=PROXIES,
        impersonate="chrome120",
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json()


def fetch_drawers_by_character(character_tag: str,
                               total_pages: int,
                               start_offset: int = 1,
                               known_drawers: set = None,
                               request_delay: float = 1.0):
    """从 Danbooru 倒序抓取指定角色标签的帖子，提取画师标签并返回新发现的画师集合。

    参数：
        character_tag: 角色标签（如 'hatsune_miku'）
        total_pages: 该角色在 Danbooru 上的总页数
        start_offset: 从倒数第几页开始（1=最后一页，total_pages=第一页）
        known_drawers: 已知画师集合，命中则不返回
        request_delay: 每页之间的等待秒数，避免触发限流

    返回：新发现的画师集合（已剔除 (voice_actor) 标签和 known_drawers）

    实现说明：原来 d1.py 用 BeautifulSoup 抓 HTML，列表页有时会被反爬挡掉；
    这里改用 posts.json 拿到 ID 列表，跟项目其他抓取保持同一套 host/cookie/proxy。
    """
    if known_drawers is None:
        known_drawers = set()
    if total_pages < 1:
        return set()

    start_page = total_pages - start_offset + 1
    if start_page < 1:
        start_page = 1

    new_drawers = set()
    failed_ids = set()

    for page in range(start_page, 0, -1):
        print(f"[fetch_drawers] 第 {page}/{total_pages} 页 tag={character_tag} (host={_HOST})")
        try:
            posts = get_posts_by_tags(character_tag, page)
        except Exception as e:
            print(f"  请求页面失败: {e}")
            sleep(request_delay)
            continue

        if not posts:
            print(f"  第 {page} 页无帖子。")
            sleep(request_delay)
            continue

        for post in posts:
            post_id = str(post.get("id") or "")
            if not post_id or post_id in failed_ids:
                continue

            # 列表接口已经带 tag_string_artist；只有缺字段时才再请求单帖详情
            artist_str = post.get("tag_string_artist") or ""
            if not artist_str:
                detail = fetch_data_with_retry(post_id, retries=2, delay=3)
                if detail is None:
                    failed_ids.add(post_id)
                    continue
                artist_str = detail.get("tag_string_artist") or ""

            if not artist_str:
                continue

            drawers = [t for t in artist_str.split(" ") if t and not t.lower().endswith("(voice_actor)")]
            if not drawers:
                continue
            primary = drawers[0]
            if primary not in known_drawers:
                new_drawers.add(primary)
                known_drawers.add(primary)

        sleep(request_delay)

    return new_drawers


def get_wiki(name, timeout=10):
    """从 Danbooru wiki 在线拉一个 tag 的描述 + other_names。
    复刻 tags_translate/tags_wiki.py 的 get_wiki，改用项目内的 curl_cffi + 代理 + cookie。
    成功返回 wiki API 的 list（通常 1 个元素，含 body / other_names）；失败/无结果返回 []。
    """
    encode_tag = urllib.parse.quote(name, safe='')
    url = f"https://{_HOST}/wiki_pages.json?search[title]={encode_tag}"
    proxies = PROXIES  # 跟随运行时代理开关（set_proxy_mode），与其余请求保持一致
    try:
        resp = requests.get(
            url,
            timeout=timeout,
            proxies=proxies,
            headers=HEADERS,
            impersonate="chrome120",
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"get_wiki failed for {name}: {e}")
        return []


def fetch_data_with_retry(post_id, retries=5, delay=3, timeout=10):
    """拉取单个 post 的元数据。
    - 永久错误（403/404/410/451，已删/不可访问）：立即抛 PermanentPostError，**不重试**。
      之前会被 raise_for_status 当 5xx 一样 5×3s=15s 浪费，并被上层记入 failed_ids
      让用户每次点重试都重新跑一遍同样的 404 循环。
    - 瞬时错误（429/5xx/超时/连接错）：仍按 retries × delay 重试；耗尽后返回 None。
    """
    url = f'https://{_HOST}/posts/{post_id}.json'
    PERMANENT_STATUS = {403, 404, 410, 451}
    attempt = 0
    while attempt < retries:
        try:
            r = requests.get(
                url,
                headers=HEADERS,
                proxies=PROXIES,
                impersonate="chrome120",
                timeout=timeout
            )
            if r.status_code in PERMANENT_STATUS:
                # 永久错误：不再重试，不再伪装成 None 让人误以为是瞬时失败
                raise PermanentPostError(post_id, r.status_code)
            r.raise_for_status()
            return r.json()
        except PermanentPostError:
            # 自己抛的，往上传，不算「重试次数」
            raise
        except Exception as e:
            attempt += 1
            print(f"请求ID {post_id} 失败 ({attempt}/{retries}): {e}")
            if attempt < retries:
                sleep(delay)
    return None

def download_image(url, folder, custom_print=print, retries=3, delay=3, raise_on_transient=False):
    """下载单张图片。瞬时错误（超时 / 连接错误 / 429 / 5xx）内部重试 retries 次。
    重试耗尽后：raise_on_transient=True 抛 TransientImageError（供桌面端记入「失败页」可重试），
    否则返回 None（独立 CLI 脚本的旧行为，不破坏现有调用）。
    永久错误（404 / 410 / 403 / 451，帖子已删除或不可访问）直接返回 None（不重试、不计失败页）。
    成功返回 filename。"""
    if not url: return None
    filename = url.split('/')[-1].split('?')[0] # 移除 URL 参数
    filepath = os.path.join(folder, filename)

    if os.path.exists(filepath):
        custom_print(f"文件已存在: {filename}")
        return filename

    PERMANENT = {403, 404, 410, 451}
    attempt = 0
    while attempt < retries:
        attempt += 1
        try:
            custom_print(f"正在下载: {filename} ...")
            r = requests.get(url, timeout=30, proxies=PROXIES, headers=HEADERS, impersonate="chrome120")
            if r.status_code == 200:
                with open(filepath, 'wb') as f:
                    f.write(r.content)
                custom_print(f"下载完成: {filename}")
                return filename
            if r.status_code in PERMANENT:
                custom_print(f"下载失败(已删除/不可用 {r.status_code}): {url}")
                return None
            # 其余状态码（429 / 5xx 等）视为瞬时，重试
            custom_print(f"下载失败(状态码 {r.status_code})，第 {attempt}/{retries} 次: {url}")
        except Exception as e:
            custom_print(f"下载出错(网络)，第 {attempt}/{retries} 次: {e}")
        if attempt < retries:
            sleep(delay)
    if raise_on_transient:
        raise TransientImageError(f"图片下载重试 {retries} 次仍失败: {filename}")
    custom_print(f"下载失败(网络，重试 {retries} 次仍失败): {filename}")
    return None

