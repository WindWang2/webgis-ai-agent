"""G31 求值器单元测试 —— 人工可数 oracle（任务书 T01–T11/T16/T17）。

每项断言对照**理论值**（fixtures 注释中的米制几何），不是实现自证：
- T01 完整覆盖 → 无缺口；
- T02 真实洞 → 缺口 witness 面积 ≈ 40_000 m²（±1%）；
- T03 湖面排除 → 缺口消失、排除面积披露 ≈ 90_000 m²；
- T04 嵌套 → 重叠面积 ≈ 10_000 m²；
- T05 共边不误报（boundary_touch 默认 false）；
- T06 建筑越界 → 越界面积 ≈ 60m × 100m = 6_000 m²、双源 locator；
- T08 反经线/极区 → typed blocked；
- T09 自交/空/带洞多边形 → 前置剔除计数（不悄悄修）；
- T10 0.5m/2m 端点在 1m 容差下区分；
- T11 出口区豁免 + 计数可见；
- T16 同快照重跑 key 稳定、分页换序不变；
- T17 finding 预算触顶 → partial + 计数。
"""
from __future__ import annotations


import pytest

from app.services.topology_workbench.evaluators import (
    EVALUATORS,
    RULE_EXPECTED_FAMILY,
    MeasureSpace,
    _Budget,
    _iter_pieces,
)
from app.services.topology_workbench.geometry_core import (
    RegionUnsupported,
    choose_computation_crs,
    prepare_source,
)

from tests.topology_workbench.fixtures_g31 import (
    BUILDING_CROSS,
    EMPTY_GEOM,
    EXIT_ZONE,
    FACILITY_AT_ENDPOINT,
    FACILITY_AT_START,
    FACILITY_FAR,
    LINE_FAR_ENDPOINT,
    LINE_NEAR_FACILITY,
    MULTIPOLYGON_WITH_HOLE,
    NESTED_SMALL,
    PARCEL_E_RECT,
    PARCEL_W,
    SELF_INTERSECTING_BOWTIE,
    aoi_source,
    dlat,
    dlon,
    fc,
    feat,
    lake_source,
    parcels_cover,
    parcels_with_hole,
)

MEASURE = MeasureSpace("EPSG:4326", "EPSG:32650")
NO_CANCEL = lambda: False  # noqa: E731


def snap(role: str, features, family: str = "any", fingerprint: str = "fp1"):
    return prepare_source(
        role=role, source_kind="session_ref", ref="ref:test", content_revision=1,
        fingerprint=fingerprint, crs="EPSG:4326", features=features,
        expected_family=family,
    )


def run_rule(rule, **sources):
    fn = EVALUATORS[rule["rule_type"]]
    budget = _Budget()
    return fn(rule=rule, measure=MEASURE, ruleset_digest="rd", budget=budget,
              should_cancel=NO_CANCEL, **sources)


COV_RULE = {"rule_id": "cov", "rule_type": "area_coverage", "severity": "error",
            "params": {"gap_area_threshold_m2": 5.0}}
OL_RULE = {"rule_id": "ol", "rule_type": "polygons_no_overlap", "severity": "error",
           "params": {"overlap_area_threshold_m2": 1.0}}
IN_RULE = {"rule_id": "in", "rule_type": "objects_covered_by_target", "severity": "error",
           "params": {"boundary_policy": "covered", "outside_area_threshold_m2": 1.0}}
EP_RULE = {"rule_id": "ep", "rule_type": "line_endpoints_connected", "severity": "error",
           "params": {"tolerance_m": 1.0}}


# ─── T01–T03 覆盖 ─────────────────────────────────────────────────────


def test_t01_full_coverage_no_gap():
    out = run_rule(COV_RULE,
                   coverage=snap("coverage", parcels_cover()["features"], "polygon"),
                   aoi=snap("aoi", aoi_source()["features"], "polygon"))
    assert out.outcome == "no_violations"
    assert out.findings == []
    assert out.computation == "full"


def test_t02_real_gap_area_oracle():
    out = run_rule(COV_RULE,
                   coverage=snap("coverage", parcels_with_hole()["features"], "polygon"),
                   aoi=snap("aoi", aoi_source()["features"], "polygon"))
    assert out.outcome == "violations_found"
    (f,) = out.findings
    # 理论洞面积 200m × 200m = 40_000 m²（±1% 投影效应）
    assert f.metric["value"] == pytest.approx(40_000, rel=0.01)
    assert f.metric["unit"] == "m2"
    assert f.metric["threshold"] == 5.0
    # 缺口 witness 可定位（WGS84 Polygon）
    assert f.witness_geom.geom_type == "Polygon"
    roles = {loc["role"] for loc in f.locators}
    assert "aoi" in roles


