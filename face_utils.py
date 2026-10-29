"""QQ 原生表情（小黄脸）的 id/名称表和回复标记解析。

这里不导入 astrbot，方便单独测试。
"""

from __future__ import annotations

import re

# QQ 经典小黄脸 id（0-104），与 OneBot v11 的 face.data.id 一致。
# 新版的"超级表情"id 在 260+，名称可以从 SnowLuma 的 get_sys_faces 动态取，
# 这里只保留最常用、最稳定的一段。
FACE_NAMES: dict[int, str] = {
    0: "惊讶",
    1: "撇嘴",
    2: "色",
    3: "发呆",
    4: "得意",
    5: "流泪",
    6: "害羞",
    7: "闭嘴",
    8: "睡",
    9: "大哭",
    10: "尴尬",
    11: "发怒",
    12: "调皮",
    13: "呲牙",
    14: "微笑",
    15: "难过",
    16: "酷",
    17: "冷汗",
    18: "抓狂",
    19: "吐",
    20: "偷笑",
    21: "可爱",
    22: "白眼",
    23: "傲慢",
    24: "饥饿",
    25: "困",
    26: "惊恐",
    27: "流汗",
    28: "憨笑",
    29: "大兵",
    30: "奋斗",
    31: "咒骂",
    32: "疑问",
    33: "嘘",
    34: "晕",
    35: "折磨",
    36: "衰",
    37: "骷髅",
    38: "敲打",
    39: "再见",
    40: "擦汗",
    41: "抠鼻",
    42: "鼓掌",
    43: "糗大了",
    44: "坏笑",
    45: "左哼哼",
    46: "右哼哼",
    47: "哈欠",
    48: "鄙视",
    49: "委屈",
    50: "快哭了",
    51: "阴险",
    52: "亲亲",
    53: "吓",
    54: "可怜",
    55: "菜刀",
    56: "西瓜",
    57: "啤酒",
    58: "篮球",
    59: "乒乓",
    60: "咖啡",
    61: "饭",
    62: "猪头",
    63: "玫瑰",
    64: "凋谢",
    65: "示爱",
    66: "爱心",
    67: "心碎",
    68: "蛋糕",
    69: "闪电",
    70: "炸弹",
    71: "刀",
    72: "足球",
    73: "瓢虫",
    74: "便便",
    75: "月亮",
    76: "太阳",
    77: "礼物",
    78: "拥抱",
    79: "强",
    80: "弱",
    81: "握手",
    82: "胜利",
    83: "抱拳",
    84: "勾引",
    85: "拳头",
    86: "差劲",
    87: "爱你",
    88: "NO",
    89: "OK",
    90: "爱情",
    91: "飞吻",
    92: "跳跳",
    93: "发抖",
    94: "怄火",
    95: "转圈",
    96: "磕头",
    97: "回头",
    98: "跳绳",
    99: "挥手",
    100: "激动",
    101: "街舞",
    102: "献吻",
    103: "左太极",
    104: "右太极",
}

NAME_TO_ID: dict[str, int] = {name: fid for fid, name in FACE_NAMES.items()}

# QQ 客户端里的表情快捷输入（/cy 之类）只在客户端生效，发出去的还是文字。
# 这里认一部分常见的拼音缩写，方便 /表情 cy 这种用法。
ALIASES: dict[str, int] = {
    "cy": 13,  # 呲牙
    "wx": 14,  # 微笑
    "tp": 12,  # 调皮
    "hx": 6,  # 害羞
    "ll": 5,  # 流泪
    "dk": 9,  # 大哭
    "fn": 11,  # 发怒
    "dy": 4,  # 得意
    "by": 22,  # 白眼
    "zk": 18,  # 抓狂
    "bz": 7,  # 闭嘴
    "se": 2,  # 色
}

# 推荐写法：&#表情名#&，例如 &#呲牙#&
NAME_MARKER_RE = re.compile(r"&#([^#&\n]{1,24})#&")

# 兼容旧写法：[face:13] [face:呲牙] [表情:13] [CQ:face,id=13]
LEGACY_MARKER_RE = re.compile(
    r"\[(?:CQ:face,id=|face:|表情:)\s*([^\]\n]{1,24})\]",
    re.IGNORECASE,
)

# 供 normalize_text 复用的“任意一种标记”
_MARKER_BODY = r"(?:&#[^#&\n]{1,24}#&|\[(?:CQ:face,id=|face:|表情:)[^\]\n]{1,24}\])"

