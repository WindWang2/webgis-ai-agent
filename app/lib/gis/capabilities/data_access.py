"""数据获取 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="poi_query", name="POI 要素获取", category="data_access",
            description="按范围/类别获取点要素（本地优先，在线兜底）。",
            output_artifact_types=["poi_feature_set"],
            geometry_requirements=["point"],
            preferred_execution="local_first",
            compatible_map_models=["visual_heatmap", "point_overlay", "simple_point_map",
                                   "proportional_symbol", "categorical_thematic"],
            purpose_template="{subject} 要素获取",
            version="1.0",
        ),

        CapabilityDescriptor(
            id="admin_boundary_query", name="行政区边界获取", category="data_access",
            description="获取行政区边界面（本地 SHP 优先）。",
            output_artifact_types=["admin_boundary_set"],
            geometry_requirements=["polygon"],
            compatible_map_models=["administrative_aggregation"],
            purpose_template="行政边界/区划面获取",
        ),
]