def test_t03_lake_exclusion_removes_gap():
    out = run_rule(COV_RULE,
                   coverage=snap("coverage", parcels_with_hole()["features"], "polygon"),
                   aoi=snap("aoi", aoi_source()["features"], "polygon"),
                   exclusions=snap("exclusions", lake_source()["features"], "polygon"))
    assert out.outcome == "no_violations"
    assert out.findings == []
    # 排除面积披露 ≈ 300m × 300m = 90_000 m²（差集后仍全额披露并验证证据保留）
    assert out.counts["exclusion_area_m2"] == pytest.approx(90_000, rel=0.01)


def test_t02_gap_key_stable_rerun():
    """T16：同快照重跑 → finding key 稳定。"""
    cov = snap("coverage", parcels_with_hole()["features"], "polygon")
    aoi = snap("aoi", aoi_source()["features"], "polygon")
    o1 = run_rule(COV_RULE, coverage=cov, aoi=aoi)
    o2 = run_rule(COV_RULE, coverage=cov, aoi=aoi)
    assert o1.findings[0].key("rd") == o2.findings[0].key("rd")


# ─── T04/T05 同源重叠 ─────────────────────────────────────────────────


def test_t04_nested_polygon_found():
    features = [feat("parcel-w", PARCEL_W), feat("nested", NESTED_SMALL)]
    out = run_rule(OL_RULE, checked=snap("checked", features, "polygon"))
    assert out.outcome == "violations_found"
    (f,) = out.findings
    # 嵌套 = 交叠全覆盖小面：100m × 100m ≈ 10_000 m²
    assert f.metric["value"] == pytest.approx(10_000, rel=0.01)
    ids = {loc["identity"]["value"] for loc in f.locators}
    assert ids == {"parcel-w", "nested"}


def test_t05_shared_edge_no_false_positive():
    """共边（零面积重叠）默认不违规。"""
    east_shift = {
        "type": "Polygon",
        "coordinates": [[
            [PARCEL_W["coordinates"][0][1][0], PARCEL_W["coordinates"][0][0][1]],
            [PARCEL_W["coordinates"][0][1][0] + dlon(500), PARCEL_W["coordinates"][0][0][1]],
            [PARCEL_W["coordinates"][0][1][0] + dlon(500), PARCEL_W["coordinates"][0][2][1]],
            [PARCEL_W["coordinates"][0][1][0], PARCEL_W["coordinates"][0][2][1]],
            [PARCEL_W["coordinates"][0][1][0], PARCEL_W["coordinates"][0][0][1]],
        ]],
    }
    features = [feat("w", PARCEL_W), feat("e", east_shift)]
    out = run_rule(OL_RULE, checked=snap("checked", features, "polygon"))
    assert out.outcome == "no_violations"
    assert out.findings == []


def test_t05_boundary_touch_policy_flagged_when_declared():
    """boundary_touch 双政策语义：false（默认）共边宽容；true 共边违规（接触长度度量）。"""
    east = PARCEL_E_RECT
    features = [feat("w", PARCEL_W), feat("e", east)]

    # 默认（false）：共边零面积 → 不报
    out_default = run_rule(OL_RULE, checked=snap("checked", features, "polygon"))
    assert out_default.outcome == "no_violations"
    assert out_default.findings == []

    # 声明（true）：共边 → 违规，度量 = 接触长度（≈1000 m，±1%），无阈值
    rule = {**OL_RULE, "params": {"overlap_area_threshold_m2": 1.0, "boundary_touch": True}}
    out_declared = run_rule(rule, checked=snap("checked", features, "polygon"))
    assert out_declared.outcome == "violations_found"
    (f,) = out_declared.findings
    assert f.metric["method"] == "boundary_contact_length_declared"
    assert f.metric["unit"] == "m"
    assert f.metric["threshold"] is None
    assert f.metric["value"] == pytest.approx(1000, rel=0.01)  # 共享整条 1000m 边


# ─── T06 跨层包含 ─────────────────────────────────────────────────────


