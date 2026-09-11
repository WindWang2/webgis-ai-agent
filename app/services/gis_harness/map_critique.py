"""MapCritique —— 地图感知观察批评（V7 ADR-0134 D5）。

V6 基线（render_observation + observation_states + completion validators）
已覆盖图层可见性 / 挂载 / 数据在场 / 组件槽位 / 布局重叠 / 导出对齐；
V7 Goal Phase E 要求的**制图语义级**缺口（audit W6）：

- blank map（全层 rendered 但数据全缺 ——「文件生成成功」≠「有内容的图」）；
- invalid bounds（结果 bbox 倒置/非有限）；
- scalebar / north_arrow / title 出版件完整性（facet 槽位只覆盖 legend 族
  —— semantics.py 自注「不建第三词表」，本模块同样只消费既有词表）；
- label collision（渲染遥测比率 → 既有 CARTO_LABEL_* 阈值，telemetry
  缺席诚实降级）；
- overlay mismatch 聚合面（planned ∩ observed = ∅ 的硬错位披露）。

契约：

- **纯函数**：``critique_map_state(chapter, mapspec, observation)`` ——
  同输入同 findings；不写状态、不修状态（修复路由走既有通道：
  findings 带 ``family`` → ``_apply_repairs`` 的 add_component /
  runtime_repair 的 reassert；无修复 → 披露）。
- **Findings = 既有 MapCompletionFinding**（同词表同 schema —— finalizer
  可直接并轨消费；不建第二 finding 类型）。
- **诚实缺席**：observation 缺席的检查一律跳过（不猜）；导出件完整性
  仅在「有结果图层 + 声明导出 png/pdf」时检查（不对抗交互-only 产品）。
- **闭环**：observation → critique → repair → re-observe 的驱动点复用
  既有 finalizer 触发面（tool_result / turn_settled / observation POST）
  —— 本模块是那个闭环的「看」半边，「修」半边全量复用 V4-V6 通道。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from app.services.gis_harness.completion.contracts import MapCompletionFinding

logger = logging.getLogger(__name__)

#: 检查词表（封闭）。
C_BLANK_MAP = "blank_map_risk"
C_INVALID_BOUNDS = "invalid_result_bounds"
C_EXPORT_COMPLETENESS = "export_component_missing"
C_LABEL_COLLISION = "label_collision"
C_OVERLAY_MISMATCH = "planned_observed_mismatch"

#: 出版件组件完整性词表（消费既有组件类型词表 —— 不建第三词表）。
_PUBLICATION_COMPONENTS: Tuple[str, ...] = ("title", "north_arrow", "scale_bar")

#: label collision 遥测键（render observation payload 的可选字段）。
_LABEL_COLLISION_KEYS: Tuple[str, ...] = (
    "label_collision_ratio", "labels_collision_ratio",
)


def _spec_components(mapspec: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not isinstance(mapspec, dict):
        return []
    comps = mapspec.get("components")
    return [c for c in (comps or []) if isinstance(c, dict)]


def _component_types(mapspec: Optional[Dict[str, Any]]) -> set:
    return {
        str(c.get("type") or "") for c in _spec_components(mapspec) if c.get("type")
    }


def _planned_layers(chapter: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        ly for ly in (chapter.get("map_layers") or [])
        if isinstance(ly, dict) and ly.get("layer_id")
    ]


def _observed_layers(observation: Optional[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    if not isinstance(observation, dict):
        return {}
    return {
        str(k): v for k, v in (observation.get("layers") or {}).items()
        if isinstance(v, dict)
    }


def _finite_bbox(bbox: Any) -> bool:
    if not isinstance(bbox, (list, tuple)) or len(bbox) != 4:
        return False
    try:
        values = [float(v) for v in bbox]
    except (TypeError, ValueError):
        return False
    if any(not math.isfinite(v) for v in values):
        return False
    minx, miny, maxx, maxy = values
    return minx < maxx and miny < maxy


def _label_collision_ratio(observation: Optional[Dict[str, Any]]) -> Optional[float]:
    """遥测比率提取（可选键；缺席 → None —— 诚实降级不猜）。"""
    if not isinstance(observation, dict):
        return None
    for key in _LABEL_COLLISION_KEYS:
        raw = observation.get(key)
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
            if 0.0 <= value <= 1.0:
                return value
    labels = observation.get("labels")
    if isinstance(labels, dict):
        raw = labels.get("collision_ratio")
        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
            value = float(raw)
            if 0.0 <= value <= 1.0:
                return value
    return None


def _label_thresholds() -> Tuple[float, float]:
    """既有 CARTO_LABEL_* 阈值（config 缺席 → 保守默认 0.10/0.25）。"""
    try:
        from app.core.config import settings

        return float(getattr(settings, "CARTO_LABEL_WARN_RATIO", 0.10)), float(
            getattr(settings, "CARTO_LABEL_FAIL_RATIO", 0.25))
    except Exception:  # noqa: BLE001 — settings 缺席按保守默认
        return 0.10, 0.25


# ── 检查（纯函数）────────────────────────────────────────────────────────


def check_blank_map(
    chapter: Dict[str, Any],
    observation: Optional[Dict[str, Any]],
) -> List[MapCompletionFinding]:
    """全层已渲染但特征计数全 0 → blank_map_risk（error；无遥测则缺席）。

    viewport-scoped 计数为 0 不谎报 —— 只在 observation 明确携带
    feature_count 键的层上判定（与 observation_states 的 data_present
    纪律同源：证据缺席不给结论）。"""
    observed = _observed_layers(observation)
    if not observed:
        return []
    rendered_layers = []
    counts: List[int] = []
    for _lid, entry in observed.items():
        if entry.get("render_complete") is not True:
            continue
        rendered_layers.append(entry)
        fc = entry.get("feature_count")
        if isinstance(fc, (int, float)) and not isinstance(fc, bool):
            counts.append(int(fc))
    if not rendered_layers:
        return []
    # 诚实缺席：任一已渲染层未报计数 → 证据不全，不猜（与
    # observation_states 的 data_present 纪律同源）。
    if len(counts) != len(rendered_layers):
        return []
    if counts and all(c <= 0 for c in counts):
        return [MapCompletionFinding(
            code=C_BLANK_MAP,
            severity="error",
            target="map",
            detail=(f"all {len(rendered_layers)} rendered layers report "
                    f"feature_count<=0 — 渲染成功但数据不在场"),
        )]
    return []


def check_invalid_bounds(
    chapter: Dict[str, Any],
    observation: Optional[Dict[str, Any]],
) -> List[MapCompletionFinding]:
    """结果 bbox 倒置/非有限 → invalid_result_bounds（error；仅观察证据）。

    评审 F12：只消费**本轮** render observation 携带的 bbox —— 上一代
    成品块的 stored bbox 可能陈旧（本轮重验会误报 error → failed）；
    观察缺席 → 不判（诚实缺席）。"""
    if not isinstance(observation, dict):
        return []
    bbox = observation.get("result_bbox")
    if bbox is None:
        return []
    if not _finite_bbox(bbox):
        return [MapCompletionFinding(
            code=C_INVALID_BOUNDS,
            severity="error",
            target="viewport",
            detail=f"result bbox invalid: {str(bbox)[:80]}",
        )]
    return []


def check_publication_components(
    chapter: Dict[str, Any],
    mapspec: Optional[Dict[str, Any]],
) -> List[MapCompletionFinding]:
    """出版件完整性：有结果图层且声明 png/pdf 导出 → title/north_arrow/
    scale_bar 应在 spec 组件中（缺席 → warning + add_component 修复路由）。

    legend/colorbar 族归 semantics/validators 既有检查 —— 本检查只补
    audit W6 点名的三件套，不重复别人的词表。"""
    if not _planned_layers(chapter):
        return []
    exports = []
    if isinstance(mapspec, dict):
        exports = [str(e) for e in (mapspec.get("exports") or [])]
    if not any(e.lower() in ("png", "pdf", "svg", "jpg") for e in exports):
        return []  # 交互-only 产品不做出版件要求（不对抗既有模板）
    present = _component_types(mapspec)
    findings: List[MapCompletionFinding] = []
    for comp_type in _PUBLICATION_COMPONENTS:
        if comp_type in present:
            continue
        findings.append(MapCompletionFinding(
            code=C_EXPORT_COMPLETENESS,
            severity="warning",
            target=comp_type,
            detail=f"publication export declared but component '{comp_type}' missing",
            family=[comp_type],
        ))
    return findings


def check_label_collision(
    observation: Optional[Dict[str, Any]],
) -> List[MapCompletionFinding]:
    """遥测比率 vs 既有 CARTO_LABEL_* 阈值（可选遥测；缺席 → 零 finding）。"""
    ratio = _label_collision_ratio(observation)
    if ratio is None:
        return []
    warn_t, fail_t = _label_thresholds()
    if ratio >= fail_t:
        return [MapCompletionFinding(
            code=C_LABEL_COLLISION,
            severity="error",
            target="labels",
            detail=f"label collision ratio {ratio:.2f} >= fail threshold {fail_t:.2f}",
        )]
    if ratio >= warn_t:
        return [MapCompletionFinding(
            code=C_LABEL_COLLISION,
            severity="warning",
            target="labels",
            detail=f"label collision ratio {ratio:.2f} >= warn threshold {warn_t:.2f}",
        )]
    return []


def check_overlay_mismatch(
    chapter: Dict[str, Any],
    observation: Optional[Dict[str, Any]],
) -> List[MapCompletionFinding]:
    """planned ∩ observed = ∅ 且观察在场 → 硬错位披露（error）。

    与 render_observation 的逐层 missing 不同：这是聚合面 —— 全部计划层
    一个都没观察到（观察可能对错了 spec / revision）。"""
    planned = {str(ly.get("layer_id")) for ly in _planned_layers(chapter)}
    observed = _observed_layers(observation)
    if not planned or not observed:
        return []
    if planned.isdisjoint(observed.keys()):
        return [MapCompletionFinding(
            code=C_OVERLAY_MISMATCH,
            severity="error",
            target="map",
            detail=(f"none of {len(planned)} planned layers observed "
                    f"(observed={len(observed)}) — 疑似观察/spec 错位"),
        )]
    return []


# ── 聚合入口 ─────────────────────────────────────────────────────────────


def critique_map_state(
    chapter: Optional[Dict[str, Any]],
    mapspec: Optional[Dict[str, Any]] = None,
    observation: Optional[Dict[str, Any]] = None,
) -> List[MapCompletionFinding]:
    """全量批评（纯函数；bounded findings —— 供 finalizer 并轨/独立消费）。"""
    if not isinstance(chapter, dict) or not chapter:
        return []
    findings: List[MapCompletionFinding] = []
    findings.extend(check_blank_map(chapter, observation))
    findings.extend(check_invalid_bounds(chapter, observation))
    findings.extend(check_publication_components(chapter, mapspec))
    findings.extend(check_label_collision(observation))
    findings.extend(check_overlay_mismatch(chapter, observation))
    return findings[:12]


__all__ = [
    "C_BLANK_MAP",
    "C_INVALID_BOUNDS",
    "C_EXPORT_COMPLETENESS",
    "C_LABEL_COLLISION",
    "C_OVERLAY_MISMATCH",
    "check_blank_map",
    "check_invalid_bounds",
    "check_publication_components",
    "check_label_collision",
    "check_overlay_mismatch",
    "critique_map_state",
]
