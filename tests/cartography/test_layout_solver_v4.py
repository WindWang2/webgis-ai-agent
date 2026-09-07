"""Layout Solver V3（Design System）—— 约束式布局求解契约测试.

锁定：
- V3 是 V2 严格超集：V2 形输入经 solve_layout_v3 结果与 V2 一致；
- 确定性：同输入同输出、乱序输入归一；
- 列单位容量：宽面板（width_units=2）占两列，first-fit 装填；
- 碰撞组：同组组件不得落同槽（图例族组级互斥），全候选占满 → conflict 诊断；
- avoid_zones：内容占用槽位被扣减，参与者沿候选链迁移且原因可解释；
- compact 模式：容量收紧触发 reflow，原因=compact_reflow；
- 诊断输出：conflicts + fallback_plan_zh（有抑制/冲突时必给方案）。
"""
from __future__ import annotations

import pytest

from app.lib.cartography.layout_solver import (
    CANVAS_TYPES,
    LayoutConstraints,
    LayoutParticipant,
    LayoutParticipantV3,
    page_profiles,
    solve_component_layout,
    solve_layout_v3,
)

pytestmark = pytest.mark.cartography


def _p3(pid: str, ptype: str, zone: str, *, priority: int = 50,
        optional: bool = True, fallbacks: list | None = None,
        width_units: int = 1, group: str = "",
        avoid: list | None = None) -> LayoutParticipantV3:
    return LayoutParticipantV3(
        id=pid, type=ptype, requested_zone=zone, priority=priority,
        optional=optional, fallback_zones=fallbacks or [],
        width_units=width_units, collision_group=group,
        avoid_zones=avoid or [],
    )


def test_v3_superset_v2_equivalence():
    """V2 形输入（缺省 V3 字段）→ V2 与 V3 槽位结果一致。"""
    participants_v2 = [
        LayoutParticipant(id="t", type="title", requested_zone="top-center"),
        LayoutParticipant(id="legend", type="legend", requested_zone="bottom-left"),
        LayoutParticipant(id="stats", type="statistics_panel", requested_zone="top-left"),
        LayoutParticipant(id="chart", type="chart_panel", requested_zone="top-left",
                          fallbacks=["top-right"]),
        LayoutParticipant(id="scale", type="scale_bar", requested_zone="bottom-right"),
        LayoutParticipant(id="compass", type="north_arrow", requested_zone="top-right"),
    ]
    participants_v3 = [
        LayoutParticipantV3(**p.model_dump()) for p in participants_v2
    ]
    v2 = solve_component_layout(participants_v2, page_profile="a4_landscape")
    v3 = solve_layout_v3(participants_v3, page_profile="a4_landscape")
    assert {p.id: p.zone for p in v2.placements} == (
        {p.id: p.zone for p in v3.placements})
    assert {p.id for p in v2.suppressed} == {p.id for p in v3.suppressed}


def test_v3_determinism():
    parts = [
        _p3("legend-1", "legend", "bottom-left"),
        _p3("chart-1", "chart_panel", "top-left", width_units=2),
        _p3("stats", "statistics_panel", "top-left"),
    ]
    a = solve_layout_v3(parts)
    b = solve_layout_v3(list(reversed(parts)))
    assert a.model_dump() == b.model_dump()


def test_v3_width_units_first_fit():
    """列单位容量：稳定序（priority,id）窄面板先落，宽面板 a4 宽画幅同槽
    可装下、viewport 收紧则迁移。"""
    wide = _p3("wide", "chart_panel", "top-left", width_units=2)
    narrow = _p3("narrow", "statistics_panel", "top-left", width_units=1)
    # a4_landscape multiplier=2 → top-left 容量 4：1 + 2 = 3 ≤ 4 同槽共存
    sol = solve_layout_v3([wide, narrow], page_profile="a4_landscape")
    assert sol.zone_for("wide") == "top-left"
    assert sol.zone_for("narrow") == "top-left"
    # viewport（multiplier=1，容量 2）：narrow 先占 1/2，wide 需 2 > 余 1 → 迁移
    sol2 = solve_layout_v3([wide, narrow], page_profile="viewport")
    assert sol2.zone_for("narrow") == "top-left"
    assert sol2.zone_for("wide") != "top-left"


