"""空间语义 preflight —— 16 场景失败/预期矩阵锁定测试（M2/M3，TEST_MATRIX）。

每条测试对应 planning/goal-spatial-semantics/TEST_MATRIX.md 的场景行；
断言 = 验收 Oracle #3（8 类典型错误在工具执行前有一致确定性结果）+
Oracle #4（无 CRS 投影数据拒绝而非猜 4326）。
"""
from __future__ import annotations

import pytest

from app.services.gis_harness.spatial_semantics import (
    ScopeEvidence,
    SpatialOperationSpec,
    compose_density_unit,
    convert_area,
    convert_density,
    normalize_distance_params,
    parse_distance_expression,
    preflight_for_feature_collection,
)

# ── 合成 fixture（无网、确定性）────────────────────────────────────────────

CHENGDU_PT = {"type": "Point", "coordinates": [104.065735, 30.659462]}


def fc(points=None, crs=None, geometry=None):
    features = [
        {"type": "Feature",
         "geometry": geometry or {"type": "Point", "coordinates": list(p)}}
        for p in (points or [])
    ]
    out = {"type": "FeatureCollection", "features": features}
    if crs:
        out["crs"] = crs
    return out


BUFFER_SPEC = dict(operation="buffer", distance=500, distance_unit="m")


# ── S1/S2/S3：CRS 声明光谱 ─────────────────────────────────────────────────

def test_s1_declared_wgs84_chengdu_passes_with_utm48() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "pass"
    assert r.contract.crs_source == "declared"
    assert r.contract.crs_class == "geographic"
    assert r.calculation.mode == "projected_local"
    assert r.calculation.working_crs == "EPSG:32648"  # 成都 → UTM 48N
    assert r.is_executable


def test_s2_undeclared_degree_like_defaults_rfc7946_with_disclosure() -> None:
    data = fc(points=[(104.065735, 30.659462)])  # 无 crs member，度样坐标
    r = preflight_for_feature_collection(data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "pass_with_disclosure"
    assert r.contract.crs_source == "rfc7946_default"
    codes = [rc.code for rc in r.verdict.reason_codes]
    assert "SSC_CRS_RFC7946_DEFAULT" in codes


def test_s3_undeclared_projected_coords_blocked_not_guessed_4326() -> None:
    # 成都 UTM 48N 米坐标（x≈5e5, y≈3.6e6）且无 crs member —— 禁止默认 4326。
    data = fc(points=[(500123.45, 3601234.56)])
    r = preflight_for_feature_collection(data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "blocked"
    assert not r.is_executable
    codes = [rc.code for rc in r.verdict.reason_codes]
    assert "SSC_CRS_UNDECLARED_NON_DEGREE" in codes
    actions = [p.action for p in r.verdict.repair_suggestions]
    assert "declare_crs" in actions
    assert r.contract.crs_source == "unknown"  # 不猜


# ── S4/S5：投影失真与单位换算 ──────────────────────────────────────────────

def test_s4_declared_3857_degrades_with_distortion_and_safe_transform() -> None:
    # Web Mercator @30.66°N 失真 = 1/cos(30.66°) − 1 ≈ 16.2%
    data = fc(points=[(11585009.0, 3597252.0)], crs="EPSG:3857")
    r = preflight_for_feature_collection(data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "degraded_with_disclosure"
    assert r.calculation.distortion_estimate is not None
    assert 0.15 < r.calculation.distortion_estimate < 0.18
    assert [rc.code for rc in r.verdict.reason_codes] == [
        "SSC_WEB_MERCATOR_SCALE_DISTORTION"]
    repair = r.verdict.repair_suggestions[0]
    assert repair.authorization == "deterministic_safe"
    assert repair.transform.startswith("EPSG:3857→EPSG:326")


def test_s5_km_unit_conversion_chain_recorded() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(operation="buffer", distance=0.5,
                                        distance_unit="km"))
    # 换算是披露事实（0.5km→500m 进证据），不改变可执行性
    assert r.status == "pass_with_disclosure"
    codes = [rc.code for rc in r.verdict.reason_codes]
    assert "SSC_DISTANCE_UNIT_CONVERTED" in codes
    chain_rc = next(rc for rc in r.verdict.reason_codes
                    if rc.code == "SSC_DISTANCE_UNIT_CONVERTED")
    assert "0.5km→500" in chain_rc.detail


def test_s5b_invalid_unit_blocked_not_guessed_meters() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(operation="buffer", distance=500,
                                        distance_unit="degrees"))
    assert r.status == "blocked"
    assert "SSC_DISTANCE_UNIT_INVALID" in [rc.code for rc in r.verdict.reason_codes]


def test_s5c_degree_scale_distance_without_unit_blocked() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(operation="buffer", distance=0.005,
                                        distance_unit=None))
    assert r.status == "blocked"
    assert "SSC_DEGREE_DISTANCE_REQUEST" in [rc.code for rc in r.verdict.reason_codes]


