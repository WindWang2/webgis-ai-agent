"""carto.scale.representation 检查（方向 5 / C7）。

从 semantic_checks.py 拆出的表达窗口计划声明一致性检查（ISSUE-044
god_modules 收敛：内聚的语义缩放表达检查独立成模块）。契约与行为
不变 —— semantic_checks.evaluate_cartography_semantics 仍以
_check_scale_representation 作为唯一调用方（re-export 兼容）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, List, Optional

if TYPE_CHECKING:
    from app.lib.cartography.semantic_checks import CartographyReport


def _check_scale_representation(
    report: CartographyReport,
    mapspec: Dict[str, Any],
    layer: Dict[str, Any],
    lid: Optional[str],
    sid: Optional[str],
) -> None:
    """carto.scale.representation — 表达窗口计划的声明一致性（方向 5 / C7）。

    核验（全部 desired-state 证据，确定性）：
    - 窗口计划可归一（词表/连续覆盖 [0,24)/合并/有界）；
    - hidden 窗口必带 reason_codes（可审计的 detail visibility 门）；
    - 聚合类窗口（cluster/heatmap/grid）必须在 representation_disclosures
      或 layer_meta 携带视觉聚合披露（视觉聚合 ≠ 分析结果口径）；
    - 结构 zoom 门（layer.visibility）与窗口计划一致（hidden 首窗 →
      min_zoom = 首个非 hidden 窗起点）；
    - 模式与几何族一致（点模式不得出现在非点层）。

    无声明（旧 spec / 平凡层）→ not_evaluated（零行为变化）。
    """
    from app.lib.cartography.representation import (
        ARTIFACT_MODES,
        normalize_windows,
    )

    declared = layer.get("representation")
    if not isinstance(declared, dict) or not declared.get("windows"):
        return
    raw_windows = declared.get("windows")
    if not isinstance(raw_windows, list):
        return
    normalized = normalize_windows(raw_windows)
    if normalized is None:
        report.add_check(
            "carto.scale.representation",
            "fail",
            f"Layer '{lid}' representation windows are not normalizable "
            "(vocab/coverage/contiguity violated)",
            severity="error",
            layer_id=lid,
            source_id=sid,
            evidence={"window_count": len(raw_windows)},
            repairability="not_repairable",
        )
        return

    view = mapspec.get("view") if isinstance(mapspec.get("view"), dict) else {}
    zoom = view.get("zoom") if isinstance(view.get("zoom"), (int, float)) else None
    problems: List[str] = []
    has_aggregation = any(w.mode in ("point_cluster", "visual_heatmap", "aggregate_grid")
                          for w in normalized)
    for w in normalized:
        if w.mode == "hidden" and not w.reason_codes:
            problems.append("hidden_window_without_reason")
    if has_aggregation:
        # review P2 收紧：披露判定只认 visual_only 文本本身 —— grammar 的
        # layer_meta.scale_visibility_hints（tier 建议）不构成聚合语义披露。
        disclosures = layer.get("representation_disclosures")
        disclosed = isinstance(disclosures, list) and any(
            isinstance(d, str) and ("视觉聚合" in d or "visual_only" in d)
            for d in disclosures
        )
        if not disclosed:
            problems.append("aggregation_without_visual_scope_disclosure")
    # 几何族一致性：非 circle 层不得声明点聚合模式（heatmap 编译改写仅
    # 支持 circle；line/fill 层的聚合窗语义不成立）。
    if layer.get("type") not in ("circle", "heatmap"):
        for w in normalized:
            if w.mode in ("point_cluster", "visual_heatmap"):
                problems.append("point_mode_on_non_point_layer")
    # 结构门一致性：首窗 hidden → visibility.min_zoom 应等于首个非 hidden
    # 窗起点（渲染端两条门路径不漂移）。
    vis_block = layer.get("visibility") if isinstance(layer.get("visibility"), dict) else {}
    visible_windows = [w for w in normalized if w.mode != "hidden"]
    if normalized and normalized[0].mode == "hidden" and visible_windows:
        expected_min = visible_windows[0].min_zoom
        got_min = vis_block.get("min_zoom")
        if got_min is not None and abs(float(got_min) - expected_min) > 1e-6:
            problems.append("visibility_gate_diverges_from_windows")

    active_mode = None
    if zoom is not None:
        z = min(23.999999, max(0.0, float(zoom)))
        active_mode = next(
            (w.mode for w in normalized if w.min_zoom <= z < w.max_zoom),
            normalized[-1].mode,
        )
    evidence = {
        "windows": [
            {"min_zoom": w.min_zoom, "max_zoom": w.max_zoom, "mode": w.mode}
            for w in normalized
        ],
        "active_mode_at_view_zoom": active_mode,
        "artifact_modes": sorted(ARTIFACT_MODES),
    }
    if problems:
        deduped = sorted(set(problems))
        report.add_check(
            "carto.scale.representation",
            "fail",
            f"Layer '{lid}' representation plan inconsistent: {','.join(deduped)}",
            severity="error",
            layer_id=lid,
            source_id=sid,
            evidence={**evidence, "problems": deduped},
            repairability="not_repairable",
        )
        return
    report.add_check(
        "carto.scale.representation",
        "pass",
        f"Layer '{lid}' representation plan is consistent"
        + (f" (active mode '{active_mode}' at view zoom {zoom})" if active_mode else ""),
        layer_id=lid,
        source_id=sid,
        evidence=evidence,
    )
