import json
from pathlib import Path

from curl_cffi import requests


BASE_DIR = Path(__file__).parent.resolve()
CUSTOM_JSON = BASE_DIR / "custom_translation.json"
SYSTEM_JSON = Path(r"C:\Users\27147\Downloads\my_work\pic\tags_translate\character_chinese.json")

print(f"[Translator] Loading custom dict from: {CUSTOM_JSON}")
print(f"[Translator] Loading system dict from: {SYSTEM_JSON}")

apikey = "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"
MODEL = "nvidia/nemotron-3-super-120b-a12b:free"

SYSTEM_PROMPT = """你是一个专业的角色翻译专家。用户会提供一个英文的角色标签（通常是罗马音或英文名）。你需要判断该角色对应的常用中文名，并返回 JSON 格式结果。格式要求：
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

    def _normalize_entry(self, value):
        if isinstance(value, str):
            return {
                "has_chinese": bool(value),
                "chinese_name": value,
                "source_hint": "",
                "source_hint_zh": "",
            }

        if not isinstance(value, dict):
            return {
                "has_chinese": False,
                "chinese_name": "",
                "source_hint": "",
                "source_hint_zh": "",
            }

        chinese_name = value.get("chinese_name", "")
        source_hint = value.get("source_hint", value.get("hint_source", ""))
        source_hint_zh = value.get("source_hint_zh", value.get("hint_source_zh", ""))

        normalized = dict(value)
        normalized["has_chinese"] = bool(value.get("has_chinese", chinese_name))
        normalized["chinese_name"] = chinese_name
        normalized["source_hint"] = source_hint
        normalized["source_hint_zh"] = source_hint_zh
        return normalized

    def update_custom_dict(self, data: dict):
        for k, v in data.items():
            self.custom_dict[k] = self._normalize_entry(v)
        self.save_custom_dict()

    def set_custom_entry(self, key: str, payload: dict):
        entry = self._normalize_entry(payload)
        self.custom_dict[key] = entry
        self.save_custom_dict()
        return entry

    def add_custom_translation(self, key: str, chinese_name: str, source_hint: str = "", source_hint_zh: str = ""):
        self.custom_dict[key] = {
            "has_chinese": bool(chinese_name),
            "chinese_name": chinese_name,
            "source_hint": source_hint,
            "source_hint_zh": source_hint_zh,
        }
        self.save_custom_dict()

    def call_api_for_translation(self, tag: str) -> str:
        user_prompt = f"角色标签: {tag}\n请直接输出 JSON 对象。"
        try:
            headers = {
                "Authorization": f"Bearer {apikey}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": MODEL,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                "temperature": 0.1,
            }
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                json=payload,
                headers=headers,
                impersonate="chrome120",
                timeout=10,
            )
            if response.status_code == 200:
                data = response.json()
                text = data["choices"][0]["message"]["content"].strip()
                if text.startswith("```json"):
                    text = text[7:]
                if text.startswith("```"):
                    text = text[3:]
                if text.endswith("```"):
                    text = text[:-3]
                parsed = json.loads(text.strip())
                chinese_name = parsed.get("chinese_name", "")
                if parsed.get("has_chinese") and chinese_name:
                    return chinese_name
            else:
                print(f"API HTTP {response.status_code}: {response.text}")
        except Exception as e:
            print(f"API translation failed for {tag}: {e}")
        return ""

    def _lookup_dict(self, tag: str) -> str:
        if tag in self.custom_dict:
            entry = self._normalize_entry(self.custom_dict[tag])
            if entry.get("has_chinese") and entry.get("chinese_name"):
                return entry["chinese_name"]

        if tag in self.system_dict:
            entry = self._normalize_entry(self.system_dict[tag])
            if entry.get("has_chinese") and entry.get("chinese_name"):
                return entry["chinese_name"]

        return ""

    def _lookup_entry(self, tag: str):
        if tag in self.custom_dict:
            return self._normalize_entry(self.custom_dict[tag])
        if tag in self.system_dict:
            return self._normalize_entry(self.system_dict[tag])
        return {}

    def _format_tag(self, tag: str) -> str:
        base = __import__("re").sub(r'_\([^)]*\)', '', tag).strip('_')
        if not base:
            base = tag
        return base.replace('_', ' ').title()

    def _get_tag_variants(self, tag: str) -> list:
        import re

        variants = [tag]
        paren_segments = re.findall(r'_\([^)]*\)', tag)

        if len(paren_segments) >= 2:
            base_name = tag[:tag.index(paren_segments[0])]
            combo = base_name + paren_segments[-1]
            if combo != tag and combo not in variants:
                variants.append(combo)

        if len(paren_segments) >= 1:
            base_name = tag[:tag.index(paren_segments[0])]
            last_combo = base_name + paren_segments[-1]
            if last_combo != tag and last_combo not in variants:
                variants.append(last_combo)

        base = re.sub(r'_\([^)]*\)', '', tag).strip('_')
        if base and base != tag and base not in variants:
            variants.append(base)

        return variants

    def translate(self, tag: str) -> str:
        tag = tag.strip()
        if not tag:
            return ""

        for variant in self._get_tag_variants(tag):
            result = self._lookup_dict(variant)
            if result:
                return result

        return self._format_tag(tag)

    def describe(self, tag: str) -> dict:
        tag = tag.strip()
        if not tag:
            return {
                "key": "",
                "matched_key": "",
                "name": "",
                "source_hint": "",
                "source_hint_zh": "",
                "description": "",
            }

        matched_key = tag
        matched_entry = {}
        for variant in self._get_tag_variants(tag):
            entry = self._lookup_entry(variant)
            if entry:
                matched_key = variant
                matched_entry = entry
                break

        name = matched_entry.get("chinese_name") if matched_entry.get("has_chinese") else ""
        return {
            "key": tag,
            "matched_key": matched_key,
            "name": name or self._format_tag(tag),
            "source_hint": matched_entry.get("source_hint", ""),
            "source_hint_zh": matched_entry.get("source_hint_zh", ""),
            "description": matched_entry.get("translated_description_zh", ""),
        }

    def get_entry_for_edit(self, tag: str) -> dict:
        tag = tag.strip()
        if not tag:
            result = self._normalize_entry({})
            result["matched_key"] = ""
            return result

        for variant in self._get_tag_variants(tag):
            entry = self._lookup_entry(variant)
            if entry:
                result = dict(entry)
                result["matched_key"] = variant
                return result

        result = self._normalize_entry({})
        result["matched_key"] = tag
        return result

    def translate_with_api(self, tag: str) -> str:
        tag = tag.strip()
        if not tag:
            return ""

        for variant in self._get_tag_variants(tag):
            result = self._lookup_dict(variant)
            if result:
                return result

        chinese_name = self.call_api_for_translation(tag)
        self.custom_dict[tag] = {
            "has_chinese": bool(chinese_name),
            "chinese_name": chinese_name,
            "source_hint": "",
            "source_hint_zh": "",
        }
        self.save_custom_dict()

        return chinese_name if chinese_name else tag


translator = Translator()
