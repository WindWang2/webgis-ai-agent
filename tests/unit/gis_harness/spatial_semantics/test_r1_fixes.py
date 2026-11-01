"""R1 独立评审修复回归 —— 每条 finding 的锁定测试（P0×1 + P1×6 + P2×8）。

对应 INDEPENDENT_REVIEW.md R1 findings 编号；断言即清偿证据。
"""
from __future__ import annotations

import pytest
from pyproj import Transformer

from app.services.gis_harness.spatial_semantics import (
    SpatialOperationSpec,
    SpatialSemanticContract,
    preflight_for_feature_collection,
)
from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry

_TR = Transformer.from_crs("EPSG:4326", "EPSG:32648", always_xy=True)

BUFFER_SPEC = dict(operation="buffer", distance=500, distance_unit="m")


def _pts(coords, crs=None):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point", "coordinates": list(c)}} for c in coords]}
    if crs:
        fc["crs"] = crs
    return fc


# ══ P0：field 分母不伪造人口口径 ═══════════════════════════════════════

@pytest.mark.asyncio
async def test_p0_field_denominator_no_fabricated_per_10k_unit() -> None:
    """field+population 未请求 per_万人 → density_unit=""（不伪造）。"""
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    zones = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [{
            "type": "Feature",
            "properties": {"district": "d1", "population": 10000},
            "geometry": {"type": "Polygon", "coordinates": [[
                [104.05, 30.65], [104.07, 30.65],
                [104.07, 30.67], [104.05, 30.67], [104.05, 30.65]]]}}]}
    pts = _pts([(104.06, 30.66), (104.061, 30.661), (104.062, 30.662)],
               "EPSG:4326")
    res = await r.dispatch("spatial_aggregate", {
        "points": pts, "polygons": zones,
        "denominator_kind": "field", "denominator": "population"})
    assert res["success"] is True
    sem = res["spatial_semantics"]
    assert sem["density_unit"] == ""  # 库层 per-person，不合成 "个/万人"
    rate = res["data"]["features"][0]["properties"]["rate"]
    assert rate == pytest.approx(3 / 10000)  # per-person 真值，未换算


@pytest.mark.asyncio
async def test_p0_field_per_10k_request_converts_values() -> None:
    """field+population + per_10k_persons 请求 → ×1e4 换算 + 口径一致。"""
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    zones = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [{
            "type": "Feature",
            "properties": {"district": "d1", "population": 10000},
            "geometry": {"type": "Polygon", "coordinates": [[
                [104.05, 30.65], [104.07, 30.65],
                [104.07, 30.67], [104.05, 30.67], [104.05, 30.65]]]}}]}
    pts = _pts([(104.06, 30.66), (104.061, 30.661), (104.062, 30.662)],
               "EPSG:4326")
    res = await r.dispatch("spatial_aggregate", {
        "points": pts, "polygons": zones,
        "denominator_kind": "field", "denominator": "population",
        "requested_density_unit": "per_10k_persons"})
    assert res["success"] is True
    sem = res["spatial_semantics"]
    assert sem["density_unit"] == "个/万人"
    rate = res["data"]["features"][0]["properties"]["rate"]
    assert rate == pytest.approx(3.0)  # 3 个 / 1 万人 —— 量级与口径一致
    conv = res["scientific_evidence"]["normalization"]["rate_unit_conversion"]
    assert "per_10k_persons" in conv and "factor=10000" in conv


@pytest.mark.asyncio
async def test_p0_numerator_field_not_counted_as_ge() -> None:
    """numerator_field（字段求和）分子显示不带"个"。"""
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    zones = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [{
            "type": "Feature",
            "properties": {"district": "d1"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [104.05, 30.65], [104.07, 30.65],
                [104.07, 30.67], [104.05, 30.67], [104.05, 30.65]]]}}]}
    pts = {"type": "FeatureCollection", "crs": "EPSG:4326", "features": [
        {"type": "Feature",
         "properties": {"gdp_m": 500.0},
         "geometry": {"type": "Point", "coordinates": [104.06, 30.66]}}]}
    res = await r.dispatch("spatial_aggregate", {
        "points": pts, "polygons": zones,
        "denominator_kind": "area", "numerator_field": "gdp_m"})
    assert res["success"] is True
    assert res["spatial_semantics"]["density_unit"] == "Σ/km²"  # 非虚构 "个"


# ══ P1-1：unknown/assumed CRS × 度量敏感 → requires_clarification ═══════

def test_p1_integer_grid_requires_clarification() -> None:
    """局部整数网格坐标（无 crs member）不再被静默当 WGS84 度缓冲。"""
    fc = _pts([(50, 70), (120, 45)])  # 全整数、包络内
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.contract.crs_source == "unknown"
    assert r.status == "requires_clarification"
    assert "SSC_UNKNOWN_CRS" in [rc.code for rc in r.verdict.reason_codes]


# ══ P1-2：近全球 bbox 不再误判单带 ═════════════════════════════════════

def test_p1_near_global_bbox_goes_geodesic() -> None:
    """bbox 触双边 ±180 的近全球数据 → 测地（采样占据弧证据），非单带。"""
    lons = [-177.5 + 5.0 * i for i in range(71)]  # -177.5..177.5 全球散布
    fc = _pts([(lon, 10.0 + (i % 30)) for i, lon in enumerate(lons)],
              "EPSG:4326")
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.calculation.mode == "geodesic", (
        f"近全球数据被误判 {r.calculation.mode} {r.calculation.working_crs}")
    assert "SSC_MULTIZONE_SPAN" in [rc.code for rc in r.verdict.reason_codes]


