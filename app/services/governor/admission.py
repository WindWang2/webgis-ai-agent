"""准入控制策略（R3，ADR-0182 D5）。

五值决策：``accept / accept_with_limits / degrade / defer / reject``。
确定性有序规则链（同输入必同决策）：

1. **硬预算违规**（非 provisional）→ reject + 可行动建议（R16 记
   exhaustion-avoided）；
2. **全局内存压力**（在飞 adjudged memory + 新请求 > 配置水位）→
   轻请求 defer、重请求 degrade/reject；
3. **provisional 预算违规** → 事实留痕 + 降级建议（校准前不硬拒，
   对齐 ADR-0159 provisional 纪律）；
4. **provider 熔断 open**（只读 HealthView）→ degrade 建议
   （local_fallback），无 estimate 的纯远端请求 → reject；
5. **SLO breach 积压**（观测面 → 准入的干净升级，G12）→
   accept 收紧为 accept_with_limits（限幅墙钟/要素数）；
6. 其余 → accept。

``defer`` 语义（spec §8 红线）：只表示**进程内排队/串行化** ——
queue_hint 指向背压通道，仍在当前 Harness 生命周期内，绝不承诺后台
异步执行。

模式（D5）：``enforce``（默认）= 决策即裁决；``observe`` = 决策照常
计算、裁决恒放行（rollback kill-switch）。governor facade 负责消费
模式；本层永远只产出诚实决策。**fail-open**：本模块任何异常由 facade
兜底放行。
"""
from __future__ import annotations

import logging
from typing import Dict, List, Optional

from app.services.governor.contract import (
    AdmissionDecision,
    Dimension,
    ResourceClass,
    ResourceDecision,
    ResourceDemand,
)
from app.services.governor.degradation import DegradePlan, plan_degradation
from app.services.governor.session_budget import SessionBudgetLedger

logger = logging.getLogger(__name__)


