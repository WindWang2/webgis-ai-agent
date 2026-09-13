"""ADR-0153 P6：3 个新 op + remove_empty 扩展 —— 语义与边界（空集/单要素/全重叠）。"""
from __future__ import annotations

import copy

import pytest
from shapely.geometry import shape as _shape

from app.services.spatial_repair_pipeline import SpatialRepairPipeline


def _poly(minx, miny, maxx, maxy):
    return {
        "type": "Feature",
        "properties": {},
        "geometry": {
            "type": "Polygon",
            "coordinates": [[
                [minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny],
            ]],
        },
    }


def _run(data, ops, **kwargs):
    return SpatialRepairPipeline.repair_dataset_with_lineage(
        data, ops=ops, op_params=kwargs.pop("op_params", {}), **kwargs
    )


class TestFixTopologyOverlap:
    def test_difference_mode_resolves_overlap(self):
        data = {"type": "FeatureCollection", "features": [
            _poly(0, 0, 2, 2), _poly(1, 0, 3, 2),
        ]}
        orig = copy.deepcopy(data)
        repaired, logs, evidence, lineage = _run(
            data, ["fix_topology_overlap"],
            op_params={"fix_topology_overlap": {"mode": "difference"}},
        )
        assert data == orig  # 非破坏
        g0 = _shape(repaired["features"][0]["geometry"])
        g1 = _shape(repaired["features"][1]["geometry"])
        # 先入要素完整，后入要素被裁剪：重叠区 x∈[1,2]×y∈[0,2] 面积 2，
        # g1 面积 4-2=2，重叠消除。
        assert g0.area == pytest.approx(4.0)
        assert g1.area == pytest.approx(2.0)
        assert g0.intersection(g1).area == pytest.approx(0.0)
        assert evidence[0]["features_affected"] == 1

    def test_flag_mode_records_pairs_without_change(self):
        data = {"type": "FeatureCollection", "features": [
            _poly(0, 0, 2, 2), _poly(1, 0, 3, 2),
        ]}
        repaired, logs, evidence, lineage = _run(
            data, ["fix_topology_overlap"],
            op_params={"fix_topology_overlap": {"mode": "flag"}},
        )
        assert _shape(repaired["features"][1]["geometry"]).area == pytest.approx(4.0)
        flags = repaired["ac04_quality_flags"]
        assert flags["topology_overlap_pairs"] == [{"first": 0, "second": 1}]

    def test_boundary_empty_and_single_feature(self):
        empty = {"type": "FeatureCollection", "features": []}
        for data in (empty, {"type": "FeatureCollection", "features": [_poly(0, 0, 1, 1)]}):
            orig = copy.deepcopy(data)
            repaired, logs, evidence, lineage = _run(
                data, ["fix_topology_overlap"],
                op_params={"fix_topology_overlap": {"mode": "difference"}},
            )
            assert data == orig
            assert evidence == [] or evidence[0]["features_affected"] == 0

    def test_boundary_all_overlapping_budget(self):
        # 5 个两两重叠的面：pair 预算内全部解析，重叠对消解后总体重叠消失。
        data = {"type": "FeatureCollection", "features": [
            _poly(i, 0, i + 2, 2) for i in range(5)
        ]}
        repaired, logs, evidence, lineage = _run(
            data, ["fix_topology_overlap"],
            op_params={"fix_topology_overlap": {"mode": "difference"}},
        )
        geoms = [_shape(f["geometry"]) for f in repaired["features"]]
        # 先到先得：任意后入面不被比它晚的面裁剪，逐对检查无残留重叠。
        for i in range(len(geoms)):
            for j in range(i + 1, len(geoms)):
                # j 只保留相对所有更早面的 difference 残余；i<j 时
                # intersection 必须为 0（j 被 i 裁剪过）。
                assert geoms[i].intersection(geoms[j]).area == pytest.approx(0.0, abs=1e-12)


class TestFixGaps:
    def test_snap_mode_closes_gap(self):
        data = {"type": "FeatureCollection", "features": [
            _poly(0, 0, 1, 1),
            _poly(1.000005, 0, 2, 1),
        ]}
        orig = copy.deepcopy(data)
        repaired, logs, evidence, lineage = _run(
            data, ["fix_gaps"],
            op_params={"fix_gaps": {"mode": "snap", "tolerance": 1e-5}},
        )
        assert data == orig
        g1 = _shape(repaired["features"][1]["geometry"])
        g0 = _shape(repaired["features"][0]["geometry"])
        assert g1.distance(g0) < 1e-5
        assert evidence[0]["features_affected"] >= 1

    def test_flag_mode_default(self):
        data = {"type": "FeatureCollection", "features": [
            _poly(0, 0, 1, 1),
            _poly(1.000005, 0, 2, 1),
        ]}
        repaired, logs, evidence, lineage = _run(
            data, ["fix_gaps"],
            op_params={"fix_gaps": {"mode": "flag", "tolerance": 1e-5}},
        )
        assert repaired["ac04_quality_flags"]["topology_gap_pairs"][0]["first"] == 0
        assert _shape(repaired["features"][1]["geometry"]).area == pytest.approx(0.999995)

    def test_boundary_two_distant_polygons_no_op(self):
        data = {"type": "FeatureCollection", "features": [
            _poly(0, 0, 1, 1), _poly(10, 10, 11, 11),
        ]}
        repaired, logs, evidence, lineage = _run(
            data, ["fix_gaps"],
            op_params={"fix_gaps": {"mode": "snap", "tolerance": 1e-5}},
        )
        assert evidence == []


