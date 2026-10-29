"""用最小 AstrBot 替身验证发送链路：标记 -> Face、策略拦截、分段后仍黏着。

运行：
    python tests/test_send_path.py
"""

from __future__ import annotations

import asyncio
import logging
import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


# ---------------------------------------------------------------- AstrBot 替身
class Plain:
    def __init__(self, text: str) -> None:
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Plain({self.text!r})"


class Face:
    def __init__(self, id: int) -> None:  # noqa: A002 - 对齐 AstrBot 字段名
        self.id = id

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Face({self.id})"


class MessageChain:
    def __init__(self, chain: list | None = None) -> None:
        self.chain = chain or []


class Star:
    def __init__(self, context) -> None:
        self.context = context

    async def get_kv_data(self, key, default=None):
        return default

    async def put_kv_data(self, key, value) -> None:
        return None


class Context:
    def __init__(self) -> None:
        self.platform_manager = types.SimpleNamespace(platform_insts=[])
        self.registered = []

    def register_web_api(self, route, handler, methods, desc) -> None:
        self.registered.append((route, handler, methods, desc))


class FilterNamespace:
    class EventMessageType:
        ALL = 0

    @staticmethod
    def _identity_decorator(*_args, **_kwargs):
        def decorate(func):
            return func

        return decorate

    event_message_type = _identity_decorator
    command = _identity_decorator
    on_llm_request = _identity_decorator
    on_decorating_result = _identity_decorator


def install_stubs() -> None:
    if "astrbot" in sys.modules:
        return
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = logging.getLogger("qq_face_test")
    event_mod = types.ModuleType("astrbot.api.event")
    event_mod.AstrMessageEvent = object
    event_mod.MessageChain = MessageChain
    event_mod.filter = FilterNamespace
    comps = types.ModuleType("astrbot.api.message_components")
    comps.Face = Face
    comps.Plain = Plain
    provider = types.ModuleType("astrbot.api.provider")
    provider.ProviderRequest = type("ProviderRequest", (), {})
    star = types.ModuleType("astrbot.api.star")
    star.Context = Context
    star.Star = Star
    web = types.ModuleType("astrbot.api.web")
    web.json_response = lambda data: data
    web.error_response = lambda message, status_code=400: {"error": message}
    web.request = types.SimpleNamespace(query={}, json=None)

    core = types.ModuleType("astrbot.core")
    agent = types.ModuleType("astrbot.core.agent")
    agent_message = types.ModuleType("astrbot.core.agent.message")

    class TextPart:
        def __init__(self, text: str) -> None:
            self.text = text

        def mark_as_temp(self):
            return self

    agent_message.TextPart = TextPart
    agent.message = agent_message
    core.agent = agent

    for name, module in {
        "astrbot": astrbot,
        "astrbot.api": api,
        "astrbot.api.event": event_mod,
        "astrbot.api.message_components": comps,
        "astrbot.api.provider": provider,
        "astrbot.api.star": star,
        "astrbot.api.web": web,
        "astrbot.core": core,
        "astrbot.core.agent": agent,
        "astrbot.core.agent.message": agent_message,
    }.items():
        sys.modules[name] = module


install_stubs()

from astrbot_plugin_qq_face.main import QQFacePlugin  # noqa: E402
from astrbot_plugin_qq_face.policy import normalize_settings  # noqa: E402

SEG_REGEX = r".*?[。？！~…]+|.+$"


class FakeResult:
    def __init__(self, chain) -> None:
        self.chain = chain
        self.use_t2i_ = None


class FakeEvent:
    def __init__(self, chain, umo: str = "aiocqhttp:FriendMessage:1") -> None:
        self._result = FakeResult(chain)
        self.unified_msg_origin = umo
        self.sent = []
        self._extras = {}

    def get_result(self):
        return self._result

    def get_extra(self, key, default=None):
        return self._extras.get(key, default)

    def set_extra(self, key, value) -> None:
        self._extras[key] = value

    async def send(self, message) -> None:
        self.sent.append(message)


def check(name, actual, expected) -> None:
    if actual != expected:
        raise AssertionError(f"{name}: expected {expected!r}, got {actual!r}")
    print(f"ok - {name}")


def split_like_astrbot(chain):
    """模拟分段回复：只拆 Plain，其它组件原样保留。"""
    new_chain = []
    for comp in chain:
        if isinstance(comp, Plain):
            for seg in re.findall(SEG_REGEX, comp.text, re.DOTALL | re.MULTILINE):
                if seg.strip():
                    new_chain.append(Plain(seg))
        else:
            new_chain.append(comp)
    return new_chain


async def run_case(text: str, enabled_ids: list[int], invited: bool = True):
    plugin = QQFacePlugin(Context())
    plugin.settings = normalize_settings(
        {
            "schemes": [{"id": "t", "name": "测试方案", "faces": enabled_ids}],
            "active_scheme": "t",
            "probability": 1.0,
            "cooldown_seconds": 0,
            "max_per_reply": 2,
            "hourly_limit": 0,
        },
    )
    plugin.policy.update_settings(plugin.settings)

    event = FakeEvent([Plain(text)])
    event.set_extra("_qq_face_invited", invited)
    await plugin.prepare_face_markers(event)

    result = event.get_result()
    result.chain = split_like_astrbot(result.chain)
    for comp in list(result.chain):
        await event.send(MessageChain([comp]))
    return event.sent


async def main() -> None:
    sent = await run_case("你好[face:13]。今天怎么样？", [13])
    check("two segmented messages", len(sent), 2)
    first = sent[0].chain
    check("first message keeps text", first[0].text, "你好")
    check("first message has face", isinstance(first[1], Face), True)
    check("face id", first[1].id, 13)
    check("second message has no face", any(isinstance(c, Face) for c in sent[1].chain), False)

    sent = await run_case("你好[face:13]。", [13], invited=False)
    check(
        "not invited drops face",
        any(isinstance(c, Face) for m in sent for c in m.chain),
        False,
    )
    check("text kept when not invited", sent[0].chain[0].text, "你好")

    sent = await run_case("你好[face:13]。", [14])
    check(
        "outside scheme but known face allowed",
        any(isinstance(c, Face) for m in sent for c in m.chain),
        True,
    )
    check("text kept when face allowed", sent[0].chain[0].text, "你好")

    sent = await run_case("你好[face:3000]。", [14])
    check(
        "unknown face dropped",
        any(isinstance(c, Face) for m in sent for c in m.chain),
        False,
    )

    sent = await run_case("好耶[face:13][face:14]", [13, 14])
    faces = [c.id for m in sent for c in m.chain if isinstance(c, Face)]
    check("two faces allowed", faces, [13, 14])

    sent = await run_case("好耶[face:13][face:13]", [13])
    faces = [c.id for m in sent for c in m.chain if isinstance(c, Face)]
    check("budget limits duplicates", faces, [13])

    sent = await run_case("好耶&#呲牙#&。", [13])
    faces = [c.id for m in sent for c in m.chain if isinstance(c, Face)]
    check("name marker resolves", faces, [13])
    check("name marker text kept", sent[0].chain[0].text, "好耶")

    sent = await run_case("好耶&#不存在#&。", [13])
    check(
        "unknown name produces no face",
        any(isinstance(c, Face) for m in sent for c in m.chain),
        False,
    )

    print("\nall send-path checks passed")


if __name__ == "__main__":
    asyncio.run(main())
