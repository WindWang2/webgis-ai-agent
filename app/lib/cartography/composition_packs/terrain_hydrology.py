"""地形 / 水文域组合包（ADR-0101 §B2）。

- terrain_analysis：解析面（坡度/指数）+ scientific 色条 + 经纬网 + 图框，
  学术出版风格 A4 横版；
- watershed_report：流域/子流域统计报告版式（choropleth 族 + 面板 + A4 竖版）。
"""
from __future__ import annotations

from typing import List

from app.lib.cartography.composition_packs._base import (
    attribution_slot,
    charts_slot,
    composition,
    export_slot,
    legend_slot,
    map_border_slot,
    north_arrow_slot,
    scale_bar_slot,
    stats_slot,
    subtitle_slot,
    title_slot,
)
from app.lib.cartography.composition_templates import ComponentSlot, MapCompositionTemplate

TERRAIN_HYDROLOGY_PACK: List[MapCompositionTemplate] = [
    composition(
        cid="composition.terrain_analysis",
        name="Terrain Analysis Map",
        description="地形分析版式：连续色面 + scientific 色条 + 经纬网 + "
                    "academic 图框 + A4 横版，地形解析的出版表达。",
        models=["terrain_analytical_surface", "raster_surface", "isoline_contour"],
        outputs=["png", "pdf", "svg"],
        profile="academic",
        fallback="composition.remote_sensing_map",
        priority=42,
        tags=["terrain", "academic", "raster"],
        slots=[
            title_slot(preferred_templates=["title/academic"]),
            subtitle_slot(preferred_templates=["subtitle/academic"]),
            legend_slot(["continuous_colorbar", "legend"],
                        cardinality="required", required=True, max_count=2,
                        zone="bottom-right",
                        preferred_templates=["colorbar/scientific"]),
            north_arrow_slot(preferred_templates=["north-arrow/compass-rose"]),
            scale_bar_slot(preferred_templates=["scale-bar/academic"]),
            attribution_slot(),
            ComponentSlot(id="graticule", category="navigation.graticule",
                          allowed_component_types=["graticule"],
                          cardinality="optional", position_zone="none"),
            stats_slot(),
            map_border_slot(cardinality="required", required=True,
                            preferred_templates=["frame/academic"]),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-landscape"]),
        ],
    ),
    composition(
        cid="composition.watershed_report",
        name="Watershed Report Map",
        description="流域报告版式：面统计分级 + 统计面板 + 图表 + A4 竖版，"
                    "水文/流域管理报告表达。",
        models=["administrative_choropleth", "normalized_choropleth",
                "diverging_choropleth", "suitability_classes"],
        outputs=["interactive", "png", "pdf"],
        profile="report",
        fallback="composition.statistical_report",
        priority=43,
        tags=["hydrology", "watershed", "report"],
        slots=[
            title_slot(preferred_templates=["title/report"]),
            subtitle_slot(),
            legend_slot(["legend", "categorical_legend", "continuous_colorbar"],
                        cardinality="required", required=True,
                        preferred_templates=["legend/report"]),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            stats_slot(cardinality="recommended"),
            charts_slot(max_count=2),
            map_border_slot(cardinality="required", required=True,
                            preferred_templates=["frame/report"]),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-portrait"]),
        ],
    ),
]
