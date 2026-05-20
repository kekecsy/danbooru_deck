import json
import os
import time
from typing import Dict, List, Optional
import re
from curl_cffi import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()


def _load_env_file(path: Path) -> None:
    """轻量级 .env 加载：KEY=VALUE 行写入 os.environ（已存在则跳过）。
    避免引入 python-dotenv 这个额外依赖。注释（#）和空行忽略；不支持引号转义。"""
    if not path.exists():
        return
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception as e:
        print(f"Failed to load .env from {path}: {e}")


_load_env_file(BASE_DIR / ".env")

CUSTOM_JSON = BASE_DIR / "custom_translation.json"
SEARCH_JSON = BASE_DIR / "character_chinese_search.json"

# 角色英文描述源：与 translator.py 同目录放 character.json 即可（项目相对路径）。
CHARACTER_SOURCE_JSON = BASE_DIR / "character.json"
# 用户在 UI 里点「在线拉描述」从 Danbooru wiki 抓回来的条目落到这里。
# 单独存一个增量文件，避免动 12MB 的 base，加载时合并到内存源。
CHARACTER_SUPPLEMENT_JSON = BASE_DIR / "character_supplement.json"

# API 配置：apikey 从 .env 读，不再硬编码
apikey = os.getenv("openrouter_api_key", "")
MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

SYSTEM_PROMPT = """你是一个专业的角色翻译专家。
用户会提供一个英文的角色标签（通常是罗马音或英文名）。
你需要判断该角色对应的常用中文名，并返回 JSON 格式结果。
格式要求：
{"has_chinese": true, "chinese_name": "初音未来"}
如果没有合适的中文名，则返回：
{"has_chinese": false, "chinese_name": ""}
请不要输出任何额外解释。"""

RICH_SYSTEM_PROMPT = """你是一个专业的角色翻译与命名专家。

用户会提供一个角色实体，包含：
- 一个唯一标识符（ID）
- 一个候选名称列表（other_names，可能含日文/中文/韩文/罗马音）
- 一段英文角色描述（description，Danbooru wiki 原文）

你的任务是：
1. 判断该角色是否存在合适的中文名称
2. 如果存在，从候选名称中选出最常用的中文名 chinese_name；不存在则置为空字符串
3. 提取一个简短的来源或身份提示 source_hint（小写英文，例如 vocaloid / touhou / blue_archive / fate / kancolle 等）
4. 把 description 概括翻译成中文 translated_description_zh，保留作品来源与角色定位

只返回 JSON 对象，格式如下：
{
  "has_chinese": true,
  "chinese_name": "初音未来",
  "source_hint": "vocaloid",
  "translated_description_zh": "VOCALOID 虚拟歌手角色。..."
}

如果该角色没有公认的中文名，请返回：
{
  "has_chinese": false,
  "chinese_name": "",
  "source_hint": "",
  "translated_description_zh": ""
}

严禁输出任何额外解释或 Markdown 包裹符号。"""

MANUAL_PROMPT_TEMPLATE = """你是一个专业的 ACG 角色翻译与命名专家。请按下面的规则给出 JSON。

【输入】
ID: {tag}
候选名称: {other_names}
英文描述: {description}

【任务】
1. 判断该角色是否存在公认的中文名
2. 如果存在，从候选名称中选出最常用的中文名 chinese_name；不存在则置为空字符串
3. 提取来源 source_hint（小写英文，如 vocaloid / touhou / blue_archive / fate / kancolle / azur_lane 等）
4. 把英文描述概括翻译成中文 translated_description_zh，保留作品来源与角色定位
5. 在如bangumi、萌娘百科等中文社区上搜索最可能的中文名

【输出格式（严格 JSON，无任何额外解释或 Markdown 包裹符号）】
{{
  "has_chinese": true 或 false,
  "chinese_name": "中文名或空字符串",
  "source_hint": "小写英文来源标签",
  "translated_description_zh": "中文简介或空字符串"
}}
"""

