"""网络 / 交通 / 可达性域组合包（ADR-0101 §B2）。

- flow_map_report：OD 流向 + 行政参考语境的报告版式；
- service_area_report：服务区/等时圈叠加的可达性报告版式。
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

DISCRETE_LEGEND = ["legend", "categorical_legend"]

NETWORK_TRANSPORT_PACK: List[MapCompositionTemplate] = [
    composition(
        cid="composition.flow_map_report",
        name="Flow Map Report",
        description="OD 流向报告版式：流向图例 + 统计面板 + 图框，"
                    "出行/迁徙/物流流量的报告表达。",
        models=["flow_od_arc", "graduated_line"],
        outputs=["interactive", "png", "pdf"],
        profile="report",
        fallback="composition.standard_analysis",
        priority=40,
        tags=["network", "flow", "transport"],
        slots=[
            title_slot(),
            subtitle_slot(),
            legend_slot(DISCRETE_LEGEND, cardinality="required", required=True),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            stats_slot(cardinality="recommended"),
            charts_slot(),
            map_border_slot(cardinality="optional"),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-landscape"]),
        ],
    ),
    composition(
        cid="composition.service_area_report",
        name="Service Area Report",
        description="服务区/可达性报告版式：分类图例 + 统计面板 + A4 版式，"
                    "回答『N 分钟能到哪』的产品化表达。",
        models=["service_area_overlay", "proximity_overlay"],
        outputs=["interactive", "png", "pdf"],
        profile="report",
        fallback="composition.standard_analysis",
        priority=41,
        tags=["network", "accessibility", "service-area"],
        slots=[
            title_slot(),
            legend_slot(["categorical_legend", "legend"],
                        cardinality="required", required=True),
            north_arrow_slot(),
            scale_bar_slot(),
            attribution_slot(),
            stats_slot(cardinality="recommended"),
            charts_slot(),
            export_slot(cardinality="required", required=True,
                        preferred_templates=["export-layout/A4-portrait"]),
        ],
    ),
]