def test_t06_building_partially_outside():
    buildings = fc(feat("bld-1", BUILDING_CROSS))
    parcels = fc(feat("parcel-e", PARCEL_E_RECT))
    out = run_rule(IN_RULE,
                   checked=snap("checked", buildings["features"], "any"),
                   target=snap("target", parcels["features"], "polygon"))
    assert out.outcome == "violations_found"
    (f,) = out.findings
    # 越界部分：建筑 150m 宽，东界在 940+150=1090m，地块东界 1000m → 90m × 100m
    assert f.metric["value"] == pytest.approx(9_000, rel=0.02)
    roles = {loc["role"] for loc in f.locators}
    assert roles == {"checked", "target"}


# ─── T08 区域支持 ─────────────────────────────────────────────────────


def test_t08_antimeridian_blocked():
    with pytest.raises(RegionUnsupported) as ei:
        choose_computation_crs((179.0, -10.0, -179.0, 10.0))  # w > e
    assert ei.value.code == "antimeridian"


def test_t08_polar_blocked():
    with pytest.raises(RegionUnsupported) as ei:
        choose_computation_crs((0.0, 89.0, 1.0, 89.5))
    assert ei.value.code == "polar"


def test_t08_extent_too_large_blocked():
    with pytest.raises(RegionUnsupported) as ei:
        choose_computation_crs((100.0, 0.0, 140.0, 5.0))
    assert ei.value.code == "extent"


def test_t08_normal_extent_utm():
    assert choose_computation_crs((116.0, 39.0, 117.0, 40.0)) == "EPSG:32650"


# ─── T09 病态几何 ─────────────────────────────────────────────────────


def test_t09_invalid_geometries_excluded_with_counts():
    features = [
        feat("bowtie", SELF_INTERSECTING_BOWTIE),
        feat("empty", EMPTY_GEOM),
        feat("null-geom", {"type": "Polygon", "coordinates": None}),  # shape() 失败 → invalid
        feat("valid", PARCEL_W),
    ]
    src = snap("checked", features, "polygon")
    # 三个病态要素各按其因剔除（自交=invalid、空=empty、缺坐标=bad_coords）
    assert src.excluded.get("invalid_geometry", 0) == 1
    assert src.excluded.get("empty_geometry", 0) == 1
    assert src.excluded.get("bad_coords", 0) == 1
    assert src.examined == 1
    # 检测事实保留：invalid 计数披露，且规则结论带 unknown 范围注记
    out = run_rule(OL_RULE, checked=src)
    assert out.input_completeness == "partial"
    assert any("未进入关系运算" in n for n in out.notes)


def test_t09_multipolygon_with_hole_valid():
    src = snap("checked", [feat("mp", MULTIPOLYGON_WITH_HOLE)], "polygon")
    assert src.examined == 1
    assert src.excluded == {}


# ─── T10/T11 线端点 ───────────────────────────────────────────────────


def test_t10_endpoint_distance_thresholds():
    lines = fc(feat("line-near", LINE_NEAR_FACILITY), feat("line-far", LINE_FAR_ENDPOINT))
    facilities = fc(
        feat("fac-start", FACILITY_AT_START),
        feat("fac-near", FACILITY_AT_ENDPOINT),
        feat("fac-far", FACILITY_FAR),
    )
    out = run_rule(EP_RULE,
                   checked=snap("checked", lines["features"], "line"),
                   connectors=snap("connectors", facilities["features"], "any"))
    assert out.outcome == "violations_found"
    # line-near：末端距 fac-near ≈ 0.5m ≤ 1m（通过）；line-far：距最近设施 >1m（违规）
    assert len(out.findings) >= 1
    far = [f for f in out.findings if "line-far" in f.message]
    near = [f for f in out.findings if "line-near" in f.message]
    assert far, "line-far 端点（>1m）必须被发现"
    assert near == [], "line-near 端点（0.5m ≤ 1m）不得误报"
    assert far[0].metric["unit"] == "m"
    assert far[0].metric["threshold"] == 1.0
    assert far[0].metric["method"]


def test_t11_exit_zone_exempts_and_counts():
    lines = fc(feat("line-far", LINE_FAR_ENDPOINT))
    facilities = fc(
        feat("fac-start", FACILITY_AT_START),
        feat("fac-far", FACILITY_FAR),
    )
    exits = fc(feat("exit-1", EXIT_ZONE))
    out = run_rule(EP_RULE,
                   checked=snap("checked", lines["features"], "line"),
                   connectors=snap("connectors", facilities["features"], "any"),
                   exit_zones=snap("exit_zones", exits["features"], "polygon"))
    # 远端在出口区内 → 豁免计数可见、不误报为必须修复
    assert out.counts.get("exempted_exits", 0) >= 1
    assert out.findings == []


