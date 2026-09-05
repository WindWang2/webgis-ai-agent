"""地形分析能力包（ADR-0099 §34 domain packs；VNext 新增 terrain 域）。

VNext：登记地形科学能力（衍生指标/视域/水文/等值线）。产出工件复用
既有词表（raster_surface / line_feature_set），不新增 artifact 类型。
"""
from __future__ import annotations

from typing import List

from app.lib.gis.capability_registry import CapabilityDescriptor

CAPABILITIES: List[CapabilityDescriptor] = [

        CapabilityDescriptor(
            id="terrain_derivatives", name="地形衍生指标", category="raster",
            domain="raster",
            description=(
                "DEM 邻域地形指标：TPI（Weiss 2001）/TRI（Riley 1999）/粗糙度"
                "（Wilson 2007）与平面、剖面曲率（Zevenbergen-Thorne 1987）。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="地形衍生指标计算",
        ),

        CapabilityDescriptor(
            id="terrain_viewshed", name="视域分析", category="raster",
            domain="raster",
            description=(
                "DEM 视域：观察点视线遮挡布尔掩膜、可见比例与可见面积"
                "（扇区视线角扫描；无地球曲率/大气折射）。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="视域分析",
        ),

        CapabilityDescriptor(
            id="terrain_hydrology", name="D8 水文分析", category="raster",
            domain="raster",
            description=(
                "D8 单向流流向（ESRI 2 的幂编码）、拓扑序汇流累积与逆 D8 "
                "上游流域圈定（平地/洼地为汇，不填洼）。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="D8 水文分析",
        ),

        CapabilityDescriptor(
            id="terrain_contours", name="等值线提取", category="raster",
            domain="raster",
            description=(
                "DEM 等值线提取（marching squares → GeoJSON LineString，"
                "顶点映射到世界坐标；nodata 断线）。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["line_feature_set"],
            compatible_map_models=["raster_surface"],
            purpose_template="等值线提取",
        ),
]
