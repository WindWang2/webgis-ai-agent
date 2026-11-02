"""非破坏性几何综合引擎测试（M3）—— 先行测试。

不变量（D5）：
- bounded_simplify：有效性/类型族/洞数/部件数保持、面积与长度误差有界，
  违反 ⇒ 该要素回退原几何（绝不输出坏几何）；
- shared_boundary_protect：共享锚点顶点在简化后必须保留（相邻面不裂缝）；
- safe_no_generalization / none：几何逐字节不动；
- 要素取舍：importance kept_values 之外的要素被抑制并留因；mandatory 存活；
- 非破坏性：输入特征对象不被就地修改；
- 确定性：同输入同输出。
"""
from __future__ import annotations

import json
import math

from shapely.geometry import shape

from app.lib.cartography.multiscale.generalize import (
    generalize_features,
)
from app.lib.cartography.multiscale.profile import (
    FeatureSelectionPolicy,
    GeometryGeneralizationPolicy,
)


def _line(n: int = 200, jitter: float = 0.00001, base_lat: float = 30.0):
    pts = [[104.0 + i * 0.0001, base_lat + (jitter if i % 2 else -jitter)]
           for i in range(n)]
    return {"type": "LineString", "coordinates": pts}


def _square(lon: float, lat: float, d: float = 0.01) -> dict:
    return {"type": "Polygon", "coordinates": [[
        [lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat],
    ]]}


def _no_sel() -> FeatureSelectionPolicy:
    return FeatureSelectionPolicy(importance_evidence="none")


def _simplify(tol_m: float = 100.0, **kw) -> GeometryGeneralizationPolicy:
    return GeometryGeneralizationPolicy(
        strategy="bounded_simplify", tolerance=tol_m,
        tolerance_units="meters_ground", **kw)


