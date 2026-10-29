"""QQ 表情包目录：优先从 SnowLuma 拉取，拿不到时退回内置经典表。"""

from __future__ import annotations

import time

from astrbot.api import logger

from .face_utils import FACE_NAMES

BUILTIN_PACK_NAME = "经典"


def builtin_packs() -> list[dict]:
    """内置的经典小黄脸，作为没有 QQ 连接时的兜底。"""
    return [
        {
            "pack_name": BUILTIN_PACK_NAME,
            "builtin": True,
            "faces": [
                {"id": face_id, "name": name, "super": False, "url": None}
                for face_id, name in sorted(FACE_NAMES.items())
            ],
        },
    ]


class FaceCatalog:
    """缓存 SnowLuma 的 fetch_sys_faces 结果。"""

    def __init__(self, ttl: int = 600) -> None:
        self.ttl = ttl
        self.packs: list[dict] = builtin_packs()
        self.using_builtin = True
        self.fetched_at = 0.0
        self.error: str | None = None

    def _stale(self) -> bool:
        return (time.time() - self.fetched_at) > self.ttl

    async def ensure(self, bot, refresh: bool = False) -> list[dict]:
        """确保目录可用。

        Args:
            bot: aiocqhttp 的 bot 实例，可能为 None。
            refresh: 是否强制忽略缓存。

        Returns:
            规范化后的表情包列表；失败时是内置包。
        """
        if bot is None:
            self.using_builtin = True
            self.error = "未连接 QQ"
            return self.packs if self.packs else builtin_packs()
        if not refresh and not self._stale() and not self.using_builtin:
            return self.packs

        try:
            data = await bot.call_action("fetch_sys_faces", refresh=refresh)
        except Exception as exc:  # noqa: BLE001 - 协议端可能不支持该 action
            logger.warning("[qq_face] fetch_sys_faces failed: %s", exc)
            self.error = str(exc)
            if not self.packs:
                self.packs = builtin_packs()
            self.using_builtin = True
            return self.packs

        packs = self._normalize(data)
        if not packs:
            self.error = "目录为空"
            if not self.packs:
                self.packs = builtin_packs()
            self.using_builtin = True
            return self.packs

        self.packs = packs
        self.using_builtin = False
        self.error = None
        self.fetched_at = time.time()
        return self.packs

    @staticmethod
    def _normalize(data) -> list[dict]:
        if not isinstance(data, dict):
            return []
        raw_packs = data.get("packs")
        if not isinstance(raw_packs, list):
            return []

        packs: list[dict] = []
        for raw_pack in raw_packs:
            if not isinstance(raw_pack, dict):
                continue
            pack_name = str(raw_pack.get("pack_name") or "").strip()
            if not pack_name:
                continue
            faces = []
            seen = set()
            for raw_face in raw_pack.get("emojis") or []:
                if not isinstance(raw_face, dict):
                    continue
                q_sid = str(raw_face.get("q_sid") or "").strip()
                # 部分目录项是 Unicode 表情（q_sid 不是数字），无法按 face id 发送
                if not q_sid.isdigit():
                    continue
                face_id = int(q_sid)
                if face_id in seen:
                    continue
                seen.add(face_id)
                name = str(raw_face.get("q_des") or "").strip().lstrip("/")
                url = raw_face.get("url")
                faces.append(
                    {
                        "id": face_id,
                        "name": name or f"表情{face_id}",
                        "super": bool(raw_face.get("is_super")),
                        "url": url if isinstance(url, str) and url.startswith("http") else None,
                    },
                )
            if faces:
                packs.append(
                    {"pack_name": pack_name, "builtin": False, "faces": faces},
                )
        return packs
