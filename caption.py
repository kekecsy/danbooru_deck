import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types

from caption_prompt import (
    DANBOORU_SYSTEM_PROMPT,
    build_user_prompt,
    PIPELINE_SYSTEM_PROMPT,
    OBSERVE_USER_PROMPT,
    OBSERVE_SCHEMA,
    build_verify_user_prompt,
    VERIFY_SCHEMA,
    build_compose_user_prompt,
)

# Windows 控制台 UTF-8 输出
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except AttributeError:
    pass

load_dotenv()
api_key = os.getenv("google_api_key")

# 国内访问 Gemini 需要走代理（.env 可配置 https_proxy；默认尝试本地 Clash 7897）
proxy = os.getenv("https_proxy") or os.getenv("HTTPS_PROXY") or "http://127.0.0.1:7897"
os.environ["HTTPS_PROXY"] = proxy
os.environ["HTTP_PROXY"] = proxy

if not api_key:
    print("❌ 错误: 未在 .env 文件中找到 'google_api_key'，请检查配置。")
    sys.exit(1)

try:
    client = genai.Client(api_key=api_key)
except Exception as e:
    print(f"❌ 初始化 Gemini 客户端失败: {e}")
    sys.exit(1)


DANBOORU_SYSTEM_PROMPT = DANBOORU_SYSTEM_PROMPT  # 从 caption_prompt 重导出，保留向后兼容





def find_metadata(image_path: Path) -> dict | None:
    """在图片所在目录找 viewer_data.json 并按 filename 匹配。"""
    viewer_json = image_path.parent / "viewer_data.json"
    if not viewer_json.exists():
        print(f"⚠️  未找到 {viewer_json}，将不使用 tag 上下文。")
        return None

    try:
        with open(viewer_json, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"⚠️  读取 {viewer_json} 失败: {e}")
        return None

    target = image_path.name
    for entry in data:
        if entry.get("filename") == target:
            return entry
    print(f"⚠️  在 viewer_data.json 中未找到 {target} 的条目，将不使用 tag 上下文。")
    return None


def generate_caption(image_path: Path, meta: dict | None, include_artist: bool = False) -> str:
    img = Image.open(image_path)
    user_prompt = build_user_prompt(meta, include_artist=include_artist)

    print("🤖 正在分析图片并生成描述，请稍候...")
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[img, user_prompt],
        config=types.GenerateContentConfig(
            system_instruction=DANBOORU_SYSTEM_PROMPT,
            temperature=0.4,
        ),
    )
    return (response.text or "").strip()


# ---------------------------------------------------------------------------
# 3 阶段管线
# ---------------------------------------------------------------------------

PIPELINE_MODEL = "gemini-2.5-flash"


def _parse_json_response(text: str) -> dict:
    """Gemini 在 response_mime_type=application/json 时一般直接返回干净 JSON，
    但偶尔会带 ```json 围栏；做一次容错。"""
    if not text:
        raise ValueError("空响应")
    stripped = text.strip()
    if stripped.startswith("```"):
        # 去掉 ```json ... ``` 围栏
        stripped = stripped.split("```", 2)
        # 形态可能是 ['', 'json\n{...}', ''] 或 ['', '{...}', '']
        body = stripped[1] if len(stripped) >= 2 else ""
        if body.lower().startswith("json"):
            body = body[4:]
        stripped = body.strip()
    return json.loads(stripped)


def _send_observe(chat, image_path: Path) -> dict:
    img = Image.open(image_path)
    resp = chat.send_message(
        [img, OBSERVE_USER_PROMPT],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=OBSERVE_SCHEMA,
            temperature=0.2,
        ),
    )
    return _parse_json_response(resp.text or "")


def _send_verify(chat, meta: dict | None) -> dict:
    resp = chat.send_message(
        build_verify_user_prompt(meta),
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=VERIFY_SCHEMA,
            temperature=0.2,
        ),
    )
    return _parse_json_response(resp.text or "")


def _send_compose(
    chat,
    meta: dict | None,
    include_artist: bool,
    verify_result: dict | None,
    *,
    skip_verify_note: bool = False,
) -> str:
    prompt = build_compose_user_prompt(
        meta,
        include_artist=include_artist,
        verify_result=verify_result,
        skip_verify_note=skip_verify_note,
    )
    resp = chat.send_message(
        prompt,
        config=types.GenerateContentConfig(temperature=0.6),
    )
    return (resp.text or "").strip()


