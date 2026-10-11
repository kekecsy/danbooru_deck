"""旧 ugoira zip 缺 `animation.json` 时，去 Danbooru 找帧延迟。

## 为什么需要这个模块

`pic_web.convert_zip_to_gif` 只认 zip 里的 `animation.json`（Pixiv ugoira 的帧表），
早期从 Danbooru 下载的 zip 里**根本没有这个文件**（实测本地 hot_pic 96 个 zip：
58 个有、38 个没有），于是转换直接 `ValueError("ZIP 中未找到 animation.json")`
—— 旧 zip 一张都转不出来。而帧延迟只能从 Danbooru 拿。

## Danbooru 侧实测结论（2026-10，用项目自己的 HEADERS + 令牌桶真跑过）

- `/posts/{id}.json` 的 `media_asset` **只有** `id / file_ext / file_size /
  image_width / image_height / duration / pixel_hash / status …`；
  **没有** `frame_delays` / `frame_count` / `variants`（variants 是 null）。
- `media_assets.metadata`（`Ugoira:FrameDelays` / `Ugoira:FrameCount` /
  `Ugoira:FrameOffsets` / `Ugoira:AnimationJsonFormat`）**在 JSON API 里读不到**：
  `?only=…,metadata` 会被静默丢掉，只回 `{"id": …}`。
- 它**只渲染在** `GET /media_assets/{id}` 的 HTML 表格里（公开、匿名可读；
  ⚠ 而且 **SFW host（safebooru）下照样能读 e 级资源** —— 这一点和搜索不同，
  搜索在 safebooru 下只有 `rating:g` 会回来）。
- ⚠⚠ **`duration` 恰好等于所有帧延迟之和**（4/4 实测逐位相等：665 / 5000 / 4500 /
  4700 ms）→ 抓不到页面时可以用 `duration ÷ 帧数` 退化成均匀延迟，**总时长仍然精确**。
- 实测延迟分布：4 例里 3 例本来就完全均匀（19×35 / 150×30 / 94×50），
  第 4 例是 120/130 交替（均值 125，正是 duration/N）。也就是说均匀近似只在
  「有长停顿帧」的动画上才会明显失真 —— 所以精确路径仍然值得优先走。
- 帧数 = zip 里图片文件的个数（实测 19/40/150/94 与 Danbooru 的
  `Ugoira:FrameCount` 完全一致），所以不需要额外请求就能对上。

## 兜底链

`resolve_frame_delays()`：抓 media_asset 页面（精确）→ `duration ÷ 帧数`（估算）
→ `None`（交给调用方用固定延迟）。**任何一步失败都只是降级，不抛异常**：
转 GIF 失败才是真失败，延迟不准只是观感问题。
"""

from __future__ import annotations

import html as _html
import re
import zipfile
from pathlib import Path
from typing import Callable, List, Optional

# ugoira 的帧就是这些图片（旧 zip 里是裸的 000000.jpg / 000001.jpg…）。
FRAME_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".avif", ".gif"}

POST_ID_RE = re.compile(r"/posts/(\d+)")

