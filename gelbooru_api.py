import os
from time import sleep

from curl_cffi import requests

import danbooru_api


BASE_URL = "https://gelbooru.com/index.php"
HOST = "gelbooru.com"
BASE_URL_PARAMS = {
    "page": "dapi",
    "json": 1,
    "s": "post",
    "q": "index",
}

HEADERS = {
    "User-Agent": danbooru_api.USER_AGENT,
    "Referer": "https://gelbooru.com/index.php?page=post&s=list",
    "Accept": "application/json",
}


class TransientImageError(Exception):
    pass


def get_host():
    return HOST


def post_url(post_id):
    raw = str(post_id or "")
    if raw.startswith("gelbooru:"):
        raw = raw.split(":", 1)[1]
    return f"https://gelbooru.com/index.php?page=post&s=view&id={raw}"


def _source_key(post_id):
    return f"gelbooru:{post_id}"


def _as_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _normalize_posts(raw_posts):
    posts = []
    for raw in raw_posts or []:
        if not isinstance(raw, dict):
            continue
        raw_id = raw.get("id")
        file_url = raw.get("file_url") or raw.get("sample_url")
        if not raw_id or not file_url:
            continue
        if isinstance(file_url, str) and file_url.startswith("//"):
            file_url = "https:" + file_url

        tags = raw.get("tags") or ""
        filename = raw.get("image") or file_url.split("/")[-1].split("?")[0]
        rating = raw.get("rating") or ""
        meta_parts = ["source:gelbooru"]
        if rating:
            meta_parts.append(f"rating:{rating}")

        posts.append({
            "id": _source_key(raw_id),
            "source_id": str(raw_id),
            "_source": "gelbooru",
            "file_url": file_url,
            "image": filename,
            "md5": raw.get("md5") or "",
            "post_url": post_url(raw_id),
            "score": _as_int(raw.get("score"), 0),
            "fav_count": _as_int(raw.get("fav_count") or raw.get("favorite_count"), 0),
            "rating": rating,
            "tag_string": tags,
            "tag_string_general": tags,
            "tag_string_character": "",
            "tag_string_copyright": "",
            "tag_string_artist": "",
            "tag_string_meta": " ".join(meta_parts),
        })
    return posts


def get_posts_by_tags(tags, page, limit=100, timeout=20):
    """Fetch one Gelbooru API page synchronously.

    The desktop UI is 1-based; Gelbooru dapi uses 0-based pid.
    """
    pid = max(_as_int(page, 1) - 1, 0)
    params = {
        **BASE_URL_PARAMS,
        "tags": tags,
        "pid": pid,
        "limit": max(1, min(100, _as_int(limit, 100))),
    }
    r = requests.get(
        BASE_URL,
        params=params,
        headers=HEADERS,
        proxies=danbooru_api.PROXIES,
        impersonate="chrome120",
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        return _normalize_posts(data)
    post_data = data.get("post", []) if isinstance(data, dict) else []
    if isinstance(post_data, dict):
        post_data = [post_data]
    return _normalize_posts(post_data)


def download_image(url, folder, custom_print=print, retries=3, delay=3, raise_on_transient=False):
    if not url:
        return None
    filename = url.split("/")[-1].split("?")[0]
    filepath = os.path.join(folder, filename)

    if os.path.exists(filepath):
        custom_print(f"文件已存在: {filename}")
        return filename

    permanent = {403, 404, 410, 451}
    attempt = 0
    while attempt < retries:
        attempt += 1
        try:
            custom_print(f"正在下载: {filename} ...")
            r = requests.get(
                url,
                timeout=30,
                proxies=danbooru_api.PROXIES,
                headers=HEADERS,
                impersonate="chrome120",
            )
            if r.status_code == 200:
                with open(filepath, "wb") as f:
                    f.write(r.content)
                custom_print(f"下载完成: {filename}")
                return filename
            if r.status_code in permanent:
                custom_print(f"下载失败(已删除/不可用 {r.status_code}): {url}")
                return None
            custom_print(f"下载失败(状态码 {r.status_code})，第 {attempt}/{retries} 次: {url}")
        except Exception as e:
            custom_print(f"下载出错(网络)，第 {attempt}/{retries} 次: {e}")
        if attempt < retries:
            sleep(delay)

    if raise_on_transient:
        raise TransientImageError(f"图片下载重试 {retries} 次仍失败: {filename}")
    custom_print(f"下载失败(网络，重试 {retries} 次仍失败): {filename}")
    return None
