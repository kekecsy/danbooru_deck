import base64
import io
import json
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFont


BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
INDEX_PATH = BASE_DIR / "index.html"
STATIC_DIR = BASE_DIR / "static"
STATIC_DIR.mkdir(exist_ok=True)

# 预设贴图目录：dev 用源码 pic_web/present，打包分发用 danbooru_DATA/present（见 runtime_paths.PRESET_DIR）。
try:
    from runtime_paths import PRESET_DIR
except ImportError:  # pic_web 单独运行（python pic_web/main.py）时的回退
    import sys as _sys

    _sys.path.insert(0, str(PROJECT_ROOT))
    from runtime_paths import PRESET_DIR

PRESET_DIRS = [PRESET_DIR]

# 帧名清单必须和 ugoira_meta 用**同一份实现**：main.py 是按 ugoira_meta 数出帧数去
# Danbooru 要延迟的，两边各写一份迟早会对不上（对不上的后果是延迟与帧错位，
# 但代码只会静默退回固定延迟，很难查）。ugoira_meta 只依赖标准库，导入很轻。
try:
    from ugoira_meta import frame_names_in_zip as _frame_names_in_zip
except ImportError:  # pic_web 单独运行（python pic_web/main.py）时的兜底
    import sys as _sys2

    _sys2.path.insert(0, str(PROJECT_ROOT))
    from ugoira_meta import frame_names_in_zip as _frame_names_in_zip

FONT_CANDIDATES = {
    "Arial": [
        Path(r"C:\Windows\Fonts\arial.ttf"),
        Path(r"C:\Windows\Fonts\ARIAL.TTF"),
    ],
    "Times New Roman": [
        Path(r"C:\Windows\Fonts\times.ttf"),
        Path(r"C:\Windows\Fonts\timesbd.ttf"),
    ],
    "Courier New": [
        Path(r"C:\Windows\Fonts\cour.ttf"),
        Path(r"C:\Windows\Fonts\courbd.ttf"),
    ],
    "Verdana": [
        Path(r"C:\Windows\Fonts\verdana.ttf"),
        Path(r"C:\Windows\Fonts\verdanab.ttf"),
    ],
    "Microsoft YaHei": [
        Path(r"C:\Windows\Fonts\msyh.ttc"),
        Path(r"C:\Windows\Fonts\msyhbd.ttc"),
    ],
    "SimHei": [
        Path(r"C:\Windows\Fonts\simhei.ttf"),
    ],
    "SimSun": [
        Path(r"C:\Windows\Fonts\simsun.ttc"),
    ],
}


app = FastAPI(title="Mosaic Web Editor")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def list_preset_files() -> List[Path]:
    preset_files: List[Path] = []
    seen: Set[str] = set()
    for preset_dir in PRESET_DIRS:
        if not preset_dir.exists():
            continue
        for path in sorted(preset_dir.iterdir()):
            if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".bmp", ".webp"}:
                continue
            key = path.name.lower()
            if key in seen:
                continue
            seen.add(key)
            preset_files.append(path)
    return preset_files


def clamp_rect(rect: Dict[str, Any], image_size: Tuple[int, int]) -> Tuple[int, int, int, int]:
    image_width, image_height = image_size
    x = max(0, int(round(float(rect.get("x", 0)))))
    y = max(0, int(round(float(rect.get("y", 0)))))
    width = max(1, int(round(float(rect.get("width", 1)))))
    height = max(1, int(round(float(rect.get("height", 1)))))

    if x >= image_width or y >= image_height:
        return 0, 0, 0, 0

    width = min(width, image_width - x)
    height = min(height, image_height - y)
    return x, y, width, height


def decode_data_url(data_url: str) -> Image.Image:
    if "," not in data_url:
        raise ValueError("invalid data url")
    _, encoded = data_url.split(",", 1)
    raw = base64.b64decode(encoded)
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def load_font(font_family: str, font_size: int):
    for candidate in FONT_CANDIDATES.get(font_family, []):
        if candidate.exists():
            return ImageFont.truetype(str(candidate), max(8, int(font_size)))
    for fallbacks in FONT_CANDIDATES.values():
        for candidate in fallbacks:
            if candidate.exists():
                return ImageFont.truetype(str(candidate), max(8, int(font_size)))
    return ImageFont.load_default()


def overlay_with_opacity(base: Image.Image, overlay: Image.Image, opacity: float) -> None:
    alpha = overlay.getchannel("A")
    alpha = alpha.point(lambda value: int(value * opacity))
    overlay.putalpha(alpha)
    base.alpha_composite(overlay)


