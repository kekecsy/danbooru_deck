import argparse
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from PIL import Image
from google import genai
from google.genai import types

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


DANBOORU_SYSTEM_PROMPT = """你是一名专业的二次元插画描述与数据标注专家。你的任务是根据所提供的图像以及 Danbooru 风格的标签元数据，生成一段细致、流畅、自然的中文描述。

### 如何使用元数据：
- 元数据可能包含角色名、版权（作品/系列）以及 Danbooru 通用标签；在显式提供时也可能包含画师信息。把身份类信息视为事实，把通用标签视为画面细节的提示。
- 始终将标签与图像进行交叉验证：图中没有的标签要安静地忽略，绝不要凭空臆造。
- 自然地把角色名、所属作品融入行文，**不要机械罗列**。仅当元数据中显式给出画师时，才可适度点出其风格；未给出画师信息时，**不要猜测或编造画师**。
- **不要原样输出标签字符串**。把它们翻译/转化为流畅的中文描述。
- 角色名、作品名、画师名可以保留其常见原文（日文/英文）写法，无需强行音译。

### 格式要求：
1. 只输出最终的中文描述段落，不要任何开场白、标题或 Markdown。
2. 使用连贯、自然的中文行文，一段为宜，最多两段且衔接紧密。
3. 语言要细腻、用词准确，避免逗号堆砌式的标签流水账，不要使用项目符号或分级标题。

### 描述应覆盖的内容（按以下顺序融入行文，但保持散文风格）：
1. 主体与构图：主要角色、姿态、构图与取景、镜头角度、视线方向。
2. 角色身份与外貌：角色名 + 所属作品（若已知），随后描写发色发型、瞳色、表情、神态及显著特征。
3. 服饰与配件：服装层次、鞋袜、饰品、所持/所穿物件，以及配色细节。
4. 背景与场景：环境、地点、时间、值得一提的道具。
5. 氛围、光影与风格：整体情绪、光照效果、色彩基调；仅在已提供画师信息时，方可自然带出画师的风格倾向。
"""


def build_user_prompt(meta: dict | None, include_artist: bool = False) -> str:
    """根据 viewer_data.json 中的条目构造带 tags 上下文的用户提示词。

    画师信息默认不注入；传 include_artist=True 才会作为风格提示加入。
    """
    if not meta:
        return "请按系统指令的结构，针对这张图片生成一段流畅自然的中文描述。"

    tags = meta.get("tags", {}) or {}
    lines = ["以下是来自图源的元数据（身份信息视为事实，其余视觉标签需与图像交叉验证）：", ""]

    if include_artist:
        artist = meta.get("artist") or tags.get("tag_string_artist")
        if artist:
            lines.append(f"- 画师 (Artist)：{artist}")
    if tags.get("tag_string_character"):
        lines.append(f"- 角色 (Character)：{tags['tag_string_character']}")
    if tags.get("tag_string_copyright"):
        lines.append(f"- 作品 / 版权 (Series)：{tags['tag_string_copyright']}")
    if tags.get("tag_string_general"):
        lines.append(f"- 通用视觉标签 (General tags)：{tags['tag_string_general']}")

    closing = "现在请按系统指令生成最终的中文描述段落：把角色与作品身份自然地融入行文"
    if include_artist:
        closing += "，并可适度点出画师的风格倾向"
    closing += "；忽略图像中不存在的标签；不要直接罗列标签原文。"
    lines += ["", closing]
    return "\n".join(lines)


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


def save_caption(image_path: Path, caption: str, meta: dict | None) -> Path:
    """把结果增量写入同目录 caption.json，key 为 filename。"""
    out_path = image_path.parent / "caption.json"
    store: dict = {}
    if out_path.exists():
        try:
            with open(out_path, "r", encoding="utf-8") as f:
                store = json.load(f)
        except Exception:
            store = {}

    store[image_path.name] = {
        "caption": caption,
        "artist": (meta or {}).get("artist"),
        "characters": (meta or {}).get("tags", {}).get("tag_string_character"),
        "copyright": (meta or {}).get("tags", {}).get("tag_string_copyright"),
    }

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

    meta = find_metadata(image_path) if not args.json else None
    if args.json:
        # 在 JSON 模式下重定向 stdout，避免 find_metadata/generate_caption 的进度信息污染 JSON
        _saved_stdout = sys.stdout
        sys.stdout = sys.stderr
        try:
            meta = find_metadata(image_path)
            caption = generate_caption(image_path, meta, include_artist=args.with_artist)
        except Exception as e:
            sys.stdout = _saved_stdout
            print(json.dumps({"ok": False, "error": f"API 调用或图片处理出错: {e}"}, ensure_ascii=False))
            sys.exit(1)
        finally:
            sys.stdout = _saved_stdout
    else:
        try:
            caption = generate_caption(image_path, meta, include_artist=args.with_artist)
        except Exception as e:
            print(f"❌ API 调用或图片处理出错: {e}")
            sys.exit(1)

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
        }
        print(json.dumps(result, ensure_ascii=False))
        return

    saved_to = save_caption(image_path, caption, meta)

    print("\n--- 生成的描述 (Caption) ---")
    print(caption)
    print("----------------------------")
    print(f"✅ 已写入 {saved_to}")


if __name__ == "__main__":
    if "--json" not in sys.argv:
        print("=== Danbooru 动漫图片多模态结构化打标工具 ===")
    main()
