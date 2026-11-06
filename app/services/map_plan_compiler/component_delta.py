"""Shared component delta semantics for compiler and lock obligations."""
from __future__ import annotations

from typing import Any, Dict, Optional

_DEFAULT_COMPONENT_ZONES: Dict[str, str] = {
    "title": "top-center",
    "subtitle": "top-center",
    "legend": "top-left",
    "continuous_colorbar": "top-left",
    "scale_bar": "bottom-left",
    "north_arrow": "top-right",
    "attribution": "bottom-right",
}

def _paint_delta(desired: Dict[str, Any], current: Dict[str, Any]) -> Dict[str, Any]:
    """paint 最小 delta：desired 中与 current 顶层键不同的条目（新键 +
    值变键；current 独有键**不触碰**）。"""
    delta: Dict[str, Any] = {}
    for key, value in (desired or {}).items():
        if key not in (current or {}) or current.get(key) != value:
            delta[str(key)[:48]] = value
    return delta

def _component_position(ci: Any) -> str:
    return ci.pinned_zone or _DEFAULT_COMPONENT_ZONES.get(ci.component_type, "none")

def _component_delta(ci: Any, current: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """组件最小 patch 字段集（只含变化的键；无变化 = 空 dict）。"""
    delta: Dict[str, Any] = {}
    if current is None:
        delta["upsert"] = True
        delta["component_type"] = ci.component_type
        delta["position"] = _component_position(ci)
        delta["enabled"] = True
        options = dict(ci.options or {})
        if ci.title:
            options.setdefault("title", ci.title)
        if ci.bound_layer_id:
            options.setdefault("layerId", ci.bound_layer_id)
        if options:
            delta["options"] = options
        return delta
    cur_options = current.get("options") if isinstance(current.get("options"), dict) else {}
    desired_enabled = False if ci.action == "hide" else True
    if bool(current.get("enabled", True)) != desired_enabled:
        delta["enabled"] = desired_enabled
    desired_options: Dict[str, Any] = dict(ci.options or {})
    if ci.title:
        desired_options.setdefault("title", ci.title)
    if ci.bound_layer_id:
        # binding 型组件（chart_panel/table_panel）契约走 options.layerId
        # （app/tools/chart.py）；intent 绑定 = 期望绑定（review P2-1）。
        desired_options.setdefault("layerId", ci.bound_layer_id)
    option_delta = _paint_delta(desired_options, cur_options or {})
    if option_delta:
        delta["options"] = option_delta
    desired_position = _component_position(ci)
    if desired_position != "none" and current.get("position") != desired_position:
        delta["position"] = desired_position
    return delta
