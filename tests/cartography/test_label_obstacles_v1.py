"""W2.1（ADR-0283 D3）：标注×图元障碍（obstacles）—— solve_export_labels 增量语义。

障碍 = 图元（点符号等）的 AABB 盒。语义：候选位估算盒与任一障碍盒
严格重叠（贴边不算）→ 该候选位不可用（与既有 label-label collision
同等对待）；全部候选位被占 → 该标签按既有抑制路径抑制（reason=
"obstacle"）。不传/空障碍时输出与既有形态逐字段一致（兼容锁定）。
"""
import pytest

from app.lib.cartography.label_collision import (
    MAX_LABELS_PER_EXPORT,
    MAX_OBSTACLES_PER_SOLVE,
    CollisionLabel,
    solve_export_labels,
)

VP = [0.0, 0.0, 200.0, 200.0]


def _label(lid: str, x: float = 100.0, y: float = 100.0, kind: str = "point",
           text: str = "A", font_size: float = 10.0, priority: int = 0,
           angle: float = 0.0) -> CollisionLabel:
    return CollisionLabel(id=lid, text=text, kind=kind, x=x, y=y,
                          angle=angle, font_size=font_size, priority=priority)


def test_max_obstacles_constant():
    assert MAX_OBSTACLES_PER_SOLVE == 200


def test_obstacle_covering_all_candidates_suppresses():
    """障碍盒盖住锚点周围全部 8 方位候选 → 抑制（reason=obstacle）。"""
    # 标签 "A" fs=10 → 盒 6×12、偏移 7.5；候选 x∈[92.5,113.5] y∈[92.5,119.5]
    obstacle = (85.0, 85.0, 115.0, 125.0)
    sol = solve_export_labels([_label("A")], VP, obstacles=[obstacle])
    assert len(sol.placements) == 1
    p = sol.placements[0]
    assert p.status == "suppressed"
    assert p.reason == "obstacle"
    assert sol.stats["obstacle_hits"] == 1
    assert sol.stats["collisions"] == 0


def test_obstacle_avoided_places_first_free_candidate():
    """障碍只占右侧三候选 → 放在首个避开障碍的候选位（正下）。"""
    # 障碍 x∈[106.5,130) 恰不与候选 4/8 的 x2=106 相交（贴边不算）
    obstacle = (106.5, 90.0, 130.0, 126.0)
    sol = solve_export_labels([_label("A")], VP, obstacles=[obstacle])
    p = sol.placements[0]
    assert p.status == "placed"
    assert (p.x, p.y) == (100.0, 92.5)   # (0,-1) 正下候选
    assert p.reason == ""
    assert sol.stats["obstacle_hits"] == 0


def test_edge_touching_obstacle_does_not_block():
    """障碍与候选盒贴边（x1 == 候选 x2）→ 严格重叠口径不算命中 → 首选位可放。"""
    obstacle = (113.5, 85.0, 140.0, 130.0)  # 候选 1-3 的 x2=113.5
    sol = solve_export_labels([_label("A")], VP, obstacles=[obstacle])
    p = sol.placements[0]
    assert p.status == "placed"
    assert (p.x, p.y) == (107.5, 107.5)   # 首选（右上）未受阻


def test_line_label_suppressed_by_obstacle():
    """line/polygon 单候选被障碍覆盖 → 抑制 reason=obstacle（同抑制路径）。"""
    lbl = _label("L1", x=100.0, y=100.0, kind="line", text="线", angle=0.0)
    obstacle = (80.0, 80.0, 120.0, 120.0)
    sol = solve_export_labels([lbl], VP, obstacles=[obstacle])
    p = sol.placements[0]
    assert p.status == "suppressed"
    assert p.reason == "obstacle"
    assert sol.stats["obstacle_hits"] == 1


def test_empty_obstacles_fieldwise_identical_to_legacy():
    """障碍为空列表 → placements 与 stats 同既有形态（兼容锁定）。"""
    labels = [
        _label("A", x=100.0, y=100.0),
        _label("B", x=101.0, y=100.5, priority=1),
    ]
    base = solve_export_labels(labels, VP)
    none_run = solve_export_labels(labels, VP, obstacles=None)
    empty = solve_export_labels(labels, VP, obstacles=[])
    assert [p.to_dict() for p in empty.placements] == [p.to_dict() for p in base.placements]
    assert empty.stats == base.stats
    assert none_run.stats == base.stats
    assert "obstacle_hits" not in base.stats
    assert "obstacles_truncated" not in base.stats


