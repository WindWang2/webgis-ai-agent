"""GOAL 04 多尺度制图能力包。

ScaleRepresentationProfile.v1 的三个工具面能力（此前仅工具声明、未注册
能力词表 → runtime manifest capability_id_dangling 致命错，CI
test_runtime_chaos_lifecycle 暴露）：

- ``cartography_scale_inspect``：图层多尺度证据只读检视；
- ``cartography_scale_preview``：零突变完整计划预览；
- ``cartography_scale_apply``：受控落笔（工件新 ref + MapSpec v1.9
  窗口源覆盖 + 顺序 CAS 链 + 幂等回执）。

category="presentation" / domain="cartography"（与 scene3d 同族——
表达决策层，非分析通道；统计口径恒 original_source）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="cartography_scale_inspect", name="多尺度表达证据检视",
        category="presentation", domain="cartography",
        description=(
            "只读检视图层的多尺度表达证据：源数据时点（ref/revision）、"
            "可信重要性字段（真实值分布 × 有序词表，fail-honest）、各尺度"
            "带取舍预览与既有 ScaleRepresentationProfile。"),
        purpose_template="{subject} 多尺度证据检视",
        deterministic=True,
    ),
    CapabilityDescriptor(
        id="cartography_scale_preview", name="多尺度表达计划预览",
        category="presentation", domain="cartography",
        description=(
            "零突变预览完整多尺度表达计划：四尺度带表达模式、每带显示/"
            "原始要素数、几何综合策略与容差、披露与语义检查（不存 ref、"
            "不改 MapSpec）。"),
        purpose_template="{subject} 多尺度表达预览",
        deterministic=True,
    ),
    CapabilityDescriptor(
        id="cartography_scale_apply", name="多尺度表达受控落笔",
        category="presentation", domain="cartography",
        description=(
            "按可信字段语义取舍 + 拓扑安全几何综合产派生工件（新 ref，"
            "原始数据不动），MapSpec v1.9 窗口源覆盖经顺序 CAS 链落笔；"
            "统计/图例口径恒 original_source；幂等回执 + 断点续链。"),
        purpose_template="{subject} 多尺度表达落笔",
        deterministic=False,
    ),
]
