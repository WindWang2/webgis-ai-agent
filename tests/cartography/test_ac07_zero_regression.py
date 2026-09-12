"""AC-07（ADR-0156 P8）：20 MapSpec × 4 版式三类版面检查归零回归。

P0 基线（docs/dev/ac-07-baseline.json）：compose 直出（未经 solver）的
20 案例 × 4 版式上，LAYOUT_COLLISION 告警 72/80；注入越界浮动件后
COMPONENT_OUTSIDE_CANVAS fail/warning 80/80。本测试对同源语料应用
修复建议（plan_layout_repairs 动作链 + resolve_floating_layout 钳制）
后复检 —— 三类版面检查必须归零（P8 验收门禁）。

本文件用 6 案例（模型全覆盖）× 4 版式 × 2 口径控制运行时长；全量
20 案例口径见 scripts/ac07_p0_baseline.py（结果记档 baseline.json）。
"""
from collections import Counter

import pytest

from app.lib.cartography.component_composer import plan_layout_repairs
from app.lib.cartography.layout_geometry import (
    CanvasSpec,
    resolve_floating_rects,
    safe_area_for,
)
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.services.gis_harness.component_composer import ComponentComposer
from app.services.gis_harness.component_resolver import ComponentResolver
from tests.cartography.golden_corpus.corpus import build_cases

PROFILES = ("viewport", "presentation_16x9", "a4_portrait", "a4_landscape")
MARGINS = {
    "viewport": {"top": 10, "bottom": 10, "left": 10, "right": 10},
    "presentation_16x9": {"top": 12, "bottom": 12, "left": 12, "right": 12},
    "a4_portrait": {"top": 42, "bottom": 42, "left": 36, "right": 36},
    "a4_landscape": {"top": 36, "bottom": 36, "left": 42, "right": 42},
}

CORRUPT_FLOATER = {
    "id": "corrupt_floating_stats",
    "type": "statistics_panel",
    "enabled": True,
    "priority": 90,
    "placement": {"mode": "floating", "x": -420, "y": -180, "width": 260, "height": 180},
    "options": {},
}

pytestmark = pytest.mark.cartography


def _pick_cases():
    cases = build_cases()
    chosen = []
    seen_models = set()
    for c in cases:
        if c["kind"] == "profiles" and c["model"] not in seen_models:
            chosen.append(c)
            seen_models.add(c["model"])
    return chosen[:6]


def _build_raw(case):
    sel = ComponentResolver().resolve(
        composition_template_id=case["template"],
        map_model_id=case["model"],
        output_target=case["output"],
        available_context=case["context"],
    )
    components = ComponentComposer().compose(
        sel,
        title_text=case["title"],
        subtitle_text=case["subtitle"],
        layer_bindings=case["bindings"],
        layer_model_ids=case["layer_models"],
    )
    return [c.model_dump() if hasattr(c, "model_dump") else dict(c) for c in components]


def _build_spec(raw, profile, corrupt):
    comps = [dict(d) for d in raw]
    if corrupt:
        comps.append(dict(CORRUPT_FLOATER))
    return {
        "version": "1.1",
        "layers": [{"id": "layer-primary", "type": "fill", "paint": {}},
                   {"id": "layer-primary-2", "type": "circle", "paint": {}}],
        "layout": {"components": comps, "margins": MARGINS[profile]},
    }


def _layout_tally(spec):
    tally = Counter()
    report = evaluate_cartography_semantics(spec)
    for chk in report.checks:
        if chk.rule in ("LAYOUT_COLLISION", "COMPONENT_OUTSIDE_CANVAS",
                        "COMPONENT_LINK_CYCLE") and chk.status in ("warning", "fail"):
            tally[f"{chk.rule}:{chk.status}"] += 1
    return tally


def _apply_repairs(spec):
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


@pytest.mark.parametrize("corrupt", [False, True], ids=["native", "corrupt"])
def test_three_layout_checks_zero_after_repairs(corrupt):
    for case in _pick_cases():
        raw = _build_raw(case)
        for profile in PROFILES:
            spec = _build_spec(raw, profile, corrupt)
            before = _layout_tally(spec)
            repaired = _apply_repairs(spec)
            after = _layout_tally(repaired)
            assert not after, (
                f"{case['id']}@{profile}: 修复后仍残留 {dict(after)}"
                f"（修复前 {dict(before)}）"
            )