def test_obstacles_cap_truncation_keeps_first_n():
    """障碍数超 MAX_OBSTACLES_PER_SOLVE → 保留前 N、截断计数入 stats。"""
    filler = [(0.0, 0.0, 1.0, 1.0)] * MAX_OBSTACLES_PER_SOLVE  # 远离，无作用
    blocker = (85.0, 85.0, 115.0, 125.0)                        # 若生效则抑制
    sol = solve_export_labels(
        [_label("A")], VP, obstacles=filler + [blocker],
    )
    assert sol.stats["obstacles_truncated"] == 1
    p = sol.placements[0]
    assert p.status == "placed"   # 第 201 个障碍被截掉 → 不生效
    assert (p.x, p.y) == (107.5, 107.5)


def test_obstacle_suppressed_label_still_counted_in_relaxed_disclosure():
    """障碍抑制计入 suppressed（供 label_collision_relaxed 披露口径）。"""
    obstacle = (85.0, 85.0, 115.0, 125.0)
    sol = solve_export_labels([_label("A")], VP, obstacles=[obstacle])
    assert sol.suppressed_count == 1
    assert sol.stats["suppressed"] == 1


def test_obstacles_do_not_change_budget_overflow_path():
    """budget 溢出抑制优先（reason=budget），障碍字段照常入 stats。"""
    labels = [_label(f"L{i:03d}", x=50.0 + i, y=50.0, priority=i) for i in range(5)]
    sol = solve_export_labels(labels, VP, max_labels=2, obstacles=[(0.0, 0.0, 1.0, 1.0)])
    reasons = sorted({p.reason for p in sol.placements})
    assert sol.budget_exceeded is True
    assert "budget" in reasons   # 溢出抑制优先，不被障碍改写
    assert sol.stats["obstacles_truncated"] == 0
    assert MAX_LABELS_PER_EXPORT == 400


@pytest.mark.parametrize("obstacle,expect_status", [
    ((85.0, 85.0, 115.0, 125.0), "suppressed"),
    ((106.5, 90.0, 130.0, 126.0), "placed"),
])
def test_obstacle_presence_matrix(obstacle, expect_status):
    sol = solve_export_labels([_label("A")], VP, obstacles=[obstacle])
    assert sol.placements[0].status == expect_status


# ── 接线（mapspec_to_svg circle 层 → 求解 obstacles）──────────────────────

def _glyph_spec(circle_radius=None):
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed  # noqa: F401

    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [115.8, 38.8]},
         "properties": {}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.8, 39.6]},
         "properties": {"other": 1}},
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.0]},
         "properties": {"name": "北京"}},
    ]
    layers = []
    if circle_radius is not None:
        layers.append({
            "id": "pts_circle", "source": "pts", "type": "circle",
            "paint": {"circle-radius": circle_radius, "circle-color": "#3b82f6"},
        })
    layers.append({
        "id": "pts_sym", "source": "pts", "type": "symbol",
        "layout": {"text-field": "{name}", "text-size": 14},
    })
    return {
        "version": "1.1",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"pts": {"type": "geojson", "inlineData": {
            "type": "FeatureCollection", "features": features}}},
        "layers": layers,
        "layout": {"labels": {"collision": "deterministic"}},
    }


def test_circle_glyph_obstacle_wiring_suppresses_label():
    """circle 层大半径点符号 → 障碍盒接线：标签被抑制 + 披露含 obstacles 计数。"""
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    result = compile_mapspec_to_svg_detailed(
        _glyph_spec(circle_radius=30), target_dpi=72, width=400, height=300, padding=10,
    )
    # 3 个点符号（含 2 个扩边要素）均成障碍（dpi=72 → 外扩 30px），盖住全部
    # 8 方位候选 → 唯一标签被抑制。
    assert "北京" not in result.svg
    relaxed = [d for d in result.diagnostics if d["code"] == "label_collision_relaxed"]
    assert relaxed, result.diagnostics
    assert "obstacles=3" in relaxed[0]["detail"]
    assert "suppressed=1" in relaxed[0]["detail"]


def test_circle_glyph_wiring_small_radius_keeps_label():
    """小半径符号（r=5）不达候选盒（偏移 0.75em）→ 标签照常放置（不误杀）。"""
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    result = compile_mapspec_to_svg_detailed(
        _glyph_spec(circle_radius=5), target_dpi=72, width=400, height=300, padding=10,
    )
    assert "北京" in result.svg


def test_symbol_only_path_unchanged_by_obstacle_wiring():
    """无 circle 层 → 障碍集空 → 不传 obstacles（legacy 输出/无 obstacles 披露）。"""
    from app.services.mapspec_to_svg import compile_mapspec_to_svg_detailed

    result = compile_mapspec_to_svg_detailed(
        _glyph_spec(circle_radius=None), target_dpi=72, width=400, height=300, padding=10,
    )
    assert "北京" in result.svg
    assert not any(d["code"] == "label_collision_relaxed" for d in result.diagnostics)
