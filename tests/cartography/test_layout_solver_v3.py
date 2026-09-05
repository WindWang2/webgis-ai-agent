"""Layout Solver V2 — 确定性布局求解契约测试（ADR-0101 D5）。

锁定：
- 确定性：同输入同输出（多次求解 + 乱序输入归一）；
- 容量：zone 容量满时按 slot fallback → 邻接 fallback 挪移；
- 单例：同类型第二实例被抑制（不产生第二个指北针）；
- required 溢出保留 + warning（功能性组件不静默消失）；
- optional 溢出确定性抑制 + 理由可读；
- page profile：宽画幅容量放宽、窄视口收紧；
- 无死循环风险：单遍算法（全溢出输入一次跑完）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.layout_solver import (
    LayoutParticipant,
    page_profiles,
    solve_component_layout,
)

pytestmark = pytest.mark.cartography


def _p(pid: str, ptype: str, zone: str, *, priority: int = 50,
       optional: bool = True, fallbacks: list | None = None) -> LayoutParticipant:
    return LayoutParticipant(id=pid, type=ptype, requested_zone=zone,
                             priority=priority, optional=optional,
                             fallback_zones=fallbacks or [])


def test_determinism_same_input_same_output() -> None:
    parts = [
        _p("legend-main", "legend", "bottom-left"),
        _p("stats", "statistics_panel", "top-left"),
        _p("chart-1", "chart_panel", "top-left"),
        _p("colorbar-main", "continuous_colorbar", "bottom-right"),
        _p("scale", "scale_bar", "bottom-right"),
    ]
    a = solve_component_layout(parts)
    b = solve_component_layout(list(reversed(parts)))
    assert a.model_dump() == b.model_dump()
    c = solve_component_layout(parts)
    assert a.model_dump() == c.model_dump()


def test_capacity_overflow_moves_to_fallback_zone() -> None:
    # top-right 容量 1：north_arrow 先到，inset_map 挪 slot fallback
    parts = [
        _p("north", "north_arrow", "top-right", priority=30),
        _p("inset", "inset_map", "top-right", fallbacks=["bottom-right"]),
    ]
    sol = solve_component_layout(parts)
    assert sol.zone_for("north") == "top-right"
    assert sol.zone_for("inset") == "bottom-right"
    inset = next(p for p in sol.placements if p.id == "inset")
    assert inset.moved and inset.reason == "fallback_zone"


def test_overflow_optional_suppressed_with_reason() -> None:
    # top-left 容量 2、exclusive 邻接 top-center（1）：5 个 annotation 挤入 →
    # 候选槽全满 → 第 4/5 个 optional 确定性抑制。
    parts = [
        _p(f"ann-{i}", "annotation", "top-left", priority=10 + i)
        for i in range(5)
    ]
    sol = solve_component_layout(parts)
    placed_ids = [p.id for p in sol.placements]
    assert placed_ids == ["ann-0", "ann-1", "ann-2"]
    assert sol.zone_for("ann-0") == "top-left"
    assert sol.zone_for("ann-2") == "top-center"  # 邻接 fallback
    sup = {s.id: s for s in sol.suppressed}
    assert "ann-3" in sup and "ann-4" in sup
    assert all(s.reason == "overflow_suppressed" for s in sup.values())
    assert any("ann-3" in w for w in sol.warnings)


def test_required_overflow_kept_with_warning() -> None:
    # viewport 底槽预算 3：scale(bc) + ann(bl) + cb(br) 恰好填满预算与
    # bc 容量 → required attribution 到达时 bc 满、预算尽 →
    # 保留在 requested 位置并告警（不静默消失）。
    parts = [
        _p("scale", "scale_bar", "bottom-center", priority=20, optional=False),
        _p("ann-a", "annotation", "bottom-left", priority=30, optional=False),
        _p("cb-a", "continuous_colorbar", "bottom-right", priority=15, optional=False),
        _p("attr", "attribution", "bottom-center", priority=50, optional=False),
    ]
    sol = solve_component_layout(parts)
    placed_ids = {p.id for p in sol.placements}
    assert "attr" in placed_ids  # required 不抑制
    assert sol.zone_for("attr") == "bottom-center"
    assert any("attr" in w and "kept" in w for w in sol.warnings)


def test_duplicate_singleton_suppressed() -> None:
    parts = [
        _p("north-1", "north_arrow", "top-right", priority=30),
        _p("north-2", "north_arrow", "top-left", priority=31),
    ]
    sol = solve_component_layout(parts)
    assert sol.zone_for("north-1") == "top-right"
    assert all(p.id != "north-2" for p in sol.placements)
    sup = {s.id: s for s in sol.suppressed}
    assert sup["north-2"].reason == "duplicate_singleton:north_arrow"


def test_canvas_types_bypass_capacity() -> None:
    parts = [
        _p("border", "map_border", "none"),
        _p("grat", "graticule", "none"),
        _p("export", "export_layout", "none"),
    ]
    sol = solve_component_layout(parts)
    assert all(p.zone == "none" for p in sol.placements)
    assert not sol.suppressed and not sol.warnings


def test_page_profiles_change_capacity() -> None:
    """宽画幅（A4 横版）同槽容纳更多面板；溢出行为随 profile 变化。"""
    parts = [
        _p("stats", "statistics_panel", "top-left"),
        _p("chart-1", "chart_panel", "top-left"),
        _p("chart-2", "chart_panel", "top-left"),
        _p("chart-3", "chart_panel", "top-left"),
    ]
    narrow = solve_component_layout(parts, page_profile="viewport")
    wide = solve_component_layout(parts, page_profile="a4_landscape")
    assert len(narrow.suppressed) >= 1
    assert not wide.suppressed


def test_wide_profile_listed_and_valid() -> None:
    profiles = page_profiles()
    assert {"viewport", "a4_portrait", "a4_landscape",
            "presentation_16x9", "academic_figure"} <= set(profiles)


def test_full_overflow_input_terminates() -> None:
    """极端输入（全挤同一槽、无 fallback）单遍跑完 —— 无循环风险。"""
    parts = [_p(f"chart-{i}", "chart_panel", "top-left", priority=i)
             for i in range(30)]
    sol = solve_component_layout(parts)
    assert len(sol.placements) + len(sol.suppressed) == 30


def test_unknown_zone_treated_as_capacity_two() -> None:
    parts = [_p("a", "legend", "mystery-zone"), _p("b", "legend", "mystery-zone"),
             _p("c", "legend", "mystery-zone")]
    sol = solve_component_layout(parts)
    assert len(sol.suppressed) >= 1


def test_solution_digest_stable_for_golden() -> None:
    """结构化 digest 供 golden corpus 使用（字段顺序无关）。"""
    sol = solve_component_layout([
        _p("title", "title", "top-center", priority=10, optional=False),
        _p("legend-main", "legend", "bottom-left"),
    ])
    digest = {
        p.id: p.zone for p in sol.placements
    }
    assert digest == {"title": "top-center", "legend-main": "bottom-left"}
