"""AC-07（ADR-0156）P0 基线 / P8 归零的共享语料 harness（可入库复现）。

从 golden corpus 构造「compose 直出（raw position，未经 solver 重排）」的
MapSpec，供基线统计与归零门禁复用。口径与结论记档：
docs/dev/ac-07-baseline.json / docs/dev/ac-07-layout-recon.md §4。
"""
from typing import Any, Dict, List

from app.lib.cartography.component_composer import plan_layout_repairs
from app.lib.cartography.layout_geometry import (
    CanvasSpec,
    resolve_floating_rects,
    safe_area_for,
)
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.services.gis_harness.component_composer import ComponentComposer
from app.services.gis_harness.component_resolver import ComponentResolver

#: §2 P0 四版式 = corpus profile 词表映射（屏幕 4:3 / 屏幕 16:9 / A4 竖 / A4 横）。
PROFILES = ("viewport", "presentation_16x9", "a4_portrait", "a4_landscape")

MARGINS = {
    "viewport": {"top": 10, "bottom": 10, "left": 10, "right": 10},
    "presentation_16x9": {"top": 12, "bottom": 12, "left": 12, "right": 12},
    "a4_portrait": {"top": 42, "bottom": 42, "left": 36, "right": 36},
    "a4_landscape": {"top": 36, "bottom": 36, "left": 42, "right": 42},
}

#: 破损口径注入物：负象限浮动统计面板（不可见、不可达 —— 破损状态）。
CORRUPT_FLOATER: Dict[str, Any] = {
    "id": "corrupt_floating_stats",
    "type": "statistics_panel",
    "enabled": True,
    "priority": 90,
    "placement": {"mode": "floating", "x": -420, "y": -180, "width": 260, "height": 180},
    "options": {},
}

LAYOUT_CHECK_RULES = ("LAYOUT_COLLISION", "COMPONENT_OUTSIDE_CANVAS", "COMPONENT_LINK_CYCLE")


def pick_cases(limit: int = 20) -> List[Dict[str, Any]]:
    """案例选择（确定性）：profiles 矩阵按模型去重取前 10 + variants 补足。"""
    cases = build_cases()
    chosen: List[Dict[str, Any]] = []
    seen_models = set()
    for c in cases:
        if c["kind"] == "profiles" and c["model"] not in seen_models:
            chosen.append(c)
            seen_models.add(c["model"])
    for c in cases:
        if c["kind"] == "variants" and len(chosen) < limit:
            chosen.append(c)
    return chosen[:limit]


def build_cases():
    """延迟导入（与 golden_corpus 复用同一构建管线）。"""
    from tests.cartography.golden_corpus.corpus import build_cases as _build

    return _build()


def build_raw_components(case: Dict[str, Any]) -> List[Dict[str, Any]]:
    """compose 直出组件（raw position —— 不经 solve_layout_v3 重排）。"""
    sel = ComponentResolver().resolve(
        composition_template_id=case["template"],
        map_model_id=case["model"],
        output_target=case["output"],
        available_context=case["context"],
        **({"preferred_variants": case["preferred_variants"]}
           if case.get("preferred_variants") else {}),
    )
    components = ComponentComposer().compose(
        sel,
        title_text=case["title"],
        subtitle_text=case["subtitle"],
        layer_bindings=case["bindings"],
        layer_model_ids=case["layer_models"],
    )
    return [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in components]


def build_spec(raw: List[Dict[str, Any]], profile: str, corrupt: bool) -> Dict[str, Any]:
    comps = [dict(d) for d in raw]
    if corrupt:
        comps.append(dict(CORRUPT_FLOATER))
    return {
        "version": "1.1",
        "layers": [{"id": "layer-primary", "type": "fill", "paint": {}},
                   {"id": "layer-primary-2", "type": "circle", "paint": {}}],
        "layout": {"components": comps, "margins": MARGINS[profile]},
    }


def layout_tally(spec: Dict[str, Any]) -> Dict[str, int]:
    """三类版面检查的告警计数（warning/fail 合并口径）。"""
    from collections import Counter

    tally: Dict[str, int] = {}
    counts = Counter()
    report = evaluate_cartography_semantics(spec)
    for chk in report.checks:
        if chk.rule in LAYOUT_CHECK_RULES and chk.status in ("warning", "fail"):
            counts[f"{chk.rule}:{chk.status}"] += 1
    tally.update(counts)
    return tally


def apply_repairs(spec: Dict[str, Any]) -> Dict[str, Any]:
    """应用修复建议：plan_layout_repairs 动作链 + 越界浮动件钳制。"""
    repaired = [dict(d) for d in spec["layout"]["components"]]
    by_id = {str(d.get("id")): d for d in repaired}
    for action in plan_layout_repairs(repaired):
        target = by_id.get(str(action.get("component_id")))
        if not target:
            continue
        kind = action.get("action")
        if kind == "change_anchor" and action.get("to"):
            target["position"] = action["to"]
        elif kind == "hide_lowest_priority":
            target["enabled"] = False
        elif kind == "collapse_to_overflow":
            target["position"] = action.get("to") or target.get("position")
            opts = target.setdefault("options", {})
            if isinstance(opts, dict):
                opts["collapsed"] = True
    canvas = CanvasSpec(width=1280, height=720)
    safe = safe_area_for(canvas, spec["layout"].get("margins"))
    for adj in resolve_floating_rects(repaired, canvas, safe).adjustments:
        d = by_id.get(adj.component_id)
        if d:
            placement = d.setdefault("placement", {})
            placement["x"] = round(adj.to_rect.x, 2)
            placement["y"] = round(adj.to_rect.y, 2)
    out = dict(spec)
    out["layout"] = dict(spec["layout"])
    out["layout"]["components"] = repaired
    return out
