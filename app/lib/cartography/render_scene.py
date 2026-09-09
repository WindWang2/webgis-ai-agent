"""Canonical Render Scene — backend projection (V6, ADR-0120 W4).

前端 ``describeRenderScene``（frontend/lib/map-kit/render-scene.ts）与
``resolveMapComponents``（frontend/lib/map-components/resolve-components.ts）
的 Python 忠实镜像。五条渲染/导出链（live / PNG / 前端 SVG / 后端孪生 SVG /
publication PDF）共同消费同一 scene 语义 —— 跨语言 parity 由共享 golden
fixtures 锁定（tests/cartography/golden_corpus/render_scene/）。

契约：纯函数、确定性排序、无副作用。visibility 语义与孪生编译器同口径
（``layout.visibility != "none"`` 且顶层 ``visible is not False`` —— 脏值
``"false"`` 字符串照常渲染，R1-C2 一致性）。
"""
from __future__ import annotations

from dataclasses import dataclass, field as _dc_field
from typing import Any, Dict, List, Optional

# ── 组件解析（resolve-components.ts 镜像）─────────────────────────────────

CHROME_ANCHORS = (
    "top-left",
    "top-center",
    "top-right",
    "bottom-left",
    "bottom-center",
    "bottom-right",
    "none",
)

#: 类型默认槽位 —— 与前端 DEFAULT_COMPONENT_ANCHOR 同表（单一默认值源，
#: 双侧由 golden fixtures 锁定；改表必须双侧同步）。
DEFAULT_COMPONENT_ANCHOR: Dict[str, str] = {
    "title": "top-center",
    "subtitle": "top-center",
    "north_arrow": "top-right",
    "scale_bar": "bottom-right",
    "attribution": "bottom-left",
    "continuous_colorbar": "bottom-right",
    "legend": "bottom-left",
    "categorical_legend": "bottom-left",
    "annotation": "top-left",
    "statistics_panel": "top-left",
    "chart_panel": "top-left",
    "inset_map": "top-right",
    "methodology_note": "bottom-left",
    "uncertainty_panel": "bottom-right",
    "decision_panel": "top-left",
    "table_panel": "bottom-right",
    "map_border": "none",
    "graticule": "none",
}

_VALID_ANCHORS = set(CHROME_ANCHORS)


def _num(val: Any) -> Optional[float]:
    return val if isinstance(val, (int, float)) and not isinstance(val, bool) else None


@dataclass
class ResolvedComponent:
    """resolveMapComponent 的镜像产物。"""

    id: str
    type: str
    enabled: bool
    anchor: str
    floating: bool
    text: str
    variant: str
    layer_id: str
    collapsed: bool
    options: Dict[str, Any]
    floating_rect: Optional[Dict[str, Any]] = None
    #: 原始组件（style/options 原样携带，供 publication 消费）。
    raw: Dict[str, Any] = _dc_field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "enabled": self.enabled,
            "anchor": self.anchor,
            "floating": self.floating,
            "text": self.text,
            "variant": self.variant,
            "layerId": self.layer_id,
            "collapsed": self.collapsed,
            "options": self.options,
        }
        if self.floating_rect is not None:
            out["floatingRect"] = self.floating_rect
        return out


def resolve_component(component: Dict[str, Any]) -> Optional[ResolvedComponent]:
    """单组件解析（placement > position > 类型默认）。"""
    if not isinstance(component, dict) or not isinstance(component.get("type"), str):
        return None
    placement = component.get("placement")
    if not isinstance(placement, dict):
        placement = {}
    floating = placement.get("mode") == "floating"
    floating_rect: Optional[Dict[str, Any]] = None
    if floating:
        floating_rect = {
            "x": _num(placement.get("x")) or 0.0,
            "y": _num(placement.get("y")) or 0.0,
            "width": _num(placement.get("width")),
            "height": _num(placement.get("height")),
            "zIndex": _num(placement.get("zIndex")) if _num(placement.get("zIndex")) is not None else 40.0,
            "collapsed": placement.get("collapsed") == True,  # noqa: E712
        }

    anchor_candidate = (
        placement.get("anchor")
        if placement.get("mode") == "anchor" and isinstance(placement.get("anchor"), str)
        else component.get("position")
    )
    anchor = (
        anchor_candidate
        if isinstance(anchor_candidate, str) and anchor_candidate in _VALID_ANCHORS
        else DEFAULT_COMPONENT_ANCHOR.get(component["type"], "none")
    )

    options = component.get("options") if isinstance(component.get("options"), dict) else {}
    text_opt = options.get("text")
    variant_opt = options.get("variant")
    layer_id_opt = options.get("layerId")
    variant_field = component.get("variant")

    return ResolvedComponent(
        id=component.get("id") if isinstance(component.get("id"), str) else "",
        type=component["type"],
        enabled=component.get("enabled") is not False,
        anchor=anchor,
        floating=floating,
        text=text_opt if isinstance(text_opt, str) else "",
        variant=(
            variant_opt
            if isinstance(variant_opt, str) and variant_opt
            else (variant_field if isinstance(variant_field, str) and variant_field else "")
        ),
        layer_id=layer_id_opt if isinstance(layer_id_opt, str) else "",
        collapsed=placement.get("collapsed") is True,
        options=options,
        floating_rect=floating_rect,
        raw=component,
    )


def resolve_components(spec: Optional[Dict[str, Any]]) -> List[ResolvedComponent]:
    """解析整个 spec 的组件列表（enabled 过滤留给消费端）。"""
    if not isinstance(spec, dict):
        return []
    layout = spec.get("layout")
    raw = layout.get("components") if isinstance(layout, dict) else None
    if not isinstance(raw, list):
        return []
    resolved = [resolve_component(c) for c in raw]
    return [r for r in resolved if r is not None]