def _fallback_caption_from_observe(observe: dict) -> str:
    """stage 3 失败时，用 stage 1 的 JSON 拼一段最小可用的兜底中文。"""
    if not isinstance(observe, dict):
        return ""
    parts = []
    chars = observe.get("characters") or []
    if chars:
        c0 = chars[0]
        seg = f"画面中一位角色，{c0.get('hair_color', '') or ''}{c0.get('hair_style', '') or ''}"
        eye = c0.get("eye_color")
        if eye:
            seg += f"，{eye}色瞳"
        feats = c0.get("distinguishing_features") or []
        if feats:
            seg += "，" + "、".join(feats)
        expr = c0.get("expression")
        if expr:
            seg += f"，神情{expr}"
        parts.append(seg.rstrip("，") + "。")
    outfit = observe.get("outfit_layers") or []
    if outfit:
        items = "、".join(
            f"{o.get('color', '')}{o.get('item', '')}".strip()
            for o in outfit if (o.get("item") or o.get("color"))
        )
        if items:
            parts.append(f"身上着 {items}。")
    bg = observe.get("background") or {}
    env = bg.get("environment")
    mood = bg.get("mood")
    if env or mood:
        parts.append(f"背景为{env or '简约'}，氛围{mood or '安静'}。")
    return "".join(parts)


def generate_caption_3stage(
    image_path: Path,
    meta: dict | None,
    include_artist: bool = False,
    *,
    debug: bool = False,
) -> dict:
    """3 阶段管线：observe → verify → compose。

    返回:
      {
        "pipeline": "3stage" | "3stage-partial" | "single-fallback",
        "caption": str,
        "observe": dict | None,
        "verify": dict | None,
        "stages_ok": [bool, bool, bool],
        "stage_errors": {1?: str, 2?: str, 3?: str},
      }
    """
    stage_errors: dict[int, str] = {}
    observe: dict | None = None
    verify: dict | None = None
    stages_ok = [False, False, False]

    chat = client.chats.create(
        model=PIPELINE_MODEL,
        config=types.GenerateContentConfig(
            system_instruction=PIPELINE_SYSTEM_PROMPT,
        ),
    )

    # Stage 1: observe
    print("🤖 [1/3] 观察阶段：仅基于图像生成结构化 JSON...", file=sys.stderr)
    try:
        observe = _send_observe(chat, image_path)
        stages_ok[0] = True
        if debug:
            print("--- stage 1 (observe) ---", file=sys.stderr)
            print(json.dumps(observe, ensure_ascii=False, indent=2), file=sys.stderr)
    except Exception as e:
        stage_errors[1] = f"{type(e).__name__}: {e}"
        print(f"⚠️  stage 1 失败: {stage_errors[1]}，回退到单轮模式。", file=sys.stderr)
        # 全管线失败：直接降级到单轮
        try:
            caption = generate_caption(image_path, meta, include_artist=include_artist)
            return {
                "pipeline": "single-fallback",
                "caption": caption,
                "observe": None,
                "verify": None,
                "stages_ok": stages_ok,
                "stage_errors": stage_errors,
            }
        except Exception as e2:
            stage_errors[0] = f"{type(e2).__name__}: {e2}"
            return {
                "pipeline": "failed",
                "caption": "",
                "observe": None,
                "verify": None,
                "stages_ok": stages_ok,
                "stage_errors": stage_errors,
            }

    # Stage 2: verify
    print("🤖 [2/3] 校验阶段：把 tags 喂给模型做 visible/absent 标注...", file=sys.stderr)
    try:
        verify = _send_verify(chat, meta)
        stages_ok[1] = True
        if debug:
            print("--- stage 2 (verify) ---", file=sys.stderr)
            print(json.dumps(verify, ensure_ascii=False, indent=2), file=sys.stderr)
    except Exception as e:
        stage_errors[2] = f"{type(e).__name__}: {e}"
        print(f"⚠️  stage 2 失败: {stage_errors[2]}，stage 3 将以未校验模式进行。", file=sys.stderr)

    # Stage 3: compose
    print("🤖 [3/3] 成文阶段：基于已校验事实生成中文段落...", file=sys.stderr)
    try:
        caption = _send_compose(
            chat,
            meta,
            include_artist=include_artist,
            verify_result=verify,
            skip_verify_note=not stages_ok[1],
        )
        if not caption:
            raise ValueError("stage 3 返回空字符串")
        stages_ok[2] = True
    except Exception as e:
        stage_errors[3] = f"{type(e).__name__}: {e}"
        print(f"⚠️  stage 3 失败: {stage_errors[3]}，用 stage 1 JSON 兜底拼接。", file=sys.stderr)
        caption = _fallback_caption_from_observe(observe or {})

    pipeline_label = "3stage" if all(stages_ok) else "3stage-partial"
    return {
        "pipeline": pipeline_label,
        "caption": caption,
        "observe": observe,
        "verify": verify,
        "stages_ok": stages_ok,
        "stage_errors": stage_errors,
    }


