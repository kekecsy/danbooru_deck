import json
from typing import List, Optional
import re
from runtime_paths import DATA_DIR, RESOURCE_DIR, ensure_user_directories

BASE_DIR = RESOURCE_DIR

CUSTOM_JSON = DATA_DIR / "custom_translation.json"
SEARCH_JSON = DATA_DIR / "character_chinese_search.json"

# 角色英文描述源：与 translator.py 同目录放 character.json 即可（项目相对路径）。
CHARACTER_SOURCE_JSON = BASE_DIR / "character.json"
# 用户在 UI 里点「在线拉描述」从 Danbooru wiki 抓回来的条目落到这里。
# 单独存一个增量文件，避免动 12MB 的 base，加载时合并到内存源。
CHARACTER_SUPPLEMENT_JSON = DATA_DIR / "character_supplement.json"

MANUAL_PROMPT_TEMPLATE = """你是一个专业的 ACG 角色翻译与命名专家。请按下面的规则给出 JSON。

【输入】
ID: {tag}
候选名称: {other_names}
英文描述: {description}

【标签结构解析（已由程序拆分，供你参考）】
角色本名: {base_name}
换装/皮肤部分: {costume}
作品来源: {source_name}

说明：Danbooru 角色标签形如 `本名_(换装)_(作品)`，最后一个括号通常是作品来源，
中间的括号是该角色在这部作品里的「换装 / 皮肤 / 形态」限定。
例如 `shun_(small)_(swimsuit)_(blue_archive)`：本名 shun、来源 blue_archive、换装为 small + swimsuit（碧蓝档案「泳装小峰」）。

【任务】
1. 判断该角色是否存在公认的中文名
2. 如果存在，从候选名称中选出最常用的中文名作为角色本名的译名；不存在则置为空字符串
3. 【重点】如果「换装/皮肤部分」不为空(无)：
   - 到该作品来源对应的中文社区 / 官方译名站（如碧蓝档案 Wiki、明日方舟 Wiki、原神 Wiki、萌娘百科、bangumi 等）
     查找这套换装/皮肤在中文版里的官方译名（例如泳装、圣诞、婚纱、体操服……）。
     注意：英文描述中也可能包含换装部位的英文或日文名称，可作为线索。
   - 把换装译名与角色本名组合成完整 chinese_name，换装部分放在括号里，例如「峰（泳装）」「阿米娅（假日威龙）」。
   - 找不到官方换装译名时，用最贴切的中文意译，仍放进括号。
   若「换装/皮肤部分」为(无)，chinese_name 就是角色本名的中文译名。
4. 提取来源 source_hint（小写英文，如 vocaloid / touhou / blue_archive / fate / kancolle / azur_lane 等）
5. 把英文描述概括翻译成中文 translated_description_zh，保留作品来源、角色定位与换装信息
6. 在如 bangumi、萌娘百科、对应的游戏论坛等中文社区上搜索最可能的中文名

【输出格式（严格 JSON，无任何额外解释或 Markdown 包裹符号）】
{{
  "has_chinese": true 或 false,
  "chinese_name": "中文名（含换装）或空字符串",
  "source_hint": "小写英文来源标签",
  "translated_description_zh": "中文简介或空字符串"
}}
"""

class Translator:
    def __init__(self):
        ensure_user_directories()
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

    @staticmethod
    def _split_tag_parts(tag: str) -> dict:
        """把 Danbooru 角色标签拆成 本名 / 换装 / 来源 三部分。
        规则：最后一个括号是作品来源，中间的括号都是换装/皮肤限定。
        e.g. 'shun_(small)_(swimsuit)_(blue_archive)' ->
            {base_name: 'shun', costume: 'small, swimsuit', source_name: 'blue_archive'}
        没有括号时 source/costume 为空。只有一个括号时视为来源，无换装。"""
        tag = (tag or "").strip()
        paren_segments = re.findall(r'_\(([^)]*)\)', tag)
        base_name = re.sub(r'_\([^)]*\).*$', '', tag).strip('_') or tag
        base_name = base_name.replace('_', ' ').strip()

        source_name = ""
        costume_parts: List[str] = []
        if paren_segments:
            source_name = paren_segments[-1].replace('_', ' ').strip()
            costume_parts = [p.replace('_', ' ').strip() for p in paren_segments[:-1] if p.strip()]

        costume = ", ".join(costume_parts)
        return {
            "base_name": base_name or "(无)",
            "costume": costume or "(无)",
            "source_name": source_name or "(无)",
        }

    def build_manual_prompt(self, tag: str, source: Optional[dict] = None) -> str:
        """组装一段「人类可贴」的 prompt：把描述+候选名称+标签结构塞进 MANUAL_PROMPT_TEMPLATE。"""
        if source is None:
            source = self.get_character_source(tag)
        names = ", ".join((source.get("other_names") or [])[:20]) or "(无)"
        desc = self._clean_description(source.get("description", "")) or "(无)"
        parts = self._split_tag_parts(tag)
        return MANUAL_PROMPT_TEMPLATE.format(
            tag=tag,
            other_names=names,
            description=desc,
            base_name=parts["base_name"],
            costume=parts["costume"],
            source_name=parts["source_name"],
        )

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

    def get_translation_entry(self, tag: str) -> dict:
        """返回当前实际命中的翻译条目，并标明 matched_key。
        精确 tag 优先；没有精确皮肤条目时才回退到系列/base，方便 UI 创建精确覆盖。"""
        search = self.load_search_dict()
        for variant in self._get_tag_variants((tag or "").strip()):
            entry = search.get(variant)
            source = "search"
            if not isinstance(entry, dict):
                entry = self.custom_dict.get(variant)
                source = "custom"
            if isinstance(entry, dict):
                return {"matched_key": variant, "source": source, **entry}
        return {"matched_key": "", "source": "", "has_chinese": False, "chinese_name": "", "source_hint": "", "translated_description_zh": ""}

    def search_translation_entries(self, query: str = "", limit: int = 100) -> list:
        """按 tag / 中文名 / 作品 source_hint / 简介搜索可编辑角色字典。"""
        merged = {}
        for tag, entry in self.custom_dict.items():
            if tag == "__source_hint_aliases__" or not isinstance(entry, dict):
                continue
            merged[tag] = {"source": "custom", **entry}
        for tag, entry in self.load_search_dict().items():
            if tag == "__source_hint_aliases__" or not isinstance(entry, dict):
                continue
            merged[tag] = {"source": "search", **entry}

        keywords = [part for part in (query or "").lower().replace("，", " ").split() if part]
        rows = []
        for tag, entry in merged.items():
            haystack = " ".join([
                tag,
                str(entry.get("chinese_name", "") or ""),
                str(entry.get("source_hint", "") or ""),
                str(entry.get("translated_description_zh", "") or ""),
            ]).lower()
            if keywords and not all(keyword in haystack for keyword in keywords):
                continue
            rows.append({
                "tag": tag,
                "fallback_name": self._format_tag(tag),
                "has_chinese": bool(entry.get("has_chinese", False)),
                "chinese_name": str(entry.get("chinese_name", "") or ""),
                "source_hint": str(entry.get("source_hint", "") or ""),
                "translated_description_zh": str(entry.get("translated_description_zh", "") or ""),
                "source": entry.get("source", ""),
            })
        rows.sort(key=lambda item: (item["source_hint"], item["chinese_name"], item["tag"]))
        return rows[:max(1, min(int(limit or 100), 500))]

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