class TestBoundedSimplifyLines:
    def test_vertex_reduction_with_bounded_length_error(self):
        feats = [{"type": "Feature", "id": "l1", "properties": {},
                  "geometry": _line()}]
        r = generalize_features(
            feats, geometry_policy=_simplify(200.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        out = r.feature_collection["features"][0]
        n_before = len(feats[0]["geometry"]["coordinates"])
        n_after = len(out["geometry"]["coordinates"])
        assert n_after < n_before
        assert r.receipt["geometry_simplified"] == 1
        # 长度误差有界（抖动 1m 级细节被抹平，主干保留）
        assert r.receipt["max_length_loss_ratio_observed"] <= 0.5

    def test_invariant_violation_falls_back_to_original(self):
        # 构造病态输入：自交环 —— simplify 后无效 ⇒ 回退原几何
        bad_ring = [[104, 30], [104.01, 30], [104.01, 30.01], [104.0, 30.01],
                    [104.005, 30.005], [104, 30]]  # 非简单环
        feats = [{"type": "Feature", "id": "b1", "properties": {},
                  "geometry": {"type": "Polygon", "coordinates": [bad_ring]}}]
        r = generalize_features(
            feats, geometry_policy=_simplify(50.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        out_geom = r.feature_collection["features"][0]["geometry"]
        if r.receipt["geometry_simplified"] == 0:
            assert r.receipt["geometry_fallback"] >= 1
            assert out_geom == feats[0]["geometry"]


class TestBoundedSimplifyPolygons:
    def test_area_error_bounded_and_valid(self):
        feats = [{"type": "Feature", "id": "p1", "properties": {},
                  "geometry": _square(104.0, 30.0)}]
        r = generalize_features(
            feats, geometry_policy=_simplify(50.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        g = shape(r.feature_collection["features"][0]["geometry"])
        assert g.is_valid
        assert r.receipt["max_area_error_ratio_observed"] <= 0.02

    def test_holes_preserved_or_fallback(self):
        ring = [[104, 30], [104.02, 30], [104.02, 30.02], [104, 30.02], [104, 30]]
        hole = [[104.005, 30.005], [104.01, 30.005], [104.01, 30.01],
                [104.005, 30.01], [104.005, 30.005]]
        feats = [{"type": "Feature", "id": "h1", "properties": {},
                  "geometry": {"type": "Polygon", "coordinates": [ring, hole]}}]
        r = generalize_features(
            feats, geometry_policy=_simplify(80.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        g = shape(r.feature_collection["features"][0]["geometry"])
        # 洞必须还在（或整要素回退原几何 —— 不允许静默丢洞）
        if r.receipt["geometry_simplified"] == 1:
            assert len(g.interiors) == 1
        else:
            assert r.receipt["geometry_fallback"] == 1
            assert len(g.interiors) == 1

    def test_small_ring_intentional_drop_only_with_policy(self):
        # min_ring_area_m2 缺省 None：微洞不得消失；设置阈值后可显式丢弃并计数
        ring = [[104, 30], [104.02, 30], [104.02, 30.02], [104, 30.02], [104, 30]]
        hole = [[104.005, 30.005], [104.0051, 30.005], [104.0051, 30.0051],
                [104.005, 30.0051], [104.005, 30.005]]  # ~11m × 11m 微洞
        feats = [{"type": "Feature", "id": "s1", "properties": {},
                  "geometry": {"type": "Polygon", "coordinates": [ring, hole]}}]
        strict = generalize_features(
            feats, geometry_policy=_simplify(30.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        g = shape(strict.feature_collection["features"][0]["geometry"])
        assert len(g.interiors) == 1 or strict.receipt["geometry_fallback"] == 1
        lenient = generalize_features(
            feats, geometry_policy=_simplify(
                30.0, min_ring_area_m2=500.0, topology_risk="medium"),
            selection_policy=_no_sel(), latitude_hint=30.0)
        assert lenient.receipt["rings_dropped"] >= 1

    def test_multipolygon_parts_preserved(self):
        mp = {"type": "MultiPolygon", "coordinates": [
            [_square(104.0, 30.0)["coordinates"][0]],
            [_square(104.5, 30.5)["coordinates"][0]],
        ]}
        feats = [{"type": "Feature", "id": "m1", "properties": {}, "geometry": mp}]
        r = generalize_features(
            feats, geometry_policy=_simplify(50.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        g = shape(r.feature_collection["features"][0]["geometry"])
        assert g.geom_type == "MultiPolygon" and len(g.geoms) == 2


class TestSharedBoundaryProtection:
    def test_adjacent_polygons_share_anchor_vertices(self):
        # 两个共享竖边的相邻方形（成都 ~30.66°N）
        a = {"type": "Polygon", "coordinates": [[
            [104.0, 30.0], [104.01, 30.0], [104.01, 30.01],
            [104.0, 30.01], [104.0, 30.0]]]}
        b = {"type": "Polygon", "coordinates": [[
            [104.01, 30.0], [104.02, 30.0], [104.02, 30.01],
            [104.01, 30.01], [104.01, 30.0]]]}
        feats = [
            {"type": "Feature", "id": "a", "properties": {}, "geometry": a},
            {"type": "Feature", "id": "b", "properties": {}, "geometry": b},
        ]
        r = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="shared_boundary_protect", tolerance=300.0,
                tolerance_units="meters_ground"),
            selection_policy=_no_sel(),
            latitude_hint=30.0,
        )
        # 共享边锚点（(104.01,30.0) 与 (104.01,30.01)）在两要素的
        # 输出坐标串中逐坐标存活（严格断言：1e-9 量化内必须出现）
        outs = {f["id"]: shape(f["geometry"])
                for f in r.feature_collection["features"]}
        for anchor in ((104.01, 30.0), (104.01, 30.01)):
            for fid, geom in outs.items():
                coords = set(
                    (round(x, 9), round(y, 9))
                    for x, y in (
                        geom.exterior.coords if geom.geom_type == "Polygon"
                        else [c for p in geom.geoms for c in p.exterior.coords]))
                assert (round(anchor[0], 9), round(anchor[1], 9)) in coords, (
                    f"anchor {anchor} lost in feature {fid}")
        assert r.receipt["strategy"] == "shared_boundary_protect"
        assert r.receipt["anchors_retained"] == r.receipt["anchors_total"]


class TestStrategiesNoOp:
    def test_safe_no_generalization_keeps_geometry_bytes(self):
        feats = [{"type": "Feature", "id": "x", "properties": {"k": 1},
                  "geometry": _line(50)}]
        before = json.dumps(feats[0]["geometry"], sort_keys=True)
        r = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="safe_no_generalization", topology_risk="high"),
            selection_policy=_no_sel(), latitude_hint=30.0)
        after = json.dumps(
            r.feature_collection["features"][0]["geometry"], sort_keys=True)
        assert before == after
        assert r.receipt["geometry_simplified"] == 0


class TestSelection:
    def test_importance_selection_suppresses_and_discloses(self):
        feats = [
            {"type": "Feature", "id": f"r{i}", "properties": {"道路等级": cls},
             "geometry": _line(10)}
            for i, cls in enumerate(
                ["高速公路", "国道", "省道", "县道", "乡道"])
        ]
        r = generalize_features(
            feats, geometry_policy=_simplify(100.0),
            selection_policy=FeatureSelectionPolicy(
                importance_field="道路等级",
                importance_evidence="field_schema+aggregate+vocabulary",
                value_order=["高速公路", "国道", "省道", "县道", "乡道"],
                kept_values=["高速公路", "国道", "省道"]),
            latitude_hint=30.0)
        kept_ids = [f["id"] for f in r.feature_collection["features"]]
        assert kept_ids == ["r0", "r1", "r2"]
        assert r.receipt["suppressed_by_importance"] == 2
        assert r.receipt["stats_match"] is True

    def test_mandatory_features_survive_selection(self):
        # keep-me 的等级（村道）在 kept_values 之外 —— 仅靠 mandatory 存活
        feats = [
            {"type": "Feature", "id": "keep-me", "properties": {"道路等级": "村道"},
             "geometry": _line(10)},
            {"type": "Feature", "id": "drop-me", "properties": {"道路等级": "村道"},
             "geometry": _line(10)},
        ]
        r = generalize_features(
            feats, geometry_policy=_simplify(100.0),
            selection_policy=FeatureSelectionPolicy(
                importance_field="道路等级",
                importance_evidence="field_schema+aggregate+vocabulary",
                value_order=["乡道", "村道"], kept_values=["乡道"]),
            latitude_hint=30.0, mandatory_ids={"keep-me"})
        kept_ids = [f["id"] for f in r.feature_collection["features"]]
        assert "keep-me" in kept_ids and "drop-me" not in kept_ids
        assert r.receipt["mandatory_saved"] == 1
        assert r.receipt["suppressed_by_importance"] == 1

    def test_no_evidence_no_selection(self):
        feats = [{"type": "Feature", "id": "a", "properties": {"x": 1},
                  "geometry": _line(5)}]
        r = generalize_features(
            feats, geometry_policy=_simplify(100.0),
            selection_policy=_no_sel(), latitude_hint=30.0)
        assert len(r.feature_collection["features"]) == 1


class TestNonDestructiveAndDeterministic:
    def test_input_features_not_mutated(self):
        feats = [{"type": "Feature", "id": "a", "properties": {"n": 1},
                  "geometry": _line(30)}]
        snapshot = json.dumps(feats, sort_keys=True)
        generalize_features(
            feats, geometry_policy=_simplify(150.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        assert json.dumps(feats, sort_keys=True) == snapshot

    def test_deterministic_output(self):
        feats = [
            {"type": "Feature", "id": f"d{i}", "properties": {"k": i},
             "geometry": _square(104.0 + i * 0.1, 30.0)}
            for i in range(5)
        ]
        r1 = generalize_features(
            feats, geometry_policy=_simplify(120.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        r2 = generalize_features(
            feats, geometry_policy=_simplify(120.0), selection_policy=_no_sel(),
            latitude_hint=30.0)
        assert json.dumps(r1.feature_collection, sort_keys=True) == json.dumps(
            r2.feature_collection, sort_keys=True)

    def test_empty_and_geometryless_inputs(self):
        feats = [
            {"type": "Feature", "id": "e1", "properties": {}, "geometry": None},
        ]
        r = generalize_features(
            feats, geometry_policy=_simplify(10.0), selection_policy=_no_sel(),
            latitude_hint=0.0)
        assert len(r.feature_collection["features"]) == 1
        assert r.feature_collection["features"][0]["geometry"] is None


class TestToleranceUnits:
    def test_degrees_unit_converted_to_meters(self):
        feats = [{"type": "Feature", "id": "u1", "properties": {},
                  "geometry": _line(100)}]
        deg = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="bounded_simplify", tolerance=0.0005,
                tolerance_units="degrees"),
            selection_policy=_no_sel(), latitude_hint=0.0)
        m = generalize_features(
            feats,
            geometry_policy=GeometryGeneralizationPolicy(
                strategy="bounded_simplify",
                tolerance=0.0005 * 111320.0 * math.cos(math.radians(0.0)),
                tolerance_units="meters_ground"),
            selection_policy=_no_sel(), latitude_hint=0.0)
        # 度容差（赤道换算）与米容差产物一致
        assert json.dumps(deg.feature_collection, sort_keys=True) == json.dumps(
            m.feature_collection, sort_keys=True)
