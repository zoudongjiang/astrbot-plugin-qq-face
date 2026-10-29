"""不依赖 AstrBot 的纯逻辑自测。

运行：
    python tests/test_policy_and_text.py
"""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

# catalog.py 只用到 astrbot 的 logger，这里放一个最小替身，避免测试依赖 AstrBot 环境
if "astrbot" not in sys.modules:
    import logging

    _astrbot = types.ModuleType("astrbot")
    _api = types.ModuleType("astrbot.api")
    _api.logger = logging.getLogger("qq_face_test")
    _astrbot.api = _api
    sys.modules["astrbot"] = _astrbot
    sys.modules["astrbot.api"] = _api

from astrbot_plugin_qq_face.catalog import FaceCatalog  # noqa: E402
from astrbot_plugin_qq_face.face_utils import (  # noqa: E402
    NAME_MARKER_RE,
    normalize_text,
    resolve_face_id,
    split_face_markers,
)
from astrbot_plugin_qq_face.policy import FacePolicy, normalize_settings  # noqa: E402
from astrbot_plugin_qq_face.prompt_builder import (  # noqa: E402
    render_prompt,
    strip_prompt,
    wrap_prompt,
)

# AstrBot 分段回复的默认正则（platform_settings.segmented_reply.regex）
SEG_REGEX = r".*?[。？！~…]+|.+$"