class Translator:
    def __init__(self):
        self.custom_dict = {}
        self._character_source: Optional[dict] = None
        self.load_dicts()

    def load_dicts(self):
        if CUSTOM_JSON.exists():
            try:
                with open(CUSTOM_JSON, "r", encoding="utf-8") as f:
                    self.custom_dict = json.load(f)
            except Exception as e:
                print(f"Failed to load custom dict: {e}")

    def save_custom_dict(self):
        with open(CUSTOM_JSON, "w", encoding="utf-8") as f:
            json.dump(self.custom_dict, f, ensure_ascii=False, indent=2)

    def update_custom_dict(self, data: dict):
        if "__source_hint_aliases__" in data:
            self.custom_dict["__source_hint_aliases__"] = data["__source_hint_aliases__"]

        for k, v in data.items():
            if k == "__source_hint_aliases__":
                continue
            if isinstance(v, str):
                self.custom_dict[k] = {"has_chinese": bool(v), "chinese_name": v}
            elif isinstance(v, dict):
                # 保持原始字典内容（包含 source_hint 等）
                self.custom_dict[k] = v
        self.save_custom_dict()

    def get_source_hint_alias(self, hint: str) -> Optional[str]:
        aliases = self.custom_dict.get("__source_hint_aliases__", {})
        return aliases.get(hint)

    def add_custom_translation(self, key: str, chinese_name: str):
        self.custom_dict[key] = {
            "has_chinese": bool(chinese_name),
            "chinese_name": chinese_name
        }
        self.save_custom_dict()

    def call_api_for_translation(self, tag: str) -> str:
        user_prompt = f"角色标签: {tag}\n请直接输出 JSON 对象。"
        try:
            headers = {
                "Authorization": f"Bearer {apikey}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt}
                ],
                "temperature": 0.1
            }
            response = requests.post("https://openrouter.ai/api/v1/chat/completions", json=payload, headers=headers, impersonate="chrome120", timeout=10)
            if response.status_code == 200:
                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()
                if text.startswith("```json"): text = text[7:]
                if text.startswith("```"): text = text[3:]
                if text.endswith("```"): text = text[:-3]
                parsed = json.loads(text.strip())
                chinese_name = parsed.get("chinese_name", "")
                if parsed.get("has_chinese") and chinese_name:
                    return chinese_name
            else:
                print(f"API HTTP {response.status_code}: {response.text}")
        except Exception as e:
            print(f"API translation failed for {tag}: {e}")
        return ""

    def _lookup_dict(self, tag: str) -> dict:
        """Look up a tag in custom dict. Returns the entry dict or empty dict."""
        if tag in self.custom_dict:
            return self.custom_dict[tag]
        return {}

    def get_tag_info(self, tag: str) -> dict:
        """获取标签的完整翻译信息，包含中文名、source_hint 等"""
        tag = tag.strip()
        if not tag:
            return {}
        for variant in self._get_tag_variants(tag):
            entry = self._lookup_dict(variant)
            if entry and entry.get("has_chinese"):
                return entry
        return {}

    def _format_tag(self, tag: str) -> str:
        """Format a raw tag into a human-readable name.
        e.g. 'ak-12_(girls\\'_frontline)' -> 'AK-12'
             'darkness_(konosuba)' -> 'Darkness'
        """
        import re
        # Strip all parenthesized suffixes
        base = re.sub(r'_\([^)]*\)', '', tag).strip('_')
        if not base:
            base = tag
        # Replace underscores with spaces, title case
        return base.replace('_', ' ').title()

    def _get_tag_variants(self, tag: str) -> list:
        """Generate possible dictionary keys from a variant tag.
        e.g. 'akagi_(ruby-laced_beauty)_(azur_lane)' yields:
            1. 'akagi_(ruby-laced_beauty)_(azur_lane)' (exact)
            2. 'akagi_(azur_lane)' (name + last series suffix)
            3. 'akagi' (pure base name)
        Handles apostrophes like "ak-12_(girls'_frontline)".
        """
        import re
        variants = [tag]  # exact match first

        # Find all parenthesized segments: _(...) including those with apostrophes
        paren_segments = re.findall(r'_\([^)]*\)', tag)

        if len(paren_segments) >= 2:
            # name_(variant)_(series) -> name_(series)
            # Get the base name (everything before the first parenthesized segment)
            base_name = tag[:tag.index(paren_segments[0])]
            combo = base_name + paren_segments[-1]
            if combo != tag and combo not in variants:
                variants.append(combo)

        if len(paren_segments) >= 1:
            # name_(series) -> try exact with just last paren
            base_name = tag[:tag.index(paren_segments[0])]
            last_combo = base_name + paren_segments[-1]
            if last_combo != tag and last_combo not in variants:
                variants.append(last_combo)

        # Strip all parenthesized parts -> pure base name
        base = re.sub(r'_\([^)]*\)', '', tag).strip('_')
        if base and base != tag and base not in variants:
            variants.append(base)

        return variants

    def translate(self, tag: str) -> str:
        """Translate a character tag to Chinese using dictionaries only (no API).
        Tries multiple matching strategies: exact → combo (name+series) → base name.
        Falls back to a human-readable formatted name.
        """
        tag = tag.strip()
        if not tag:
            return ""

        for variant in self._get_tag_variants(tag):
            entry = self._lookup_dict(variant)
            if entry.get("has_chinese") and entry.get("chinese_name"):
                return entry["chinese_name"]

        # No Chinese translation found — return a nicely formatted name
        return self._format_tag(tag)

    def translate_with_api(self, tag: str) -> str:
        """Translate with API fallback — use only when explicitly requested, not during page loads."""
        tag = tag.strip()
        if not tag:
            return ""

        # Try dictionaries with all variant strategies
        for variant in self._get_tag_variants(tag):
            entry = self._lookup_dict(variant)
            if entry.get("has_chinese") and entry.get("chinese_name"):
                return entry["chinese_name"]

        # API fallback
        chinese_name = self.call_api_for_translation(tag)
        self.custom_dict[tag] = {
            "has_chinese": bool(chinese_name),
            "chinese_name": chinese_name
        }
        self.save_custom_dict()

        return chinese_name if chinese_name else tag

    # ====================================================================
    # 增量翻译：character_chinese_search.json 流水线
    # ====================================================================

    def load_character_source(self) -> dict:
        """Lazy load BASE_DIR/character.json（12MB，避免 import 时阻塞）。
        加载完成后把 CHARACTER_SUPPLEMENT_JSON 里的「在线拉回来」的条目合并到上面。
        base 文件不存在时只用 supplement，UI 可以靠「在线拉描述」逐个补。"""
        if self._character_source is not None:
            return self._character_source
        base = {}
        if CHARACTER_SOURCE_JSON.exists():
            try:
                with open(CHARACTER_SOURCE_JSON, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    base = data
            except Exception as e:
                print(f"Failed to load character source from {CHARACTER_SOURCE_JSON}: {e}")
        # 合并增量
        supplement = self.load_supplement_dict()
        for k, v in supplement.items():
            if isinstance(v, dict):
                base[k] = v
        self._character_source = base
        return self._character_source

    @staticmethod
    def load_supplement_dict() -> dict:
        if not CHARACTER_SUPPLEMENT_JSON.exists():
            return {}
        try:
            with open(CHARACTER_SUPPLEMENT_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"Failed to load character supplement: {e}")
            return {}

    @staticmethod
    def save_supplement_dict(data: dict) -> None:
        with open(CHARACTER_SUPPLEMENT_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def fetch_character_source(self, tag: str) -> dict:
        """从 Danbooru wiki 在线拉描述 + other_names，落到 supplement 文件并刷新内存源。
        返回与 get_character_source 同样的 shape：{description, other_names, exists, matched_key}。
        wiki 没结果时 exists=False。"""
        from danbooru_api import get_wiki

        tag = (tag or "").strip()
        if not tag:
            return {"description": "", "other_names": [], "exists": False, "matched_key": ""}

        # 按 _get_tag_variants 顺序逐个试，命中就停 —— 与 get_character_source 行为一致
        for variant in self._get_tag_variants(tag):
            wiki = get_wiki(variant)
            if not wiki or not isinstance(wiki, list):
                continue
            entry_raw = wiki[0] if wiki else None
            if not isinstance(entry_raw, dict):
                continue
            body = str(entry_raw.get("body", "") or "").strip()
            names = entry_raw.get("other_names") or []
            if not isinstance(names, list):
                names = []
            if not body and not names:
                continue

            entry = {"description": body, "other_names": names}
            # 落到 supplement 文件
            supplement = self.load_supplement_dict()
            supplement[variant] = entry
            self.save_supplement_dict(supplement)
            # 同步刷新内存源（若已加载过）
            if self._character_source is not None:
                self._character_source[variant] = entry
            return {
                "description": body,
                "other_names": names,
                "exists": True,
                "matched_key": variant,
            }
        return {"description": "", "other_names": [], "exists": False, "matched_key": ""}

    def get_character_source(self, tag: str) -> dict:
        """按 tag variants 顺序在 character.json 里查 description / other_names。
        命中返回 {description, other_names, exists=True, matched_key}；
        未命中返回 {description: '', other_names: [], exists=False, matched_key: ''}。"""
        tag = (tag or "").strip()
        if not tag:
            return {"description": "", "other_names": [], "exists": False, "matched_key": ""}

        source = self.load_character_source()
        if not source:
            return {"description": "", "other_names": [], "exists": False, "matched_key": ""}

        for variant in self._get_tag_variants(tag):
            entry = source.get(variant)
            if isinstance(entry, dict):
                desc = str(entry.get("description", "") or "").strip()
                names = entry.get("other_names") or []
                if not isinstance(names, list):
                    names = []
                return {
                    "description": desc,
                    "other_names": names,
                    "exists": True,
                    "matched_key": variant,
                }
        return {"description": "", "other_names": [], "exists": False, "matched_key": ""}

    @staticmethod
    def _clean_description(text: str) -> str:
        text = (text or "").replace("\r\n", " ").replace("\n", " ").strip()
        return " ".join(text.split())

    def build_manual_prompt(self, tag: str, source: Optional[dict] = None) -> str:
        """组装一段「人类可贴」的 prompt：把描述+候选名称塞进 MANUAL_PROMPT_TEMPLATE。"""
        if source is None:
            source = self.get_character_source(tag)
        names = ", ".join((source.get("other_names") or [])[:20]) or "(无)"
        desc = self._clean_description(source.get("description", "")) or "(无)"
        return MANUAL_PROMPT_TEMPLATE.format(
            tag=tag,
            other_names=names,
            description=desc,
        )

    def call_rich_translation(self, tag: str, source: Optional[dict] = None) -> dict:
        """调 openrouter + RICH_SYSTEM_PROMPT。返回 dict：
        - 成功: {"ok": True, "entry": {...}, "raw": "<原始 LLM 输出>", "error": ""}
        - 失败: {"ok": False, "entry": {}, "raw": "<原始 LLM 输出，可能为空>", "error": "<可读错误>"}

        raw 字段把 LLM 原文带出来，前端 JSON 解析失败时可以让用户手工修复后重新解析。"""
        if not apikey:
            return {"ok": False, "entry": {}, "raw": "",
                    "error": "未在 .env 中配置 openrouter_api_key，请填写后重启后端"}

        if source is None:
            source = self.get_character_source(tag)
        names = ", ".join((source.get("other_names") or [])[:20])
        desc = self._clean_description(source.get("description", ""))
        user_prompt = (
            f"ID: {tag}\n"
            f"候选名称: {names or '(无)'}\n"
            f"角色描述: {desc or '(无)'}\n\n"
            "请直接输出 JSON 对象。"
        )

        raw_text = ""
        try:
            headers = {
                "Authorization": f"Bearer {apikey}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": RICH_SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            }
            # 描述长 + 翻译输出长，10s 不够；放宽到 60s
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                impersonate="chrome120",
                timeout=60,
            )
            if response.status_code != 200:
                snippet = response.text[:500] if hasattr(response, "text") else ""
                err = f"HTTP {response.status_code}: {snippet}"
                print(f"Rich API failed for {tag}: {err}")
                return {"ok": False, "entry": {}, "raw": snippet, "error": err}

            try:
                raw_text = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, ValueError, TypeError) as e:
                err = f"上游响应结构异常: {e}; body 前 500 字: {response.text[:500]}"
                print(f"Rich API for {tag}: {err}")
                return {"ok": False, "entry": {}, "raw": response.text[:2000], "error": err}

            text = (raw_text or "").strip()
            if text.startswith("```json"):
                text = text[7:]
            if text.startswith("```"):
                text = text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
            # 兼容模型偶尔写出 \日 这种非法转义
            cleaned = re.sub(r'\\([^"\\/bfnrtu])', r'\\\\\1', text)
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError as e:
                err = f"JSON 解析失败: {e}（可在下方文本框人工修复后重新解析）"
                print(f"Rich translation parse fail for {tag}: {e}")
                return {"ok": False, "entry": {}, "raw": raw_text, "error": err}

            if not isinstance(parsed, dict):
                return {"ok": False, "entry": {}, "raw": raw_text,
                        "error": "返回的不是 JSON 对象，请人工修复"}

            entry = {
                "has_chinese": bool(parsed.get("has_chinese", False)),
                "chinese_name": str(parsed.get("chinese_name", "") or "").strip(),
                "source_hint": str(parsed.get("source_hint", "") or "").strip().lower(),
                "translated_description_zh": str(parsed.get("translated_description_zh", "") or "").strip(),
            }
            return {"ok": True, "entry": entry, "raw": raw_text, "error": ""}
        except Exception as e:
            err = f"请求异常: {type(e).__name__}: {e}"
            print(f"Rich translation failed for {tag}: {err}")
            return {"ok": False, "entry": {}, "raw": raw_text, "error": err}

    @staticmethod
    def load_search_dict() -> dict:
        if not SEARCH_JSON.exists():
            return {}
        try:
            with open(SEARCH_JSON, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception as e:
            print(f"Failed to load search dict: {e}")
            return {}

    @staticmethod
    def save_search_dict(data: dict) -> None:
        with open(SEARCH_JSON, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def upsert_search_entry(self, tag: str, entry: dict) -> None:
        """单条覆盖写入 SEARCH_JSON。entry 至少含 has_chinese / chinese_name。"""
        tag = (tag or "").strip()
        if not tag:
            raise ValueError("tag 为空")
        data = self.load_search_dict()
        normalized = {
            "has_chinese": bool(entry.get("has_chinese", False)),
            "chinese_name": str(entry.get("chinese_name", "") or "").strip(),
            "source_hint": str(entry.get("source_hint", "") or "").strip().lower(),
            "translated_description_zh": str(entry.get("translated_description_zh", "") or "").strip(),
        }
        if not normalized["has_chinese"]:
            normalized["chinese_name"] = ""
        data[tag] = normalized
        self.save_search_dict(data)

    def import_search_to_custom(self) -> dict:
        """把 SEARCH_JSON 整体合并进 custom_dict 并写盘。返回 {imported, total}。"""
        search = self.load_search_dict()
        imported = 0
        for k, v in search.items():
            if k == "__source_hint_aliases__":
                self.custom_dict[k] = v
                continue
            if not isinstance(v, dict):
                continue
            self.custom_dict[k] = v
            imported += 1
        self.save_custom_dict()
        return {"imported": imported, "total": len(search)}

translator = Translator()