# 分段回复会按这些标点切句，表情必须落在标点前面才不会被切走
SPLIT_PUNCT = "。！？!?~…，,、；;：:"

_AFTER_PUNCT_RE = re.compile(rf"([{SPLIT_PUNCT}]+)(\s*)({_MARKER_BODY})")
_BEFORE_SPACE_RE = re.compile(rf"\s+({_MARKER_BODY})")


def normalize_text(text: str) -> str:
    """调整表情标记的位置，避免被分段回复切开。

    规则：
    - "你好。&#呲牙#&" -> "你好&#呲牙#&。"（挪到标点前，标点仍由表情后面切）
    - "你好 &#呲牙#&" -> "你好&#呲牙#&"（去掉前面的空白）

    Args:
        text: 回复里的纯文本。

    Returns:
        调整过标记位置的文本。
    """
    text = _AFTER_PUNCT_RE.sub(r"\3\1", text)
    text = _BEFORE_SPACE_RE.sub(r"\1", text)
    return text


def resolve_face_token(
    token: str,
    name_to_id: dict[str, int] | None = None,
) -> int | None:
    """把标记里的内容解析成表情 id。

    Args:
        token: "13"、"呲牙"、"/呲牙"、"cy" 等。
        name_to_id: 可选的名字索引（键应已小写），来自 SnowLuma 目录。

    Returns:
        表情 id，解析不出来返回 None。
    """
    token = token.strip()
    if not token:
        return None
    if token.isdigit():
        face_id = int(token)
        return face_id if 0 <= face_id <= 10_000 else None

    name = token.lstrip("/").strip()
    key = name.lower()
    if name_to_id:
        face_id = name_to_id.get(key)
        if face_id is not None:
            return face_id
    face_id = NAME_TO_ID.get(name)
    if face_id is not None:
        return face_id
    return ALIASES.get(key)


def scan_markers(
    text: str,
    name_to_id: dict[str, int] | None = None,
) -> list[tuple[int, int, int | None, str]]:
    """找出文本里的所有表情标记。

    Args:
        text: 回复文本。
        name_to_id: 可选的名字索引。

    Returns:
        (start, end, face_id 或 None, 原始 token) 的列表，按出现顺序。
    """
    spans: list[tuple[int, int, str]] = []
    for match in NAME_MARKER_RE.finditer(text):
        spans.append((match.start(), match.end(), match.group(1)))
    for match in LEGACY_MARKER_RE.finditer(text):
        spans.append((match.start(), match.end(), match.group(1)))
    spans.sort(key=lambda item: item[0])

    result: list[tuple[int, int, int | None, str]] = []
    last_end = -1
    for start, end, token in spans:
        if start < last_end:
            continue
        result.append((start, end, resolve_face_token(token, name_to_id), token))
        last_end = end
    return result


def split_face_markers(
    text: str,
    name_to_id: dict[str, int] | None = None,
) -> list[tuple[str, object]]:
    """把一段文本拆成 [("text", str) | ("face", int), ...]。

    Args:
        text: 可能包含 &#呲牙#& 或 [face:13] 的文本。
        name_to_id: 可选的名字索引。

    Returns:
        顺序保留的片段列表；解析不出来的标记会被丢弃。

    Examples:
        >>> split_face_markers("好耶&#呲牙#&", {"呲牙": 13})
        [('text', '好耶'), ('face', 13)]
    """
    parts: list[tuple[str, object]] = []
    pos = 0
    for start, end, face_id, _token in scan_markers(text, name_to_id):
        if start > pos:
            parts.append(("text", text[pos:start]))
        if face_id is not None:
            parts.append(("face", face_id))
        pos = end
    if pos < len(text):
        parts.append(("text", text[pos:]))
    return parts


def resolve_face_id(token: str) -> int | None:
    """把 "13" / "呲牙" / "/cy" 解析成表情 id；解析不出来返回 None。

    Args:
        token: 用户输入的表情编号或名称。

    Returns:
        表情 id，或 None。
    """
    token = token.strip().lstrip("/")
    if not token:
        return None
    if token.isdigit():
        face_id = int(token)
        return face_id if 0 <= face_id <= 10_000 else None
    if token in NAME_TO_ID:
        return NAME_TO_ID[token]
    return ALIASES.get(token.lower())
