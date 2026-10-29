"""让 AstrBot 看懂、并按频率发送 QQ 原生小黄脸（face 消息段）。

背景（为什么需要这个插件）：

1. 出站：AstrBot 的 aiocqhttp 适配器把回复拆成 OneBot 消息段数组发出去，
   文本段里的 ``[CQ:face,id=14]`` 不会被解析；SnowLuma 只有在整个
   ``message`` 字段是字符串且 ``auto_escape=false`` 时才解析 CQ 码。
2. 入站：SnowLuma 会把用户发的表情正确转成 ``{"type":"face","data":{"id":13}}``，
   AstrBot 也解析成了 Face 组件，但 Face 不会进入 ``event.message_str``，
   而 LLM 请求正是用 ``message_str`` 拼的。

本插件的处理链路：

- 入站：把 Face 组件补成 ``[表情:呲牙(id=13)]`` 写回 message_str；
- 请求前：按概率/冷却/配额决定这一轮要不要"邀请"模型发表情，邀请时注入
  可编辑的提示词（说明最多发几个、怎么写）；
- 发送前：只做文本归一化（把表情挪到标点前、去掉前面的空白），标记本身
  留到 ``event.send()`` 时才换成 Face 组件——这样分段回复切句时，
  表情仍然黏在前面的那句话里，不会单独成为一条消息；
- 真正发送前：按白名单、每条上限、每小时上限放行，其余标记直接丢弃。
"""

from __future__ import annotations

import time

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Face, Plain
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.api.web import error_response, json_response, request
from astrbot.core.agent.message import TextPart

from .catalog import FaceCatalog, builtin_packs
from .face_utils import (
    ALIASES,
    FACE_NAMES,
    normalize_text,
    resolve_face_id,
    scan_markers,
    split_face_markers,
)
from .policy import (
    FacePolicy,
    active_scheme,
    alias_map,
    default_scheme,
    normalize_settings,
    sync_enabled_ids,
)
from .prompt_builder import (
    DEFAULT_PROMPT_TEMPLATE,
    render_prompt,
    strip_prompt,
    wrap_prompt,
)

PLUGIN_NAME = "astrbot_plugin_qq_face"

_INVITE_DECIDED = "_qq_face_invite_decided"
_INVITE_GRANTED = "_qq_face_invited"
_INJECTED = "_qq_face_prompt_injected"


def _install_send_translator(
    event: AstrMessageEvent,
    allowed_ids: list[int],
    name_to_id: dict[str, int],
) -> None:
    """给事件装一个 send 包装器，把标记换成 Face 组件。

    Args:
        event: 当前消息事件。
        allowed_ids: 策略放行的表情 id，按顺序消耗。
        name_to_id: 表情名到 id 的索引。
    """
    if getattr(event, "_qq_face_send_patched", False):
        return

    original_send = event.send
    budget = list(allowed_ids)

    async def send_with_faces(message, *args, **kwargs):
        if isinstance(message, MessageChain):
            new_chain = []
            changed = False
            for comp in message.chain:
                if not isinstance(comp, Plain) or not scan_markers(
                    comp.text,
                    name_to_id,
                ):
                    new_chain.append(comp)
                    continue
                changed = True
                for kind, value in split_face_markers(comp.text, name_to_id):
                    if kind == "face":
                        face_id = int(value)
                        if face_id in budget:
                            budget.remove(face_id)
                            new_chain.append(Face(id=face_id))
                        # 不在预算里的表情直接丢掉，只保留文字
                    elif str(value).strip():
                        new_chain.append(Plain(str(value)))
            if changed:
                message.chain[:] = new_chain
        return await original_send(message, *args, **kwargs)

    event.send = send_with_faces
    event._qq_face_send_patched = True