# ── S6/S7：分母与密度口径 ──────────────────────────────────────────────────

def test_s6_area_denominator_per_km2_coherent() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(
            operation="spatial_aggregate", denominator_kind="area",
            requested_density_denominator="per_km2"))
    assert r.status == "pass"


def test_s7_population_denominator_vs_per_km2_requires_clarification() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(
            operation="spatial_aggregate", denominator_kind="population",
            requested_density_denominator="per_km2"))
    assert r.status == "requires_clarification"
    assert not r.is_executable  # 歧义不执行
    assert "SSC_DENOMINATOR_KIND_MISMATCH" in [
        rc.code for rc in r.verdict.reason_codes]
    assert any(p.action == "switch_denominator"
               for p in r.verdict.repair_suggestions)


def test_s7b_per_10k_persons_with_population_denominator_passes() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(
            operation="spatial_aggregate", denominator_kind="population",
            requested_density_denominator="per_10k_persons"))
    assert r.status == "pass"


# ── S8/S9：偏移帧 ──────────────────────────────────────────────────────────

def test_s8_declared_bd09_normalization_chain_in_contract() -> None:
    data = fc(points=[(104.08, 30.66)], crs="bd09")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(operation="spatial_join"))
    assert r.status == "pass_with_disclosure"
    assert r.contract.datum_normalization == ["bd09→gcj02→wgs84"]
    assert "SSC_DATUM_NORMALIZED" in [rc.code for rc in r.verdict.reason_codes]


def test_s9_undeclared_china_data_offset_risk_warned_not_normalized() -> None:
    data = fc(points=[(104.065735, 30.659462)])  # 未声明
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(operation="spatial_join"))
    assert r.status == "pass_with_disclosure"  # 非阻塞警示
    assert r.contract.datum_normalization == []  # 绝不自动归一未声明数据
    assert "SSC_CHINA_OFFSET_FRAME_RISK" in [
        rc.code for rc in r.verdict.reason_codes]


def test_s9b_declared_wgs84_china_data_no_offset_risk() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(operation="spatial_join"))
    assert "SSC_CHINA_OFFSET_FRAME_RISK" not in [
        rc.code for rc in r.verdict.reason_codes]


# ── S10/S13：跨 180° 与极区 ────────────────────────────────────────────────

