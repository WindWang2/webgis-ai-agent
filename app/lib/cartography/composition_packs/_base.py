"""composition_packs 共享构造 helper（ADR-0101 D1）。"""
from __future__ import annotations

from typing import Any, Dict, List

from app.lib.cartography.composition_templates import (
    ComponentSlot,
    MapCompositionTemplate,
)

# 通用槽位构造捷径 —— 显式字段优先，避免 20+ 模板里重复 300 行样板。
def slot(sid: str, category: str, types: List[str], **kw: Any) -> ComponentSlot:
    return ComponentSlot(id=sid, category=category,
                         allowed_component_types=types, **kw)


def title_slot(**kw: Any) -> ComponentSlot:
    return slot("title", "annotation.title", ["title"],
                cardinality="required", required=True, min_count=1,
                position_zone="top-center", **kw)


def subtitle_slot(**kw: Any) -> ComponentSlot:
    return slot("subtitle", "annotation.subtitle", ["subtitle"],
                cardinality="optional", position_zone="top-center",
                fallback_zones=["top-left"], **kw)


def legend_slot(types: List[str], *, cardinality: str = "conditional",
                max_count: int = 2, zone: str = "bottom-left", **kw: Any) -> ComponentSlot:
    return slot("legend", "legend.graduated", types,
                cardinality=cardinality, max_count=max_count,
                position_zone=zone, bind_role="primary",
                bind_scope="all_thematic", **kw)


def north_arrow_slot(required: bool = True, **kw: Any) -> ComponentSlot:
    return slot("north_arrow", "navigation.north_arrow", ["north_arrow"],
                cardinality="required" if required else "optional",
                required=required, position_zone="top-right", **kw)


def scale_bar_slot(required: bool = True, **kw: Any) -> ComponentSlot:
    return slot("scale_bar", "navigation.scale_bar", ["scale_bar"],
                cardinality="required" if required else "optional",
                required=required, position_zone="bottom-right", **kw)


def attribution_slot(**kw: Any) -> ComponentSlot:
    return slot("attribution", "annotation.attribution", ["attribution"],
                cardinality="required", required=True,
                position_zone="bottom-left", **kw)


def map_border_slot(**kw: Any) -> ComponentSlot:
    return slot("map_border", "frame.map_border", ["map_border"],
                position_zone="none", **kw)


def export_slot(**kw: Any) -> ComponentSlot:
    return slot("export_layout", "export.page_layout", ["export_layout"],
                position_zone="none", **kw)


def stats_slot(cardinality: str = "optional", **kw: Any) -> ComponentSlot:
    return slot("statistics_panel", "analysis.statistics_panel", ["statistics_panel"],
                cardinality=cardinality,
                required=cardinality == "required",
                position_zone="top-left", **kw)


def charts_slot(max_count: int = 3, **kw: Any) -> ComponentSlot:
    return slot("chart_panel", "analysis.chart_panel", ["chart_panel"],
                cardinality="optional", max_count=max_count,
                position_zone="top-left", fallback_zones=["top-right"], **kw)


def composition(
    *, cid: str, name: str, description: str,
    models: List[str], outputs: List[str], slots: List[ComponentSlot],
    profile: str = "standard", fallback: str = "composition.standard_analysis",
    priority: int = 50, tags: List[str] | None = None,
) -> MapCompositionTemplate:
    return MapCompositionTemplate(
        id=cid, name=name, description=description,
        compatible_map_models=models, compatible_product_types=[],
        output_targets=outputs, component_slots=slots,
        layout_profile=profile,  # type: ignore[arg-type]
        collision_policy="exclusive", fallback_template=fallback,
        priority=priority, tags=tags or [],
    )


__all__ = [
    "slot", "title_slot", "subtitle_slot", "legend_slot", "north_arrow_slot",
    "scale_bar_slot", "attribution_slot", "map_border_slot", "export_slot",
    "stats_slot", "charts_slot", "composition", "ComponentSlot",
    "MapCompositionTemplate",
]
