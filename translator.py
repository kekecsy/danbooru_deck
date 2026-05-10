import json
import os
import time
from typing import Dict, List, Optional
import re
from curl_cffi import requests
from pathlib import Path

BASE_DIR = Path(__file__).parent.resolve()
CUSTOM_JSON = BASE_DIR / "custom_translation.json"
SYSTEM_JSON = Path(r"C:\Users\27147\Downloads\my_work\pic\tags_translate\character_chinese.json")

# API 配置
apikey = "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"
MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

SYSTEM_PROMPT = """你是一个专业的角色翻译专家。
用户会提供一个英文的角色标签（通常是罗马音或英文名）。
你需要判断该角色对应的常用中文名，并返回 JSON 格式结果。
格式要求：
{"has_chinese": true, "chinese_name": "初音未来"}
如果没有合适的中文名，则返回：
{"has_chinese": false, "chinese_name": ""}
请不要输出任何额外解释。"""

class Translator:
    def __init__(self):
        self.system_dict = {}
        self.custom_dict = {}
        self.load_dicts()

    def load_dicts(self):
        if SYSTEM_JSON.exists():
            try:
                with open(SYSTEM_JSON, "r", encoding="utf-8") as f:
                    self.system_dict = json.load(f)
            except Exception as e:
                print(f"Failed to load system dict: {e}")
                
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
        """Look up a tag in custom dict first, then system dict. Returns the entry dict or empty dict."""
        if tag in self.custom_dict:
            return self.custom_dict[tag]
        if tag in self.system_dict:
            return self.system_dict[tag]
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

translator = Translator()

