"""表情发送策略：决定「这一轮邀不邀请模型发表情」，以及放行哪些。

两个阶段：
1. 请求前 invite()：按概率 / 冷却 / 每小时配额决定这一轮要不要注入提示词；
2. 发送前 select()：被邀请的那一轮，按方案白名单和每条上限放行具体表情。
"""

from __future__ import annotations

import random
import time

VALID_MODES = {"prompt", "emotion"}
VALID_FACE_MODES = {"all", "random"}

DEFAULT_SCHEME_ID = "default"
DEFAULT_SCHEME_NAME = "默认小黄脸"

# 默认方案：QQ 小黄脸里最常用的一批（名字用于展示，id 用于发送）
DEFAULT_SCHEME_FACES: list[tuple[str, int]] = [
    ("呲牙", 13),
    ("斜眼笑", 178),
    ("捂脸", 264),
    ("笑哭", 182),
    ("汪汪", 277),
    ("喷血", 177),
    ("可爱", 21),
    ("OK", 124),
    ("卖萌", 175),
    ("问号脸", 268),
    ("发怒", 11),
    ("惊讶", 0),
    ("可怜", 111),
    ("幽灵", 187),
    ("傲慢", 23),
    ("抓狂", 18),
]


def default_scheme() -> dict:
    """内置的默认方案。"""
    return {
        "id": DEFAULT_SCHEME_ID,
        "name": DEFAULT_SCHEME_NAME,
        "faces": [face_id for _name, face_id in DEFAULT_SCHEME_FACES],
    }


DEFAULT_SETTINGS: dict = {
    "enabled": True,
    "mode": "prompt",
    "probability": 0.5,
    "max_per_reply": 2,
    "cooldown_seconds": 30,
    "hourly_limit": 40,
    "schemes": [default_scheme()],
    "active_scheme": DEFAULT_SCHEME_ID,
    # 表情 id（字符串）-> 别名
    "aliases": {},
    # all = 全部注入；random = 每轮随机抽 N 个
    "prompt_faces_mode": "all",
    "prompt_faces_count": 12,
    # 由当前方案推导，运行期只认这个集合
    "enabled_ids": default_scheme()["faces"],
    # 模型用了方案之外、但确实存在于 QQ 表情目录里的表情时，是否放行
    "allow_outside_scheme": True,
    # 空字符串表示使用内置默认提示词
    "prompt_template": "",
}


def _to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_faces(values) -> list[int]:
    result = set()
    if isinstance(values, list):
        for value in values:
            face_id = _to_int(value)
            if face_id is not None and 0 <= face_id <= 10_000:
                result.add(face_id)
    return sorted(result)


def _normalize_schemes(raw) -> list[dict]:
    schemes: list[dict] = []
    seen: set[str] = set()
    if isinstance(raw, list):
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            scheme_id = str(item.get("id") or f"scheme{index + 1}")[:32]
            if not scheme_id or scheme_id in seen:
                scheme_id = f"scheme{index + 1}"
            if scheme_id in seen:
                continue
            seen.add(scheme_id)
            name = str(item.get("name") or f"方案 {index + 1}")[:24]
            schemes.append(
                {
                    "id": scheme_id,
                    "name": name,
                    "faces": _clean_faces(item.get("faces")),
                },
            )
            if len(schemes) >= 20:
                break
    return schemes


def _normalize_aliases(raw) -> dict[str, str]:
    aliases: dict[str, str] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            face_id = _to_int(key)
            alias = str(value or "").strip()[:16]
            if face_id is None or not alias:
                continue
            aliases[str(face_id)] = alias
    return aliases


def active_scheme(settings: dict) -> dict | None:
    """返回当前方案。"""
    for scheme in settings.get("schemes") or []:
        if scheme["id"] == settings.get("active_scheme"):
            return scheme
    schemes = settings.get("schemes") or []
    return schemes[0] if schemes else None


def alias_map(settings: dict) -> dict[int, str]:
    """把字符串键的别名字典转成 int 键。"""
    result: dict[int, str] = {}
    for key, value in (settings.get("aliases") or {}).items():
        face_id = _to_int(key)
        if face_id is not None and value:
            result[face_id] = str(value)
    return result


def sync_enabled_ids(settings: dict) -> dict:
    """按当前方案刷新 enabled_ids（原地修改并返回）。"""
    scheme = active_scheme(settings)
    settings["enabled_ids"] = list(scheme["faces"]) if scheme else []
    return settings


