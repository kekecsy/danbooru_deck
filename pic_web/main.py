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

PRESET_DIRS = [
    BASE_DIR / "present",
    PROJECT_ROOT / "mosaic_qt" / "present",
]

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


def convert_zip_to_gif(zip_file: Path, output_file: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp_dir_name:
        tmp_dir = Path(tmp_dir_name)
        with zipfile.ZipFile(zip_file) as archive:
            archive.extractall(tmp_dir)

        animation_json = tmp_dir / "animation.json"
        if not animation_json.exists():
            candidates = list(tmp_dir.glob("*/animation.json"))
            if not candidates:
                raise ValueError("ZIP 中未找到 animation.json")
            animation_json = candidates[0]
            tmp_dir = animation_json.parent

        with animation_json.open("r", encoding="utf-8") as file:
            payload = json.load(file)

        frames = payload.get("frames") or []
        if not frames:
            raise ValueError("animation.json 中没有 frames")

        concat_lines: List[str] = []
        for frame in frames:
            frame_path = tmp_dir / frame["file"]
            if not frame_path.exists():
                continue
            concat_lines.append(f"file '{frame_path.as_posix()}'")
            concat_lines.append(f"duration {float(frame.get('delay', 100)) / 1000}")

        if not concat_lines:
            raise ValueError("没有可用的帧图片")

        last_frame = tmp_dir / frames[-1]["file"]
        concat_lines.append(f"file '{last_frame.as_posix()}'")

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
        relative = path.relative_to(PROJECT_ROOT).as_posix()
        items.append({"name": path.name, "url": f"{root_path}/api/preset-file/{relative}"})
    return items


@app.get("/api/preset-file/{relative_path:path}")
def get_preset_file(relative_path: str) -> FileResponse:
    target = (PROJECT_ROOT / relative_path).resolve()
    allowed = [directory.resolve() for directory in PRESET_DIRS if directory.exists()]
    if not any(str(target).startswith(str(directory)) for directory in allowed):
        raise HTTPException(status_code=404, detail="preset not found")
    if not target.exists():
        raise HTTPException(status_code=404, detail="preset not found")
    return FileResponse(target)


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

    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
