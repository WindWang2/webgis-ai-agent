"""ADR-0153 P3：CRS 自动识别 —— 4 类投影（4326/3857/CGCS2000/UTM）识别准确。

验收口径（任务书 §5）：4 类投影识别准确、人工传参场景降为 0（缺声明时
不再静默假设 4326，而是给出带证据的推断）、低置信落 low_confidence。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.spatial_quality_gate import infer_crs

# 真实投影坐标（pyproj 生成，rounded）—— 测试不以记忆的 EPSG 常数为锚，
# 而以「推断结果能被 pyproj round-trip 回正确经纬度」为准绳。
from pyproj import Transformer


def _pt(lon, lat, epsg):
    x, y = Transformer.from_crs("EPSG:4326", epsg, always_xy=True).transform(lon, lat)
    return [round(x, 3), round(y, 3)]


def _fc(coords, crs=None):
    data = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": c}}
            for c in coords
        ],
    }
    if crs:
        data["crs"] = {"type": "name", "properties": {"name": crs}}
    return data


CASES_DIR = Path(__file__).parent.parent / "cartography" / "fixtures" / "quality_cases"


def test_fixture_inputs_are_loadable():
    # 15 个诊断码 fixture 的 input 都是合法 JSON（矩阵回归的数据基础）。
    count = 0
    for case_dir in CASES_DIR.iterdir():
        if not (case_dir / "expected.json").exists():
            continue
        json.loads((case_dir / "input.geojson").read_text(encoding="utf-8"))
        count += 1
    assert count >= 15


class TestEpsg4326:
    def test_degree_range_high_confidence(self):
        inf = infer_crs(_fc([[116.4, 39.9], [0.5, 0.5]]))
        assert inf["crs"] == "EPSG:4326"
        assert inf["confidence"] == "high"
        assert inf["low_confidence"] is False
        assert inf["method"] == "degree_range"

    def test_china_extent_notes_cgcs_candidate(self):
        inf = infer_crs(_fc([[104.0, 30.6]]))
        assert inf["crs"] == "EPSG:4326"
        assert "4490" in inf["evidence"].get("note", "")


class TestEpsg3857:
    def test_declared_contradiction_infers_3857(self):
        # Beijing in Web Mercator, declared as 4326 → SUSPICIOUS_CRS 根因。
        coords = _pt(116.4, 39.9, "EPSG:3857")
        inf = infer_crs(_fc([coords], crs="EPSG:4326"))
        assert inf["crs"] == "EPSG:3857"
        assert inf["confidence"] == "medium"
        assert inf["method"] == "declared_geographic_contradicted_web_mercator"

    def test_no_declaration_still_3857(self):
        coords = _pt(116.4, 39.9, "EPSG:3857")
        inf = infer_crs(_fc([coords]))
        assert inf["crs"] == "EPSG:3857"
        assert inf["method"] == "web_mercator_envelope"


class TestCgcs2000:
    def test_geographic_by_name_hint(self):
        inf = infer_crs(_fc([[104.0, 30.6]]), crs_name_hint="CGCS2000 国家大地坐标系")
        assert inf["crs"] == "EPSG:4490"
        assert inf["method"] == "hint_cgcs2000_geographic"

    def test_zone_prefixed_easting_is_china_only_convention(self):
        # 3° 带号前推 easting（zone 42, CM 126°E）+ 中国经度锚点。
        coords = _pt(126.0, 45.0, "EPSG:4530")
        inf = infer_crs(_fc([coords]), lon_hint=126.0)
        assert inf["crs"] == "EPSG:4530"
        assert inf["method"] == "gk_zone_prefixed_cgcs2000_zone"

    def test_zone_prefixed_without_hint_low_confidence(self):
        coords = _pt(126.0, 45.0, "EPSG:4530")
        inf = infer_crs(_fc([coords]))
        assert inf["crs"] is None
        assert inf["low_confidence"] is True

    def test_roundtrip_cgcs2000_zone(self):
        # 推断出的 EPSG 必须能 round-trip 回源经纬度（识别"准确"的准绳）。
        coords = _pt(126.0, 45.0, "EPSG:4530")
        inf = infer_crs(_fc([coords]), lon_hint=126.0)
        lon, lat = Transformer.from_crs(inf["crs"], "EPSG:4326", always_xy=True).transform(*coords)
        assert abs(lon - 126.0) < 1e-4
        assert abs(lat - 45.0) < 1e-4


class TestUtm:
    def test_non_china_lon_hint_gives_utm(self):
        # 法兰克福（8.68°E, 50.11°N）→ UTM 32N；CM 制 GK 坐标 + 非中国锚点。
        coords = _pt(8.68, 50.11, "EPSG:25832")  # ETRS89 / UTM 32N 同族量级
        inf = infer_crs(_fc([coords]), lon_hint=8.68, lat_hint=50.11)
        assert inf["crs"] == "EPSG:32632"
        assert inf["method"] == "utm_shape_zone"

    def test_explicit_utm_hint_overrides_china_prior(self):
        # lon 123 在 zone 51 内部（126 是 51/52 边界，公式判 52 是对的）。
        coords = _pt(123.0, 45.0, "EPSG:32651")
        inf = infer_crs(_fc([coords]), lon_hint=123.0, lat_hint=45.0, crs_name_hint="UTM WGS84")
        assert inf["crs"] == "EPSG:32651"

    def test_gk_shape_without_anchor_low_confidence(self):
        coords = _pt(126.0, 45.0, "EPSG:32651")
        inf = infer_crs(_fc([coords]))
        assert inf["crs"] is None
        assert inf["confidence"] == "low"
        assert inf["low_confidence"] is True
        assert "indeterminate" in inf["method"]

    def test_utm_roundtrip(self):
        coords = _pt(8.68, 50.11, "EPSG:25832")
        inf = infer_crs(_fc([coords]), lon_hint=8.68, lat_hint=50.11)
        lon, lat = Transformer.from_crs(inf["crs"], "EPSG:4326", always_xy=True).transform(*coords)
        assert abs(lon - 8.68) < 1e-3
        assert abs(lat - 50.11) < 1e-3


class TestDegenerateAndExtreme:
    def test_null_island_cluster_low_confidence(self):
        inf = infer_crs(_fc([[0.0, 0.0]]))
        assert inf["crs"] is None
        assert inf["method"] == "null_island_degenerate"
        assert inf["low_confidence"] is True

    def test_extreme_coordinates_not_inferred(self):
        inf = infer_crs(_fc([[1e11, 5e10]]))
        assert inf["crs"] is None
        assert inf["method"] == "extreme_coordinates"
        assert inf["low_confidence"] is True

    def test_empty_collection(self):
        inf = infer_crs({"type": "FeatureCollection", "features": []})
        assert inf["crs"] is None
        assert inf["method"] == "no_parseable_geometry"

    def test_declared_projected_honored(self):
        inf = infer_crs(_fc([[486594.2, 4987329.5]]), declared_crs="EPSG:32632")
        assert inf["crs"] == "EPSG:32632"
        assert inf["method"] == "declared_projected"


@pytest.mark.cartography
def test_manual_declaration_beats_inference_in_planner():
    """人工传参优先：declared projected CRS 压过推断（验收：人工传参场景
    降为 0 指的是「必须人工才能走通」，声明仍是最优先证据）。"""
    from app.services.spatial_repair_pipeline import plan_repair_ops
    from app.services.spatial_quality_service import SpatialQualityEngine

    data = _fc([_pt(116.4, 39.9, "EPSG:3857")], crs="EPSG:4326")
    report = SpatialQualityEngine.audit_dataset(data, crs="EPSG:4326")

    # 无人工声明：推断 3857 兜底 → crs_transform 计划给出。
    plan_auto = plan_repair_ops(report, crs_inference=infer_crs(data))
    assert "crs_transform" in plan_auto.ops
    assert plan_auto.source_crs == "EPSG:3857"

    # 人工声明 3857：直接采信人工值（不经推断）。
    plan_manual = plan_repair_ops(
        report, declared_crs="EPSG:3857", crs_inference=infer_crs(data)
    )
    assert plan_manual.source_crs == "EPSG:3857"
    assert "manual_declaration" in plan_manual.destructive_decisions["crs_transform"]
