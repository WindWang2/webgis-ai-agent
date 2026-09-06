"""Dataset Profile V3 —— 有界剖析契约与纯函数剖析器测试。"""
import math

import pytest

from app.lib.data.profile import (
    DEFAULT_MAX_SCAN_ROWS,
    DatasetProfileV3,
    ProfileQuality,
    profile_features,
    profile_from_field_schema,
    profile_rows,
    unit_hint_for_field,
)


def _fc_features(n: int = 100, *, bad_coords: int = 0, empty: int = 0):
    feats = []
    for i in range(n):
        if i < empty:
            feats.append({"geometry": None, "properties": {"v": i}})
            continue
        lng = 116.0 + (i % 50) * 0.01
        lat = 39.0 + (i % 50) * 0.01
        if i < bad_coords:
            lng, lat = 200.0, 500.0  # 出界（假 CRS 信号）
        feats.append(
            {
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": {"v": i, "cat": f"c{i % 5}", "when": f"2024-01-{(i % 28) + 1:02d}"},
            }
        )
    return feats


class TestProfileFeatures:
    def test_complete_scan_stats(self):
        feats = _fc_features(100)
        vp, q = profile_features(feats, crs="EPSG:4326")
        assert q is ProfileQuality.COMPLETE
        assert vp.row_count == 100
        assert vp.scanned_rows == 100
        assert vp.geometry_types == ["Point"]
        assert vp.extent[0] == pytest.approx(116.0)
        assert vp.fields["v"].min == 0.0
        assert vp.fields["v"].max == 99.0
        assert vp.fields["v"].mean == pytest.approx(49.5)
        assert vp.fields["v"].unique_count == 100
        assert vp.fields["cat"].unique_count == 5
        assert vp.fields["cat"].samples

    def test_temporal_and_unit_hints(self):
        feats = [
            {"geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
             "properties": {"acquisition_date": "2024-05-01", "pop_density": 12.5, "name": "x"}},
        ]
        vp, _ = profile_features(feats)
        assert "acquisition_date" in vp.temporal_fields
        assert vp.fields["acquisition_date"].temporal_hint
        assert vp.fields["pop_density"].unit_hint  # 命中 ratio/密度类命名约定

    def test_deterministic_stride_sampling(self):
        feats = _fc_features(300)
        a = profile_features(feats, max_scan_rows=100)
        b = profile_features(feats, max_scan_rows=100)
        assert a == b  # 同输入必同剖析（确定性采样）
        vp, q = a
        assert q is ProfileQuality.SAMPLED
        assert vp.row_count == 300       # 总行数诚实
        assert vp.scanned_rows == 100    # 扫描只覆盖样本

    def test_bad_coordinates_counted(self):
        vp, _ = profile_features(_fc_features(50, bad_coords=5), crs="EPSG:4326")
        assert vp.impossible_coordinate_count == 5

    def test_projected_crs_not_flagged(self):
        # 投影 CRS（米制）坐标出经纬度界是正常的，不计入 impossible
        feats = [{"geometry": {"type": "Point", "coordinates": [500000.0, 4000000.0]},
                  "properties": {}}]
        vp, _ = profile_features(feats, crs="EPSG:32650")
        assert vp.impossible_coordinate_count == 0
        vp2, _ = profile_features(feats, crs="EPSG:4326")
        assert vp2.impossible_coordinate_count == 1

    def test_empty_and_zero_zero(self):
        feats = [
            {"geometry": None, "properties": {}},
            {"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}},
            {"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}},
            {"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}},
            {"geometry": {"type": "Point", "coordinates": [0, 0]}, "properties": {}},
        ]
        vp, _ = profile_features(feats)
        assert vp.empty_geometry_count == 1
        assert vp.zero_zero_coordinate_count == 4  # > max(3, 5%) → 质量检查会报

    def test_fields_truncation(self):
        feats = [
            {
                "geometry": {"type": "Point", "coordinates": [1.0, 2.0]},
                "properties": {f"f{i:03d}": i for i in range(80)},
            }
        ]
        vp, _ = profile_features(feats)
        assert vp.fields_truncated
        assert len(vp.fields) == 64


class TestProfileRows:
    def test_candidate_keys_and_coords(self):
        rows = [{"id": i, "lng": 116 + i * 0.1, "lat": 39.0, "city": f"c{i % 3}"} for i in range(200)]
        tp, q = profile_rows(rows)
        assert q is ProfileQuality.COMPLETE
        assert "id" in tp.candidate_keys
        assert set(tp.coordinate_candidates) == {"lng", "lat"}
        assert "city" not in tp.candidate_keys  # 基数低

    def test_time_candidates_by_value(self):
        rows = [{"d": "2024-01-0%d" % (i % 9 + 1), "v": i} for i in range(9)]
        tp, _ = profile_rows(rows)
        assert "d" in tp.time_candidates

    def test_sampling_declares_coverage(self):
        rows = [{"id": i} for i in range(2000)]
        tp, q = profile_rows(rows, max_scan_rows=500)
        assert q is ProfileQuality.SAMPLED
        assert tp.row_count == 2000
        assert tp.scanned_rows == 500


class TestProfileFromFieldSchema:
    def test_projection_is_partial_and_honest(self):
        vp, q = profile_from_field_schema(
            {"pop": {"type": "number", "null_count": 3, "min": 1, "max": 9,
                     "sampleValues": [1, 5]}},
            complete=True,
            row_count=10,
            geometry_types=["Point", "Point"],
            bbox=[0, 0, 1, 1],
        )
        assert q is ProfileQuality.PARTIAL
        assert vp.row_count == 10
        assert vp.scanned_rows == 0          # 没扫就是没扫
        assert vp.fields["pop"].mean is None  # 证据缺失不虚构
        assert vp.fields["pop"].null_rate == 0.3
        assert vp.fields["pop"].unit_hint == "persons"
        assert not vp.fields_truncated

    def test_truncated_schema_flagged(self):
        vp, _ = profile_from_field_schema({"a": {"type": "string"}}, complete=False, row_count=5)
        assert vp.fields_truncated


class TestDatasetProfileV3:
    def test_summary_bounded(self):
        import json

        vp, _ = profile_features(_fc_features(10))
        prof = DatasetProfileV3(
            target_ref="ref:x", category="vector", crs="EPSG:4326",
            extent=vp.extent, vector=vp, profile_quality=ProfileQuality.COMPLETE,
        )
        s = prof.summary(max_chars=1200)
        assert len(json.dumps(s, ensure_ascii=False)) <= 1200
        assert s["category"] == "vector"
        assert s["row_count"] == 10

    def test_invalid_category_rejected(self):
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            DatasetProfileV3(target_ref="x", category="not-a-cat")

    def test_failed_quality_allowed(self):
        prof = DatasetProfileV3(
            target_ref="x", category="unknown",
            profile_quality=ProfileQuality.FAILED, diagnostics=["boom"],
        )
        assert prof.profile_quality is ProfileQuality.FAILED


def test_unit_hint_table():
    assert unit_hint_for_field("population") == "persons"
    assert unit_hint_for_field("area_km2") == "km2"
    assert unit_hint_for_field("temperature_c") == "celsius"
    assert unit_hint_for_field("elevation") == "meters"
    assert unit_hint_for_field("name") == ""