def save_caption(
    image_path: Path,
    caption: str,
    meta: dict | None,
    *,
    extra: dict | None = None,
) -> Path:
    """把结果增量写入同目录 caption.json，key 为 filename。

    extra: 可选附加字段（如 observe/verify/pipeline/stages_ok），仅在 CLI（非 --json）
    模式下被传入；--json 模式由前端控制持久化。
    """
    out_path = image_path.parent / "caption.json"
    store: dict = {}
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                store = json.load(f)
        except Exception:
            store = {}

    entry = {
        "caption": caption,
        "artist": (meta or {}).get("artist"),
        "characters": (meta or {}).get("tags", {}).get("tag_string_character"),
        "copyright": (meta or {}).get("tags", {}).get("tag_string_copyright"),
    }
    if extra:
        entry.update(extra)
    store[image_path.name] = entry

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(store, f, ensure_ascii=False, indent=2)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="Danbooru 多模态结构化打标工具")
    parser.add_argument(
        "image",
        nargs="?",
        default="./hot_pic/2026-05-10/b0a711cbd94283f10b0b72ab2cb371dc.jpg",
        help="图片路径（默认使用示例图片）",
    )
    parser.add_argument("--force", action="store_true", help="即使 caption.json 已有该图条目也重新生成")
    parser.add_argument("--with-artist", action="store_true", help="在提示词中加入画师信息（默认不加入）")
    parser.add_argument("--json", action="store_true", help="只把结果以 JSON 形式输出到 stdout，不写入 caption.json（供 GUI 调用）")
    parser.add_argument(
        "--pipeline",
        choices=["3stage", "single"],
        default="3stage",
        help="生成管线：3stage=观察+校验+成文（默认，质量更高）；single=旧的单轮模式",
    )
    parser.add_argument(
        "--debug-pipeline",
        action="store_true",
        help="把 observe/verify 的 JSON 中间结果输出到 stderr 便于调试",
    )
    args = parser.parse_args()

    image_path = Path(args.image.strip().strip("'\""))
    if not image_path.exists():
        if args.json:
            print(json.dumps({"ok": False, "error": f"文件不存在: {image_path}"}, ensure_ascii=False))
        else:
            print(f"❌ 找不到文件 {image_path}")
        sys.exit(1)

    if not args.json:
        # 普通 CLI 行为：已存在则跳过
        out_path = image_path.parent / "caption.json"
        if not args.force and out_path.exists():
            try:
                with open(out_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if image_path.name in existing:
                    print(f"ℹ️  {image_path.name} 已有 caption，跳过（用 --force 重跑）。")
                    print(existing[image_path.name].get("caption", ""))
                    return
            except Exception:
                pass

    def _run_pipeline(meta_arg):
        if args.pipeline == "single":
            text = generate_caption(image_path, meta_arg, include_artist=args.with_artist)
            return {
                "pipeline": "single",
                "caption": text,
                "observe": None,
                "verify": None,
                "stages_ok": [True, False, False],
                "stage_errors": {},
            }
        return generate_caption_3stage(
            image_path, meta_arg, include_artist=args.with_artist, debug=args.debug_pipeline
        )

    meta: dict | None = None
    pipeline_result: dict = {}
    if args.json:
        # 在 JSON 模式下重定向 stdout，避免 find_metadata/管线进度信息污染 JSON
        _saved_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            meta = find_metadata(image_path)
            pipeline_result = _run_pipeline(meta)
        except Exception as e:
            sys.stdout = _saved_stdout
            print(json.dumps({"ok": False, "error": f"API 调用或图片处理出错: {e}"}, ensure_ascii=False))
            sys.exit(1)
        finally:
            sys.stdout = _saved_stdout
    else:
        meta = find_metadata(image_path)
        try:
            pipeline_result = _run_pipeline(meta)
        except Exception as e:
            print(f"❌ API 调用或图片处理出错: {e}")
            sys.exit(1)

    caption = pipeline_result.get("caption", "")

    if args.json:
        # JSON 模式：只输出结果，不落盘（由 GUI 决定是否保存）
        result = {
            "ok": True,
            "filename": image_path.name,
            "caption": caption,
            "with_artist": bool(args.with_artist),
            "artist": (meta or {}).get("artist"),
            "characters": (meta or {}).get("tags", {}).get("tag_string_character"),
            "copyright": (meta or {}).get("tags", {}).get("tag_string_copyright"),
            "pipeline": pipeline_result.get("pipeline"),
            "observe": pipeline_result.get("observe"),
            "verify": pipeline_result.get("verify"),
            "stages_ok": pipeline_result.get("stages_ok"),
            "stage_errors": pipeline_result.get("stage_errors") or {},
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    # CLI 模式：把中间结果一并写入 caption.json，便于离线检查质量
    extra = {
        "pipeline": pipeline_result.get("pipeline"),
        "observe": pipeline_result.get("observe"),
        "verify": pipeline_result.get("verify"),
        "stages_ok": pipeline_result.get("stages_ok"),
    }
    saved_to = save_caption(image_path, caption, meta, extra=extra)

    print("\n--- 生成的描述 (Caption) ---")
    print(caption)
    print("----------------------------")
    print(f"✅ 已写入 {saved_to}  [pipeline={extra['pipeline']}, stages_ok={extra['stages_ok']}]")


if __name__ == "__main__":
    if "--json" not in sys.argv:
        print("=== Danbooru 动漫图片多模态结构化打标工具 ===")
    main()
