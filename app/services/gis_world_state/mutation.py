"""GISMutation 门面 —— 所有 MapSpec mutation 的统一入口（GISWorldState C2）。

单一职责链（底层仍是 MapSpecLifecycleEngine，本门面绝不直接写状态）：

    identity/CAS/事务 ──> engine.apply_mutation（不变）
    origin 策略     ──> UserPresentationGuard（服务端强制 user interaction wins）
    provenance      ──> 成功后记录决策链

UserPresentationGuard（本轮落地的唯一硬策略）：
origin="agent" 的 PatchLayerPresentationIntent 若要把某层 presentation
**反转**为与"用户最后决策值"相反的值 → 拒绝（is_error=True + correction_hint）。
重放用户已有同值决策 → 允许（幂等）。层无用户决策记录 → 允许（现状语义，
如 finalize 隐藏中间层）。这是 G6（用户隐藏 → 对话不覆盖）的服务端不变量。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    MapSpecResult,
    MutationIntent,
    MutationOrigin,
    PatchLayerPresentationIntent,
)
from app.services.gis_world_state.provenance import (
    ProvenanceEntry,
    append_provenance,
    get_provenance,
    last_presentation_owner,
)

logger = logging.getLogger(__name__)


@dataclass
class UserPresentationGuardError(Exception):
    """agent 试图反转用户最后 presentation 决策（user wins 违例）。"""

    layer_id: str
    user_value: Any
    agent_value: Any

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return (
            f"layer {self.layer_id}: presentation last decided by user as "
            f"{self.user_value!r}; agent reversal to {self.agent_value!r} refused"
        )


_engine: Optional[MapSpecLifecycleEngine] = None


def _get_engine() -> MapSpecLifecycleEngine:
    global _engine
    if _engine is None:
        _engine = MapSpecLifecycleEngine()
    return _engine


async def _check_user_presentation_guard(
    session_id: str,
    intent: MutationIntent,
    origin: MutationOrigin,
) -> Optional[UserPresentationGuardError]:
    """user-wins 守卫：agent 不得反转用户最后的显隐/透明度决策。"""
    if origin != "agent" or not isinstance(intent, PatchLayerPresentationIntent):
        return None
    if intent.visible is None:
        # opacity 反转难以判定"意图对抗"（连续值）；本轮只硬守卫 visible。
        return None
    entries = await get_provenance(session_id)
    last = last_presentation_owner(entries, intent.layer_id)
    if last is None or last.get("origin") != "user":
        return None
    user_visible = last.get("detail", {}).get("visible")
    if user_visible is None or bool(user_visible) == bool(intent.visible):
        # 用户没有显式 visible 决策，或 agent 与用户决策同值（幂等重放）
        return None
    return UserPresentationGuardError(
        layer_id=intent.layer_id,
        user_value=user_visible,
        agent_value=intent.visible,
    )


def _intent_target(intent: MutationIntent) -> Optional[str]:
    for attr in ("layer_id", "component_id", "source_id"):
        value = getattr(intent, attr, None)
        if isinstance(value, str):
            return value
    return None


def _intent_summary(intent: MutationIntent) -> str:
    if isinstance(intent, PatchLayerPresentationIntent):
        parts = []
        if intent.visible is not None:
            parts.append(f"visible={intent.visible}")
        if intent.opacity is not None:
            parts.append(f"opacity={intent.opacity}")
        return " ".join(parts)
    return type(intent).__name__


async def apply_gis_mutation(
    session_id: str,
    intent: MutationIntent,
    *,
    origin: MutationOrigin = "agent",
    actor: str = "unknown",
    expected_revision: Optional[int] = None,
    engine: Optional[MapSpecLifecycleEngine] = None,
) -> MapSpecResult:
    """统一 mutation 入口：守卫 → engine（锁/CAS/COW/事务）→ provenance。

    语义与 engine.apply_mutation 完全一致（透传），额外提供：
    - origin="user" 的 CAS 仍由 engine 强制（expected_revision 必填）；
    - user-wins 守卫（见模块 docstring）——违例返回 is_error 结果而非抛出，
      与工具错误契约（{"error": ...} + correction_hint）对齐；
    - 成功后追加 provenance（best-effort）。
    """
    guard_error = await _check_user_presentation_guard(session_id, intent, origin)
    if guard_error is not None:
        logger.info(
            "[gis_world_state] user-presentation guard refused agent mutation: %s", guard_error
        )
        return MapSpecResult(
            is_error=True,
            origin=origin,
            error_msg=(
                f"图层 {guard_error.layer_id} 的显示状态由用户手动设定"
                f"（visible={guard_error.user_value}），Agent 不覆盖用户显式操作。"
            ),
            correction_hint=(
                "保留该层用户设定的显示状态继续成图；如确需反转，请先向用户说明并"
                "由用户操作（图层面板开关），或在 MapSpec 层使用非 presentation 途径"
                "重建图层。"
            ),
        )

    active_engine = engine or _get_engine()
    result = await active_engine.apply_mutation(
        session_id, intent, origin=origin, expected_revision=expected_revision
    )
    if not result.is_error and not result.superseded:
        detail: dict[str, Any] = {}
        if isinstance(intent, PatchLayerPresentationIntent):
            if intent.visible is not None:
                detail["visible"] = bool(intent.visible)
            if intent.opacity is not None:
                detail["opacity"] = float(intent.opacity)
        await append_provenance(
            session_id,
            ProvenanceEntry(
                seq=int(result.mutation_revision or 0),
                ts=datetime.now(timezone.utc).isoformat(),
                origin=str(origin),
                actor=actor,
                kind=type(intent).__name__,
                target=_intent_target(intent),
                revision=int(result.mutation_revision or 0),
                summary=_intent_summary(intent),
                detail=detail,
            ),
        )
    return result

