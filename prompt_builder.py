"""提示词构建：把「这一轮必须发表情」的要求拼成可编辑的模板。"""

from __future__ import annotations

import random

PROMPT_START = "<!-- qq_face_prompt:start -->"
PROMPT_END = "<!-- qq_face_prompt:end -->"

DEFAULT_PROMPT_TEMPLATE = (
    "[QQ 表情要求] 这是本轮硬性要求：必须在回复里发至少 1 个、最多 {max} 个 QQ 表情，"
    "不能一个都不发。\n"
    "- 只能从下面这份列表里选，不要用列表之外的表情：{faces}\n"
    "- 写法：&#表情名#&，例如 &#呲牙#&；系统会替换成真正的表情。"
    "不要写 /cy，也不要写 [CQ:face,...]。\n"
    "- 位置：放在想强调的那句话末尾，不要单独占一行，也不要每句都加；"
    "没有完美位置就选最接近的一句。\n"
    "- 例外：报错、代码说明情况除外。"
)

# 提示词里最多列多少个表情（随机模式下由 prompt_faces_count 控制）


def render_prompt(
    settings: dict,
    face_names: dict[int, str],
    rng: random.Random | None = None,
) -> str:
    """按设置渲染提示词。

    Args:
        settings: 插件设置（含 max_per_reply / enabled_ids / prompt_faces_mode / prompt_template）。
        face_names: 表情 id 到名称（或别名）的映射，用于渲染 {faces}。
        rng: 随机源；随机模式下用于抽样，测试可注入固定种子。

    Returns:
        渲染后的提示词文本；模板里的 {max}、{faces}、{packs} 已被替换。
    """
    template = str(settings.get("prompt_template") or "").strip()
    if not template:
        template = DEFAULT_PROMPT_TEMPLATE

    face_ids = list(settings.get("enabled_ids") or [])
    if settings.get("prompt_faces_mode") == "random":
        count = int(settings.get("prompt_faces_count") or 12)
        if 0 < count < len(face_ids):
            face_ids = (rng or random).sample(face_ids, count)

    listed: list[str] = []
    seen: set[str] = set()
    for face_id in face_ids:
        name = face_names.get(face_id) or f"表情{face_id}"
        if name in seen:
            continue
        seen.add(name)
        listed.append(name)
    faces = "、".join(listed) if listed else "（当前没有启用的表情）"
    scheme_name = ""
    for scheme in settings.get("schemes") or []:
        if scheme.get("id") == settings.get("active_scheme"):
            scheme_name = str(scheme.get("name") or "")
            break
    packs = scheme_name or "（未选方案）"

    return (
        template.replace("{max}", str(settings.get("max_per_reply", 2)))
        .replace("{faces}", faces)
        .replace("{packs}", packs)
    )


def wrap_prompt(text: str) -> str:
    """用标记包住提示词，方便重复注入时去重。"""
    return f"\n\n{PROMPT_START}\n{text}\n{PROMPT_END}"


def strip_prompt(system_prompt: str | None) -> str:
    """移除上一次注入的提示词块。

    Args:
        system_prompt: 现有 system prompt。

    Returns:
        去掉 QQ 表情提示块后的文本。
    """
    text = system_prompt or ""
    start = text.find(PROMPT_START)
    if start < 0:
        return text.rstrip()
    end = text.find(PROMPT_END, start)
    if end < 0:
        return text[:start].rstrip()
    return (text[:start] + text[end + len(PROMPT_END) :]).rstrip()