# media_asset 页面里那一行：<th ...>Frame Delays</th><td ...>[35, 35, …]</td>
# ⚠ 用 DOTALL 跨行匹配；页面改版会让它失配 —— 失配只降级，不报错。
_FRAME_DELAYS_TD_RE = re.compile(r"Frame\s*Delays\s*</th>\s*<td[^>]*>(.*?)</td>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_BRACKET_LIST_RE = re.compile(r"\[([0-9,\s]+)\]")

# duration 的合理性区间（秒）：0 是脏数据，超过 10 分钟基本是解析错。
_MIN_DURATION_S = 0.001
_MAX_DURATION_S = 600.0
# 均匀延迟的夹取区间（毫秒）：GIF 的延迟粒度是 1cs = 10ms，低于 20ms 浏览器还会
# 统一按 100ms 处理，所以下限取 20 比 10 更贴近实际观感。
_MIN_UNIFORM_MS = 20
_MAX_UNIFORM_MS = 2000


def post_id_from_url(url: str) -> str:
    """从 `https://danbooru.donmai.us/posts/7164656` 里抠出 `7164656`。"""
    m = POST_ID_RE.search(url or "")
    return m.group(1) if m else ""


def _natural_key(name: str):
    """自然排序的 key：`2.jpg` < `10.jpg`（纯字典序会反过来）。"""
    return [int(part) if part.isdigit() else part.lower()
            for part in re.split(r"(\d+)", name)]


def frame_names_in_zip(zip_path) -> List[str]:
    """zip 里参与动画的帧名（zip 内路径，自然排序）。

    - 跳过目录项、`__MACOSX`、隐藏文件、`animation.json`、非图片扩展名；
    - 容忍一层子目录（新一点的 zip 会把帧放在子目录里）；
    - 按**基名**自然排序，`000000.jpg`… 因此顺序永远正确。
    """
    with zipfile.ZipFile(zip_path) as archive:
        names: List[str] = []
        for info in archive.infolist():
            if info.is_dir():
                continue
            raw = info.filename.replace("\\", "/")
            if "__MACOSX" in raw:
                continue
            base = Path(raw).name
            if not base or base.startswith("."):
                continue
            if base.lower() == "animation.json":
                continue
            if Path(base).suffix.lower() not in FRAME_EXTS:
                continue
            names.append(raw)
    return sorted(names, key=lambda n: _natural_key(Path(n).name))


def frame_count_of_zip(zip_path) -> int:
    """帧数（= Danbooru 的 `Ugoira:FrameCount`，实测一致）。"""
    try:
        return len(frame_names_in_zip(zip_path))
    except Exception:
        return 0


def has_animation_json(zip_path) -> bool:
    """zip 里有没有 `animation.json`（根目录或一层子目录，与转换端同一判据）。

    用来**不联网**地筛出「需要去 Danbooru 补延迟」的旧 zip；判错只会多/少一次
    网路请求，转换端自己还会按 animation.json 的实际可用性再决定一次。
    """
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for name in archive.namelist():
                if Path(name).name.lower() == "animation.json":
                    return True
    except Exception:
        return False
    return False


def parse_frame_delays(page_html: str) -> Optional[List[int]]:
    """从 media_asset 页面 HTML 里抠出 Frame Delays（毫秒整数列表）。

    先找方括号数组（页面就是这么渲染的），找不到再退回「取这一格里所有整数」。
    返回 None 表示这一格不存在或不是延迟数组 —— 调用方按「拿不到」处理。
    """
    m = _FRAME_DELAYS_TD_RE.search(page_html or "")
    if not m:
        return None
    text = _html.unescape(_TAG_RE.sub("", m.group(1)))
    bracket = _BRACKET_LIST_RE.search(text)
    if bracket:
        nums = [int(x) for x in re.findall(r"\d+", bracket.group(1))]
        return nums or None
    nums = [int(x) for x in re.findall(r"\d+", text)]
    return nums or None


# ---------------- 网络层（测试可注入） ----------------


def _default_get_json(url: str, timeout: float = 20.0):
    import danbooru_api
    import http_client

    r = http_client.request(
        "GET", url, kind="json", retries=1, timeout=timeout,
        headers=danbooru_api.HEADERS, proxies=danbooru_api.PROXIES,
        limiter=http_client.danbooru_limiter,
    )
    return r.json()


def _default_get_text(url: str, timeout: float = 20.0) -> str:
    """取 HTML（Accept: text/html）。⚠ 走 json 类别 + 同一个令牌桶：
    1 req/s 是账号级限制，页面请求同样要排队，不能绕过去猛打。"""
    import danbooru_api
    import http_client

    headers = dict(danbooru_api.HEADERS)
    headers["Accept"] = "text/html"
    r = http_client.request(
        "GET", url, kind="json", retries=1, timeout=timeout,
        headers=headers, proxies=danbooru_api.PROXIES,
        limiter=http_client.danbooru_limiter,
    )
    return r.text


def _host() -> str:
    import danbooru_api

    return danbooru_api.get_host()


def _uniform_delays(duration, frame_count: int) -> Optional[List[int]]:
    """`duration ÷ 帧数` 的均匀延迟（总时长精确，逐帧被抹平）。"""
    try:
        seconds = float(duration)
    except (TypeError, ValueError):
        return None
    if not (_MIN_DURATION_S <= seconds <= _MAX_DURATION_S) or frame_count <= 0:
        return None
    per = int(round(seconds * 1000.0 / frame_count))
    per = max(_MIN_UNIFORM_MS, min(_MAX_UNIFORM_MS, per))
    return [per] * frame_count


def resolve_frame_delays(post_id, frame_count: int,
                         *, get_json: Optional[Callable] = None,
                         get_text: Optional[Callable] = None,
                         log: Optional[Callable] = None) -> dict:
    """给一个缺 animation.json 的 zip 找帧延迟。

    返回 `{"delays": [int] | None, "source": "danbooru"|"duration"|"", "detail": str}`：
    - `source == "danbooru"`：逐帧精确（页面里的 Frame Delays，长度已与 zip 帧数对齐）；
    - `source == "duration"`：用 `duration ÷ 帧数` 估的均匀延迟（总时长精确）；
    - `source == ""` / `delays is None`：什么都没拿到，调用方用固定延迟兜底。

    ⚠ 长度对不上就**不用**页面里的延迟：帧数和延迟一旦错位，动画会整体错拍，
      比均匀延迟难看得多。此时退回 duration（用本地帧数，自洽），并把原因写进 detail。
    """
    def _log(msg: str) -> None:
        if log:
            try:
                log(msg)
            except Exception:
                pass

    result = {"delays": None, "source": "", "detail": ""}
    pid = str(post_id or "").strip()
    if not pid:
        result["detail"] = "没有帖子链接"
        return result

    if get_json is None:
        get_json = _default_get_json
    if get_text is None:
        get_text = _default_get_text

    # ① 帖子 JSON：只要能拿到 media_asset.id（精确路径的入口）和 duration（兜底用）
    ma_id, duration = None, None
    try:
        post = get_json(f"https://{_host()}/posts/{pid}.json") or {}
        asset = post.get("media_asset") or {}
        ma_id = asset.get("id")
        duration = asset.get("duration")
    except Exception as e:
        _log(f"[ugoira] 帖子 {pid} 元数据取不到（{type(e).__name__}: {e}），改用固定延迟")
        result["detail"] = "帖子元数据取不到"
        return result

    # ② 精确路径：media_asset 页面的 Frame Delays
    if ma_id:
        try:
            page = get_text(f"https://{_host()}/media_assets/{ma_id}") or ""
            delays = parse_frame_delays(page)
        except Exception as e:
            delays = None
            _log(f"[ugoira] media_asset {ma_id} 页面取不到（{type(e).__name__}: {e}）")
        if delays:
            if len(delays) == frame_count:
                result.update(delays=delays, source="danbooru",
                              detail=f"Danbooru Frame Delays（{len(delays)} 帧）")
                return result
            _log(f"[ugoira] 帖子 {pid} 的 Frame Delays 有 {len(delays)} 条，"
                 f"本地 zip 是 {frame_count} 帧 —— 不对齐，退回均匀延迟")

    # ③ 兜底：duration ÷ 帧数
    delays = _uniform_delays(duration, frame_count)
    if delays:
        result.update(delays=delays, source="duration",
                      detail=f"Danbooru duration={duration}s ÷ {frame_count} 帧 ≈ {delays[0]}ms")
        return result

    result["detail"] = "Danbooru 没有可用的时长信息"
    return result
