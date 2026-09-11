"""FinalDisplayConfirmation —— 最终显示确认钩子（V7 ADR-0134 D6）。

V6/V6- 基线：task_complete 的「最终显示」语义 = 后端 finalizer 的裁决
（READY + final verified），渲染是否真的被用户看见没有确认面 —— 前端
observation POST 是证据回流，但不是「确认」语义。

V7 契约（**默认 auto-confirm，零行为变化；架构上留 human confirmation
seam**）：

- ``FinalDisplayMode``：``auto``（默认 —— 观察证据 + 后端裁决即确认，
  与 V6 行为逐位一致）/ ``required``（等待显式 ack —— 前端/人在环写入
  map_state``_final_display_ack``；未 ack → display_confirmed=False）。
- ack 载荷带 ``render_seq``（观察代次）—— 陈旧 ack（老于当前裁决依据
  的观察代次）不算确认（与 finalizer 观察代次门同纪律）。
- 本模块**不发起**任何前端交互 —— 只提供后端判定 seam；未来 human
  confirmation 端点写 ack 即接入，无需改 finalizer。
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: map_state 单键（additive；白名单纪律归 durable_context 管辖）。
FINAL_DISPLAY_ACK_KEY = "_final_display_ack"

MODE_AUTO = "auto"
MODE_REQUIRED = "required"


def display_mode() -> str:
    """当前确认模式（env 门；未知值按 auto —— 失败开路只在非鉴权面）。"""
    raw = (os.getenv("GIS_FINAL_DISPLAY_CONFIRM") or MODE_AUTO).strip().lower()
    return MODE_REQUIRED if raw == MODE_REQUIRED else MODE_AUTO


async def is_display_confirmed(
    session_id: str,
    *,
    render_seq: int = 0,
) -> bool:
    """最终显示是否已确认（auto → True；required → 比对 ack 代次）。"""
    if not session_id:
        return False
    if display_mode() != MODE_REQUIRED:
        return True
    try:
        from app.services.session_data import session_data_manager

        state = await session_data_manager.get_map_state(session_id)
        ack = (state or {}).get(FINAL_DISPLAY_ACK_KEY)
        if not isinstance(ack, dict):
            return False
        return int(ack.get("render_seq") or 0) >= int(render_seq)
    except Exception:  # noqa: BLE001 — 判定失败按未确认（required 模式下诚实）
        logger.debug("[DisplayConfirm] ack read failed session=%s", session_id,
                     exc_info=True)
        return False


async def record_display_ack(
    session_id: str,
    *,
    render_seq: int,
    source: str = "frontend",
) -> Optional[Dict[str, Any]]:
    """写显式确认（human confirmation 通道的写半边；幂等 —— 同代次覆盖）。"""
    if not session_id:
        return None
    try:
        from app.services.session_data import session_data_manager

        ack = {
            "render_seq": int(render_seq),
            "source": str(source)[:24],
        }
        await session_data_manager.set_map_state(
            session_id, FINAL_DISPLAY_ACK_KEY, ack)
        return ack
    except Exception:  # noqa: BLE001 — ack 写失败不阻断（下次重写）
        logger.debug("[DisplayConfirm] ack write failed session=%s", session_id,
                     exc_info=True)
        return None


__all__ = [
    "FINAL_DISPLAY_ACK_KEY",
    "MODE_AUTO",
    "MODE_REQUIRED",
    "display_mode",
    "is_display_confirmed",
    "record_display_ack",
]