class TestDropOutliersOrFlag:
    def _outlier_data(self):
        features = []
        for i in range(15):
            features.append({
                "type": "Feature", "properties": {"idx": i, "val": float(10 + i)},
                "geometry": None,
            })
        features.append({
            "type": "Feature", "properties": {"idx": 15, "val": 1e6}, "geometry": None,
        })
        return {"type": "FeatureCollection", "features": features}

    def test_flag_mode_default(self):
        data = self._outlier_data()
        orig = copy.deepcopy(data)
        repaired, logs, evidence, lineage = _run(
            data, ["drop_outliers_or_flag"],
            op_params={"drop_outliers_or_flag": {"mode": "flag", "fields": [
                {"field": "val", "outlier_ratio": 0.06, "upper": 30.0}
            ]}},
        )
        assert data == orig
        assert len(repaired["features"]) == 16  # 只标记不删
        flags = repaired["ac04_quality_flags"]
        assert flags["outlier_fields"][0]["field"] == "val"
        assert flags["outlier_feature_indices"] == [15]

    def test_drop_mode_removes_only_outliers(self):
        data = self._outlier_data()
        repaired, logs, evidence, lineage = _run(
            data, ["drop_outliers_or_flag"],
            op_params={"drop_outliers_or_flag": {"mode": "drop", "fields": [
                {"field": "val", "outlier_ratio": 0.06, "upper": 30.0}
            ]}},
        )
        assert len(repaired["features"]) == 15
        assert all(f["properties"]["val"] <= 100 for f in repaired["features"])
        lineage_entry = next(entry for entry in lineage if entry["op"] == "drop_outliers_or_flag")
        assert lineage_entry["before_count"] == 16
        assert lineage_entry["after_count"] == 15

    def test_three_sigma_fallback_without_fields(self):
        data = self._outlier_data()
        repaired, logs, evidence, lineage = _run(
            data, ["drop_outliers_or_flag"],
            op_params={"drop_outliers_or_flag": {"mode": "flag"}},
        )
        flags = repaired["ac04_quality_flags"]
        assert flags["outlier_fields"]  # 3σ 回退命中 val 字段


class TestAttributeDropOrFlag:
    def _null_heavy_data(self):
        return {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {"a": 1, "sparse": 1}, "geometry": None},
            {"type": "Feature", "properties": {"a": 2}, "geometry": None},
            {"type": "Feature", "properties": {"a": 3}, "geometry": None},
        ]}

    def test_flag_mode_default(self):
        data = self._null_heavy_data()
        repaired, logs, evidence, lineage = _run(
            data, ["attribute_drop_or_flag"],
            op_params={"attribute_drop_or_flag": {"mode": "flag", "fields": ["sparse"]}},
        )
        assert repaired["ac04_quality_flags"]["null_heavy_fields"] == ["sparse"]
        assert all("sparse" in f["properties"] for f in repaired["features"][:1])

    def test_drop_column_mode(self):
        data = self._null_heavy_data()
        repaired, logs, evidence, lineage = _run(
            data, ["attribute_drop_or_flag"],
            op_params={"attribute_drop_or_flag": {"mode": "drop_column", "fields": ["sparse"]}},
        )
        assert all("sparse" not in f["properties"] for f in repaired["features"])
        assert all("a" in f["properties"] for f in repaired["features"])


class TestRemoveEmptyZeroCoords:
    def test_drop_zero_coordinates_disabled_by_default(self):
        data = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
        ]}
        repaired, logs, evidence, lineage = _run(data, ["remove_empty"])
        assert len(repaired["features"]) == 1  # 默认保留（advisory 路径）

    def test_drop_zero_coordinates_enabled(self):
        data = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [0.0, 0.0]}},
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point", "coordinates": [116.4, 39.9]}},
        ]}
        repaired, logs, evidence, lineage = _run(
            data, ["remove_empty"],
            op_params={"remove_empty": {"drop_zero_coordinates": True}},
        )
        assert len(repaired["features"]) == 1
        # mapping() 输出的坐标是 tuple —— 与列表序相等即可。
        assert list(repaired["features"][0]["geometry"]["coordinates"]) == [116.4, 39.9]
        lineage_entry = next(entry for entry in lineage if entry["op"] == "remove_empty")
        assert lineage_entry["before_count"] == 2
        assert lineage_entry["after_count"] == 1


@pytest.mark.cartography
def test_non_destructive_across_all_new_ops():
    """非破坏红线：所有新 op 组合跑完，原输入 deepcopy 级不受污染。"""
    data = {
        "type": "FeatureCollection",
        "features": [
            _poly(0, 0, 2, 2),
            {**_poly(1, 0, 3, 2), "properties": {"v": 2}},
            _poly(0.0, 0.0, 0.0, 0.0),
        ],
    }
    data["features"][2]["geometry"]["coordinates"] = [
        [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.01], [0.0, 0.0]]
    ]
    orig = copy.deepcopy(data)
    _run(
        data,
        ["remove_empty", "fix_topology_overlap", "fix_gaps",
         "drop_outliers_or_flag", "attribute_drop_or_flag"],
        op_params={
            "fix_topology_overlap": {"mode": "difference"},
            "fix_gaps": {"mode": "snap", "tolerance": 1e-5},
            "remove_empty": {"drop_zero_coordinates": True},
            "drop_outliers_or_flag": {"mode": "drop", "fields": []},
            "attribute_drop_or_flag": {"mode": "drop_column", "fields": ["sparse"]},
        },
    )
    assert data == orig
