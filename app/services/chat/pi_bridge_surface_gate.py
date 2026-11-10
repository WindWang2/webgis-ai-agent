"""Pi bridge dispatch 前置门禁：turn SurfaceReceipt 对照（Goal 04, ADR-0224；
发现 grant 扩展：ADR-0288 / G12-W05）。

从 ``app.agent_pi_bridge._dispatch_tool_bound`` 抽出的 ADR-0224 校验面
（god-module 棘轮 #1377 收敛：bridge 只保留薄调用点，语义在此单点维护）：

- fingerprint echo —— 扩展从 dump v3 的 per-tool ``spec.fingerprint``
  原样回传；后端对照 turn SurfaceReceipt 判 stale（registry 漂移 /
  跨 turn 重放）。echo 自愿携带：缺席 = master 兼容行为（不校验）；
- bare-name 面成员硬校验（``PI_SURFACE_SCOPE_ENFORCE``，ADR-0288 起默认
  ON）：直调不在 turn selected 面时，先走 **discovery grant** —— 工具
  必须是 spawn 注册超集成员（``registered_surface_names``：model_visible ∧
  tier<3 ∧ 非 DESTRUCTIVE ∧ schema 在预算内），且（echo 在场时）与从
  registry 现编译的 spec 指纹一致；grant 有界（16/turn）、幂等、随 turn
  回收。grant 绝不是 LLM 自授权：铸造点在宿主 gate，授权集从未因发现
  扩大（等价于 proxy 通道的权限面）。grant 失败 → typed
  ``SURFACE_SCOPE_VIOLATION`` 拒绝（proxy 通道不受限）；
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
    record_discovered_tool,
    surface_receipt_store,
    verify_callback_fingerprints,
)

if TYPE_CHECKING:  # pragma: no cover — 仅类型标注；运行时延迟导入防环
    from app.agent_pi_bridge import PiToolRequest, PiToolResponse

logger = logging.getLogger(__name__)


def _stale_surface_recovery(tool_name: str, mismatch: str) -> str:
    """Recovery text that matches the extension wrap rule.

    Any name registered in the spawn dump must be called directly. The
    extension rejects wrapping it. webgis_execute is only for names that
    are not in the tool list.
    """
    return (
        f"Tool surface for this turn is stale ({mismatch}): "
        "the registered tool contract changed or the call belongs to a "
        "superseded surface. "
        f"Call {tool_name} directly if it is in your tool list. "
        "Do not wrap a registered name in webgis_execute. "
        "Use list_available_tools, then webgis_execute, only for a name "
        "that is not registered."
    )


def evict_turn_receipts(turn_sid: str) -> None:
    """Turn 终态时清理该 session 的 SurfaceReceipt 与发现 grant（对照真相随 turn 回收）。

    清理失败不阻断 turn 收尾（只 debug 记录）——receipt/grant 残留只会让后续
    echo 校验对新 turn 天然缺席（放行路径），不会误拒。
    """
    try:
        surface_receipt_store.evict_session(turn_sid)
    except Exception:  # noqa: BLE001 — receipt 清理失败不阻断 turn 收尾
        logger.debug(
            "[PiBridge] surface receipt evict failed turn=%s", turn_sid, exc_info=True
        )
    try:
        from app.services.chat.pi_surface_receipt import discovered_store

        discovered_store.evict_session(turn_sid)
    except Exception:  # noqa: BLE001 — grant 清理失败不阻断 turn 收尾
        logger.debug(
            "[PiBridge] discovery grant evict failed turn=%s", turn_sid, exc_info=True
        )


def _try_discovery_grant(
    registry: Optional[object],
    session_id: str,
    turn_id: str,
    tool_name: str,
    echoed_fingerprint: str,
) -> bool:
    """ADR-0288/W05：注册超集内的发现调用 → 铸造 turn 内 grant。

    条件（全部满足才 grant）：
    - 工具在 ``registered_surface_names``（spawn 注册超集，与扩展注册现实
      一致 —— projector 的 tier/side-effect/可见性/预算裁决已经发生过）；
    - echo 在场时与 **registry 现编译**的 PiToolSpecV1 指纹一致（防跨代漂移：
      receipt 里的指纹是 turn 起始快照，grant 用现编译 = 追真值）；
    - 每 turn grant 未超上限（``_DISCOVERY_GRANT_MAX``）。

    返回 True = grant 成功（放行）；False = 不满足（调用方按 scope violation
    拒绝）。registry 缺席 → False（fail-closed：无法核实注册面就拒绝）。
    """
    if registry is None:
        return False
    try:
        from app.services.chat.pi_native_surface import registered_surface_names

        if tool_name not in set(registered_surface_names(registry)):
            return False
        if echoed_fingerprint:
            from app.services.chat.pi_tool_spec import (
                compile_tool_spec,
                current_registry_generation,
            )

            spec = compile_tool_spec(
                registry, tool_name,
                registry_generation=current_registry_generation(),
            )
            if spec is None or spec.fingerprint() != echoed_fingerprint:
                logger.info(
                    "[PiBridge] discovery grant fingerprint mismatch tool=%s "
                    "turn=%s (echo != freshly compiled spec)", tool_name, turn_id,
                )
                return False
        granted = record_discovered_tool(
            session_id, turn_id, tool_name, echoed_fingerprint
        )
        if granted:
            logger.info(
                "[PiBridge] discovery grant tool=%s turn=%s session=%s",
                tool_name, turn_id, session_id,
            )
        return granted
    except Exception:  # noqa: BLE001 — grant 面故障不能放行未核实调用
        logger.debug(
            "[PiBridge] discovery grant check failed tool=%s", tool_name, exc_info=True
        )
        return False


def _echo_matches_fresh_spec(registry: Optional[object], tool_name: str, echo: str) -> bool:
    """echo 与 registry **现编译**的 PiToolSpecV1 指纹对拍（ADR-0288）。

    用于已 grant/已发现工具的后续调用：grant 时记录的指纹是当时快照，
    后续 echo 仍需对拍现编译 spec —— registry 漂移（schema 变更/撤权后重注
    册）后旧 echo 必须失效。spec 无法编译（超预算/schema 缺席）→ True
    （无可对照值，与 echo 缺席同语义，不额外拒绝）。
    """
    if not echo or registry is None:
        return True
    try:
        from app.services.chat.pi_tool_spec import (
            compile_tool_spec,
            current_registry_generation,
        )

        spec = compile_tool_spec(
            registry, tool_name,
            registry_generation=current_registry_generation(),
        )
        if spec is None:
            return True
        return spec.fingerprint() == echo
    except Exception:  # noqa: BLE001 — 对拍面故障不额外拒绝（gate 整体 fail-open）
        logger.debug(
            "[PiBridge] fresh-spec echo verification unavailable tool=%s", tool_name,
            exc_info=True,
        )
        return True


def stale_surface_gate(
    request: "PiToolRequest",
    session_id: str,
    tool_name: str,
    proxied: bool,
    registry: Optional[object] = None,
) -> "Optional[PiToolResponse]":
    """对照 turn SurfaceReceipt 校验回调；返回 stale 拒绝响应或 None。

    None = 放行（echo 匹配 / echo 缺席 / receipt 缺席 / 直连调用 /
    发现 grant 成功 / 校验面故障）。非 None = typed ``surface_stale`` 拒绝
    （调用方直接短路返回）。
    """
    if not request.verifiedTurnId:
        return None
    try:
        echo = str(getattr(request, "toolFingerprint", "") or "")
        mismatch = verify_callback_fingerprints(
            session_id=session_id,
            turn_id=str(request.verifiedTurnId),
            resolved_tool=tool_name,
            tool_fingerprint=echo,
        )
        if mismatch is None and not proxied:
            scope_mismatch = enforce_callback_scope(
                session_id=session_id,
                turn_id=str(request.verifiedTurnId),
                resolved_tool=tool_name,
            )
            if scope_mismatch is not None:
                # ADR-0288：注册超集内 + 指纹校验通过 → turn 内 discovery
                # grant（发现的中途声明合法化；权限面从不扩大）。
                if _try_discovery_grant(
                    registry, session_id, str(request.verifiedTurnId), tool_name, echo,
                ):
                    scope_mismatch = None
            if scope_mismatch is None:
                # 已发现的直调（不在 turn selected 面）：后续调用的 echo 对拍
                # **现编译** spec —— 撤权/schema 变更后旧 echo 失效（A6）。
                # 与 grant 同族：kill-switch（=0）时整体跳过，回到 G12 前行为。
                from app.services.chat.pi_surface_receipt import (
                    surface_scope_enforce_enabled,
                )

                if surface_scope_enforce_enabled():
                    receipt = surface_receipt_store.lookup(
                        session_id, str(request.verifiedTurnId)
                    )
                    not_selected = receipt is None or tool_name not in receipt.selected
                    if not_selected and echo and not _echo_matches_fresh_spec(
                        registry, tool_name, echo
                    ):
                        mismatch = "SURFACE_FINGERPRINT_MISMATCH"
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
                "text": _stale_surface_recovery(tool_name, mismatch),
            }],
            details={
                "error": "surface_stale",
                "code": mismatch,
                "tool": tool_name,
                "retryable": True,
            },
            isError=True,
        )
    except Exception:  # noqa: BLE001 — a failed scope check cannot authorize dispatch
        logger.warning("[PiBridge] surface receipt check failed; callback rejected", exc_info=True)
        from app.services.chat.pi_surface_metrics import record_invalid_tool_name
        from app.agent_pi_bridge import PiToolResponse

        record_invalid_tool_name(tool_name, reason="surface_check_unavailable")
        return PiToolResponse(toolCallId=request.toolCallId,
            content=[{"type": "text", "text": "Tool surface verification is unavailable. Retry discovery before execution."}],
            details={"error": "surface_check_unavailable", "retryable": True}, isError=True)
