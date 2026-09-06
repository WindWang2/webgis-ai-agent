"""统计 / 密度探索域组合包（ADR-0101 §B2）。

- statistical_report：choropleth 族的报告版组合（统计推荐 + 图表可选 +
  PDF 版式）—— 与既有 statistical_map（interactive 主导）错位互补；
- density_exploration：热力/连续场的探索版（scientific 色条 + 统计 +
  图表），density_map 的扩展面。
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
from app.lib.cartography.composition_templates import MapCompositionTemplate

LEGEND_FAMILY = ["legend", "categorical_legend", "continuous_colorbar"]

STATISTICAL_PACK: List[MapCompositionTemplate] = [
    composition(
        cid="composition.statistical_report",
        name="Statistical Report Map",
        description="统计专题报告版式：分级图例必备、统计面板推荐、图表可选、"
                    "图框 + A4 版式，面向 PDF 报告产物。",
        models=["administrative_choropleth", "normalized_choropleth",
                "diverging_choropleth", "aggregate_grid", "vulnerability_index"],
        outputs=["interactive", "png", "pdf"],
        profile="report",
        fallback="composition.statistical_map",
        priority=36,
        tags=["statistical", "report", "pdf"],
        slots=[
            title_slot(preferred_templates=["title/report"]),
            subtitle_slot(),
            legend_slot(LEGEND_FAMILY, preferred_templates=["legend/report"]),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            stats_slot(cardinality="recommended"),
            charts_slot(),
            map_border_slot(cardinality="required", required=True,
                            preferred_templates=["frame/report"]),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-portrait"]),
        ],
    ),
    composition(
        cid="composition.density_exploration",
        name="Density Exploration Map",
        description="密度/连续场探索版式：scientific 色条 + 统计面板 + 图表，"
                    "交互探索与 PNG 导出兼顾。",
        models=["visual_heatmap", "raster_surface", "terrain_analytical_surface",
                "spectral_index_surface"],
        outputs=["interactive", "png"],
        profile="standard",
        fallback="composition.density_map",
        priority=37,
        tags=["density", "exploration", "continuous"],
        slots=[
            title_slot(),
            legend_slot(["continuous_colorbar"],
                        cardinality="required", required=True, max_count=2,
                        zone="bottom-right",
                        preferred_templates=["colorbar/scientific"]),
            north_arrow_slot(),
            scale_bar_slot(fallback_zones=["bottom-center"]),
            attribution_slot(),
            stats_slot(),
            charts_slot(),
        ],
    ),
]
