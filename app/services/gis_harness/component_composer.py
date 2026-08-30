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


# 实例默认值（priority / default_position）单一权威是组件描述符目录
# （component_registry.py descriptors）；_FALLBACK_* 仅在目录不可用时兜底，
# 数值与 descriptor seed 保持一致（composition 校验测试锁定两者不漂移）。
_FALLBACK_PRIORITY = 50
_FALLBACK_POSITION = "none"


def _descriptor_defaults(ctype: str, comp_reg) -> tuple:
    """从 descriptor 目录取 (priority, default_position)。"""
    desc = comp_reg.get(ctype) or comp_reg.get_by_type(ctype)
    if desc is None:
        return _FALLBACK_PRIORITY, _FALLBACK_POSITION
    return desc.priority, desc.default_position or _FALLBACK_POSITION


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
        layer_model_ids: Optional[Dict[str, str]] = None,
    ) -> List[CartographyComponent]:
        """layer_bindings：role → layer_id（primary 必备；secondary/reference
        为可选主题层）。``layer_model_ids``：role → 该层 MapModel id —— v2
        图例族按层选型（heatmap 层→colorbar、choropleth 层→legend）。"""
        from app.lib.cartography.component_registry import get_component_registry
        from app.lib.cartography.component_templates import get_component_template_registry
        from app.lib.cartography.composition_templates import get_composition_template_registry

        comp_reg = get_component_registry()
        tmpl_reg = get_component_template_registry()
        compo_reg = get_composition_template_registry()

        layer_bindings = layer_bindings or {}
        overrides = overrides or {}
        layer_model_ids = layer_model_ids or {}

        # slot position lookup from composition
        slot_positions: Dict[str, str] = {}
        slot_by_type: Dict[str, Any] = {}
        if composition_template_id:
            compo = compo_reg.get(composition_template_id)
        elif hasattr(selection, "composition_template_id") and selection.composition_template_id:
            compo = compo_reg.get(selection.composition_template_id)
        else:
            compo = None
        if compo:
            for slot in compo.component_slots:
                for ctype in slot.allowed_component_types:
                    if ctype not in slot_positions:
                        slot_positions[ctype] = slot.position_zone
                    slot_by_type.setdefault(ctype, slot)

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

            slot = slot_by_type.get(ctype)
            # v2：图例族 all_thematic 槽位 → 按主题层逐层展开实例
            #（heatmap 主层→colorbar、choropleth 参考层→legend，各绑各层）。
            if (
                ctype in ("legend", "continuous_colorbar", "categorical_legend")
                and slot is not None
                and getattr(slot, "bind_scope", "primary") == "all_thematic"
                and int(getattr(slot, "max_count", 1) or 1) >= 2
                and layer_bindings
            ):
                components.extend(self._compose_per_layer_legends(
                    slot=slot,
                    primary_type=ctype,
                    layer_bindings=layer_bindings,
                    layer_model_ids=layer_model_ids,
                    comp_reg=comp_reg,
                    tmpl_reg=tmpl_reg,
                    template_map=template_map,
                    overrides=overrides,
                ))
                continue

            desc = comp_reg.get(ctype) or comp_reg.get_by_type(ctype)
            category = desc.category if desc else ""
            variant = ""
            template_id = template_map.get(ctype, "")
            desc_priority, desc_position = _descriptor_defaults(ctype, comp_reg)
            if ctype in slot_positions:
                position = slot_positions[ctype]
            else:
                position = desc_position
            priority = desc_priority

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

    # 图例族 per-role 实例 id：primary 层保留旧固定 id（向后兼容既有会话
    # 与测试），其余角色 id 内嵌角色名（legend-secondary / colorbar-reference）。
    _LEGEND_PRIMARY_IDS = {
        "legend": "legend-main",
        "continuous_colorbar": "colorbar-main",
        "categorical_legend": "legend-categorical",
    }

    def _compose_per_layer_legends(
        self,
        *,
        slot,
        primary_type: str,
        layer_bindings: Dict[str, str],
        layer_model_ids: Dict[str, str],
        comp_reg,
        tmpl_reg,
        template_map: Dict[str, str],
        overrides: Dict[str, Any],
    ) -> List[CartographyComponent]:
        from app.lib.cartography.component_registry import get_component_registry  # noqa: F401

        allowed = list(slot.allowed_component_types) or [primary_type]
        out: List[CartographyComponent] = []
        for role in sorted(layer_bindings):  # 确定性：角色字典序
            layer_id = layer_bindings[role]
            if not layer_id:
                continue
            layer_model = str(layer_model_ids.get(layer_id) or "")
            chosen = self._legend_type_for_layer(
                allowed, layer_model, comp_reg, primary_type,
                fallback_allowed=(role == "primary"),
            )
            if chosen is None:
                continue  # 该层无兼容图例类型（如 simple point 层）→ 如实跳过
            desc = comp_reg.get(chosen) or comp_reg.get_by_type(chosen)
            desc_priority, desc_position = _descriptor_defaults(chosen, comp_reg)
            position = slot.position_zone if slot.position_zone else desc_position
            template_id = template_map.get(chosen, "")
            variant = ""
            options: Dict[str, Any] = {"layerId": layer_id}
            style: Dict[str, Any] = {}
            if template_id:
                tpl = tmpl_reg.get(template_id)
                if tpl:
                    variant = tpl.variant
                    options = {"layerId": layer_id, **dict(tpl.default_options)}
                    style = dict(tpl.default_style)
            if role == "primary":
                comp_id = self._LEGEND_PRIMARY_IDS.get(chosen, f"{chosen.replace('_', '-')}-primary")
            else:
                comp_id = f"{chosen.replace('_', '-')}-{role}"
            if chosen in overrides and isinstance(overrides[chosen], dict):
                for k, v in overrides[chosen].items():
                    if k == "position":
                        position = v
                    elif k == "variant":
                        variant = v
                        options["variant"] = v
                    elif k != "layerId":
                        options[k] = v
            out.append(CartographyComponent(
                id=comp_id,
                type=chosen,  # type: ignore[arg-type]
                position=position,  # type: ignore[arg-type]
                priority=desc_priority,
                style=style,
                options=options,
                category=desc.category if desc else "",
                variant=variant,
                templateId=template_id,
            ))
        return out

    def _legend_type_for_layer(
        self, allowed: List[str], layer_model: str, comp_reg, fallback: str,
        *, fallback_allowed: bool = True,
    ) -> "str | None":
        """按绑定层 MapModel 选图例类型；无兼容类型 → None（跳过该层）。

        layer_model 未知时：primary 层回退 fallback（主层已选型 —— 行为
        向后兼容）；非 primary 层不猜（给边界参考层挂 colorbar 是错误
        语义）→ 跳过。
        """
        if not layer_model:
            return fallback if fallback_allowed else None
        for cand in allowed:
            d = comp_reg.get(cand) or comp_reg.get_by_type(cand)
            if d and d.compatible_map_models and layer_model in d.compatible_map_models:
                return cand
        return None


_composer: Optional[ComponentComposer] = None


def get_component_composer() -> ComponentComposer:
    global _composer
    if _composer is None:
        _composer = ComponentComposer()
    return _composer


__all__ = ["ComponentComposer", "get_component_composer"]
