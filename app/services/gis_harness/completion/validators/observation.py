"""Completion-time 观察/模型审计 validator（V4 Wave 7 — ADR-0104）。

审计 06 确认的两个 completion-time 缺口（desired-state 面，warning 级
增值披露 —— 不推翻 status/verdict 语义）：

1. **地图模型兼容性**只在组合期（component_resolver.component slot
   过滤）执行；组合被绕过（手工 webgis_component_update / 图层手工
   改动）后完成期从不复核。本 validator 对照 planned layer 的
   cartography 模型（geometry_layer_types ∪ 计划类型）复核 composed
   spec 的 layer type。
2. **全透明结果层**：生产观察面（render observation）是结构性的，
   看不见 paint；像素级空白画布验证只在 headless agent 工具里
   （heuristic_visual_proxies，明确不进判定门）。这里做诚实的结构
   代理：结果层（primary/secondary/result）enabled+visible 但 paint
   不透明度为 0 → warning 披露（detail 明示是结构代理）。

红线：MapSpec 是 desired truth，observation 是 observation；本模块
零持久化、零修复动作（修复仍走既有 mutation 通道）、全部有界。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..contracts import (
    F_LAYER_TRANSPARENT,
    F_MAP_MODEL_MISMATCH,
    RESULT_LAYER_ROLES,
    MapCompletionFinding,
    _spec_layers,
)

#: paint 中表达「不透明度」的键（MapLibre paint 属性 + 历史防御值）。
_OPACITY_KEYS = (
    "fill-opacity", "line-opacity", "circle-opacity",
    "heatmap-opacity", "icon-opacity", "text-opacity",
    "raster-opacity", "fill-extrusion-opacity", "sky-opacity",
)
_TRANSPARENT_EPS = 1e-6


def _result_layer_ids(chapter: Dict[str, Any]) -> Dict[str, str]:
    """planned 结果层 id → cartography 模型 id（role ∈ RESULT_LAYER_ROLES）。"""
    out: Dict[str, str] = {}
    for layer in chapter.get("map_layers") or []:
        if not isinstance(layer, dict):
            continue
        role = str(layer.get("role") or "")
        layer_id = str(layer.get("layer_id") or "")
        if layer_id and role in RESULT_LAYER_ROLES:
            out[layer_id] = str(layer.get("cartography") or "")
    return out


def _is_visible_enabled(layer: Dict[str, Any]) -> bool:
    if layer.get("enabled") is False:
        return False
    visibility = str((layer.get("layout") or {}).get("visibility") or "visible")
    return visibility != "none"


def _paint_opacities(paint: Dict[str, Any]) -> List[float]:
    vals: List[float] = []
    for key in _OPACITY_KEYS:
        raw = paint.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            vals.append(float(raw))
    return vals


def _rgba_alpha(color: Any) -> Optional[float]:
    """rgba(...)/#rrggbbaa 颜色的 alpha（结构性判 0；非字面量 → None）。"""
    if not isinstance(color, str):
        return None
    text = color.strip().lower()
    if text.startswith("rgba(") and text.endswith(")"):
        parts = text[5:-1].split(",")
        if len(parts) == 4:
            try:
                return float(parts[3].strip())
            except ValueError:
                return None
    if text.startswith("#") and len(text) == 9:
        try:
            return int(text[7:9], 16) / 255.0
        except ValueError:
            return None
    return None


def validate_layer_visibility_quality(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
) -> List[MapCompletionFinding]:
    """全透明结果层的结构代理检查（warning；像素验证仍归 agent 工具）。"""
    findings: List[MapCompletionFinding] = []
    result_layers = _result_layer_ids(chapter)
    for layer in _spec_layers(mapspec)[:48]:
        layer_id = str(layer.get("id") or "")
        if layer_id not in result_layers:
            continue
        if not _is_visible_enabled(layer):
            continue  # 显式隐藏是合法用户/计划状态（layers validator 管）
        paint = layer.get("paint") or {}
        if not isinstance(paint, dict):
            continue
        zero_opacity = any(v <= _TRANSPARENT_EPS for v in _paint_opacities(paint))
        if not zero_opacity:
            for key in ("fill-color", "line-color", "circle-color"):
                alpha = _rgba_alpha(paint.get(key))
                if alpha is not None and alpha <= _TRANSPARENT_EPS:
                    zero_opacity = True
                    break
        if zero_opacity:
            findings.append(MapCompletionFinding(
                code=F_LAYER_TRANSPARENT,
                severity="warning",
                target=layer_id,
                detail=(
                    "result layer paint opacity is 0 (structural proxy: layer "
                    "renders nothing); pixel-level blank-canvas verification "
                    "remains the agent-tool heuristic_visual_proxies path"
                ),
            ))
    return findings[:4]


def validate_map_model_compat(
    chapter: Dict[str, Any],
    mapspec: Dict[str, Any],
) -> List[MapCompletionFinding]:
    """planned cartography 模型 ↔ composed spec layer type 复核（warning）。

    组合期过滤（component_resolver.compatible_map_models）被绕过时，
    完成期在此如实披露 —— 不修复（改 layer type 是科学/呈现双重决策，
    归用户/Planner），不阻断（模型元数据缺席 = unknown ≠ violation）。
    允许集 = 模型 geometry_layer_types 值 ∪ planner 计划类型（计划类型
    是 planner 的显式裁决，不得被本检查推翻）。
    """
    findings: List[MapCompletionFinding] = []
    result_layers = _result_layer_ids(chapter)
    if not result_layers:
        return findings
    spec_by_id = {
        str(ly.get("id") or ""): ly for ly in _spec_layers(mapspec)[:48]
    }
    planned_type_by_id: Dict[str, str] = {}
    for layer in chapter.get("map_layers") or []:
        if isinstance(layer, dict) and layer.get("layer_id"):
            planned_type_by_id[str(layer["layer_id"])] = str(
                layer.get("layer_type") or "")
    registry = None
    try:
        from app.lib.cartography.model_library import get_map_model_registry

        registry = get_map_model_registry()
    except Exception:  # noqa: BLE001 — 模型库缺席 → 无断言（诚实退化）
        registry = None
    for layer_id, cartography in list(result_layers.items())[:12]:
        spec = spec_by_id.get(layer_id)
        if spec is None or registry is None or not cartography:
            continue  # 层在场性归 layers/render validators；无模型 = 无断言
        spec_type = str(spec.get("type") or "")
        if not spec_type:
            continue
        try:
            model = registry.resolve(cartography)
            table = getattr(model, "geometry_layer_types", None) or {}
        except Exception:  # noqa: BLE001 — 单模型解析失败不阻断
            continue
        if not table:
            continue
        permitted = {str(v) for v in table.values()}
        planned_type = planned_type_by_id.get(layer_id)
        if planned_type:
            permitted.add(planned_type)
        if spec_type not in permitted:
            findings.append(MapCompletionFinding(
                code=F_MAP_MODEL_MISMATCH,
                severity="warning",
                target=layer_id,
                detail=(
                    f"composed layer type '{spec_type}' is outside map "
                    f"model '{cartography}' geometry_layer_types "
                    f"{sorted(permitted)[:4]} (composition-time filter "
                    "was bypassed)"
                ),
            ))
    return findings[:4]


__all__ = [
    "validate_layer_visibility_quality",
    "validate_map_model_compat",
]