# ── 图例 oracle 推导（render-scene.ts legendEntryCount 同口径）────────────
# W5 起完整条目模型由 derive_legend_items 提供；本函数保留 oracle 数量口径
# 与前端 describeRenderScene parity（golden fixtures 锁定）。


def legend_oracle(legend_spec: Any) -> Dict[str, Any]:
    """(entryCount, hasNodata, title) —— 前端 oracle 同口径。"""
    if not isinstance(legend_spec, dict):
        return {"entryCount": 0, "hasNodata": False, "title": ""}
    count = 0
    if legend_spec.get("type") == "categorical":
        categories = legend_spec.get("categories")
        count = len(categories) if isinstance(categories, list) else 0
    elif legend_spec.get("type") == "graduated":
        breaks = legend_spec.get("breaks")
        palette = legend_spec.get("palette_colors")
        breaks_len = len(breaks) if isinstance(breaks, list) else 0
        palette_len = len(palette) if isinstance(palette, list) else 0
        count = min(max(breaks_len - 1, 0), palette_len)
    elif isinstance(legend_spec.get("min"), (int, float)) and isinstance(
        legend_spec.get("max"), (int, float)
    ):
        count = 3
    nodata = legend_spec.get("nodata")
    has_nodata = isinstance(nodata, dict) and bool(nodata.get("color"))
    if has_nodata:
        count += 1
    title = legend_spec.get("title")
    if not isinstance(title, str) or not title:
        fld = legend_spec.get("field")
        title = f"字段: {fld}" if isinstance(fld, str) and fld else "图例"
    return {"entryCount": count, "hasNodata": has_nodata, "title": title}


# ── scene 快照（describeRenderScene 镜像）────────────────────────────────


@dataclass
class RenderSceneLayer:
    id: str
    visible: bool
    has_legend_spec: bool
    has_label: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "visible": self.visible,
            "hasLegendSpec": self.has_legend_spec,
            "hasLabel": self.has_label,
        }


@dataclass
class RenderSceneLegend:
    layer_id: str
    title: str
    entry_count: int
    has_nodata: bool

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layerId": self.layer_id,
            "title": self.title,
            "entryCount": self.entry_count,
            "hasNodata": self.has_nodata,
        }


@dataclass
class RenderSceneSnapshot:
    layers: List[RenderSceneLayer]
    components: List[Dict[str, Any]]
    legends: List[RenderSceneLegend]
    degradation_codes: List[str]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "layers": [x.to_dict() for x in self.layers],
            "components": self.components,
            "legends": [x.to_dict() for x in self.legends],
            "degradationCodes": self.degradation_codes,
        }

    def to_json(self, **kwargs: Any) -> str:
        import json

        return json.dumps(self.to_dict(), ensure_ascii=False, **kwargs)


def _layer_visible(layer: Dict[str, Any]) -> bool:
    layout = layer.get("layout")
    visibility = layout.get("visibility") if isinstance(layout, dict) else None
    return visibility != "none" and layer.get("visible") is not False


def describe_render_scene(
    spec: Optional[Dict[str, Any]],
    degradations: Optional[List[Dict[str, Any]]] = None,
) -> RenderSceneSnapshot:
    """MapSpec dict → 语义快照（与前端 describeRenderScene 同投影）。

    组件快照键序对齐前端（id/type/position/variant/enabled/collapsed），
    排序 id 字典序 → type 字典序（localeCompare 的确定性近似：字典序；
    fixture 语料使用 ASCII id 规避 locale 差异）。
    """
    spec_layers = (
        spec.get("layers") if isinstance(spec, dict) and isinstance(spec.get("layers"), list) else []
    )
    layers: List[RenderSceneLayer] = []
    for layer_raw in spec_layers:
        if not isinstance(layer_raw, dict) or not isinstance(layer_raw.get("id"), str):
            continue
        layout = layer_raw.get("layout") if isinstance(layer_raw.get("layout"), dict) else {}
        layers.append(
            RenderSceneLayer(
                id=layer_raw["id"],
                visible=_layer_visible(layer_raw),
                has_legend_spec=isinstance(layer_raw.get("legend_spec"), dict),
                has_label=bool(layer_raw.get("label")) or bool(layout.get("labelField")),
            )
        )
    layers.sort(key=lambda x: x.id)

    components = [
        {
            "id": c.id,
            "type": c.type,
            "position": c.anchor,
            "variant": c.variant,
            "enabled": c.enabled,
            "collapsed": c.collapsed,
        }
        for c in sorted(resolve_components(spec), key=lambda c: (c.id, c.type))
    ]

    legends: List[RenderSceneLegend] = []
    for layer_raw in spec_layers:
        if not isinstance(layer_raw, dict) or not isinstance(layer_raw.get("id"), str):
            continue
        if not isinstance(layer_raw.get("legend_spec"), dict):
            continue
        oracle = legend_oracle(layer_raw["legend_spec"])
        legends.append(
            RenderSceneLegend(
                layer_id=layer_raw["id"],
                title=oracle["title"],
                entry_count=oracle["entryCount"],
                has_nodata=oracle["hasNodata"],
            )
        )
    legends.sort(key=lambda x: x.layer_id)

    codes = sorted({d.get("code") for d in (degradations or []) if isinstance(d, dict) and isinstance(d.get("code"), str)})

    return RenderSceneSnapshot(
        layers=layers,
        components=components,
        legends=legends,
        degradation_codes=codes,
    )
