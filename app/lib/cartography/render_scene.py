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


# ── 图例条目单源（frontend/lib/map-kit/legend-model.ts 的 Python 镜像）───
# W5 收敛：数量/标题/条目推导唯一权威；跨语言 parity 由共享 fixtures 锁定。


def _significant(n: float) -> str:
    """legend-card.ts significant() 同口径。"""
    abs_n = abs(n)
    decimals = 0 if abs_n >= 100 else 1 if abs_n >= 10 else 2 if abs_n >= 1 else 3
    out = float(f"{n:.{decimals}f}")
    if out == 0 and n != 0:
        exp_str = f"{n:.1e}"
        return exp_str.replace("e-0", "e-").replace("e+0", "e+")
    if out == int(out):
        return str(int(out))
    return str(out)


def format_legend_value(n: Any) -> str:
    """legend-card.ts formatLegendValue() 的 Python 镜像（zh-CN 分组）。

    数值字符串按 TS ``Number()`` 口径 coerce（"5" → 5）；不可 coerce → "—"
    （TS NaN → '—' 同语义，R1-Min6 脏输入对齐）。指数格式去前导零
    （4.0e-04 → 4.0e-4，与 JS toExponential 同形）。
    """
    import math

    try:
        n_val = float(n)
    except (TypeError, ValueError):
        return "—"
    if not math.isfinite(n_val):
        return "—"
    n = n_val
    abs_n = abs(n)
    if abs_n >= 1_000_000:
        return f"{_significant(n / 1_000_000)}M"
    if abs_n >= 10_000:
        return f"{_significant(n / 1_000)}k"
    if float(n).is_integer():
        return f"{int(n):,}"
    return _significant(n)


def _pick_continuous_color(colors: List[Any], t: float) -> str:
    if not colors:
        return "#888"
    idx = min(len(colors) - 1, round(t * (len(colors) - 1)))
    val = colors[idx]
    return val if isinstance(val, str) and val else "#888"


def derive_legend_items(legend_spec: Any) -> Optional[Dict[str, Any]]:
    """legend_spec → 图例条目模型（前端 deriveLegendModel 同语义）。

    返回 None = 无可呈现条目（消费端不画空卡）；bivariate kind 只承载
    nodata 语义（专用色阵渲染器消费）。
    """
    if not isinstance(legend_spec, dict):
        return None
    spec = legend_spec
    entries: List[Dict[str, str]] = []
    nodata = spec.get("nodata")
    nodata_entry: Optional[Dict[str, str]] = None
    if isinstance(nodata, dict) and nodata.get("color"):
        label = nodata.get("label")
        nodata_entry = {
            "label": label if isinstance(label, str) and label else "无数据",
            "color": nodata["color"],
            "kind": "nodata",
        }

    def _title() -> str:
        title = spec.get("title")
        if isinstance(title, str) and title:
            return title
        fld = spec.get("field")
        return f"字段: {fld}" if isinstance(fld, str) and fld else "图例"

    def _finish(kind: str, allow_empty: bool = False) -> Optional[Dict[str, Any]]:
        if nodata_entry:
            entries.append(nodata_entry)
        if not entries and not allow_empty:
            return None  # 无可呈现条目（前端同规则：不画空卡）
        return {
            "kind": kind,
            "title": _title(),
            "entries": entries,
            "hasNodata": nodata_entry is not None,
        }

    legacy = spec.get("entries")
    if isinstance(legacy, list):
        for e in legacy:
            if isinstance(e, dict) and isinstance(e.get("color"), str) and e.get("color"):
                label = e.get("label")
                entries.append({
                    "color": e["color"],
                    "label": str(label) if label is not None and str(label).strip() != "" else "",
                    "kind": "class",
                })
        return _finish(str(spec.get("type", "categorical")))

    spec_type = spec.get("type")
    if spec_type == "bivariate":
        return _finish("bivariate", allow_empty=True)

    if spec_type == "categorical":
        for c in spec.get("categories") or []:
            # 非 dict 条目不跳过（R1-Min6：与 TS 同口径 —— #888 + 空标签）
            c = c if isinstance(c, dict) else {}
            label = c.get("label")
            color = c.get("color")
            key = c.get("key", "")
            if key is None:
                key_str = ""
            elif key is True:
                key_str = "true"
            elif key is False:
                key_str = "false"
            else:
                key_str = str(key)
            entries.append({
                "label": str(label) if label is not None and str(label).strip() != "" else key_str,
                "color": color if isinstance(color, str) and color else "#888",
                "kind": "class",
            })
        return _finish("categorical")

    if spec_type == "graduated":
        breaks = spec.get("breaks") or []
        colors = spec.get("palette_colors") or []
        # breaks 不预过滤（R1-Min6：与 TS 同口径 —— 非数值在标签层折为 "—"）
        n = min(max(len(breaks) - 1, 0), len(colors))
        labels = spec.get("labels")
        for i in range(n):
            override = labels[i] if isinstance(labels, list) and i < len(labels) else None
            entries.append({
                "label": str(override) if override is not None and str(override).strip() != ""
                else f"{format_legend_value(breaks[i])} – {format_legend_value(breaks[i + 1])}",
                "color": colors[i] if isinstance(colors[i], str) and colors[i] else "#888",
                "kind": "class",
            })
        return _finish("graduated")

    # continuous / divergent：min/mid/max 三读数（min/max 缺失 → 仅 nodata）
    mn, mx = spec.get("min"), spec.get("max")
    mn_num = isinstance(mn, (int, float)) and not isinstance(mn, bool)
    mx_num = isinstance(mx, (int, float)) and not isinstance(mx, bool)
    if mn_num and mx_num:
        colors = spec.get("palette_colors") or []
        mid = (mn + mx) / 2
        entries.append({"label": format_legend_value(mn), "color": _pick_continuous_color(colors, 0), "kind": "class"})
        entries.append({"label": format_legend_value(mid), "color": _pick_continuous_color(colors, 0.5), "kind": "class"})
        entries.append({"label": format_legend_value(mx), "color": _pick_continuous_color(colors, 1), "kind": "class"})
    return _finish("continuous")


def derive_legend_title(legend_spec: Any) -> str:
    """标题兜底单源（模型 None 时 oracle/快照仍需标题语义）。"""
    if not isinstance(legend_spec, dict):
        return "图例"
    title = legend_spec.get("title")
    if isinstance(title, str) and title:
        return title
    fld = legend_spec.get("field")
    return f"字段: {fld}" if isinstance(fld, str) and fld else "图例"


def legend_oracle(legend_spec: Any) -> Dict[str, Any]:
    """(entryCount, hasNodata, title) —— 前端 oracle 同口径（W5 起由
    derive_legend_items 单源派生）。"""
    model = derive_legend_items(legend_spec)
    if model is None:
        return {
            "entryCount": 0,
            "hasNodata": False,
            "title": derive_legend_title(legend_spec),
        }
    return {
        "entryCount": len(model["entries"]),
        "hasNodata": model["hasNodata"],
        "title": model["title"],
    }


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