def normalize_settings(raw: dict | None) -> dict:
    """合并默认值并做类型/范围校正。

    Args:
        raw: 从 KV 或视图读到的设置，可能缺字段、类型不对或越界。

    Returns:
        完整、可安全使用的设置字典。
    """
    settings = dict(DEFAULT_SETTINGS)
    raw_schemes = raw.get("schemes") if isinstance(raw, dict) else None
    legacy_ids = _clean_faces((raw or {}).get("enabled_ids")) if isinstance(raw, dict) else []
    if isinstance(raw, dict):
        settings.update(raw)

    def _num(key: str, default: float, low: float, high: float, cast=float):
        try:
            value = cast(settings.get(key, default))
        except (TypeError, ValueError):
            value = cast(default)
        return min(max(value, low), high)

    settings["enabled"] = bool(settings.get("enabled", True))
    settings["allow_outside_scheme"] = bool(settings.get("allow_outside_scheme", True))
    mode = settings.get("mode")
    settings["mode"] = mode if mode in VALID_MODES else "prompt"
    settings["probability"] = _num("probability", 0.5, 0.0, 1.0)
    settings["max_per_reply"] = _num("max_per_reply", 2, 0, 5, int)
    settings["cooldown_seconds"] = _num("cooldown_seconds", 30, 0, 3600, int)
    settings["hourly_limit"] = _num("hourly_limit", 40, 0, 1000, int)
    face_mode = settings.get("prompt_faces_mode")
    settings["prompt_faces_mode"] = face_mode if face_mode in VALID_FACE_MODES else "all"
    settings["prompt_faces_count"] = _num("prompt_faces_count", 12, 1, 200, int)

    template = settings.get("prompt_template")
    settings["prompt_template"] = str(template)[:4000] if isinstance(template, str) else ""

    schemes = _normalize_schemes(raw_schemes)
    if not schemes:
        schemes = [default_scheme()]
        if legacy_ids and set(legacy_ids) != set(default_scheme()["faces"]):
            # 0.4 及以前：勾选表情包后算出来的白名单，保留成"升级前选择"
            schemes.append(
                {"id": "legacy", "name": "升级前选择", "faces": legacy_ids},
            )
    settings["schemes"] = schemes

    active = str(settings.get("active_scheme") or "")
    if not any(scheme["id"] == active for scheme in schemes):
        active = schemes[0]["id"]
    settings["active_scheme"] = active
    settings["aliases"] = _normalize_aliases(settings.get("aliases"))

    sync_enabled_ids(settings)
    return settings


class FacePolicy:
    """按会话限流地决定邀请与放行。"""

    def __init__(
        self,
        settings: dict,
        clock=time.time,
        rng=random.random,
    ) -> None:
        self.settings = normalize_settings(settings)
        self.clock = clock
        self.rng = rng
        self.last_sent: dict[str, float] = {}
        self.total = 0
        self.day_key = ""
        self.day_count = 0
        self.hour_key = -1
        self.hour_count = 0

    def update_settings(self, settings: dict) -> None:
        """热更新设置（视图保存后调用）。"""
        self.settings = normalize_settings(settings)

    def _roll_buckets(self, now: float) -> None:
        day_key = time.strftime("%Y-%m-%d", time.localtime(now))
        if day_key != self.day_key:
            self.day_key = day_key
            self.day_count = 0
        hour_key = int(now // 3600)
        if hour_key != self.hour_key:
            self.hour_key = hour_key
            self.hour_count = 0

    def invite(self, umo: str) -> bool:
        """这一轮要不要邀请模型发表情（也就是注不注入提示词）。

        Args:
            umo: 会话标识，用于冷却判断。

        Returns:
            True 表示本轮打开表情窗口。
        """
        settings = self.settings
        if (
            not settings["enabled"]
            or settings["mode"] != "prompt"
            or settings["max_per_reply"] <= 0
            or not settings["enabled_ids"]
        ):
            return False

        now = self.clock()
        self._roll_buckets(now)

        limit = settings["hourly_limit"]
        if limit and self.hour_count >= limit:
            return False

        last = self.last_sent.get(umo)
        if last is not None and now - last < settings["cooldown_seconds"]:
            return False

        return self.rng() <= settings["probability"]

    def select(
        self,
        face_ids: list[int],
        umo: str,
        invited: bool,
        extra_allowed: set[int] | None = None,
    ) -> list[int]:
        """从模型写的标记里挑出真正允许发送的。

        Args:
            face_ids: 回复里出现的表情 id，按出现顺序。
            umo: 会话标识。
            invited: 本轮是否被 invite() 放行。
            extra_allowed: 方案之外但确实存在的表情 id（开关打开时才生效）。

        Returns:
            允许发送的 id 列表；空列表表示这一轮不发任何表情。
        """
        settings = self.settings
        if not settings["enabled"] or not invited or not face_ids:
            return []

        now = self.clock()
        self._roll_buckets(now)

        allowed_ids = set(settings["enabled_ids"])
        if settings.get("allow_outside_scheme") and extra_allowed:
            allowed_ids |= extra_allowed
        max_per_reply = settings["max_per_reply"]
        if max_per_reply <= 0 or not allowed_ids:
            return []

        picked: list[int] = []
        seen: set[int] = set()
        for face_id in face_ids:
            if len(picked) >= max_per_reply:
                break
            if face_id in seen:
                continue
            seen.add(face_id)
            if face_id not in allowed_ids:
                continue
            picked.append(face_id)

        if not picked:
            return []

        limit = settings["hourly_limit"]
        if limit:
            remaining = limit - self.hour_count
            if remaining <= 0:
                return []
            picked = picked[:remaining]

        if not picked:
            return []

        self.last_sent[umo] = now
        self.hour_count += len(picked)
        self.day_count += len(picked)
        self.total += len(picked)
        return picked

    def stats(self) -> dict:
        """返回给视图展示的统计信息。"""
        self._roll_buckets(self.clock())
        return {
            "day": self.day_key,
            "day_count": self.day_count,
            "hour_count": self.hour_count,
            "total": self.total,
        }

    def load_stats(self, data: dict | None) -> None:
        """从 KV 恢复统计（跨重启保留日/累计计数）。"""
        if not isinstance(data, dict):
            return
        now = self.clock()
        self._roll_buckets(now)
        if data.get("day") == self.day_key:
            try:
                self.day_count = int(data.get("day_count", 0))
            except (TypeError, ValueError):
                self.day_count = 0
        try:
            self.total = max(int(data.get("total", 0)), self.day_count)
        except (TypeError, ValueError):
            self.total = self.day_count
