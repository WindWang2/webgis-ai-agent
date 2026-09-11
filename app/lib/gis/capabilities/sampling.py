"""空间抽样能力包（Goal 07 Science V6 新域）。

抽样是生成型能力：输入面要素抽样框，输出样本点集。产出工件复用
既有词表（point_feature_set），不新增 artifact 类型。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="spatial_sampling", name="空间抽样", category="analysis",
        domain="general",
        description=(
            "在面要素抽样框上生成统计样本点：简单随机（逐面 SRS）、系统"
            "网格（随机起点偏移）与分层设计（equal/proportional 面积分"
            "配）；种子可控、可复现。野外核查点、精度评估、地统计布点"
            "输入。"),
        input_artifact_types=["polygon_feature_set", "admin_boundary_set",
                              "admin_aggregate_table"],
        output_artifact_types=["point_feature_set"],
        geometry_requirements=["polygon"],
        purpose_template="空间抽样（生成样本点）",
    ),
]
