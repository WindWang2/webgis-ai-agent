"""ComponentComposer — 将 ComponentSelection + templates 组合为 CartographyComponent 实例.

负责：
- MapModel requirements + Composition slots + Component Templates + ProductTemplate overrides
- layer binding
- layout zone assignment via slot metadata
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from app.services.gis_harness.components import CartographyComponent


def _component_id_for_type(ctype: str) -> str:
    mapping = {
        "title": "title",
        "subtitle": "subtitle",
        "legend": "legend-main",
        "continuous_colorbar": "colorbar-main",
        "categorical_legend": "legend-categorical",
        "north_arrow": "north-arrow",
        "scale_bar": "scale-bar",
        "attribution": "attribution",
        "graticule": "graticule",
        "map_border": "map-border",
        "statistics_panel": "statistics",
        "chart_panel": "chart-panel",
        "export_layout": "export-layout",
        "annotation": "annotation",
        "inset_map": "inset-map",
    }
    return mapping.get(ctype, ctype)


def _default_position_for_type(ctype: str) -> str:
    defaults = {
        "title": "top-center",
        "subtitle": "top-center",
        "legend": "bottom-left",
        "continuous_colorbar": "bottom-right",
        "categorical_legend": "bottom-left",
        "north_arrow": "top-right",
        "scale_bar": "bottom-right",
        "attribution": "bottom-left",
        "graticule": "none",
        "map_border": "none",
        "statistics_panel": "top-left",
        "chart_panel": "top-left",
        "export_layout": "none",
        "annotation": "top-left",
        "inset_map": "top-right",
    }
    return defaults.get(ctype, "none")


_PRIORITY = {
    "title": 10, "subtitle": 11, "continuous_colorbar": 15, "legend": 16,
    "categorical_legend": 17, "scale_bar": 20, "north_arrow": 30,
    "statistics_panel": 40, "chart_panel": 41, "attribution": 50,
    "graticule": 60, "map_border": 70, "export_layout": 90, "inset_map": 65,
}


class ComponentComposer:
    """确定性组件装配器."""

    def compose(
        self,
        selection: Any,
        *,
        title_text: str = "",
        subtitle_text: str = "",
        attribution_text: str = "© OpenStreetMap contributors",
        layer_bindings: Optional[Dict[str, str]] = None,
        composition_template_id: str = "",
        overrides: Optional[Dict[str, Any]] = None,
    ) -> List[CartographyComponent]:
        from app.lib.cartography.component_registry import get_component_registry
        from app.lib.cartography.component_templates import get_component_template_registry
        from app.lib.cartography.composition_templates import get_composition_template_registry

        comp_reg = get_component_registry()
        tmpl_reg = get_component_template_registry()
        compo_reg = get_composition_template_registry()

        layer_bindings = layer_bindings or {}
        overrides = overrides or {}

        # slot position lookup from composition
        slot_positions: Dict[str, str] = {}
        if composition_template_id:
            compo = compo_reg.get(composition_template_id)
            if compo:
                for slot in compo.component_slots:
                    for ctype in slot.allowed_component_types:
                        if ctype not in slot_positions:
                            slot_positions[ctype] = slot.position_zone
        elif hasattr(selection, "composition_template_id") and selection.composition_template_id:
            compo = compo_reg.get(selection.composition_template_id)
            if compo:
                for slot in compo.component_slots:
                    for ctype in slot.allowed_component_types:
                        if ctype not in slot_positions:
                            slot_positions[ctype] = slot.position_zone

        components: List[CartographyComponent] = []

        selected_types: List[str] = []
        if hasattr(selection, "selected"):
            selected_types = list(selection.selected)
        elif isinstance(selection, list):
            selected_types = list(selection)

        template_map: Dict[str, str] = {}
        if hasattr(selection, "component_templates"):
            template_map = dict(selection.component_templates)

        for ctype in selected_types:
            if ctype in overrides and overrides[ctype] is False:
                continue

            desc = comp_reg.get(ctype) or comp_reg.get_by_type(ctype)
            category = desc.category if desc else ""
            variant = ""
            template_id = template_map.get(ctype, "")
            # #1075(D-11): position/priority 以描述符为权威（缺省回退表）——
            # 此前只查硬编码表，annotation 的描述符 priority=55 被静默降为 50。
            if desc is not None and desc.default_position:
                position = slot_positions.get(ctype, desc.default_position)
            else:
                position = slot_positions.get(ctype, _default_position_for_type(ctype))
            if desc is not None and desc.priority is not None:
                priority = int(desc.priority)
            else:
                priority = _PRIORITY.get(ctype, 50)

            # resolve template variant / options
            options: Dict[str, Any] = {}
            style: Dict[str, Any] = {}
            if template_id:
                tpl = tmpl_reg.get(template_id)
                if tpl:
                    variant = tpl.variant
                    options = dict(tpl.default_options)
                    style = dict(tpl.default_style)

            # per-type text / binding
            if ctype == "title":
                options["text"] = title_text or options.get("text", "专题地图")
            elif ctype == "subtitle" and subtitle_text:
                options["text"] = subtitle_text
            elif ctype == "attribution":
                options["text"] = attribution_text
            elif ctype in ("legend", "continuous_colorbar", "categorical_legend"):
                if ctype in layer_bindings:
                    options["layerId"] = layer_bindings[ctype]
                elif "primary" in layer_bindings:
                    options["layerId"] = layer_bindings["primary"]
                if title_text and not options.get("title"):
                    # legend title defaults to short map title
                    pass

            # apply overrides
            if ctype in overrides and isinstance(overrides[ctype], dict):
                for k, v in overrides[ctype].items():
                    if k == "position":
                        position = v
                    elif k == "variant":
                        variant = v
                        options["variant"] = v
                    else:
                        options[k] = v

            comp = CartographyComponent(
                id=_component_id_for_type(ctype),
                type=ctype,  # type: ignore[arg-type]
                position=position,  # type: ignore[arg-type]
                priority=priority,
                style=style,
                options=options,
                category=category,
                variant=variant,
                templateId=template_id,
            )
            components.append(comp)

        components.sort(key=lambda c: (c.priority, c.id))
        return components


_composer: Optional[ComponentComposer] = None


def get_component_composer() -> ComponentComposer:
    global _composer
    if _composer is None:
        _composer = ComponentComposer()
    return _composer


__all__ = ["ComponentComposer", "get_component_composer"]
