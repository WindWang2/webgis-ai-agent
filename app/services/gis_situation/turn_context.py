"""Turn context 集成助手（方向 2 M4，ADR-0180；主动记忆切片见 ADR-0190）。

chat.py 的两个 Pi 分支把 ``environment_context`` 的构造委托给
``build_situation_turn_context``：

- 返回 ``None``（kill-switch 关闭 / 编译异常）→ 调用方回落
  ``_build_environment_turn_context(req.map_state)`` 原文本 —— fail-open，
  注入是增值上下文，绝不阻断 turn（DC-2）；
- 正常路径：compile → diff（对上轮持久快照）→ advance（只前进）→
  有界投影文本；随后 best-effort 追加 ``[GIS_MEMORY_PROACTIVE]`` 主动
  记忆切片（ADR-0190）——切片失败只丢切片，situation 投影原样返回。
  turn marker 仍由 ``bind_turn_prompt`` 放在最后（不变）。

本模块不 import ``app.api.routes.chat``（避免环）；fallback 的调用权
在 route 层。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from app.services.gis_situation.compiler import (
    compile_situation,
    situation_enabled,
)
from app.services.gis_situation.diff import (
    advance_snapshot,
    diff_situation,
    load_snapshot,
)
from app.services.gis_situation.projection import render_situation_for_context

logger = logging.getLogger(__name__)


def _frozen_clock(session_id: str) -> str:
    """session 冻结时钟（#388 prefix-cache 纪律的同一实现）。"""
    from app.services.chat.context_builder import _env_timestamp

    return _env_timestamp(session_id)


async def build_proactive_memory_slice(
    session_id: str,
    *,
    query_text: str = "",
) -> str:
    """主动空间记忆切片（ADR-0190）：身份烙印 → 热索引唤醒 → 有界卡片块。

    fail-closed：无 session / 无租户烙印（首轮 harvest 之前）→ 空串；
    fail-open：唤醒链路任何异常 → 空串（记忆绝不阻断 turn）。
    """
    if not session_id:
        return ""
    try:
        from app.services.gis_memory.proactive_retriever import (
            default_proactive_retriever,
            render_cards,
        )
        from app.services.gis_memory.queries import read_memory_identity

        org_id, user_id = await read_memory_identity(session_id)
        if not org_id:
            return ""
        result = await asyncio.to_thread(
            default_proactive_retriever.awake,
            query_text or "",
            org_id=org_id,
            user_id=user_id or None,
            session_id=session_id,
        )
        return render_cards(result.cards)
    except Exception:  # noqa: BLE001 — 切片是增值上下文，绝不阻断 turn
        logger.debug(
            "[gis_situation] proactive memory slice unavailable for %s",
            session_id, exc_info=True,
        )
        return ""


async def build_situation_turn_context(
    session_id: str,
    *,
    turn_id: str = "",
    store: Any = None,
    mapspec_store: Any = None,
    query_text: str = "",
) -> Optional[str]:
    """编译本轮 [GIS 情境] 投影并追加主动记忆切片；situation 不可用返回
    None（调用方回落 legacy env block）。"""
    if not situation_enabled() or not session_id:
        return None
    try:
        situation = await compile_situation(
            session_id,
            turn_id=turn_id,
            compiled_at=_frozen_clock(session_id),
            store=store,
            mapspec_store=mapspec_store,
        )
        previous = await load_snapshot(session_id, store=store)
        delta = diff_situation(previous, situation)
        advanced = await advance_snapshot(session_id, situation, store=store)
        if not advanced:
            # 迟到/倒退编译：diff 不可信（regressed），按无增量投影本轮事实。
            delta = diff_situation(None, situation)
        projection = render_situation_for_context(situation, delta=delta)
        text = projection.text
    except Exception:  # noqa: BLE001 — 情境块绝不阻断 turn（fail-open）
        logger.warning(
            "[gis_situation] turn context unavailable for %s", session_id,
            exc_info=True,
        )
        return None
    # 主动记忆切片（ADR-0190）：situation 成功后才追加；切片自身 fail-open。
    try:
        slice_text = await build_proactive_memory_slice(
            session_id, query_text=query_text
        )
        if slice_text:
            text = f"{text}\n{slice_text}"
    except Exception:  # noqa: BLE001 — 双保险；正常路径不会到这里
        logger.debug(
            "[gis_situation] proactive slice append failed for %s",
            session_id, exc_info=True,
        )
    return text
