"""G38 空间点面关联验收（J10–J13；人工推导 oracle）。

oracle（fixtures.regions_fc / points_fc）：
- P1(2,5)   → 仅 R1
- P2(7,5)   → R1∩R2 重叠区（多命中）
- P3(10,5)  → 恰在 R1 东边界 x=10
- P4(25,5)  → R3 洞内（不算面内）
- P5(21,5)  → R3 实体环带内
- P6(40,5)  → 面外
"""
from __future__ import annotations

import pytest

pytest.importorskip("shapely")

from app.services.data_join_authoring.errors import DataJoinError
from app.services.data_join_authoring.executor import JoinExecutor
from app.services.data_join_authoring.plan import parse_plan
from tests.unit.data_join_authoring.fixtures import (
    loaded_from_fc,
    points_fc,
    points_4326_fc,
    regions_fc,
    regions_3857_fc,
    spatial_plan,
)


def _run(plan_dict, left, right, **kw):
    plan = parse_plan(plan_dict)
    ex = JoinExecutor(plan, **kw)
    out = ex.run_spatial(left, right)
    return out, ex.stats, plan


def _by_pid(rows):
    return {r["pid"]: r for r in rows}


MAP = {"left": [("pid", "pid")], "right": [("rid", "rid"), ("priority", "priority")]}


def _map_entries():
    return [
        {"side": "left", "field": "pid"},
        {"side": "right", "field": "rid"},
    ]


# ── J10：重叠面下的同一点 ────────────────────────────────────────────

def test_j10_require_unique_blocks_on_overlap():
    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(field_map=_map_entries())
    with pytest.raises(DataJoinError) as ei:
        _run(plan, left, right)
    assert ei.value.code == "needs_decision"
    assert ei.value.detail["identity"] == "P2"
    assert ei.value.detail["hits"] == 2


def test_j10_expand_all_returns_all_matches():
    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(
        spatial={"predicate": "within", "multi_match": "expand_all"},
        output_cardinality="one_to_many", keep_side="inner",
        field_map=_map_entries())
    out, stats, _ = _run(plan, left, right)
    p2_rows = [r for r in out if r["pid"] == "P2"]
    assert {r["rid"] for r in p2_rows} == {"R1", "R2"}     # 全部命中返回
    assert len({r["_wfid"] for r in out}) == len(out)       # 派生身份唯一
    # P1 + P2×2 + P3(R2 内部) + P5 = 5；P4 洞/P6 面外/P7 边界不命中
    assert stats.output_rows == 5


def test_j10_pick_by_field_deterministic_and_tie_blocked():
    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    # priority：R1=2，R2=1 → min 择 R2（确定性，不靠输入顺序）
    plan = spatial_plan(
        spatial={"predicate": "within", "multi_match": "pick_by_field",
                 "pick_field": "priority", "pick_direction": "min"},
        field_map=_map_entries() + [{"side": "right", "field": "priority"}])
    out, _, _ = _run(plan, left, right)
    by = _by_pid(out)
    assert by["P2"]["rid"] == "R2"
    assert by["P2"]["priority"] == 1
    # 同值并列 → needs_decision（拒绝偶然顺序）：R1/R2 priority 改同值
    right2 = loaded_from_fc("right", regions_fc(), "fpR")
    for row in right2.rows:
        row["priority"] = 7
    plan_tie = spatial_plan(
        spatial={"predicate": "within", "multi_match": "pick_by_field",
                 "pick_field": "priority", "pick_direction": "min"},
        field_map=_map_entries())
    with pytest.raises(DataJoinError) as ei:
        _run(plan_tie, left, right2)
    assert ei.value.code == "needs_decision"
    assert "并列" in ei.value.message


def test_j10_aggregate_multi_match():
    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(
        spatial={"predicate": "within", "multi_match": "aggregate"},
        field_map=_map_entries(),
        aggregates=[{"function": "sum", "right_field": "priority"}])
    out, _, _ = _run(plan, left, right)
    by = _by_pid(out)
    assert by["P2"]["match_count"] == 2
    assert by["P2"]["priority_sum"] == 3        # R1(2)+R2(1)
    assert by["P1"]["priority_sum"] == 2


# ── J11：边界点与洞 ──────────────────────────────────────────────────

def test_j11_within_excludes_boundary_covers_includes():
    """边界 oracle（人工推导，shapely contains/covers 语义）：

    - P7(35,5) 恰在 R4 西边界：within（contains）不命中；covers 命中 R4。
    - P3(10,5) 是 R1 东边界同时是 R2 内部：within 命中 R2（非 R1 边界）。
    """
    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan_w = spatial_plan(
        spatial={"predicate": "within", "multi_match": "pick_by_field",
                 "pick_field": "priority", "pick_direction": "min"},
        field_map=_map_entries())
    out_w, _, _ = _run(plan_w, left, right)
    by_w = _by_pid(out_w)
    assert by_w["P7"]["_join_status"] == "unmatched"     # 严格内部：边界不命中
    assert by_w["P3"]["rid"] == "R2"                     # R2 内部（非 R1 边界）
    plan_c = spatial_plan(
        spatial={"predicate": "within_or_boundary",
                 "multi_match": "pick_by_field",
                 "pick_field": "priority", "pick_direction": "min"},
        field_map=_map_entries())
    out_c, _, _ = _run(plan_c, left, right)
    by_c = _by_pid(out_c)
    assert by_c["P7"]["_join_status"] == "matched"       # 边界包含：命中 R4
    assert by_c["P7"]["rid"] == "R4"


