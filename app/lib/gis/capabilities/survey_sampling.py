"""概率抽样调查能力包（GOAL-20261127-03）。

与 ``spatial_sampling``（布点生成）的边界：本能力是**概率调查闭环**——
冻结抽样框（分类栅格分层+面积核查）、设计合同（纳入概率/权重）、
独立参考观测（盲标/状态机）与设计一致估计（加权误差矩阵/面积 CI）。
产出复用既有词表（stats_table / point_feature_set），不新增 artifact 类型。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="survey_sampling", name="概率抽样调查", category="analysis",
        domain="general",
        description=(
            "概率抽样调查与专题图精度核查：从分类栅格建冻结抽样框"
            "（等面积核查），分层 SRS 设计（Olofsson 2014），可复现选样"
            "（纳入概率/权重/预抽替补），独立参考标签调查（盲标/状态机/"
            "CAS），设计一致估计（加权误差矩阵、OA/UA/PA、类别面积与 "
            "95% CI）。"),
        input_artifact_types=["raster_object", "stats_table"],
        output_artifact_types=["stats_table", "point_feature_set"],
        geometry_requirements=[],
        purpose_template="概率抽样调查（精度核查与面积估计）",
    ),
]
