"""Pi inherit 路径的运行时模型选择策略（GOAL-20261127-01 / WP2）。

一个职责：把**继承默认**的 turn 目标模型从「固定 spawn 默认」升级为
「同输入同输出的健康感知选择」—— 复用路由权威的既有语义（硬资格过滤 +
健康护栏 + 理由码），但候选池被**管理员会话目录**显式限定（授权边界，
D7）。用户显式选择（explicit preference）完全不经此模块 —— 行为与现状
一致：目录重验证 + 能力门，撤销/不可用 typed 拒绝，绝不静默换模型。

纪律：
- **descriptor ≠ 授权**：候选只能来自 ``session_catalog.build_catalog()``
  的可用项；描述符注册表/角色 fallbacks 里的模型即使更健康也不进池。
- **路由语义单一权威**：本模块不复制排序/护栏逻辑，只组装
  ``RouteRequest(candidate_models=...)`` 调 ``ModelRouter.route()``；
  理由码词表与 ChatEngine 路径逐字同源（health_skip/health_deprioritize/
  capability_skip/primary_cooldown/no_capable_fallback）。
- **诚实降级**：路由关闭（env kill-switch）、路由器异常、目录无可用项 →
  回落既有 default item 语义，绝不阻断 turn；决策理由码有界透出。
- **安全点**：本策略只在 turn 边界（prompt 字节发出前）被消费 ——
  set_model 的既有调用点（``apply_session_model``）已经是安全点；
  活动工具/部分输出中不切换。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 运行策略总开关（默认开；关闭 → inherit 恒取目录默认项，零行为变化）。
#: 与 MODEL_ROUTER_ENABLED（路由权威总闸）独立：本闸只关 Pi 侧的消费接线，
#: 不动 ChatEngine 路径。
_PI_STRATEGY_ENV = "PI_MODEL_HEALTH_ROUTING"


def strategy_enabled() -> bool:
    return os.getenv(_PI_STRATEGY_ENV, "1") != "0"


@dataclass(frozen=True)
class StrategyDecision:
    """inherit 目标选择结果（有界、可持久化、可投影）。

    ``item`` 为空表示策略未产出可用目标（调用方回落目录默认项语义）；
    ``reason_codes`` 与 ModelRouter 理由码同词表，含 ``strategy_off`` /
    ``strategy_error`` 宿主侧码。
    """

    item: object = None  # CatalogItem（避免循环 import，运行期总是 CatalogItem）
    reason_codes: Tuple[str, ...] = ()
    fallback_chain: Tuple[str, ...] = ()

    def codes_bounded(self, limit: int = 8) -> List[str]:
        return [str(c)[:64] for c in self.reason_codes[:limit]]

    @property
    def selected_model(self) -> str:
        return getattr(self.item, "item_id", "") or ""

    @property
    def used_fallback(self) -> bool:
        """选中了非默认目录项（inherit 降级发生，set_model 必须应用）。"""
        return bool(self.item is not None and not getattr(self.item, "is_default", True))


def resolve_inherit_target(
    catalog: List,
    *,
    needs_tools: bool = True,
    context_estimate_tokens: Optional[int] = None,
    est_output_tokens: Optional[int] = None,
):
    """继承默认 → 健康感知目录内选择（纯函数式外观，无 IO）。

    返回 :class:`StrategyDecision`。选择语义（与 ModelRouter.route 逐字
    同源，候选池=目录可用项）：
    1. 硬资格：needs_tools 且 tool_calling=False → capability_skip 留痕跳过；
       已知 context_window 不足以容纳投影 → capability_skip:context 跳过；
    2. 健康护栏：冷却中 → health_skip 留痕跳过；近期限流/能力不匹配 →
       排健康候选之后（降级链）；
    3. 全链不可用 → 目录默认项 + no_capable_fallback（诚实单次尝试，
       绝不无声跨授权降级）。
    策略关闭/路由器异常 → 默认项 + 对应宿主侧理由码（既有行为）。
    """
    default = None
    for item in catalog:
        if getattr(item, "is_default", False):
            default = item
            break
    if default is None:
        return StrategyDecision(reason_codes=("strategy_no_default",))
    if not strategy_enabled():
        return StrategyDecision(item=default, reason_codes=("strategy_off",))

    candidates: List[str] = []
    items_by_id = {}
    for item in catalog:
        mid = getattr(item, "item_id", "")
        if not mid or mid in items_by_id:
            continue
        if not getattr(item, "available", False):
            continue
        items_by_id[mid] = item
        candidates.append(mid)

    try:
        from app.services.chat.model_runtime.routing import RouteRequest, get_model_router

        req = RouteRequest(
            role="execution",
            require_tools=needs_tools,
            est_context_tokens=context_estimate_tokens,
            candidate_models=tuple(candidates),
        )
        decision = get_model_router().route(req)
    except Exception:  # noqa: BLE001 — 路由是增强，失败回落默认项（既有行为）
        logger.debug("[turn-strategy] route failed; falling back to default item", exc_info=True)
        return StrategyDecision(item=default, reason_codes=("strategy_error",))

    selected_id = decision.model_id
    item = items_by_id.get(selected_id)
    if item is None:
        # 路由器回落的主模型（primary_configured 路径）不在可用目录 ——
        # 理论不可达（默认项恒在可用目录），如实记录并回落，不越权。
        logger.warning(
            "[turn-strategy] router selected %r outside available catalog; "
            "falling back to default item", selected_id,
        )
        return StrategyDecision(
            item=default,
            reason_codes=("strategy_out_of_catalog",) + tuple(decision.reason_codes[:6]),
        )
    codes = tuple(decision.reason_codes[:8])
    return StrategyDecision(
        item=item,
        reason_codes=codes,
        fallback_chain=tuple(str(m) for m in decision.fallback_chain[:8]),
    )


__all__ = [
    "StrategyDecision",
    "resolve_inherit_target",
    "strategy_enabled",
]