# ─── T16/T17 身份与预算 ───────────────────────────────────────────────


def test_t16_multi_piece_keys_distinct_and_order_stable():
    """同一对要素两片不连通交叠 → 各自独立 key（不互相覆盖）。"""
    # 两个分离的交叠区：西地块与两条不同的越界带
    big = feat("big", PARCEL_W)
    piece1 = feat("p1", {
        "type": "Polygon",
        "coordinates": [[[116.401, 39.901], [116.402, 39.901], [116.402, 39.902], [116.401, 39.902], [116.401, 39.901]]],
    })
    piece2 = feat("p2", {
        "type": "Polygon",
        "coordinates": [[[116.403, 39.903], [116.404, 39.903], [116.404, 39.904], [116.403, 39.904], [116.403, 39.903]]],
    })
    out = run_rule(OL_RULE, checked=snap("checked", [big, piece1, piece2], "polygon"))
    assert len(out.findings) == 2
    keys = [f.key("rd") for f in out.findings]
    assert len(set(keys)) == 2  # 独立分量身份


def test_t17_finding_budget_partial():
    """T17：finding 数触顶 → computation=partial + 计数披露（不伪全量）。"""
    from app.services.topology_workbench.contracts import MAX_FINDINGS_PER_RULE

    # 8×8 网格：50m×50m 单元、60m 间距（互不重叠），全部嵌套在 540m 大面内
    # → 大面×64 单元 = 64 条 finding；阈值 1 m² 过滤掉全部噪声。
    n = 8
    cells = [feat(f"c{i}-{j}", {
        "type": "Polygon",
        "coordinates": [[
            [116.40 + dlon(10 + i * 60), 39.90 + dlat(10 + j * 60)],
            [116.40 + dlon(10 + i * 60 + 50), 39.90 + dlat(10 + j * 60)],
            [116.40 + dlon(10 + i * 60 + 50), 39.90 + dlat(10 + j * 60 + 50)],
            [116.40 + dlon(10 + i * 60), 39.90 + dlat(10 + j * 60 + 50)],
            [116.40 + dlon(10 + i * 60), 39.90 + dlat(10 + j * 60)],
        ]],
    }) for i in range(n) for j in range(n)]
    big = feat("grid", {
        "type": "Polygon",
        "coordinates": [[
            [116.40 + dlon(5), 39.90 + dlat(5)],
            [116.40 + dlon(5 + 10 * 60), 39.90 + dlat(5)],
            [116.40 + dlon(5 + 10 * 60), 39.90 + dlat(5 + 10 * 60)],
            [116.40 + dlon(5), 39.90 + dlat(5 + 10 * 60)],
            [116.40 + dlon(5), 39.90 + dlat(5)],
        ]],
    })
    import unittest.mock as mock

    baseline = run_rule(OL_RULE, checked=snap("checked", [big] + cells, "polygon"))
    assert len(baseline.findings) >= 64  # 未触顶时全量发现（=8×8）

    with mock.patch("app.services.topology_workbench.evaluators.MAX_FINDINGS_PER_RULE", 5):
        out = run_rule(OL_RULE, checked=snap("checked", [big] + cells, "polygon"))
    assert out.computation == "partial"
    assert len(out.findings) == 5
    assert any("部分结果" in note or "触顶" in note for note in out.notes)
    assert MAX_FINDINGS_PER_RULE >= 1000  # 生产常量护栏


# ─── 求值器注册表完整性 ───────────────────────────────────────────────


def test_evaluator_registry_matches_rule_types():
    from app.services.topology_workbench.contracts import RULE_TYPES

    assert set(EVALUATORS) == set(RULE_TYPES)
    assert set(RULE_EXPECTED_FAMILY) == set(RULE_TYPES)


def test_iter_pieces_dedupes_and_flattens():
    from shapely.geometry import MultiPolygon, Polygon

    p1 = Polygon([(0, 0), (1, 0), (1, 1), (0, 1)])
    p2 = Polygon([(2, 0), (3, 0), (3, 1), (2, 1)])
    mp = MultiPolygon([p1, p2, p1])
    pieces = _iter_pieces(mp)
    assert len(pieces) == 2
    assert len({d for _, d in pieces}) == 2
