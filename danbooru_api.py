import os
import socket
import urllib.parse
from time import sleep
from curl_cffi import requests
from my_utils import get_proxies_for_url

COOKIES = "_danbooru2_session=dzlzD2gYvCdxOQfBzhHUpXyPWZdvd8kXWARK6n0KdX4VDPgzGj9sLOyHfTrMVFdFpaJLHAP3LfMiptyeQeiNGE1yNM8tY7IGQXtYV2u8aFKglH7khCVW8FVqurTZSNR25VdDBgoTBDzu9p/gTeTrazCCWzLRPlg7hglvs6F6Xmfd7VVN/Mb+HbA5y7HAykGYk9kXDOTbE5s/HTOvPZd3hT6t/WcVUL8VlEW0nv1aiJt2h0byWwJBgBDGIvPgTebOWaH+xlRuqaHPhU0BmTEP+MtffzowVs9EQBUaO6LCky5e+fYQmcxXl68ANSAF/DQmp1EqppEU/TDW86rPMwoLCJZmIfC+XaAe5Z+PnwLV+DeOrBVtWdYWa8klazul6KqGHX8W6Z7WYMOoB9LpDfCzVnyDXOqCA+w2wbM2GuAUI7uH3A3Nuc/Z73esVF/qhN3CyImze/KOleoApwSRSjMXeb6oSpks1MvFGvN2lADMAtibu3cEfpC0glc+0YieVxc2J8TTQFVAhhSb+PYzohNdDR533ubSH5fikI2D8hiZmR1WSl1gWTol2eDkohCDmi5S+tqGOE1em0ZzJ/lpdfBhoJcwMYMA7dWp--YLXy4wFzNb8Zce8g--/4vINxtQJS/LT/9J9QoqKA=="
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:150.0) Gecko/20100101 Firefox/150.0"

HEADERS = {
    "User-Agent": USER_AGENT,
    "Cookie": COOKIES,
    "Referer": "https://danbooru.donmai.us/posts",
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


_RAW_PROXIES = get_proxies_for_url("https://danbooru.donmai.us")
if _RAW_PROXIES and not _proxy_alive(_RAW_PROXIES):
    # 环境变量里写了代理但端口没人监听（典型场景：Clash/v2ray 没开），
    # 用户大概率本身可以直连 —— 直接清空 PROXIES，否则 curl_cffi 会卡死在
    # "Failed to connect to 127.0.0.1 port 7897" 然后整个抓图任务失败。
    print(f"[proxy] 检测到环境变量代理 {_RAW_PROXIES} 不可达，自动改用直连。如需走代理请先启动代理软件再重启后端。")
    PROXIES = {}
else:
    PROXIES = _RAW_PROXIES

def check_proxy_simple():
    url = "https://danbooru.donmai.us"
    try:
        resp = requests.get(url, timeout=10, proxies=PROXIES, headers=HEADERS, impersonate="chrome120")
        return resp.status_code == 200
    except:
        return False

def get_posts_by_rank(page, timeout=20):
    params = {
        "d": "1",
        "page": page,
        "tags": "order:rank"
    }
    r = requests.get(
        "https://danbooru.donmai.us/posts.json",
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
        "https://danbooru.donmai.us/explore/posts/popular.json",
        params=params,
        headers=HEADERS,
        proxies=PROXIES,
        impersonate="chrome120",
        timeout=timeout
    )
    r.raise_for_status()
    return r.json()

def fetch_data_with_retry(post_id, retries=5, delay=3, timeout=10):
    url = f'https://danbooru.donmai.us/posts/{post_id}.json'
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
            r.raise_for_status()
            return r.json()
        except Exception as e:
            attempt += 1
            print(f"请求ID {post_id} 失败 ({attempt}/{retries}): {e}")
            sleep(delay)
    return None

def download_image(url, folder, custom_print=print):
    if not url: return None
    filename = url.split('/')[-1].split('?')[0] # 移除 URL 参数
    filepath = os.path.join(folder, filename)

    if os.path.exists(filepath):
        custom_print(f"文件已存在: {filename}")
        return filename

    try:
        custom_print(f"正在下载: {filename} ...")
        r = requests.get(url, timeout=30, proxies=PROXIES, headers=HEADERS, impersonate="chrome120")
        if r.status_code == 200:
            with open(filepath, 'wb') as f:
                f.write(r.content)
            custom_print(f"下载完成: {filename}")
            return filename
        else:
            custom_print(f"下载失败 (状态码 {r.status_code}): {url}")
            return None
    except Exception as e:
        custom_print(f"下载出错: {e}")
        return None
