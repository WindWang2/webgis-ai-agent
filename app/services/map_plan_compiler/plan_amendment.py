"""Typed plan amendment（F12 多轮演进词表，ADR-0214 D2）。

封闭词表 + 有界载荷：每个 amendment 只指名一个目标节点；未知 kind 在
构造期即被 Literal 拒绝。投影/编译器对未知目标一律 fail-closed。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.lib.cartography.plan_ir import AmendmentKind

__all__ = ["PlanAmendment"]


class PlanAmendment(BaseModel):
    """一条多轮修改意图（LLM/用户只提供选择，编译器负责一致性）。"""

    model_config = ConfigDict(extra="forbid")

    kind: AmendmentKind
    #: 目标（layer/component 二选一，按 kind 语义）。
    layer_id: str = Field(default="", max_length=128)
    component_id: str = Field(default="", max_length=128)
    #: add_chart
    chart_type: str = Field(default="", max_length=64)
    #: set_layer_visibility
    visible: Optional[bool] = None
    #: restyle_layer（paint 顶层键合并；classification: k/method/palette）
    paint: Dict[str, Any] = Field(default_factory=dict)
    classification: Dict[str, Any] = Field(default_factory=dict)
    #: set_title / add_chart
    title: str = Field(default="", max_length=256)
    #: add_export
    fmt: str = Field(default="", max_length=24)
    #: pin_component_zone
    zone: str = Field(default="", max_length=48)
    #: 可选证据注记（amendment 来源披露；自由文本禁入 —— 只收短码）。
    note: str = Field(default="", max_length=96)
