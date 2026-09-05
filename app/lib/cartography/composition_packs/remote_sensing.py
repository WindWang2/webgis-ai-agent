"""遥感 / SAR 域组合包（ADR-0101 §B2）。

- rs_index_report：光谱指数面 + colorbar + 出版版式（native 模型即刻可用）；
- rs_classification：分级栅格（classified_raster 为 planned —— 模板先行
  登记，模型渲染链路落地后即插即用）；
- sar_change_report：SAR 变化（planned 模型前瞻登记，同上）。
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

REMOTE_SENSING_PACK: List[MapCompositionTemplate] = [
    composition(
        cid="composition.rs_index_report",
        name="Remote Sensing Index Report",
        description="遥感指数报告版式：指数面 + colorbar + 经纬网 + 图框 + "
                    "A4 横版（remote_sensing_map 的报告化扩展）。",
        models=["spectral_index_surface", "raster_surface"],
        outputs=["png", "pdf", "svg"],
        profile="academic",
        fallback="composition.remote_sensing_map",
        priority=44,
        tags=["remote-sensing", "index", "report"],
        slots=[
            title_slot(preferred_templates=["title/academic"]),
            subtitle_slot(),
            legend_slot(["continuous_colorbar"],
                        cardinality="required", required=True, max_count=2,
                        zone="bottom-right",
                        preferred_templates=["colorbar/scientific"]),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            ComponentSlot(id="graticule", category="navigation.graticule",
                          allowed_component_types=["graticule"],
                          cardinality="optional", position_zone="none"),
            charts_slot(max_count=2),
            map_border_slot(cardinality="required", required=True,
                            preferred_templates=["frame/academic"]),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-landscape"]),
        ],
    ),
    composition(
        cid="composition.rs_classification",
        name="Remote Sensing Classification",
        description="遥感分类版式：分级栅格 + 分类图例 + 出版版式。"
                    "classified_raster 为 planned —— 模板先行登记。",
        models=["classified_raster"],
        outputs=["png", "pdf"],
        profile="academic",
        fallback="composition.rs_index_report",
        priority=45,
        tags=["remote-sensing", "classification", "planned-model"],
        slots=[
            title_slot(),
            legend_slot(["legend", "categorical_legend"],
                        cardinality="required", required=True),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            ComponentSlot(id="graticule", category="navigation.graticule",
                          allowed_component_types=["graticule"],
                          cardinality="optional", position_zone="none"),
            map_border_slot(cardinality="required", required=True,
                            preferred_templates=["frame/academic"]),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-landscape"]),
        ],
    ),
    composition(
        cid="composition.sar_change_report",
        name="SAR Change Report",
        description="SAR 变化报告版式：发散变化面 + 发散图例 + 出版版式。"
                    "sar_change_detection 为 planned —— 模板先行登记。",
        models=["sar_change_detection", "sar_intensity_surface"],
        outputs=["png", "pdf"],
        profile="academic",
        fallback="composition.rs_index_report",
        priority=46,
        tags=["sar", "change", "planned-model"],
        slots=[
            title_slot(),
            subtitle_slot(),
            legend_slot(["legend", "continuous_colorbar"],
                        cardinality="required", required=True),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            ComponentSlot(id="graticule", category="navigation.graticule",
                          allowed_component_types=["graticule"],
                          cardinality="optional", position_zone="none"),
            map_border_slot(cardinality="required", required=True,
                            preferred_templates=["frame/academic"]),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-landscape"]),
        ],
    ),
]