class AdmissionPolicy:
    """确定性准入策略（无 IO；全部输入显式注入 → 可测可回放）。"""

    def __init__(
        self,
        ledger: SessionBudgetLedger,
        *,
        global_memory_pressure_bytes: float = 6 * 1024**3,
        slo_breach_soft_limit: int = 20,
        defer_when_channel_waiters: int = 8,
    ) -> None:
        self._ledger = ledger
        self._global_memory_pressure_bytes = global_memory_pressure_bytes
        self._slo_breach_soft_limit = slo_breach_soft_limit
        self._defer_when_channel_waiters = defer_when_channel_waiters

    # ── 决策 ─────────────────────────────────────────────────────────

    def decide(
        self,
        demand: ResourceDemand,
        *,
        channel_state: Optional[Dict[str, tuple]] = None,
        global_live_memory: float = 0.0,
        open_providers: Optional[List[str]] = None,
        slo_breach_total: int = 0,
        remote_only: bool = False,
    ) -> ResourceDecision:
        estimate = demand.estimate
        adjudged = {
            d: estimate.adjudged(d) for d in Dimension
            if estimate.dim(d).is_meaningful()
        }
        # unknown 维按保守地板计费（unknown≠0 的落地），unavailable 不参与
        adjudged.update({
            d: estimate.adjudged(d) for d in Dimension
            if estimate.dim(d).certainty.value == "unknown"
        })
        violations = self._ledger.projection_violations(demand, adjudged)
        reasons: List[str] = []
        suggestions: List[str] = []
        limits: Dict[Dimension, float] = {}
        degrade_plan = DegradePlan()

        # 1. 硬预算违规（provisional=False 的预算）
        hard = [v for v in violations if not v.provisional]
        soft = [v for v in violations if v.provisional]
        if hard:
            for v in hard:
                reasons.append("hard_" + v.reason_code())
            suggestions.extend([
                "narrow the request extent / lower limits",
                "free in-flight work in this session (cancel or wait)",
                "raise the scope budget explicitly for approved heavy paths",
            ])
            return self._decision(
                AdmissionDecision.REJECT, demand, reasons, limits,
                degrade_plan, suggestions,
            )

        # 2. 全局内存压力
        new_mem = adjudged.get(Dimension.MEMORY_BYTES, 0.0)
        if (self._global_memory_pressure_bytes > 0
                and global_live_memory + new_mem > self._global_memory_pressure_bytes):
            reasons.append(
                f"global_memory_pressure:{global_live_memory + new_mem:.4g}>"
                f"{self._global_memory_pressure_bytes:.4g}"
            )
            if (estimate.resource_class is ResourceClass.LIGHT
                    or new_mem <= 256 * 1024**2):
                reasons.append("light_demand_deferred_not_rejected")
                return self._decision(
                    AdmissionDecision.DEFER, demand, reasons, limits,
                    degrade_plan, suggestions,
                    queue_hint="heavy",
                )
            degrade_plan = plan_degradation(demand, soft)
            if remote_only:
                reasons.append("remote_only_under_pressure")
                return self._decision(
                    AdmissionDecision.REJECT, demand, reasons, limits,
                    degrade_plan, suggestions,
                )
            return self._decision(
                AdmissionDecision.DEGRADE, demand, reasons, limits,
                degrade_plan, suggestions,
            )

        # 3. provisional 预算违规 → 留痕 + 降级建议（校准前不硬拒）
        if soft:
            reasons.extend("soft_" + v.reason_code() for v in soft)
            degrade_plan = plan_degradation(demand, soft)

        # 4. provider 熔断 open（只读输入）
        if open_providers:
            reasons.extend(f"provider_open:{p}" for p in open_providers)
            degrade_plan.steps = _local_fallback_step(demand) + degrade_plan.steps
            if remote_only:
                reasons.append("remote_only_with_open_provider")
                return self._decision(
                    AdmissionDecision.REJECT, demand, reasons, limits,
                    degrade_plan, suggestions=[
                        "retry after provider cooldown",
                        "use a local fallback source explicitly",
                    ],
                )
            return self._decision(
                AdmissionDecision.DEGRADE, demand, reasons, limits,
                degrade_plan, suggestions,
            )

        # 5. SLO breach 积压 → 收紧限幅（观测面 → 准入的升级）
        if slo_breach_total >= self._slo_breach_soft_limit:
            reasons.append(f"slo_breach_backlog:{slo_breach_total}")
            wall_limit = adjudged.get(Dimension.WALL_TIME_S, 0.0)
            if wall_limit > 0:
                limits[Dimension.WALL_TIME_S] = min(wall_limit, 60.0)
            feat = adjudged.get(Dimension.FEATURE_COUNT, 0.0)
            if feat > 0:
                limits[Dimension.FEATURE_COUNT] = min(feat, 50_000.0)
            return self._decision(
                AdmissionDecision.ACCEPT_WITH_LIMITS, demand, reasons, limits,
                degrade_plan, suggestions,
            )

        # 6. 通道积压预测 → 提前排 defer（背压层执行真正的排队）
        if channel_state:
            heavy_channel = ("raster" if estimate.resource_class is ResourceClass.RASTER
                             else "heavy")
            in_flight, waiting = channel_state.get(heavy_channel, (0, 0))
            if waiting >= self._defer_when_channel_waiters:
                reasons.append(
                    f"channel_backlog:{heavy_channel}:waiting={waiting}")
                return self._decision(
                    AdmissionDecision.DEFER, demand, reasons, limits,
                    degrade_plan, suggestions, queue_hint=heavy_channel,
                )

        if not reasons:
            reasons.append("clean_admission")
        return self._decision(
            AdmissionDecision.ACCEPT, demand, reasons, limits,
            degrade_plan, suggestions,
        )

    # ── 内部 ─────────────────────────────────────────────────────────

    def _decision(
        self,
        decision: AdmissionDecision,
        demand: ResourceDemand,
        reasons: List[str],
        limits: Dict[Dimension, float],
        degrade_plan: DegradePlan,
        suggestions: List[str],
        *,
        queue_hint: Optional[str] = None,
    ) -> ResourceDecision:
        return ResourceDecision(
            decision=decision,
            reasons=reasons,
            limits=limits,
            queue_hint=queue_hint,
            degrade_hint=(degrade_plan.as_dict() if degrade_plan.steps else None),
            suggestions=suggestions,
            estimate_snapshot=demand.estimate.as_dict(),
        )


def _local_fallback_step(demand: ResourceDemand):
    """provider open 时的 local_fallback 建议（D7 阶梯的直接引用）。"""
    from app.services.governor.degradation import (
        DegradeAction, DegradeStep,
        _option_by_action,
    )
    option = _option_by_action(DegradeAction.LOCAL_FALLBACK)
    return [DegradeStep(
        action=option.action,
        semantics=option.semantics,
        reason_codes=["provider_open"],
        savings=dict(option.savings),
        description=option.description,
    )]


__all__ = [
    "AdmissionPolicy",
]
