"""栅格/遥感/地形 能力包（ADR-0099 §34 domain packs）。

描述符逐字迁自 capability_registry._SEED_CAPS（2026-09 split）。
新能力在各自域模块注册，勿回填中央文件。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="raster_source", name="栅格数据源", category="raster",
            domain="raster",
            description="DEM/遥感栅格获取。",
            output_artifact_types=["terrain_surface", "raster_surface"],
            geometry_requirements=["raster"],
            compatible_map_models=["raster_surface"],
            purpose_template="栅格数据源",
        ),

        CapabilityDescriptor(
            id="terrain_slope", name="坡度分析", category="raster",
            domain="raster", description="DEM 坡度。",
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["terrain_surface"],
            purpose_template="坡度分析",
        ),

        CapabilityDescriptor(
            id="terrain_aspect", name="坡向分析", category="raster",
            domain="raster", description="DEM 坡向。",
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["terrain_surface"],
            purpose_template="坡向分析",
        ),

        CapabilityDescriptor(
            id="terrain_hillshade", name="山体阴影", category="raster",
            domain="raster", description="DEM 山体阴影。",
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["terrain_surface"],
            purpose_template="山体阴影",
        ),

        CapabilityDescriptor(
            id="ndvi", name="NDVI 植被指数", category="raster",
            domain="raster", description="遥感 NDVI 计算。",
            input_artifact_types=["terrain_surface", "raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="NDVI 指数",
        ),

        CapabilityDescriptor(
            id="band_math", name="波段/栅格代数", category="raster",
            domain="raster",
            description="逐像元栅格代数（A/B 表达式、常数运算；A 为基准网格，B 自动对齐）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="波段/栅格代数",
        ),

        CapabilityDescriptor(
            id="raster_change_detection", name="双时相栅格变化检测", category="raster",
            domain="raster",
            description="两个栅格工件的对齐像元级变化检测（差值/绝对差/归一化差 + 阈值分类）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface", "change_set"],
            compatible_map_models=["raster_surface"],
            purpose_template="双时相栅格变化检测",
        ),

        CapabilityDescriptor(
            id="zonal_statistics", name="分区统计", category="raster",
            domain="raster", description="面内栅格 min/max/mean/sum 统计。",
            input_artifact_types=["raster_surface", "polygon_feature_set"],
            output_artifact_types=["stats_table"],
            purpose_template="分区统计",
        ),

        CapabilityDescriptor(
            id="raster_reclassify", name="栅格重分类", category="raster",
            domain="raster", description="连续栅格值按方案映射为离散类别。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="栅格重分类",
        ),

        CapabilityDescriptor(
            id="raster_resample", name="栅格重采样", category="raster",
            domain="raster", description="改变像元大小和/或 CRS（对齐预处理）。",
            input_artifact_types=["raster_surface"],
            output_artifact_types=["raster_surface"],
            purpose_template="栅格重采样",
        ),
]