class QQFacePlugin(Star):
    """把 Face 组件接入 AstrBot 的消息链，并按设置限频。"""

    def __init__(self, context: Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.settings = normalize_settings(None)
        self.policy = FacePolicy(self.settings)
        self.catalog = FaceCatalog()
        self._bot = None
        self._stats_saved_at = 0.0
        self.id_to_name: dict[int, str] = dict(FACE_NAMES)
        self.name_to_id: dict[str, int] = {
            name.lower(): face_id for face_id, name in FACE_NAMES.items()
        }
        for alias, face_id in ALIASES.items():
            self.name_to_id[alias] = face_id

        context.register_web_api(
            f"/{PLUGIN_NAME}/state",
            self.api_state,
            ["GET"],
            "Get face settings, packs and stats",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/settings",
            self.api_save_settings,
            ["POST"],
            "Save face settings",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/catalog",
            self.api_catalog,
            ["GET"],
            "Fetch the QQ face catalog",
        )
        context.register_web_api(
            f"/{PLUGIN_NAME}/preview",
            self.api_preview,
            ["POST"],
            "Render the prompt that would be injected",
        )

    async def initialize(self) -> None:
        """插件激活时恢复设置和统计。"""
        try:
            stored = await self.get_kv_data("settings", None)
            self.settings = normalize_settings(stored)
            stats = await self.get_kv_data("stats", None)
            self.policy = FacePolicy(self.settings)
            self.policy.load_stats(stats)
            self._rebuild_face_index(self.catalog.packs or builtin_packs())
        except Exception as exc:  # noqa: BLE001 - 存储异常不该让插件加载失败
            logger.warning("[qq_face] failed to restore settings: %s", exc)

    async def terminate(self) -> None:
        try:
            await self._save_stats(force=True)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 钩子

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def annotate_incoming_faces(self, event: AstrMessageEvent) -> None:
        """把用户消息里的表情补成可读文本，顺带救回"纯表情"消息。"""
        self._bot = getattr(event, "bot", None) or self._bot

        chain = getattr(event.message_obj, "message", None) or []
        labels: list[str] = []
        for comp in chain:
            if not isinstance(comp, Face):
                continue
            try:
                face_id = int(comp.id)
            except (TypeError, ValueError):
                continue
            name = FACE_NAMES.get(face_id, "未知表情")
            labels.append(f"[表情:{name}(id={face_id})]")

        if not labels:
            return

        annotation = "".join(labels)
        # 纯表情消息的 message_str 是空的，会被 AstrBot 当成空消息跳过；
        # 这里补上标注，同时也让模型知道用户发了什么表情。
        event.message_str = f"{event.message_str}{annotation}".strip()

    @filter.on_llm_request()
    async def invite_face_turn(
        self,
        event: AstrMessageEvent,
        req: ProviderRequest,
    ) -> None:
        """按频率决定这一轮要不要注入发表情提示词。"""
        if event.get_extra(_INVITE_DECIDED) is None:
            invited = self.policy.invite(event.unified_msg_origin)
            event.set_extra(_INVITE_DECIDED, True)
            event.set_extra(_INVITE_GRANTED, invited)
        if not event.get_extra(_INVITE_GRANTED):
            return
        if event.get_extra(_INJECTED) == id(req):
            return
        # 同一个 event 可能有多次请求（工具循环），每次都要带上这段要求
        event.set_extra(_INJECTED, id(req))

        prompt_text = render_prompt(self.settings, self.id_to_name)
        try:
            # 放进本轮用户消息之后，比系统提示词更"当轮"，模型更容易执行
            req.extra_user_content_parts.append(
                TextPart(text=prompt_text).mark_as_temp(),
            )
            req.system_prompt = strip_prompt(req.system_prompt)
        except AttributeError:
            # 老版本 AstrBot 没有 extra_user_content_parts，退回 system prompt
            req.system_prompt = strip_prompt(req.system_prompt) + wrap_prompt(
                prompt_text,
            )

    @filter.on_decorating_result()
    async def prepare_face_markers(self, event: AstrMessageEvent) -> None:
        """统一标记格式，并按策略决定这一轮能发哪些表情。"""
        result = event.get_result()
        if result is None or not result.chain:
            return

        marker_ids: list[int] = []
        has_marker = False
        for comp in result.chain:
            if not isinstance(comp, Plain):
                continue
            comp.text = normalize_text(comp.text)
            for _start, _end, face_id, token in scan_markers(
                comp.text,
                self.name_to_id,
            ):
                has_marker = True
                if face_id is None:
                    logger.debug("[qq_face] unknown face marker: %s", token)
                    continue
                marker_ids.append(face_id)

        if not has_marker:
            return

        # 表情是消息段，不该被画进文字转图片里
        result.use_t2i_ = False

        invited = bool(event.get_extra(_INVITE_GRANTED))
        allowed = self.policy.select(
            marker_ids,
            event.unified_msg_origin,
            invited=invited,
            extra_allowed=set(self.id_to_name),
        )
        _install_send_translator(event, allowed, self.name_to_id)

        if allowed:
            try:
                await self._save_stats()
            except Exception:  # noqa: BLE001
                pass

    @filter.command("表情")
    async def send_face(self, event: AstrMessageEvent) -> None:
        """手动发一个 QQ 表情：/表情 13 或 /表情 呲牙。"""
        text = "".join(
            comp.text for comp in event.message_obj.message if isinstance(comp, Plain)
        )
        token = text.rsplit("表情", 1)[-1].strip()
        face_id = resolve_face_id(token)
        if face_id is None:
            yield event.plain_result(
                "用法：/表情 13 或 /表情 呲牙。常用：13 呲牙、14 微笑、20 偷笑、12 调皮。",
            )
            return
        if face_id not in set(self.settings["enabled_ids"]):
            yield event.plain_result("这个表情没在设置页里启用。")
            return
        yield event.chain_result([Face(id=face_id)])

    # ------------------------------------------------------------- 内部工具

    def _get_bot(self):
        if self._bot is not None:
            return self._bot
        try:
            for inst in self.context.platform_manager.platform_insts:
                bot = getattr(inst, "bot", None)
                if bot is not None and hasattr(bot, "call_action"):
                    self._bot = bot
                    return bot
        except Exception:  # noqa: BLE001 - 平台管理器在极端情况下可能不可用
            return None
        return None

    def _build_face_maps(
        self,
        settings: dict,
        packs: list[dict],
    ) -> tuple[dict[int, str], dict[str, int]]:
        """构建「id <-> 名字」索引（别名优先）。

        Args:
            settings: 当前设置（读取 aliases）。
            packs: 规范化后的表情包列表。

        Returns:
            (id -> 展示名, 名字 -> id)；展示名优先用别名。
        """
        id_to_name: dict[int, str] = {}
        name_to_id: dict[str, int] = {}
        for pack in packs:
            for face in pack.get("faces") or []:
                try:
                    face_id = int(face["id"])
                except (KeyError, TypeError, ValueError):
                    continue
                name = str(face.get("name") or "").strip().lstrip("/")
                if not name:
                    continue
                id_to_name.setdefault(face_id, name)
                name_to_id.setdefault(name.lower(), face_id)
        for face_id, name in FACE_NAMES.items():
            id_to_name.setdefault(face_id, name)
            name_to_id.setdefault(name.lower(), face_id)
        for alias, face_id in ALIASES.items():
            name_to_id.setdefault(alias, face_id)
        for face_id, alias in alias_map(settings).items():
            id_to_name[face_id] = alias
            name_to_id[alias.lower()] = face_id
        return id_to_name, name_to_id

    def _rebuild_face_index(self, packs: list[dict]) -> None:
        """按当前设置和目录刷新索引。

        Args:
            packs: 规范化后的表情包列表。
        """
        self.id_to_name, self.name_to_id = self._build_face_maps(self.settings, packs)

    def _apply_settings(self, settings: dict, packs: list[dict]) -> dict:
        """落库前的统一处理：同步白名单、策略和索引。

        Args:
            settings: 已 normalize 的设置。
            packs: 当前表情目录。

        Returns:
            同步过 enabled_ids 的设置。
        """
        sync_enabled_ids(settings)
        self.settings = settings
        self.policy.update_settings(settings)
        self._rebuild_face_index(packs)
        return settings

    async def _save_stats(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self._stats_saved_at < 5:
            return
        self._stats_saved_at = now
        await self.put_kv_data("stats", self.policy.stats())

    def _public_settings(self) -> dict:
        scheme = active_scheme(self.settings)
        return {
            "enabled": self.settings["enabled"],
            "mode": self.settings["mode"],
            "probability": self.settings["probability"],
            "max_per_reply": self.settings["max_per_reply"],
            "cooldown_seconds": self.settings["cooldown_seconds"],
            "hourly_limit": self.settings["hourly_limit"],
            "schemes": self.settings["schemes"],
            "active_scheme": self.settings["active_scheme"],
            "active_scheme_name": scheme["name"] if scheme else "",
            "aliases": self.settings["aliases"],
            "prompt_faces_mode": self.settings["prompt_faces_mode"],
            "prompt_faces_count": self.settings["prompt_faces_count"],
            "allow_outside_scheme": self.settings["allow_outside_scheme"],
            "enabled_count": len(self.settings["enabled_ids"]),
            "prompt_template": self.settings["prompt_template"],
            "default_prompt": DEFAULT_PROMPT_TEMPLATE,
            "default_scheme": default_scheme(),
        }

    # ------------------------------------------------------------------ API

    async def api_state(self):
        """视图首屏数据。"""
        bot = self._get_bot()
        packs = await self.catalog.ensure(bot)
        self._rebuild_face_index(packs)
        return json_response(
            {
                "settings": self._public_settings(),
                "packs": packs,
                "stats": self.policy.stats(),
                "prompt_preview": render_prompt(self.settings, self.id_to_name),
                "qq_connected": bot is not None,
                "using_builtin": self.catalog.using_builtin,
                "error": self.catalog.error,
            },
        )

    async def api_catalog(self):
        """强制刷新表情目录。"""
        refresh = str(request.query.get("refresh", "0")).lower() in {"1", "true", "yes"}
        bot = self._get_bot()
        packs = await self.catalog.ensure(bot, refresh=refresh)
        self._rebuild_face_index(packs)
        return json_response(
            {
                "packs": packs,
                "prompt_preview": render_prompt(self.settings, self.id_to_name),
                "qq_connected": bot is not None,
                "using_builtin": self.catalog.using_builtin,
                "error": self.catalog.error,
            },
        )

    async def api_preview(self):
        """按当前表单（未保存）渲染提示词预览。"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("invalid payload", status_code=400)

        merged = {**self.settings, **payload}
        settings = normalize_settings(merged)
        packs = await self.catalog.ensure(self._get_bot())
        id_to_name, _name_to_id = self._build_face_maps(settings, packs)
        return json_response({"prompt": render_prompt(settings, id_to_name)})

    async def api_save_settings(self):
        """保存设置，并按当前方案重算白名单。"""
        payload = await request.json(default={})
        if not isinstance(payload, dict):
            return error_response("invalid payload", status_code=400)

        merged = {**self.settings, **payload}
        settings = normalize_settings(merged)
        packs = await self.catalog.ensure(self._get_bot())
        self._apply_settings(settings, packs)

        try:
            await self.put_kv_data("settings", settings)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[qq_face] failed to persist settings: %s", exc)
            return error_response(f"保存失败: {exc}", status_code=500)

        return json_response(
            {
                "ok": True,
                "settings": self._public_settings(),
            },
        )
