"""降级阶梯（R7，ADR-0182 D7）。

Governor 只产出 **DegradePlan（建议 + 理由 + 科学语义标注）**，执行权在
调用方 —— 与 ``GisBudgetAdvisor``「建议不落刀」同一纪律。每条降级建议
必须携带：

- ``semantics``：comparable / approximate / non_comparable（**绝不偷偷
  改变科学语义** —— spec §12 红线）；
- ``reason_codes``：结构化理由（admission 违规 × 降级动作的映射）；
- ``savings``：按维度的预估节省倍率（供 admission 估算降级后是否可行）。

阶梯动作全部是**既有能力的消费建议**（采样/聚合/粗分辨率/简化几何/
interactive-first/deterministic judge/local fallback/essential views），
不是新的执行路径。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from app.services.governor.contract import (
    DegradationSemantics,
    ResourceDemand,
)
from app.services.governor.session_budget import BudgetViolation


class DegradeAction(str, Enum):
    """降级动作封闭词表（复用既有能力；不新增执行路径）。"""

    SAMPLE_FEATURES = "sample_features"              # full features → sampled/aggregated
    COARSEN_RESOLUTION = "coarsen_resolution"        # high-res raster → coarser
    SIMPLIFY_GEOMETRY = "simplify_geometry"          # exact geometry → simplified
    INTERACTIVE_FIRST = "interactive_first"          # interactive + export → interactive
    DETERMINISTIC_JUDGE = "deterministic_judge"      # VLM judge → deterministic
    LOCAL_FALLBACK = "local_fallback"                # external source → local
    ESSENTIAL_VIEWS = "essential_views"              # complex product → essential
    REDUCE_EXPORT_DPI = "reduce_export_dpi"          # 300DPI → screen DPI


@dataclass
class DegradeOption:
    """一个降级动作的静态画像。"""

    action: DegradeAction
    semantics: DegradationSemantics
    #: 动作主要缓解的检查键（= 维度名 或 BudgetViolation.check 名）
    addresses: tuple
    #: 各维度节省倍率（估计降级后 expected × saving）
    savings: Dict[str, float] = field(default_factory=dict)
    reversible: bool = True
    description: str = ""


_LADDER: List[DegradeOption] = [
    DegradeOption(
        action=DegradeAction.SAMPLE_FEATURES,
        semantics=DegradationSemantics.APPROXIMATE,
        addresses=("feature_count",),
        savings={"feature_count": 0.25, "memory_bytes": 0.4, "wall_time_s": 0.5},
        description="full features → deterministic sample/aggregation",
    ),
    DegradeOption(
        action=DegradeAction.COARSEN_RESOLUTION,
        semantics=DegradationSemantics.APPROXIMATE,
        addresses=("pixel_count",),
        savings={"pixel_count": 0.25, "memory_bytes": 0.4, "wall_time_s": 0.5},
        description="high-resolution raster → coarser grid",
    ),
    DegradeOption(
        action=DegradeAction.SIMPLIFY_GEOMETRY,
        semantics=DegradationSemantics.APPROXIMATE,
        addresses=("feature_count", "memory_bytes"),
        savings={"memory_bytes": 0.6, "wall_time_s": 0.7},
        description="exact geometry → tolerance-simplified",
    ),
    DegradeOption(
        action=DegradeAction.INTERACTIVE_FIRST,
        semantics=DegradationSemantics.COMPARABLE,
        addresses=("wall_time_s", "heavy_count"),
        savings={"wall_time_s": 0.4},
        description="interactive view first; export deferred/re-queued",
    ),
    DegradeOption(
        action=DegradeAction.DETERMINISTIC_JUDGE,
        semantics=DegradationSemantics.NON_COMPARABLE,
        addresses=("external_calls",),
        savings={"external_service_calls": 0.0, "wall_time_s": 0.6},
        reversible=False,
        description="VLM visual judge → deterministic checks (no VLM call)",
    ),
    DegradeOption(
        action=DegradeAction.LOCAL_FALLBACK,
        semantics=DegradationSemantics.NON_COMPARABLE,
        addresses=("network_bytes",),
        savings={"network_bytes": 0.0, "wall_time_s": 0.8},
        reversible=False,
        description="remote source unavailable → local asset index",
    ),
    DegradeOption(
        action=DegradeAction.ESSENTIAL_VIEWS,
        semantics=DegradationSemantics.NON_COMPARABLE,
        addresses=("render_work_units",),
        savings={"render_work_units": 0.3, "wall_time_s": 0.4},
        description="complex map product → essential views only",
    ),
    DegradeOption(
        action=DegradeAction.REDUCE_EXPORT_DPI,
        semantics=DegradationSemantics.APPROXIMATE,
        addresses=("pixel_count",),
        savings={"pixel_count": 0.1, "wall_time_s": 0.2},
        description="publication DPI → screen DPI (re-render later)",
    ),
]

#: subsystem → 偏好动作（阶梯排序用；其余动作按 addresses 匹配）
_SUBSYSTEM_PREFERENCE: Dict[str, tuple] = {
    "raster_compute": (DegradeAction.COARSEN_RESOLUTION,),
    "remote_sensing": (DegradeAction.COARSEN_RESOLUTION, DegradeAction.LOCAL_FALLBACK),
    "vector_compute": (DegradeAction.SAMPLE_FEATURES, DegradeAction.SIMPLIFY_GEOMETRY),
    "statistics": (DegradeAction.SAMPLE_FEATURES,),
    "data_fabric": (DegradeAction.SAMPLE_FEATURES, DegradeAction.LOCAL_FALLBACK),
    "download": (DegradeAction.LOCAL_FALLBACK,),
    "render": (DegradeAction.ESSENTIAL_VIEWS, DegradeAction.REDUCE_EXPORT_DPI),
    "browser": (DegradeAction.INTERACTIVE_FIRST,),
    "export": (DegradeAction.INTERACTIVE_FIRST, DegradeAction.REDUCE_EXPORT_DPI),
    "vlm_judge": (DegradeAction.DETERMINISTIC_JUDGE,),
    "map_compile": (DegradeAction.ESSENTIAL_VIEWS,),
}


@dataclass
class DegradeStep:
    """计划中的一个降级步骤（建议；执行权在调用方）。"""

    action: DegradeAction
    semantics: DegradationSemantics
    reason_codes: List[str] = field(default_factory=list)
    savings: Dict[str, float] = field(default_factory=dict)
    description: str = ""

    def as_dict(self) -> Dict:
        return {
            "action": self.action.value,
            "semantics": self.semantics.value,
            "reason_codes": list(self.reason_codes),
            "savings": dict(self.savings),
            "description": self.description,
        }


@dataclass
class DegradePlan:
    """一次降级建议（有序：语义保持的动作优先）。"""

    steps: List[DegradeStep] = field(default_factory=list)

    @property
    def best_semantics(self) -> Optional[DegradationSemantics]:
        """整计划的最优语义承诺（最友好的一条决定上限）。"""
        if not self.steps:
            return None
        order = {
            DegradationSemantics.COMPARABLE: 0,
            DegradationSemantics.APPROXIMATE: 1,
            DegradationSemantics.NON_COMPARABLE: 2,
        }
        return min((s.semantics for s in self.steps), key=lambda x: order[x])

    def as_dict(self) -> Dict:
        return {
            "steps": [s.as_dict() for s in self.steps],
            "best_semantics": (self.best_semantics.value
                               if self.best_semantics else None),
        }


def _violation_key(v: BudgetViolation) -> str:
    return v.dimension.value if v.dimension else v.check


def _option_by_action(action: DegradeAction) -> DegradeOption:
    return next(o for o in _LADDER if o.action is action)


def plan_degradation(demand: ResourceDemand,
                     violations: List[BudgetViolation],
                     *, max_steps: int = 3) -> DegradePlan:
    """违规事实 → 有序降级建议（确定性；同输入必同输出）。

    排序规则：subsystem 偏好动作优先（稳定排序保持阶梯声明序），其余按
    阶梯声明序；每步携带触发它的违规 reason codes。无违规 → 空计划。
    """
    if not violations:
        return DegradePlan()
    prefs = set(_SUBSYSTEM_PREFERENCE.get(demand.subsystem.value, ()))
    violation_keys = {_violation_key(v) for v in violations}

    ordered = sorted(_LADDER, key=lambda o: o.action not in prefs)
    steps: List[DegradeStep] = []
    for option in ordered:
        if not set(option.addresses) & violation_keys:
            continue
        codes = [v.reason_code() for v in violations
                 if _violation_key(v) in option.addresses]
        steps.append(DegradeStep(
            action=option.action,
            semantics=option.semantics,
            reason_codes=codes,
            savings=dict(option.savings),
            description=option.description,
        ))
        if len(steps) >= max_steps:
            break
    return DegradePlan(steps=steps)


__all__ = [
    "DegradeAction",
    "DegradeOption",
    "DegradeStep",
    "DegradePlan",
    "plan_degradation",
]
