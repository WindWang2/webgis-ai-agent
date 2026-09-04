"""时序分析 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="temporal_profile", name="时间画像", category="statistics",
            description="时间字段/跨度/粒度画像（元数据，不产新数据）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="时间维度画像",
        ),

        CapabilityDescriptor(
            id="temporal_aggregate", name="时间聚合", category="statistics",
            description="按时间窗重采样汇总。",
            input_artifact_types=["point_feature_set", "poi_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="时间聚合统计",
        ),

        CapabilityDescriptor(
            id="temporal_trend", name="时序趋势", category="analysis",
            domain="statistics", description="时间维度的趋势/聚合/时空热点分析。",
            input_artifact_types=["poi_feature_set", "raster_surface", "stats_table"],
            output_artifact_types=["stats_table", "raster_surface"],
            purpose_template="时序趋势",
        ),

        CapabilityDescriptor(
            id="change_detection", name="时序要素变化检测", category="analysis",
            description="矢量要素的双时相对比变化集（栅格图像变化用 raster_change_detection）。",
            input_artifact_types=["poi_feature_set", "point_feature_set"],
            output_artifact_types=["change_set"],
            purpose_template="时序变化检测",
        ),
]
