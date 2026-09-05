"""决策分析能力包（ADR-0099 §34 domain packs）。

MCDA 能力迁自 workbench VNext（PR #1142）—— 合并时按域包架构归位。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    # DecisionEngineV3（spatial_decision_v3 工具）—— 观测证据、用户权重、
    # 假设在结果里必须可区分（不合成证据）。
    CapabilityDescriptor(
        id="mcda_evaluation", name="多准则决策评价", category="analysis",
        description="候选方案×准则×约束的 MCDA 评价（WSM/TOPSIS + Pareto + 敏感性）。",
        input_artifact_types=["poi_feature_set", "point_feature_set",
                              "polygon_feature_set", "admin_aggregate_table"],
        output_artifact_types=["stats_table"],
        deterministic=True,
        purpose_template="多准则决策评价（MCDA）",
    ),
]