def test_s10_antimeridian_cluster_selects_wrapped_zone() -> None:
    data = fc(points=[(179.72, -17.9), (-179.98, -16.8)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.calculation.working_crs == "EPSG:32760"  # UTM 60S，非格林尼治带
    assert r.calculation.antimeridian is True
    assert "SSC_ANTIMERIDIAN_CROSSED" in [rc.code for rc in r.verdict.reason_codes]


def test_s10b_wide_span_china_geodesic_degraded() -> None:
    data = fc(points=[(116.4, 39.9), (87.6, 43.8)], crs="EPSG:4326")  # 跨 28.8°
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "degraded_with_disclosure"
    assert r.calculation.mode == "geodesic"
    assert "SSC_MULTIZONE_SPAN" in [rc.code for rc in r.verdict.reason_codes]


def test_s13_polar_selects_ups_with_disclosure() -> None:
    data = fc(points=[(15.0, 88.0), (16.0, 88.5)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(operation="measure"))
    assert r.calculation.mode == "projected_polar"
    assert r.calculation.working_crs == "EPSG:3413"
    assert "SSC_POLAR_PROJECTION_SELECTED" in [
        rc.code for rc in r.verdict.reason_codes]


# ── S11/S12：范围对账 ──────────────────────────────────────────────────────

def test_s11_scope_crs_mismatch_requires_clarification() -> None:
    data = fc(points=[(11585009.0, 3597252.0)], crs="EPSG:3857")
    scope = ScopeEvidence(name="成都", bbox=[103.9, 30.5, 104.2, 30.8],
                          bbox_crs="EPSG:4326", source="user")
    r = preflight_for_feature_collection(
        data, scope=scope, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "requires_clarification"
    assert "SSC_SCOPE_DATA_CRS_MISMATCH" in [
        rc.code for rc in r.verdict.reason_codes]


def test_s11b_disjoint_scope_blocked() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    scope = ScopeEvidence(name="北京", bbox=[116.0, 39.8, 116.6, 40.2],
                          bbox_crs="EPSG:4326", source="user")
    r = preflight_for_feature_collection(
        data, scope=scope, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "blocked"
    assert r.contract.scope_overlap == "disjoint"
    assert "SSC_SCOPE_DISJOINT_FROM_DATA" in [
        rc.code for rc in r.verdict.reason_codes]


def test_s12_invalid_scope_bbox_blocked() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    scope = ScopeEvidence(name="乱序", bbox=[104.2, 30.8, 103.9, 30.5],
                          bbox_crs="EPSG:4326", source="user")
    r = preflight_for_feature_collection(
        data, scope=scope, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "blocked"
    assert "SSC_SCOPE_INVALID" in [rc.code for rc in r.verdict.reason_codes]


def test_s12b_out_of_envelope_geographic_scope_blocked() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    scope = ScopeEvidence(name="越界", bbox=[-200.0, 30.5, -199.0, 30.8],
                          bbox_crs="EPSG:4326", source="user")
    r = preflight_for_feature_collection(
        data, scope=scope, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "blocked"


def test_s12c_declared_geographic_but_projected_coords_blocked() -> None:
    # CRS 声明与数据矛盾（4326 声明 + 米坐标）—— 决不改写执行
    data = fc(points=[(500123.45, 3601234.56)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.status == "blocked"
    assert "SSC_COORD_OUT_OF_ENVELOPE" in [rc.code for rc in r.verdict.reason_codes]


# ── 轴序（G9）──────────────────────────────────────────────────────────────

def test_axis_order_suspect_latlon_detected() -> None:
    # (30.66, 104.06)：x 在纬度带、y 在经度带 —— 中国 bbox 对调口径
    data = fc(points=[(30.66, 104.06)], crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r.contract.axis_order == "lat_lon"
    assert "SSC_AXIS_ORDER_SUSPECT" in [rc.code for rc in r.verdict.reason_codes]


# ── 单位数学（M3）──────────────────────────────────────────────────────────

class TestUnits:
    def test_parse_chinese_and_english(self) -> None:
        assert parse_distance_expression("500米")[:3] == (500.0, "m", 500.0)
        assert parse_distance_expression("0.5km")[:3] == (0.5, "km", 500.0)
        assert parse_distance_expression("1 公里")[:3] == (1.0, "km", 1000.0)
        assert parse_distance_expression("300 m")[:3] == (300.0, "m", 300.0)

    def test_parse_rejects_bare_number_without_default(self) -> None:
        with pytest.raises(ValueError):
            parse_distance_expression("500")

    def test_parse_rejects_unknown_unit(self) -> None:
        with pytest.raises(ValueError):
            parse_distance_expression("500 英里")

    def test_normalize_params_records_chain(self) -> None:
        value_m, chain = normalize_distance_params(0.5, "km")
        assert value_m == 500.0
        assert chain == ["0.5km→500.0m(factor=1000)"]

    def test_area_conversion(self) -> None:
        assert convert_area(1.0, "km2", "m2") == 1e6
        assert convert_area(1e4, "m2", "ha") == 1.0
        assert convert_area(2.0, "ha", "km2") == pytest.approx(0.02)
        with pytest.raises(ValueError):
            convert_area(1.0, "亩", "m2")

    def test_density_compose_and_convert(self) -> None:
        assert compose_density_unit("个", "km2") == "个/km²"
        assert compose_density_unit("所", "ha") == "所/公顷"
        assert compose_density_unit("个", "population") == "个/万人"
        # per_m² → per_km²：分母放大 1e6 ⇒ 密度值 ÷ 1e6
        assert convert_density(2.5e-6, "m2", "km2") == pytest.approx(2.5)
        with pytest.raises(ValueError):
            convert_density(1.0, "population", "km2")


# ── 披露投影（LLM 安全）────────────────────────────────────────────────────

def test_disclosure_projection_is_bounded_and_geometry_free() -> None:
    pts = [(104.0 + i * 0.001, 30.6 + i * 0.001) for i in range(50)]
    data = fc(points=pts, crs="EPSG:4326")
    r = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    d = r.disclosure()
    assert d["status"] == "pass"
    assert d["working_crs"] == "EPSG:32648"
    assert "reason_codes" in d and isinstance(d["reason_codes"], list)
    # 披露面绝不携带坐标采样/几何全量（working_crs_basis 里的 0.1° 粒度
    # 中心坐标是有界元数据，非几何转储）。
    dumped = repr(d)
    assert "coord_samples" not in dumped
    assert "coordinates" not in dumped
    import re as _re
    coordinate_like = _re.findall(r"10[34]\.\d{4,}", dumped)
    assert len(coordinate_like) == 0  # 无 ≥4 位小数的坐标值（采样精度）


def test_determinism_same_input_same_output() -> None:
    data = fc(points=[(104.065735, 30.659462)], crs="EPSG:4326")
    r1 = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    r2 = preflight_for_feature_collection(
        data, spec=SpatialOperationSpec(**BUFFER_SPEC))
    assert r1.disclosure() == r2.disclosure()
