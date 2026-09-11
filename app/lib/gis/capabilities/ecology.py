"""生态分析能力包（Goal 07 Science V6 新域）。

生境适宜度（属性响应曲线评分，产出 feature_collection）与景观格局
（分类栅格连通/多样性指标，产出 stats_table）。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [
    CapabilityDescriptor(
        id="habitat_suitability", name="生境适宜度", category="analysis",
        domain="general",
        description=(
            "生境适宜度指数（HSI，USFWS 1981 口径）：逐要素的梯形/高斯"
            "响应曲线评分 + 加权（算术/几何）聚合，输出 0-1 适宜度与"
            "适宜度分级；保护区选址/栖息地评估输入。"),
        input_artifact_types=["polygon_feature_set", "point_feature_set",
                              "grid_aggregate", "admin_aggregate_table"],
        output_artifact_types=["feature_collection"],
        purpose_template="生境适宜度评价",
    ),

    CapabilityDescriptor(
        id="landscape_metrics", name="景观格局指标", category="raster",
        domain="raster",
        description=(
            "分类栅格景观格局（FRAGSTATS 口径 4 邻接）：PLAND/斑块数/"
            "斑块密度/最大斑块指数/边缘密度与 SHDI/SIDI 多样性；"
            "景观破碎化与连通性诊断。"),
        input_artifact_types=["raster_surface", "terrain_surface"],
        output_artifact_types=["stats_table"],
        purpose_template="景观格局指标计算",
    ),
]
