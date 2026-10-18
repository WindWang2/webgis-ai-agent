"""Pi bridge dispatch 前置门禁：turn SurfaceReceipt 对照（Goal 04, ADR-0224）。

从 ``app.agent_pi_bridge._dispatch_tool_bound`` 抽出的 ADR-0224 校验面
（god-module 棘轮 #1377 收敛：bridge 只保留薄调用点，语义在此单点维护）：

- fingerprint echo —— 扩展从 dump v3 的 per-tool ``spec.fingerprint``
  原样回传；后端对照 turn SurfaceReceipt 判 stale（registry 漂移 /
  跨 turn 重放）。echo 自愿携带：缺席 = master 兼容行为（不校验）；
- opt-in 面成员硬校验（bare-name 直调不在 turn 选中面 → typed 拒绝；
  proxy 通道不受限）；
- 只对 route 验证过的回调（``verifiedTurnId`` 在场）生效；直连调用
  保持旧行为。receipt 缺席（动态面关 / 弃成 / 旧 turn）→ 放行；
- echo 值本身不是权限 —— 权限仍是 bridge secret + turn token +
  live-turn；面级（surface）指纹是 turn receipt 私有值，扩展无从合法
  获知（dump 超集指纹 ≠ turn 选中面指纹），故协议中不存在
  ``surfaceFingerprint`` 字段（review P2-2：防误接成永假阳性）。

故障语义：校验面自身故障绝不阻断 dispatch（外层 except 只 debug 记录）；
stale 指标记录失败同样不阻断。命中 mismatch 返回 typed ``surface_stale``
``PiToolResponse``（模型可读 guidance + retryable，非 HTTP 错误）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from app.services.chat.pi_surface_receipt import (
    enforce_callback_scope,
    surface_receipt_store,
    verify_callback_fingerprints,
)

if TYPE_CHECKING:  # pragma: no cover — 仅类型标注；运行时延迟导入防环
    from app.agent_pi_bridge import PiToolRequest, PiToolResponse

logger = logging.getLogger(__name__)


def evict_turn_receipts(turn_sid: str) -> None:
    """Turn 终态时清理该 session 的 SurfaceReceipt（对照真相随 turn 回收）。

    清理失败不阻断 turn 收尾（只 debug 记录）——receipt 残留只会让后续
    echo 校验对新 turn 天然缺席（放行路径），不会误拒。
    """
    try:
        surface_receipt_store.evict_session(turn_sid)
    except Exception:  # noqa: BLE001 — receipt 清理失败不阻断 turn 收尾
        logger.debug(
            "[PiBridge] surface receipt evict failed turn=%s", turn_sid, exc_info=True
        )


def stale_surface_gate(
    request: "PiToolRequest",
    session_id: str,
    tool_name: str,
    proxied: bool,
) -> "Optional[PiToolResponse]":
    """对照 turn SurfaceReceipt 校验回调；返回 stale 拒绝响应或 None。

    None = 放行（echo 匹配 / echo 缺席 / receipt 缺席 / 直连调用 /
    校验面故障）。非 None = typed ``surface_stale`` 拒绝（调用方直接
    短路返回）。
    """
    if not request.verifiedTurnId:
        return None
    try:
        mismatch = verify_callback_fingerprints(
            session_id=session_id,
            turn_id=str(request.verifiedTurnId),
            resolved_tool=tool_name,
            tool_fingerprint=str(getattr(request, "toolFingerprint", "") or ""),
        )
        if mismatch is None and not proxied:
            mismatch = enforce_callback_scope(
                session_id=session_id,
                turn_id=str(request.verifiedTurnId),
                resolved_tool=tool_name,
            )
        if mismatch is None:
            return None
        try:
            from app.services.chat.pi_surface_metrics import record_invalid_tool_name

            record_invalid_tool_name(tool_name, reason=mismatch)
        except Exception:  # noqa: BLE001 — 指标绝不阻断
            logger.debug(
                "[PiBridge] stale-surface metric record failed tool=%s",
                tool_name,
                exc_info=True,
            )
        from app.agent_pi_bridge import PiToolResponse

        return PiToolResponse(
            toolCallId=request.toolCallId,
            content=[{
                "type": "text",
                "text": (
                    f"Tool surface for this turn is stale ({mismatch}): "
                    "the registered tool contract changed or the call "
                    "belongs to a superseded surface. Re-discover via "
                    "list_available_tools, then call webgis_execute."
                ),
            }],
            details={
                "error": "surface_stale",
                "code": mismatch,
                "tool": tool_name,
                "retryable": True,
            },
            isError=True,
        )
    except Exception:  # noqa: BLE001 — 校验面故障绝不阻断 dispatch
        logger.debug("[PiBridge] surface receipt check failed", exc_info=True)
        return None