def check(name: str, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
    print(f"ok - {name}")


def test_marker_text() -> None:
    check(
        "marker moved before period",
        normalize_text("你好。[face:13]"),
        "你好[face:13]。",
    )
    check(
        "space before marker removed",
        normalize_text("你好 [face:13]"),
        "你好[face:13]",
    )
    check(
        "full-width question mark",
        normalize_text("真的吗？[表情:20]"),
        "真的吗[表情:20]？",
    )
    check(
        "no marker untouched",
        normalize_text("今天天气不错。"),
        "今天天气不错。",
    )
    check(
        "cq form also recognized",
        split_face_markers("a[CQ:face,id=14]b"),
        [("text", "a"), ("face", 14), ("text", "b")],
    )
    check(
        "name marker resolves",
        split_face_markers("好耶&#呲牙#&", {"呲牙": 13}),
        [("text", "好耶"), ("face", 13)],
    )
    check(
        "name marker normalized",
        normalize_text("你好。&#呲牙#&"),
        "你好&#呲牙#&。",
    )
    check(
        "legacy marker with name",
        split_face_markers("[face:呲牙]", {"呲牙": 13}),
        [("face", 13)],
    )
    check(
        "unknown name dropped",
        split_face_markers("好&#不存在#&耶"),
        [("text", "好"), ("text", "耶")],
    )


def test_segmentation_keeps_face_attached() -> None:
    """表情所在的那一段必须同时包含前面的文字。"""
    text = "你好[face:13]。今天怎么样？"
    segments = re.findall(SEG_REGEX, text, re.DOTALL | re.MULTILINE)
    check("split count", len(segments), 2)
    check("face stays in first segment", "[face:13]" in segments[0], True)
    check("first segment", segments[0], "你好[face:13]。")

    moved = normalize_text("你好。[face:13]今天怎么样？")
    segments = re.findall(SEG_REGEX, moved, re.DOTALL | re.MULTILINE)
    check("after normalize, face not alone", "[face:13]" in segments[0], True)
    check("normalized text", moved, "你好[face:13]。今天怎么样？")


def test_resolve() -> None:
    check("resolve id", resolve_face_id("13"), 13)
    check("resolve name", resolve_face_id("呲牙"), 13)
    check("resolve alias", resolve_face_id("cy"), 13)
    check("resolve slash alias", resolve_face_id("/cy"), 13)
    check("resolve unknown", resolve_face_id("不存在"), None)
    check("resolve out of range", resolve_face_id("99999"), None)


def _scheme_settings(face_ids: list[int], **extra) -> dict:
    return {
        "schemes": [{"id": "t", "name": "测试方案", "faces": face_ids}],
        "active_scheme": "t",
        **extra,
    }


def test_settings() -> None:
    check("default probability", normalize_settings(None)["probability"], 0.5)
    settings = normalize_settings(
        {
            "probability": 5,
            "max_per_reply": 99,
            "cooldown_seconds": -1,
            "prompt_faces_mode": "nonsense",
            "prompt_faces_count": 999,
            "aliases": {"13": "开心露牙", "x": "坏数据", "14": ""},
        },
    )
    check("probability clamped", settings["probability"], 1.0)
    check("max clamped", settings["max_per_reply"], 5)
    check("cooldown clamped", settings["cooldown_seconds"], 0)
    check("face mode fallback", settings["prompt_faces_mode"], "all")
    check("face count clamped", settings["prompt_faces_count"], 200)
    check("aliases cleaned", settings["aliases"], {"13": "开心露牙"})
    check(
        "default scheme",
        settings["schemes"][0]["name"],
        "默认小黄脸",
    )
    check("default scheme size", len(settings["enabled_ids"]), 16)

    migrated = normalize_settings({"enabled_ids": [13, 14]})
    check("migration keeps legacy", [s["id"] for s in migrated["schemes"]], ["default", "legacy"])
    check("migration active default", migrated["active_scheme"], "default")
    check("migration enabled ids", migrated["enabled_ids"], list(migrated["schemes"][0]["faces"]))


def test_policy() -> None:
    now = [1000.0]
    settings = normalize_settings(
        _scheme_settings(
            [13, 14, 20],
            probability=1.0,
            max_per_reply=2,
            cooldown_seconds=20,
            hourly_limit=5,
        ),
    )
    policy = FacePolicy(settings, clock=lambda: now[0], rng=lambda: 0.0)

    check("invite passes", policy.invite("umo"), True)
    check("whitelist respected", policy.select([74], "umo", invited=True), [])
    check("not invited drops all", policy.select([13], "umo", invited=False), [])
    check("disabled face dropped", policy.select([13, 74], "umo", invited=True), [13])
    check("dedup and cap", policy.select([13, 13, 14], "umo2", invited=True), [13, 14])

    now[0] += 1
    check("cooldown blocks invite", policy.invite("umo2"), False)

    now[0] += 30
    check("cooldown expires", policy.select([13], "umo2", invited=True), [13])

    # 每小时上限 5，已经发了 4 个，这里只能再放 1 个
    now[0] += 100
    check("hourly limit trims", policy.select([13, 14, 20], "umo3", invited=True), [13])

    stats = policy.stats()
    check("stats hour count", stats["hour_count"], 5)
    check("hourly limit blocks invite", policy.invite("umo5"), False)

    now[0] += 4000
    check("hourly limit reset", policy.select([13], "umo4", invited=True), [13])

    disabled = FacePolicy(
        normalize_settings(_scheme_settings([13], enabled=False)),
        clock=lambda: now[0],
        rng=lambda: 0.0,
    )
    check("global switch off", disabled.select([13], "umo", invited=True), [])
    check("global switch off invite", disabled.invite("umo"), False)

    zero_prob = FacePolicy(
        normalize_settings(
            _scheme_settings([13], probability=0.0, cooldown_seconds=0),
        ),
        clock=lambda: now[0],
        rng=lambda: 1.0,
    )
    check("probability blocks invite", zero_prob.invite("umo"), False)

    emotion_mode = FacePolicy(
        normalize_settings(_scheme_settings([13], mode="emotion")),
        clock=lambda: now[0],
        rng=lambda: 0.0,
    )
    check("emotion mode not wired yet", emotion_mode.invite("umo"), False)

    # 方案外但目录里存在的表情：开关打开时放行，关闭时拒绝
    permissive = FacePolicy(
        normalize_settings(_scheme_settings([13], probability=1.0, cooldown_seconds=0)),
        clock=lambda: now[0],
        rng=lambda: 0.0,
    )
    check(
        "outside scheme allowed when known",
        permissive.select([77], "umo", invited=True, extra_allowed={77}),
        [77],
    )
    check(
        "outside scheme dropped when unknown",
        permissive.select([78], "umo", invited=True, extra_allowed={77}),
        [],
    )

    strict = FacePolicy(
        normalize_settings(
            _scheme_settings(
                [13],
                probability=1.0,
                cooldown_seconds=0,
                allow_outside_scheme=False,
            ),
        ),
        clock=lambda: now[0],
        rng=lambda: 0.0,
    )
    check(
        "outside scheme rejected when toggle off",
        strict.select([77], "umo", invited=True, extra_allowed={77}),
        [],
    )


def test_prompt_builder() -> None:
    settings = normalize_settings(
        _scheme_settings(
            [13, 14],
            max_per_reply=2,
            aliases={"13": "开心露牙"},
            prompt_template="最多 {max} 个，可用 {faces}，方案 {packs}。",
        ),
    )
    rendered = render_prompt(settings, {13: "开心露牙", 14: "微笑"})
    check("max placeholder", "最多 2 个" in rendered, True)
    check("faces placeholder", "开心露牙、微笑" in rendered, True)
    check("packs placeholder", "方案 测试方案。" in rendered, True)

    default_settings = normalize_settings(_scheme_settings([13]))
    default_text = render_prompt(default_settings, {13: "呲牙"})
    check("default template used", "[QQ 表情要求]" in default_text, True)
    check("default prompt is mandatory", "至少 1 个" in default_text, True)
    check("default prompt restricts list", "只能从下面这份列表里选" in default_text, True)
    check("default prompt has exception", "例外：报错" in default_text, True)
    check("default prompt teaches name marker", "&#呲牙#&" in default_text, True)


def test_prompt_sampling() -> None:
    rng = __import__("random").Random(7)
    settings = normalize_settings(
        _scheme_settings(
            [13, 14, 20, 21, 22],
            prompt_faces_mode="random",
            prompt_faces_count=2,
            prompt_template="{faces}",
        ),
    )
    names = {13: "呲牙", 14: "微笑", 20: "偷笑", 21: "可爱", 22: "白眼"}
    text = render_prompt(settings, names, rng=rng)
    listed = [name for name in names.values() if name in text]
    check("random picks N", len(listed), 2)

    all_settings = normalize_settings(
        _scheme_settings([13, 14, 20], prompt_faces_mode="all", prompt_template="{faces}"),
    )
    all_text = render_prompt(all_settings, names, rng=rng)
    listed_all = [name for name in names.values() if name in all_text]
    check("all lists everything", len(listed_all), 3)

    wrapped = wrap_prompt("测试提示")
    check("wrap markers", wrapped.startswith("\n\n<!-- qq_face_prompt:start -->"), True)
    check("strip removes block", strip_prompt("人设" + wrapped), "人设")
    check("strip without block", strip_prompt("人设"), "人设")


def test_catalog_normalize() -> None:
    packs = FaceCatalog._normalize(
        {
            "packs": [
                {
                    "pack_name": "经典",
                    "emojis": [
                        {"q_sid": "14", "q_des": "/微笑", "is_super": False},
                        {"q_sid": "13", "q_des": "/呲牙", "is_super": False},
                        {"q_sid": "😊", "q_des": "/嘿嘿", "is_super": False},
                        {"q_sid": "13", "q_des": "/重复", "is_super": False},
                    ],
                },
                {"pack_name": "", "emojis": [{"q_sid": "1", "q_des": "/x"}]},
                {"pack_name": "空包", "emojis": []},
            ],
        },
    )
    check("pack count", len(packs), 1)
    check("faces parsed", [face["id"] for face in packs[0]["faces"]], [14, 13])
    check("names parsed", [face["name"] for face in packs[0]["faces"]], ["微笑", "呲牙"])

    check("garbage input", FaceCatalog._normalize({"packs": "nope"}), [])


def test_marker_regex_bounds() -> None:
    check("name marker matches", bool(NAME_MARKER_RE.search("x&#呲牙#&")), True)
    check("name marker body", NAME_MARKER_RE.search("&#呲牙#&").group(1), "呲牙")


if __name__ == "__main__":
    test_marker_text()
    test_segmentation_keeps_face_attached()
    test_resolve()
    test_settings()
    test_policy()
    test_prompt_builder()
    test_prompt_sampling()
    test_catalog_normalize()
    test_marker_regex_bounds()
    print("\nall checks passed")