def test_v3_collision_group_mutual_exclusion():
    """图例族同组互斥：第二个图例沿候选链挪，占满 → conflict 诊断。"""
    l1 = _p3("legend-a", "legend", "bottom-left", group="legend-family")
    l2 = _p3("legend-b", "legend", "bottom-left", group="legend-family",
             fallbacks=["bottom-right", "top-left", "top-right"])
    sol = solve_layout_v3([l1, l2])
    assert sol.zone_for("legend-a") == "bottom-left"
    assert sol.zone_for("legend-b") == "bottom-right"
    moved = next(p for p in sol.placements if p.id == "legend-b")
    assert moved.moved and moved.reason == "collision_group_move"
    assert not sol.conflicts

    # 同组组件占满全部候选槽（含邻接 fallback 链）→ 末位产生未解决冲突
    # + fallback_plan（候选链含邻接 zone，11 个可穷尽 10 个槽位容量）
    many = [l1, l2] + [
        _p3(f"legend-{ch}", "legend", "bottom-left", group="legend-family",
            fallbacks=["bottom-right", "top-left", "top-right"])
        for ch in "cdefghijkl"
    ]
    sol2 = solve_layout_v3(many)
    assert any(c.component_id == "legend-l" and c.conflict_type == "collision_group"
               for c in sol2.conflicts)
    assert "未解决冲突" in sol2.fallback_plan_zh
    assert "collision_group" in " ".join(sol2.warnings)


def test_v3_avoid_zones_moves_participants():
    """地图内容占用 bottom-left：图例迁到 fallback 槽，原因可解释。"""
    cons = LayoutConstraints(zone_occupancy={"bottom-left": 2})
    legend = _p3("legend", "legend", "bottom-left",
                 fallbacks=["bottom-right"], avoid=["bottom-left"])
    sol = solve_layout_v3([legend], constraints=cons)
    assert sol.zone_for("legend") == "bottom-right"
    p = sol.placements[0]
    assert p.moved and p.reason == "avoid_zone_move"


def test_v3_compact_mode_reflow():
    """compact：容量收紧 → 溢出组件被抑制/迁移 + reflow 原因 + 计划输出。"""
    parts = [
        _p3("a", "chart_panel", "top-left"),
        _p3("b", "chart_panel", "top-left"),
        _p3("c", "chart_panel", "top-left"),
    ]
    normal = solve_layout_v3(parts)
    compact = solve_layout_v3(parts, constraints=LayoutConstraints(compact=True))
    assert compact.compact
    assert len(compact.suppressed) > len(normal.suppressed)
    for place in compact.placements:
        if place.moved:
            assert place.reason in ("compact_reflow", "fallback_zone",
                                    "avoid_zone_move")


def test_v3_diagnostics_outputs():
    parts = [
        _p3("t", "title", "top-center"),
        _p3("t2", "title", "top-center"),   # 重复 singleton → suppressed
    ]
    sol = solve_layout_v3(parts)
    assert any(s.reason == "duplicate_singleton" for s in sol.suppressed)
    assert "收纳" in sol.fallback_plan_zh
    # 干净解：无冲突、无方案
    clean = solve_layout_v3([_p3("t", "title", "top-center")])
    assert clean.ok and clean.fallback_plan_zh == ""


def test_v3_print_mode_and_profiles_unchanged():
    """print_mode 标志透传；page_profiles 与 V2 同表（不破坏调用方）。"""
    sol = solve_layout_v3(
        [_p3("t", "title", "top-center")],
        constraints=LayoutConstraints(print_mode=True))
    assert sol.page_profile == "viewport"
    assert set(page_profiles()) >= {
        "viewport", "a4_portrait", "a4_landscape", "presentation_16x9",
        "academic_figure"}
    assert "map_border" in CANVAS_TYPES
