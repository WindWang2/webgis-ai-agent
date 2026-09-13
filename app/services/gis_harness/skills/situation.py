"""Skill 选择的事实面 —— SituationLike Protocol 与 SelectionFacts
（ADR-0182 §2.4；goal S29）。

Situation 分支（#1275，ADR-0180）未合并：本模块用 typing.Protocol 做
**结构性松耦合** —— `GISSituation` 合并后天然满足协议（同名字段），
本线零改动。事实缺席 = unknown（unknown ≠ 不满足，与 eligibility 红线
一致），绝不虚构。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, Field


@runtime_checkable
class SituationLike(Protocol):
    """Situation 协议（结构化鸭子类型）。

    只声明 SkillResolver 实际消费的最小字段面；#1275 的 `GISSituation`
    的 `geographic` / `data` / `user_goal` / `delivery` 分区满足这些
    属性名即可直接传入（不需要适配器）。
    """

    def get_geographic_scope_name(self) -> str: ...

    def get_active_geometry_kinds(self) -> List[str]: ...

    def get_active_data_roles(self) -> List[str]: ...

    def get_delivery_target(self) -> str: ...


class SelectionFacts(BaseModel):
    """选择事实：resolver 的确定性输入（goal/intent/situation 投影）。

    字段缺席或 unknown → 该信号不参与打分与硬门槛（unknown 放行）。
    """
    goal_text: str = ""                        # 原始目标文本（zh/en）
    task_type: str = ""                        # ⊆ intent.TaskType（已知时）
    ontology_matches: List[str] = Field(default_factory=list)  # 已知本体任务
    geometry_kinds: List[str] = Field(default_factory=list)
    data_roles: List[str] = Field(default_factory=list)
    scope_name: str = ""
    scope_unit: str = ""                       # city/district/...
    feature_count: Optional[int] = None
    measure_semantics: str = ""                # ⊆ semantics.MEASURE_SEMANTICS（已知时）
    temporal_mode: str = ""                    # ⊆ semantics.TEMPORAL_MODES（已知时）
    period_count: Optional[int] = None         # 已知期数（trend/comparison 门槛用）
    delivery_target: str = ""                  # interactive/png/pdf/report...
    map_purpose: str = ""                      # presentation/publication/atlas...
    has_boundary: Optional[bool] = None
    has_network: Optional[bool] = None
    has_dem: Optional[bool] = None
    # 已经绑定 / 在场的数据集引用（角色 → ref），用于角色可满足性判断
    bound_roles: Dict[str, str] = Field(default_factory=dict)

    @classmethod
    def from_intent(cls, intent: Any) -> "SelectionFacts":
        """MapRequestIntent → SelectionFacts（诚实投影，缺省保 unknown）。"""
        scope = getattr(intent, "scope", None)
        return cls(
            goal_text=str(getattr(intent, "query", "") or ""),
            task_type=str(getattr(intent, "task", "") or ""),
            geometry_kinds=[g] if (g := str(getattr(intent, "geometry_expectation", "") or "")) else [],
            scope_name=str(getattr(scope, "name", "") or ""),
            scope_unit=str(getattr(scope, "level", "") or ""),
            temporal_mode="comparison" if str(getattr(intent, "comparison", "") or "") else "",
            delivery_target="report" if bool(getattr(intent, "report_product", False)) else "",
        )

    @classmethod
    def from_situation(cls, situation: Any) -> "SelectionFacts":
        """SituationLike/GISSituation → SelectionFacts（鸭子类型容错）。

        属性缺席时诚实留空 —— 惰性 getattr，不要求完整 GISSituation 在场。
        """
        geographic = getattr(situation, "geographic", None)
        data = getattr(situation, "data", None)
        user_goal = getattr(situation, "user_goal", None)
        delivery = getattr(situation, "delivery", None)
        geom: List[str] = []
        roles: List[str] = []
        try:
            datasets = list(getattr(data, "datasets", []) or [])
            for ds in datasets:
                g = str(getattr(ds, "geometry_kind", "") or "")
                if g and g not in geom:
                    geom.append(g)
                r = str(getattr(ds, "role", "") or "")
                if r:
                    roles.append(r)
        except Exception:  # noqa: BLE001 - 非契约形态 → 诚实留空
            pass
        return cls(
            goal_text=str(getattr(user_goal, "goal_text", "") or ""),
            scope_name=str(getattr(geographic, "scope_name", "") or ""),
            geometry_kinds=geom,
            data_roles=roles,
            delivery_target=str(getattr(delivery, "target", "") or ""),
        )


__all__ = [
    "SituationLike",
    "SelectionFacts",
]
