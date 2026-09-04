"""空间聚合 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="admin_aggregation", name="行政区聚合统计", category="analysis",
            description="点落入面聚合（各区数量）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["admin_aggregate_table"],
            geometry_requirements=["point"],
            compatible_map_models=["administrative_choropleth", "administrative_aggregation", "extrusion_3d"],
            purpose_template="按行政区聚合统计",
        ),

        CapabilityDescriptor(
            id="grid_binning", name="格网聚合", category="density",
            description="点聚合入 H3 六边形/渔网格网。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["grid_aggregate"],
            geometry_requirements=["point"],
            compatible_map_models=["aggregate_grid"],
            fallback_capabilities=["density_surface"],
            purpose_template="H3/渔网格网聚合",
        ),
]