def test_j11_hole_point_not_inside():
    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(
        spatial={"predicate": "within_or_boundary",
                 "multi_match": "pick_by_field",
                 "pick_field": "priority", "pick_direction": "min"},
        field_map=_map_entries())
    out, _, _ = _run(plan, left, right)
    by = _by_pid(out)
    assert by["P4"]["_join_status"] == "unmatched"   # 洞内不算面内（covers 也不算）
    assert by["P5"]["rid"] == "R3"                   # 环带内命中
    assert by["P6"]["_join_status"] == "unmatched"   # 面外


def test_keep_side_left_preserves_unmatched_points_with_left_geometry():
    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(
        spatial={"predicate": "within",
                 "multi_match": "pick_by_field",
                 "pick_field": "priority", "pick_direction": "min"},
        field_map=_map_entries())
    out, stats, _ = _run(plan, left, right)
    assert stats.output_rows == 7                     # 全部左点保留（left keep）
    by = _by_pid(out)
    assert by["P6"]["rid"] is None
    assert by["P6"]["__geometry__"]["type"] == "Point"
    assert by["P7"]["rid"] is None                    # 边界点 within 不命中
    assert by["P7"]["__geometry__"]["type"] == "Point"


# ── J12：异 CRS ──────────────────────────────────────────────────────

def test_j12_crs_mismatch_transforms_points_to_polygon_frame():
    pytest.importorskip("pyproj")
    left = loaded_from_fc("left", points_4326_fc(), "fpL")
    right = loaded_from_fc("right", regions_3857_fc(), "fpR")
    plan = spatial_plan(
        spatial={"predicate": "within", "multi_match": "require_unique"},
        field_map=_map_entries())
    out, stats, _ = _run(plan, left, right)
    assert stats.working_crs == "EPSG:3857"
    assert "4326" in (stats.transform_note or "")
    assert out and out[0]["rid"] == "Z1"              # 变换后真实命中


# ── J13：未声明/偏移 CRS ─────────────────────────────────────────────

def test_j13_undeclared_crs_blocked():
    fc = points_fc()
    fc.pop("crs")
    left = loaded_from_fc("left", fc, "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(field_map=_map_entries())
    with pytest.raises(DataJoinError) as ei:
        _run(plan, left, right)
    assert ei.value.code == "crs_unresolvable"
    assert ei.value.detail.get("side") == "left"


def test_j13_offset_frame_blocked():
    fc = points_fc()
    fc["crs"] = "GCJ02"
    left = loaded_from_fc("left", fc, "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(field_map=_map_entries())
    with pytest.raises(DataJoinError) as ei:
        _run(plan, left, right)
    assert ei.value.code == "crs_unresolvable"


def test_table_source_for_spatial_blocked():
    from tests.unit.data_join_authoring.fixtures import loaded_from_rows

    left = loaded_from_rows("left", {"rows": [{"pid": "P1"}]}, "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(field_map=[{"side": "left", "field": "pid"}])
    with pytest.raises(DataJoinError) as ei:
        _run(plan, left, right)
    assert ei.value.code == "source_geometry_missing"


def test_malformed_point_coordinates_do_not_escape():
    """坐标 null/字符串 → 按 null_geometry 披露，不抛 TypeError/ValueError 逃出。"""
    left_fc = points_fc()
    left_fc["features"][0]["geometry"]["coordinates"] = [None, 5.0]
    left_fc["features"][1]["geometry"]["coordinates"] = ["bad", 5.0]
    left = loaded_from_fc("left", left_fc, "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(
        spatial={"predicate": "within", "multi_match": "require_unique"},
        field_map=_map_entries())
    out, stats, _ = _run(plan, left, right)
    by = _by_pid(out)
    assert by["P1"]["_join_status"] == "null_geometry"
    assert by["P2"]["_join_status"] == "null_geometry"
    assert stats.left_null_geometry == 2


def test_deterministic_replay_same_snapshot_identical_output():
    """J18：同快照重放 → 配对/输出值/身份逐位一致。"""

    left = loaded_from_fc("left", points_fc(), "fpL")
    right = loaded_from_fc("right", regions_fc(), "fpR")
    plan = spatial_plan(
        spatial={"predicate": "within", "multi_match": "expand_all"},
        output_cardinality="one_to_many", keep_side="inner",
        field_map=_map_entries())
    out1, stats1, _ = _run(plan, left, right)
    out2, stats2, _ = _run(
        plan,
        loaded_from_fc("left", points_fc(), "fpL"),   # 重新装载（同指纹）
        loaded_from_fc("right", regions_fc(), "fpR"))
    assert out1 == out2
    assert stats1.matched_pairs == stats2.matched_pairs
    # 数据重排（同内容不同顺序 → 不同指纹）不应影响「键决定」的属性配对语义：
    # 此处验证重放一致性（同指纹）；跨版本身份不作承诺（无稳定 _wfid 时为 locator）
    assert [r["_wfid"] for r in out1] == [r["_wfid"] for r in out2]
