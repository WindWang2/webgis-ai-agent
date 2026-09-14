"""Turn context 集成助手（方向 2 M4，ADR-0180）。

chat.py 的两个 Pi 分支把 ``environment_context`` 的构造委托给
``build_situation_turn_context``：

- 返回 ``None``（kill-switch 关闭 / 编译异常）→ 调用方回落
  ``_build_environment_turn_context(req.map_state)`` 原文本 —— fail-open，
  注入是增值上下文，绝不阻断 turn（DC-2）；
- 正常路径：compile → diff（对上轮持久快照）→ advance（只前进）→
  有界投影文本。turn marker 仍由 ``bind_turn_prompt`` 放在最后（不变）。

本模块不 import ``app.api.routes.chat``（避免环）；fallback 的调用权
在 route 层。
"""
from __future__ import annotations

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


async def build_situation_turn_context(
    session_id: str,
    *,
    turn_id: str = "",
    store: Any = None,
    mapspec_store: Any = None,
) -> Optional[str]:
    """编译本轮 [GIS 情境] 投影；不可用返回 None（调用方回落 legacy env block）。"""
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
        return projection.text
    except Exception:  # noqa: BLE001 — 情境块绝不阻断 turn（fail-open）
        logger.warning(
            "[gis_situation] turn context unavailable for %s", session_id,
            exc_info=True,
        )
        return None
