"""几何处理 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="geometry_buffer", name="几何缓冲", category="analysis",
            description="点/线/面缓冲几何。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "line_feature_set", "polygon_feature_set"],
            output_artifact_types=["proximity_zone"],
            purpose_template="几何缓冲",
        ),

        CapabilityDescriptor(
            id="geometry_clip", name="几何裁剪", category="analysis",
            description="要素裁剪。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "polygon_feature_set"],
            output_artifact_types=["polygon_feature_set"],
            purpose_template="几何裁剪",
        ),

        CapabilityDescriptor(
            id="geometry_dissolve", name="融合/溶解", category="analysis",
            description="同属性面融合。",
            input_artifact_types=["polygon_feature_set", "admin_boundary_set"],
            output_artifact_types=["polygon_feature_set"],
            purpose_template="融合溶解",
        ),

        CapabilityDescriptor(
            id="spatial_join", name="空间连接", category="analysis",
            description="按拓扑关系把右表属性挂到左表（区别于几何裁剪）。",
            input_artifact_types=["poi_feature_set", "polygon_feature_set"],
            output_artifact_types=["polygon_feature_set"],
            purpose_template="空间连接",
        ),
]
