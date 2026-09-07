"""地形分析能力包（ADR-0099 §34 domain packs；VNext 新增 terrain 域）。

VNext：登记地形科学能力（衍生指标/视域/水文/等值线）。产出工件复用
既有词表（raster_surface / line_feature_set），不新增 artifact 类型。

Terrain V3：追加水平线与天空可视因子能力（terrain_sky_view，地平线角
+ SVF 同族；horizon angle 与 sky view factor 不拆两个能力）。
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

        # ── Foundation V2（A5）：水文与地貌量测扩展 ────────────────────

        CapabilityDescriptor(
            id="terrain_hydrology_advanced", name="高级地形水文", category="raster",
            domain="raster",
            description=(
                "Priority-Flood 填洼（Barnes 2014，epsilon 单调变体）、D∞ 多向流"
                "（Tarboton 1997 比例分流）、流程长度、河网提取与 Strahler 分级、"
                "流域形态量测（Strahler 1957）。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="高级地形水文分析",
        ),

        CapabilityDescriptor(
            id="terrain_wetness_indices", name="湿润与侵蚀指数", category="raster",
            domain="raster",
            description=(
                "地形湿润指数 TWI（Beven-Kirkby 1979）、水流功率指数 SPI 与 "
                "USLE LS 因子（Wischmeier-Smith 1978 / Desmet-Govers 1996）。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="湿润与侵蚀指数计算",
        ),

        CapabilityDescriptor(
            id="terrain_geomorphometry", name="地貌形态分类", category="raster",
            domain="raster",
            description=(
                "地形开放度（Yokoyama 2002）、geomorphons 地貌分类（Jasiewicz "
                "& Stepinski 2013）、Weiss 双尺度 TPI 地类分级与多方位山体阴影。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="地貌形态分类",
        ),

        # ── Terrain V3：地平线角与天空可视因子（同一能力族，不拆两个）──

        CapabilityDescriptor(
            id="terrain_sky_view", name="地平线与天空可视因子", category="raster",
            domain="raster",
            description=(
                "地平线角（逐方位最大正仰角，openness 家族射线行走）与天空可视"
                "因子 SVF（Steyn 1980 的 (1/N)Σcos²ψ 口径）；城市通风/日照/"
                "辐射与景观开敞度分析输入。"),
            input_artifact_types=["terrain_surface"],
            output_artifact_types=["raster_surface"],
            compatible_map_models=["raster_surface"],
            purpose_template="地平线与天空可视因子分析",
        ),
]
