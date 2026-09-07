"""Fallback V3 —— 四层语义回退的统一裁决（Goal V3）。

把散落的回退事实（数据资格状态 / 科学义务阻断 / planned 能力 / 本体
fallback_strategy / 场景 minimal 兜底）收敛为单一确定性裁决：

    preferred  最专业路径：数据充足、能力 native、无阻断；
    degraded   数据不完整仍可给近似结果 —— 必须显式披露，不得静默；
    minimal    仅安全、真实、不误导的描述性输出；
    blocked    不能科学执行 —— 明确指出缺什么。

红线（goal §八）：

- 禁止用假算法补洞：remediation 无自动实现时不得声称可修复；
- 禁止缺数据时静默构造：任何降级必须携带用户可见 disclosure；
- 禁止把 planned 能力当 native：uses_planned_capability=True 时 tier
  不得为 preferred（强制降档 + 披露）；
- 降级分类词表复用 DOWNGRADE_CLASSES（与算法层 fallback_semantics
  单一事实源），不另造词表。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from pydantic import BaseModel, Field

#: 回退层（与 gis_ontology.FallbackTier 同词表；单一事实源在本体层声明
#: 任务级策略，本模块做运行期事实裁决）。
FALLBACK_TIERS = ("preferred", "degraded", "minimal", "blocked")



class FallbackResolution(BaseModel):
    """一次工作流级回退裁决（compiler / finalize 共用证据）。"""
    tier: str = "preferred"            # ⊆ FALLBACK_TIERS
    # 复用 DOWNGRADE_CLASSES 词表：equivalent / approximation / proxy /
    # degraded / not_allowed
    downgrade_class: str = "equivalent"
    disclosures: List[str] = Field(default_factory=list)
    blocked_reasons: List[str] = Field(default_factory=list)
    evidence: Dict[str, Any] = Field(default_factory=dict)

    def to_bounded_dict(self) -> Dict[str, Any]:
        return {
            "tier": self.tier,
            "downgrade_class": self.downgrade_class,
            "disclosures": [d[:200] for d in self.disclosures[:6]],
            "blocked_reasons": [r[:80] for r in self.blocked_reasons[:6]],
            "evidence": dict(list(self.evidence.items())[:6]),
        }


_PLANNED_DISCLOSURE = (
    "所选路径包含 planned（未注册实现）能力：该环节只能以近似/代理方式"
    "呈现，不得表述为原生分析结论。"
)
_MINIMAL_DISCLOSURE = (
    "数据证据不足：仅提供描述性输出（计数/清单/概况），不做推断性结论。"
)


def resolve_fallback_tier(
    *,
    ontology_task_id: str = "",
    data_states: Tuple[str, ...] = (),
    method_blockers: Tuple[str, ...] = (),
    data_blockers: Tuple[str, ...] = (),
    uses_planned_capability: bool = False,
    scenario_minimal_disclosure: str = "",
    extra_disclosures: Tuple[str, ...] = (),
) -> FallbackResolution:
    """四层回退的确定性裁决（同输入同输出）。

    优先级：blocked > minimal > degraded > preferred（只降不升）。
    """
    from app.services.gis_harness.gis_ontology import get_task_ontology
    from app.services.gis_harness.workflow_schema import DOWNGRADE_CLASSES

    disclosures: List[str] = list(extra_disclosures)
    blocked_reasons: List[str] = (
        [f"method_blocker:{b}" for b in method_blockers]
        + [f"data_blocker:{b}" for b in data_blockers]
    )
    evidence: Dict[str, Any] = {
        "data_states": list(data_states)[:8],
        "uses_planned_capability": uses_planned_capability,
        "ontology_task": ontology_task_id[:64] or None,
    }

    def _blocked() -> FallbackResolution:
        return FallbackResolution(
            tier="blocked", downgrade_class="not_allowed",
            disclosures=disclosures,
            blocked_reasons=blocked_reasons[:8],
            evidence=evidence,
        )

    if blocked_reasons:
        return _blocked()

    # 本体任务级策略：任务声明 planned（能力未注册）→ 不得 preferred
    task_status = ""
    if ontology_task_id:
        desc = get_task_ontology().get(ontology_task_id)
        if desc is not None:
            task_status = desc.semantic_status

    planned = uses_planned_capability or task_status == "planned"
    states = set(data_states)

    if "blocked" in states:
        blocked_reasons.append("data_qualification_blocked")
        return _blocked()

    if planned:
        disclosures.append(_PLANNED_DISCLOSURE)

    # 无数据事实（规划期未带 profile）：诚实缺省 preferred —— unknown ≠
    # unsatisfied，不因「画像缺席」而虚构降级；planned 在场仍强制降档。
    if not states or states == {"unknown"}:
        if planned:
            return FallbackResolution(
                tier="degraded", downgrade_class="proxy",
                disclosures=disclosures, blocked_reasons=[],
                evidence=evidence,
            )
        return FallbackResolution(
            tier="preferred", downgrade_class="equivalent",
            disclosures=disclosures, blocked_reasons=[],
            evidence=evidence,
        )

    has_executable = bool(states & {"eligible", "transform_required"})
    if not has_executable:
        # 事实在手且无任何可执行路径：仅描述性输出（minimal）
        if scenario_minimal_disclosure:
            disclosures.append(scenario_minimal_disclosure)
        disclosures.append(_MINIMAL_DISCLOSURE)
        return FallbackResolution(
            tier="minimal", downgrade_class="degraded",
            disclosures=disclosures, blocked_reasons=[],
            evidence=evidence,
        )

    if "degraded" in states or planned:
        return FallbackResolution(
            tier="degraded",
            downgrade_class="proxy" if planned else "approximation",
            disclosures=disclosures, blocked_reasons=[],
            evidence=evidence,
        )

    return FallbackResolution(
        tier="preferred", downgrade_class="equivalent",
        disclosures=disclosures, blocked_reasons=[],
        evidence=evidence,
    )


__all__ = [
    "FALLBACK_TIERS",
    "FallbackResolution",
    "resolve_fallback_tier",
]