def test_p1_fiji_cluster_still_single_zone() -> None:
    """回归保护：真环绕簇（斐济）仍选 UTM 60S 单带。"""
    fc = _pts([(179.72, -17.9), (-179.98, -16.8)], "EPSG:4326")
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.calculation.working_crs == "EPSG:32760"
    assert r.calculation.mode == "projected_local"


# ══ P1-3：spatial_aggregate 点层过门 ═══════════════════════════════════

@pytest.mark.asyncio
async def test_p1_aggregate_points_layer_gated() -> None:
    """未声明投影样**点层**（zones 正常）在工具入口被拒。"""
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    x, y = _TR.transform(104.06, 30.66)
    pts = _pts([(x, y)])  # 无 crs member，投影样
    zones = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [{
            "type": "Feature", "properties": {"district": "d1"},
            "geometry": {"type": "Polygon", "coordinates": [[
                [104.05, 30.65], [104.07, 30.65],
                [104.07, 30.67], [104.05, 30.67], [104.05, 30.65]]]}}]}
    res = await r.dispatch("spatial_aggregate", {
        "points": pts, "polygons": zones})
    assert res.get("success") is not True
    assert "SSC_CRS_UNDECLARED_NON_DEGREE" in str(res)


# ══ P1-4：口径词表外 fail-closed ══════════════════════════════════════

def test_p1_out_of_vocab_density_unit_rejected() -> None:
    fc = _pts([(104.06, 30.66)], "EPSG:4326")
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(
            operation="spatial_aggregate", denominator_kind="area",
            requested_density_denominator="per_km²"))  # 全角 ² —— 词表外
    assert r.status == "blocked"
    assert "SSC_DENOMINATOR_KIND_MISMATCH" in [
        rc.code for rc in r.verdict.reason_codes]


# ══ P1-5：per_10k_persons 经 field 载体可达 ═══════════════════════════

def test_p1_per_10k_persons_via_field_denominator_passes() -> None:
    fc = _pts([(104.06, 30.66)], "EPSG:4326")
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(
            operation="spatial_aggregate", denominator_kind="field",
            requested_density_denominator="per_10k_persons"))
    assert r.status == "pass"


# ══ P1-6：crs member code 形态识别 ════════════════════════════════════

def test_p1_crs_object_code_form_recognized() -> None:
    """{"type":"name","properties":{"code":32648}} 声明不再误 blocked。"""
    fc = _pts([(500123.45, 3601234.56)],
              crs={"type": "name", "properties": {"code": 32648}})
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.contract.crs_source == "declared"
    assert r.contract.source_crs == "EPSG:32648"
    assert r.status != "blocked"


# ══ P2：杂项修复 ══════════════════════════════════════════════════════

def test_p2_area_unit_vocabulary_gate() -> None:
    with pytest.raises(Exception):
        SpatialSemanticContract(area_unit="亩")


def test_p2_no_duplicate_reason_codes() -> None:
    fc = _pts([(50, 70)])  # unknown CRS 路径
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    codes = [rc.code for rc in r.verdict.reason_codes]
    assert len(codes) == len(set(codes))  # SSC_UNKNOWN_CRS 只出现一次


def test_p2_nan_coordinates_flagged() -> None:
    fc = {
        "type": "FeatureCollection", "crs": "EPSG:4326",
        "features": [
            {"type": "Feature", "properties": {},
             "geometry": {"type": "Point",
                          "coordinates": [float("nan"), float("nan")]}}]}
    r = preflight_for_feature_collection(
        fc, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "blocked"
    assert "SSC_COORD_OUT_OF_ENVELOPE" in [
        rc.code for rc in r.verdict.reason_codes]


def test_p2_truncated_scan_does_not_falsely_disjoint() -> None:
    """扫描截断（>2000 要素）时不再误判 AOI disjoint。"""
    from app.services.gis_harness.spatial_semantics import ScopeEvidence
    feats = [{"type": "Feature", "properties": {},
              "geometry": {"type": "Point",
                           "coordinates": [116.0 + i * 0.001, 39.9]}}
             for i in range(2100)]
    feats[-1]["geometry"]["coordinates"] = [104.06, 30.66]  # 唯一在成都
    r = preflight_for_feature_collection(
        {"type": "FeatureCollection", "crs": "EPSG:4326", "features": feats},
        scope=ScopeEvidence(name="成都", bbox=[104.0, 30.5, 104.2, 30.8],
                            bbox_crs="EPSG:4326", source="user"),
        spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.contract.scope_overlap == "unknown"  # 截断扫描 → 不判 disjoint


def test_p2_contract_roundtrip_with_occupied_span() -> None:
    c = SpatialSemanticContract(lon_span_occupied=0.3, lon_center_hint=179.87)
    d = c.to_dict()
    assert d["lon_span_occupied"] == 0.3
    c2 = SpatialSemanticContract.from_dict(d)
    assert c2.lon_span_occupied == 0.3 and c2.lon_center_hint == 179.87
    # 旧序列化（无新字段）round-trip 兼容
    d.pop("lon_span_occupied"), d.pop("lon_center_hint")
    c3 = SpatialSemanticContract.from_dict(d)
    assert c3.lon_span_occupied is None
