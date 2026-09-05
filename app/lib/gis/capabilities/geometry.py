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

        # ── VNext：几何叠加/构造族能力（配套 algorithms/geometry.py 新描述符）──

        CapabilityDescriptor(
            id="geometry_overlay", name="几何叠加", category="analysis",
            description="GEOS 拓扑叠加（intersection/union/difference 等），纯拓扑不量度。",
            input_artifact_types=["polygon_feature_set", "line_feature_set",
                                  "point_feature_set", "poi_feature_set"],
            output_artifact_types=["polygon_feature_set", "line_feature_set", "point_feature_set"],
            purpose_template="几何叠加",
        ),

        CapabilityDescriptor(
            id="convex_hull", name="凸包", category="analysis",
            description="点/面要素集的最小凸包围合多边形。",
            input_artifact_types=["poi_feature_set", "point_feature_set", "polygon_feature_set"],
            output_artifact_types=["polygon_feature_set"],
            purpose_template="凸包围合",
        ),

        CapabilityDescriptor(
            id="voronoi_tessellation", name="Voronoi 剖分", category="analysis",
            description="点的 Voronoi/Thiessen 有限区域剖分（镜像外推 + 范围裁剪）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["polygon_feature_set"],
            geometry_requirements=["point"],
            purpose_template="Voronoi 服务区剖分",
        ),

        CapabilityDescriptor(
            id="multi_ring_buffer", name="多环缓冲", category="analysis",
            description="同心多距离环/环带（band 互斥、并集覆盖最大盘）。",
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "line_feature_set", "polygon_feature_set"],
            output_artifact_types=["proximity_zone"],
            purpose_template="多环缓冲区",
        ),

        CapabilityDescriptor(
            id="geometry_centroid", name="几何中心统计", category="analysis",
            description="图层量纲摘要中的质心/平均中心（并集质心或显式 mean_center）。",
            input_artifact_types=["poi_feature_set", "point_feature_set",
                                  "line_feature_set", "polygon_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="几何中心统计",
        ),
]
