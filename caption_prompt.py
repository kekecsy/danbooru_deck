"""Caption 提示词构造：被 caption.py（Gemini 自动调用）和 main.py（手动模式
/api/caption_prompt 端点）共用。这里不依赖 Gemini SDK / Pillow，纯字符串处理，
确保前端「手动复制提示词」功能在没装 google-genai 时也能工作。"""


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
3. 服饰与配件：服装层次、鞋袜、饰品、所持/所穿物件,以及配色细节。
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


def build_combined_prompt(meta: dict | None, include_artist: bool = False) -> str:
    """把 system + user 拼成一段，方便用户一键复制到任何 chat LLM。"""
    user = build_user_prompt(meta, include_artist=include_artist)
    return f"{DANBOORU_SYSTEM_PROMPT.rstrip()}\n\n---\n\n{user}"


# =========================================================================
# 3 阶段管线 (observe → verify → compose)
# =========================================================================
#
# 设计意图：
#   - 单轮调用容易把"看图 + 校验 tag + 成文"挤在一次推理里，导致：
#     1) tag 里有但图里没有的元素被臆造进 caption (幻觉)
#     2) tag_string_character 与画面不符时仍照搬名字 (角色识别错误)
#   - 拆成 3 轮后：第 1 轮纯观察 (不喂 tags 避免引导)，第 2 轮才把
#     tags 喂进去做交叉校验并标注每个 tag 是否真的可见，第 3 轮只
#     基于已校验事实写自然中文段落。

PIPELINE_SYSTEM_PROMPT = """你是一名严谨的二次元插画分析师与数据标注员。本次任务会分为多轮对话，每一轮按用户提示词的具体要求作答。

通用原则（每一轮都遵守）：
- 只描述你直接观察到的内容；不要凭外部知识或元数据臆造画面里没有的元素。
- 不要输出任何 markdown 标题、列表符号或代码块；当用户要求 JSON 时，只输出符合 schema 的 JSON 本体，不要包裹解释。
- 角色名、作品名、画师名可以保留常见原文（日文/英文）写法。
- 对二次元元素（兽耳/光环/翅膀/角/尾巴/瞳孔特殊符号/制服/泳装/和服 等）要明确指出，因为这些是识别锚点。
"""


OBSERVE_USER_PROMPT = """这是第 1 轮：纯观察。

请仅根据图像本身（不要使用任何外部知识或元数据，假设你完全不知道角色是谁、出自什么作品）输出一份结构化 JSON，覆盖以下要素：

- subjects_count: 画中主要人物数量（整数）。
- composition: framing（close-up / portrait / cowboy_shot / full_body / wide / other）、camera_angle（eye_level / from_below / from_above / dutch / other）、gaze_direction（at_viewer / away / down / up / side / other）。
- characters: 每个主要角色一项，记录 hair_color、hair_style、hair_accessories（数组）、eye_color、eye_features（如 heart_in_eye、symbol_in_eye、半闭等；没有特殊则填 "none"）、expression（自然中文短语，如"含羞带笑"）、distinguishing_features（数组，记录兽耳/光环/翅膀/角/尾巴/尖耳/瞳孔特殊纹样 等）。
- outfit_layers: 服饰分层数组，每层独立一项；layer 取值如 outerwear / dress / shirt / skirt / pants / underwear / legwear / footwear / headwear / accessory；item 具体名称；color 精确配色（避免"深色"这种模糊词）；details 记录蕾丝/褶皱/破损/卷起/半脱 等细节。若画面包含可见的内衣/泳装/裸露状态，如实记入对应层级，不要回避。
- pose_action: stance（站/坐/跪/趴/躺/其他）、hand_position、body_orientation（正面/侧面/背面/3/4 侧）、action（一句话描述当前正在做什么）。
- background: environment（场景类型）、props（道具数组）、lighting（如 soft_natural / harsh_rim / studio / night_neon / 等）、palette（主色调）、mood（氛围一两词）。
- art_style: line_quality（crisp / sketchy / soft）、shading（cel / soft / painterly / flat）、notable_features（数组：如 gradient / glossy / matte / glow 等）。
- nsfw: { is_nsfw: 布尔, elements: 数组（仅在 is_nsfw=true 时填写画面中的露骨元素，如 nude / sex / penetration / cum 等；如无则空数组） }。
- notable_details: 其他值得提及的视觉细节数组（不重复以上字段已记录的内容）。

要求：
- 配色尽量精确，例如写 "black thigh-high stockings" 而不是 "black legwear"。
- 看不清就写 "unclear"，不要猜。
- 输出严格符合上述 schema 的 JSON。"""