def render_mosaic_fill(size: Tuple[int, int]) -> Image.Image:
    width, height = size
    image = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    block = 15
    for top in range(0, height, block):
        for left in range(0, width, block):
            use_light = ((left // block) + (top // block)) % 2 == 0
            color = (180, 180, 180, 255) if use_light else (120, 120, 120, 255)
            draw.rectangle(
                [left, top, min(left + block, width), min(top + block, height)],
                fill=color,
            )
    return image


def render_image_fill(size: Tuple[int, int], source: Image.Image) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    image = source.copy()
    image.thumbnail((width, height), Image.Resampling.LANCZOS)
    left = (width - image.width) // 2
    top = (height - image.height) // 2
    overlay.alpha_composite(image, (left, top))
    return overlay


def render_stripe_fill(size: Tuple[int, int], operation: Dict[str, Any]) -> Image.Image:
    width, height = size
    overlay = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    text = str(operation.get("stripeText") or "该信息已被管理员撤回")
    font_family = str(operation.get("stripeFontFamily") or "Times New Roman")
    font_size = int(operation.get("stripeFontSize") or 25)
    orientation = str(operation.get("stripeOrientation") or "horizontal")
    font = load_font(font_family, font_size)

    if orientation == "vertical":
        bbox = draw.textbbox((0, 0), "测", font=font)
        line_height = max(1, bbox[3] - bbox[1])
        total_height = line_height * len(text)
        start_y = (height - total_height) / 2
        for index, char in enumerate(text):
            char_bbox = draw.textbbox((0, 0), char, font=font)
            char_width = char_bbox[2] - char_bbox[0]
            x = (width - char_width) / 2
            y = start_y + index * line_height
            draw.text((x, y), char, fill=(0, 0, 0, 255), font=font)
    else:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (width - text_width) / 2
        y = (height - text_height) / 2
        draw.text((x, y), text, fill=(0, 0, 0, 255), font=font)

    return overlay


def render_operation(base: Image.Image, operation: Dict[str, Any]) -> None:
    x, y, width, height = clamp_rect(operation, base.size)
    if width <= 0 or height <= 0:
        return

    fill_mode = str(operation.get("fillMode") or "mosaic")
    opacity = float(operation.get("opacity", 1))
    opacity = max(0.1, min(1.0, opacity))

    if fill_mode == "image":
        data_url = operation.get("imageDataUrl")
        if not data_url:
            return
        fill = render_image_fill((width, height), decode_data_url(data_url))
    elif fill_mode == "stripe":
        fill = render_stripe_fill((width, height), operation)
    else:
        fill = render_mosaic_fill((width, height))

    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    layer.alpha_composite(fill, (x, y))
    overlay_with_opacity(base, layer, opacity)


def apply_output_resize(image: Image.Image, max_edge: Optional[int]) -> Image.Image:
    if not max_edge or max_edge <= 0:
        return image
    current_max = max(image.width, image.height)
    if current_max <= max_edge:
        return image
    scale = max_edge / current_max
    target = (max(1, int(image.width * scale)), max(1, int(image.height * scale)))
    return image.resize(target, Image.Resampling.LANCZOS)


def check_ffmpeg_available() -> bool:
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True, check=False)
        return result.returncode == 0
    except OSError:
        return False


def convert_zip_to_gif(zip_file: Path, output_file: Path,
                       delays: Optional[List[int]] = None,
                       default_delay_ms: int = 100) -> Dict[str, Any]:
    """把 ugoira zip 转成 GIF，返回 {"frames": int, "delay_source": str}。

    帧延迟按优先级取：
      1. `animation.json`（Pixiv 原始帧表，逐帧精确）—— 新 zip 都带；
      2. 调用方传进来的 `delays`（毫秒列表）—— **旧 zip 没有 animation.json**，
         由 `ugoira_meta` 去 Danbooru 找出帧延迟后传进来；
      3. `default_delay_ms` 固定延迟 —— 兜底，保证至少「转得出来」。

    ⚠ `delays` 必须与帧数**长度相等**才用：错位的延迟会让整段动画错拍，
      比均匀延迟难看得多，所以宁可退回第 3 条。
    ⚠ 帧名清单走 `ugoira_meta.frame_names_in_zip`（与 main.py 数帧数的那份同一实现），
      自己再写一份枚举迟早会两边对不上。
    """
    with tempfile.TemporaryDirectory() as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        with zipfile.ZipFile(zip_file) as archive:
            archive.extractall(tmp_dir)

        animation_json = tmp_dir / "animation.json"
        if not animation_json.exists():
            candidates = list(tmp_dir.glob("*/animation.json"))
            if candidates:
                animation_json = candidates[0]
                tmp_dir = animation_json.parent

        # 组装 (帧文件, 延迟毫秒) 的执行计划
        plan: List[Tuple[Path, float]] = []
        delay_source = "default"

        frames = []
        if animation_json.exists():
            try:
                with animation_json.open("r", encoding="utf-8") as file:
                    payload = json.load(file)
                frames = payload.get("frames") or []
            except Exception:
                frames = []   # 坏了就当没有，走下面的兜底（旧 zip 路径）
        for frame in frames:
            try:
                frame_path = tmp_dir / str(frame["file"])
            except (KeyError, TypeError):
                continue
            if not frame_path.exists():
                continue
            try:
                delay_ms = float(frame.get("delay", default_delay_ms))
            except (TypeError, ValueError):
                delay_ms = float(default_delay_ms)
            plan.append((frame_path, delay_ms if delay_ms > 0 else float(default_delay_ms)))
        if plan:
            delay_source = "animation_json"

        if not plan:
            # 旧 zip 路径：没有（或不可用的）animation.json → 以 zip 清单为帧序
            names = _frame_names_in_zip(zip_file)
            use_delays: List[Optional[float]] = [None] * len(names)
            if delays and len(delays) == len(names):
                for i, d in enumerate(delays):
                    try:
                        val = float(d)
                    except (TypeError, ValueError):
                        continue
                    use_delays[i] = val if val > 0 else None
                delay_source = "delays"
            for name, delay in zip(names, use_delays):
                frame_path = tmp_dir / name
                if not frame_path.exists():
                    continue
                plan.append((frame_path, delay if delay else float(default_delay_ms)))

        if not plan:
            raise ValueError("ZIP 里没有可用的帧图片")

        concat_lines: List[str] = []
        for frame_path, delay_ms in plan:
            concat_lines.append(f"file '{frame_path.as_posix()}'")
            concat_lines.append(f"duration {delay_ms / 1000}")

        # ffmpeg 的 concat demuxer 会丢掉最后一段 duration，末帧要再写一次才停得住。
        concat_lines.append(f"file '{plan[-1][0].as_posix()}'")

        concat_path = tmp_dir / "frames.txt"
        concat_path.write_text("\n".join(concat_lines), encoding="utf-8")

        result = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(concat_path),
                "-vf",
                "scale=640:-1:flags=lanczos",
                "-loop",
                "0",
                str(output_file),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not output_file.exists():
            raise RuntimeError(result.stderr or "ffmpeg 转换失败")

        return {"frames": len(plan), "delay_source": delay_source}


@app.get("/", response_class=HTMLResponse)
def read_index() -> HTMLResponse:
    if not INDEX_PATH.exists():
        return HTMLResponse("<h1>index.html not found</h1>", status_code=404)
    return HTMLResponse(INDEX_PATH.read_text(encoding="utf-8"))


@app.get("/api/health")
def health_check() -> Dict[str, Any]:
    return {"ok": True, "presets": len(list_preset_files()), "ffmpeg": check_ffmpeg_available()}


@app.get("/api/presets")
def get_presets(request: Request) -> List[Dict[str, str]]:
    items: List[Dict[str, str]] = []
    root_path = request.scope.get("root_path", "").rstrip("/")
    for path in list_preset_files():
        items.append({"name": path.name, "url": f"{root_path}/api/preset-file/{path.name}"})
    return items


@app.get("/api/preset-file/{filename:path}")
def get_preset_file(filename: str) -> FileResponse:
    # 只按文件名在预设目录里查找，避免路径穿越；PRESET_DIR 可能位于 danbooru_DATA 之下。
    safe_name = Path(filename).name
    for directory in PRESET_DIRS:
        target = (directory / safe_name).resolve()
        try:
            target.relative_to(directory.resolve())
        except ValueError:
            continue
        if target.exists() and target.is_file():
            return FileResponse(target)
    raise HTTPException(status_code=404, detail="preset not found")


@app.post("/api/render")
async def render_image(
    source_image: UploadFile = File(...),
    operations_json: str = Form("[]"),
    max_edge: Optional[int] = Form(None),
) -> StreamingResponse:
    try:
        operations = json.loads(operations_json)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail=f"operations_json 无法解析: {exc}") from exc

    raw = await source_image.read()
    try:
        image = Image.open(io.BytesIO(raw)).convert("RGBA")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"图片无法读取: {exc}") from exc

    for operation in operations:
        if isinstance(operation, dict):
            render_operation(image, operation)

    image = apply_output_resize(image, max_edge)

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={"Content-Disposition": 'attachment; filename="mosaic_export.png"'},
    )


@app.post("/api/zip-to-gif")
async def zip_to_gif(zip_file: UploadFile = File(...)) -> StreamingResponse:
    if not zip_file.filename or not zip_file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 ZIP 文件")
    if not check_ffmpeg_available():
        raise HTTPException(status_code=400, detail="系统中未找到 ffmpeg，无法转换 ZIP")

    with tempfile.TemporaryDirectory() as temp_dir_name:
        temp_dir = Path(temp_dir_name)
        source_path = temp_dir / zip_file.filename
        output_path = temp_dir / f"{Path(zip_file.filename).stem}.gif"

        with source_path.open("wb") as file:
            shutil.copyfileobj(zip_file.file, file)

        try:
            convert_zip_to_gif(source_path, output_path)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        content = output_path.read_bytes()

    return StreamingResponse(
        io.BytesIO(content),
        media_type="image/gif",
        headers={"Content-Disposition": f'attachment; filename="{Path(zip_file.filename).stem}.gif"'},
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="127.0.0.1", port=18765, reload=True)
