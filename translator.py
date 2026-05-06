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
        for k, v in data.items():
            if isinstance(v, str):
                self.custom_dict[k] = {"has_chinese": bool(v), "chinese_name": v}
            elif isinstance(v, dict):
                chinese_name = v.get("chinese_name", "")
                self.custom_dict[k] = {"has_chinese": bool(chinese_name), "chinese_name": chinese_name}
        self.save_custom_dict()

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

    def translate(self, tag: str) -> str:
        tag = tag.strip()
        if not tag:
            return ""
            
        # 1. 查自定义字典
        if tag in self.custom_dict:
            entry = self.custom_dict[tag]
            if entry.get("has_chinese") and entry.get("chinese_name"):
                return entry["chinese_name"]
            if not entry.get("has_chinese"):
                return tag # 已确认没有中文名
                
        # 2. 查系统字典
        if tag in self.system_dict:
            entry = self.system_dict[tag]
            if entry.get("has_chinese") and entry.get("chinese_name"):
                return entry["chinese_name"]
            
        # 3. 调用 API (如果前面都没找到)
        chinese_name = self.call_api_for_translation(tag)
        self.custom_dict[tag] = {
            "has_chinese": bool(chinese_name),
            "chinese_name": chinese_name
        }
        self.save_custom_dict()
        
        return chinese_name if chinese_name else tag

translator = Translator()