OBSERVE_SCHEMA = {
    "type": "object",
    "properties": {
        "subjects_count": {"type": "integer"},
        "composition": {
            "type": "object",
            "properties": {
                "framing": {"type": "string"},
                "camera_angle": {"type": "string"},
                "gaze_direction": {"type": "string"},
            },
            "required": ["framing", "camera_angle", "gaze_direction"],
        },
        "characters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "hair_color": {"type": "string"},
                    "hair_style": {"type": "string"},
                    "hair_accessories": {"type": "array", "items": {"type": "string"}},
                    "eye_color": {"type": "string"},
                    "eye_features": {"type": "string"},
                    "expression": {"type": "string"},
                    "distinguishing_features": {"type": "array", "items": {"type": "string"}},
                },
                "required": [
                    "hair_color", "hair_style", "hair_accessories",
                    "eye_color", "eye_features", "expression",
                    "distinguishing_features",
                ],
            },
        },
        "outfit_layers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "layer": {"type": "string"},
                    "item": {"type": "string"},
                    "color": {"type": "string"},
                    "details": {"type": "string"},
                },
                "required": ["layer", "item", "color", "details"],
            },
        },
        "pose_action": {
            "type": "object",
            "properties": {
                "stance": {"type": "string"},
                "hand_position": {"type": "string"},
                "body_orientation": {"type": "string"},
                "action": {"type": "string"},
            },
            "required": ["stance", "hand_position", "body_orientation", "action"],
        },
        "background": {
            "type": "object",
            "properties": {
                "environment": {"type": "string"},
                "props": {"type": "array", "items": {"type": "string"}},
                "lighting": {"type": "string"},
                "palette": {"type": "string"},
                "mood": {"type": "string"},
            },
            "required": ["environment", "props", "lighting", "palette", "mood"],
        },
        "art_style": {
            "type": "object",
            "properties": {
                "line_quality": {"type": "string"},
                "shading": {"type": "string"},
                "notable_features": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["line_quality", "shading", "notable_features"],
        },
        "nsfw": {
            "type": "object",
            "properties": {
                "is_nsfw": {"type": "boolean"},
                "elements": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["is_nsfw", "elements"],
        },
        "notable_details": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "subjects_count", "composition", "characters", "outfit_layers",
        "pose_action", "background", "art_style", "nsfw", "notable_details",
    ],
}


def build_verify_user_prompt(meta: dict | None) -> str:
    """第 2 轮：把 character/copyright/general tags 喂给模型做交叉校验。"""
    tags = (meta or {}).get("tags", {}) or {}
    character = tags.get("tag_string_character") or ""
    copyright_ = tags.get("tag_string_copyright") or ""
    general = tags.get("tag_string_general") or ""

    lines = [
        "这是第 2 轮：交叉校验。",
        "",
        "下面是来自图源（Danbooru 风格）的元数据。请基于你在第 1 轮观察到的事实做交叉验证。",
        "",
        f"- 提供的角色 (character)：{character or '(无)'}",
        f"- 提供的作品 (copyright)：{copyright_ or '(无)'}",
        f"- 提供的通用视觉标签 (general tags, 空格分隔)：{general or '(无)'}",
        "",
        "请按以下要求作答：",
        "1. character_identification：判断提供的角色 + 作品与你第 1 轮观察到的角色特征（发色/瞳色/光环/兽耳/服饰风格/识别锚点）是否一致；",
        "   - consistent=true/false，confidence 0-1，reason 一句中文解释；",
        "   - 若 consistent=false，给出 fallback_description（一段简短中文，描述你实际看到的角色，用以代替名字）；一致则 fallback_description 为 null。",
        "2. tag_evaluation：把 general tags 拆成单个 tag，**逐条**评估：",
        "   - status: 'visible' (图中明确可见)、'absent' (图中明确没有)、'uncertain' (看不清/无法判断)；",
        "   - reason: 一句简短中文。",
        "   - 类似 highres / commentary_request / absurdres 这种 meta 性标签可以标 uncertain 并在 reason 里说明是元数据。",
        "3. additional_observations：你在第 1 轮看到、但 general tags 没提到的视觉元素（数组，每项一句中文）。",
        "",
        "严格按 schema 输出 JSON，不要额外解释。",
    ]
    return "\n".join(lines)


VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "character_identification": {
            "type": "object",
            "properties": {
                "provided_name": {"type": "string", "nullable": True},
                "provided_series": {"type": "string", "nullable": True},
                "consistent": {"type": "boolean"},
                "confidence": {"type": "number"},
                "reason": {"type": "string"},
                "fallback_description": {"type": "string", "nullable": True},
            },
            "required": ["consistent", "confidence", "reason"],
        },
        "tag_evaluation": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "status": {"type": "string", "enum": ["visible", "absent", "uncertain"]},
                    "reason": {"type": "string"},
                },
                "required": ["tag", "status", "reason"],
            },
        },
        "additional_observations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["character_identification", "tag_evaluation", "additional_observations"],
}


def build_compose_user_prompt(
    meta: dict | None,
    include_artist: bool = False,
    verify_result: dict | None = None,
    *,
    skip_verify_note: bool = False,
) -> str:
    """第 3 轮：基于前两轮的事实写自然中文段落。

    - verify_result: 第 2 轮的 JSON。若为 None 或缺失，按"未校验"提示模型谨慎使用 tags。
    - skip_verify_note: 仅当 stage 2 失败时为 True，提示成文时小心 tags 可能含图中未出现的元素。
    """
    tags = (meta or {}).get("tags", {}) or {}
    char_id = (verify_result or {}).get("character_identification") or {}
    fallback_desc = char_id.get("fallback_description")
    consistent = char_id.get("consistent")

    lines = [
        "这是第 3 轮：成文。",
        "",
        "现在请把第 1 轮的观察与第 2 轮的校验结论融为一段自然、细腻的中文描述。",
        "",
        "格式与文风要求：",
        "- 只输出最终段落，不要任何开场白、标题或 markdown。",
        "- 一段为宜，最多两段且衔接紧密；连贯的散文，不要逗号堆砌式标签流水账。",
        "- 行文顺序融入：主体与构图 → 角色身份与外貌 → 服饰与配件 → 背景与场景 → 氛围/光影/风格。",
        "",
        "事实使用规则（重要）：",
    ]

    if skip_verify_note:
        lines.append("- ⚠️ 本次第 2 轮校验缺失：请只描写你在第 1 轮明确观察到的元素；对元数据中的 tags 持怀疑态度，凡是不能直接对应到第 1 轮观察的 tag 一律不要写入。")
    else:
        lines += [
            "- 只允许使用第 2 轮中 status='visible' 的 tag，以及第 1 轮观察到的事实和 additional_observations。",
            "- 第 2 轮中 status='absent' 的 tag **严禁出现**在文中。",
            "- 第 2 轮中 status='uncertain' 的 tag 仅在第 1 轮明确观察到的前提下才能用，否则忽略。",
        ]

    if consistent is False and fallback_desc:
        lines.append(
            f"- 角色识别校验为不一致：**不要使用元数据中的角色名**。请用以下描述替代角色身份：「{fallback_desc}」。"
        )
    elif consistent is True:
        name = tags.get("tag_string_character") or ""
        series = tags.get("tag_string_copyright") or ""
        if name or series:
            lines.append(
                f"- 角色识别校验通过：可以自然地融入角色名「{name}」与所属作品「{series}」，不要机械罗列。"
            )

    if include_artist:
        artist = (meta or {}).get("artist") or tags.get("tag_string_artist")
        if artist:
            lines.append(f"- 画师 (Artist)：{artist} —— 可适度点出其风格倾向，但不要喧宾夺主。")

    lines += [
        "",
        "现在请输出最终的中文描述段落（仅段落本身）。",
    ]
    return "\n".join(lines)
