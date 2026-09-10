"""Epic 11 —— Method Qualification Engine V2 单测（硬错误锁定 + oracle 对齐）。"""
from __future__ import annotations

import pytest

from app.lib.gis.methodology.qualification import (
    DIMENSION_STATES,
    QUAL_METHOD_STATUSES,
    QualificationFacts,
    normalize_precondition_state,
    qualify_method,
    qualify_methods,
)


# ── 硬错误对（Epic §5.D 五禁止项的机器可读落点）─────────────────────────

def test_kde_requires_point_geometry() -> None:
    """无点数据不得选 KDE（面数据 → geometry fail）。"""
    report = qualify_method("density.kernel_surface", QualificationFacts.from_profile(
        {"geometryTypes": ["Polygon"], "featureCount": 100}))
    assert report.status == "rejected"
    assert "QUAL_GEOMETRY_REJECTED" in report.reason_codes
    assert "reproject" not in report.recommended_preprocessing


def test_continuous_interpolation_rejects_categorical_measure() -> None:
    """类别字段 × 连续量假设 → 拒绝（R1-F4 scope）。"""
    facts = QualificationFacts.from_profile(
        {"geometryTypes": ["Point"], "featureCount": 50},
        measure_kind="categorical")
    assert qualify_method("interp.ordinary_kriging", facts).status == "rejected"
    assert qualify_method("interp.idw", facts).status == "rejected"
    assert ("QUAL_MEASURE_SEMANTICS_REJECTED"
            in qualify_method("interp.ordinary_kriging", facts).reason_codes)


def test_indicator_kriging_accepts_categorical() -> None:
    """指示克里金对类别数据合法（assumes_continuous_measure=False）。"""
    facts = QualificationFacts.from_profile(
        {"geometryTypes": ["Point"], "featureCount": 50},
        measure_kind="categorical")
    report = qualify_method("interp.indicator_kriging", facts)
    assert report.status == "viable"


def test_geographic_crs_metric_buffer_not_silent() -> None:
    """地理 CRS：GEOGRAPHIC_OK buffer → pass（实现内建投影，R1-F1）；
    LOCAL_METRIC 算法 → transform 修复链显式化（非静默、非拒绝）。"""
    geo = QualificationFacts.from_profile(
        {"geometryTypes": ["Point"], "featureCount": 30, "crs": "EPSG:4326"})
    assert qualify_method("proximity.euclidean_buffer", geo).status == "viable"
    # 克里金声明 PROJECTED_REQUIRED → crs_scale transform
    report = qualify_method("interp.ordinary_kriging", QualificationFacts.from_profile(
        {"geometryTypes": ["Point"], "featureCount": 60, "crs": "EPSG:4326",
         "fields": {"pm25": {"type": "number"}}}))
    crs_dim = report.dimension("crs_scale")
    assert crs_dim is not None and crs_dim.state == "transform"
    assert "reproject" in report.recommended_preprocessing
    assert any("非静默" in d for d in report.disclosures)


def test_tiny_sample_hotspot_rejected() -> None:
    """极少样本不得声称显著热点（V4 min_sample_size=20 同源）。"""
    report = qualify_method("stats.local_cluster", QualificationFacts.from_profile(
        {"geometryTypes": ["Point"], "featureCount": 6,
         "fields": {"price": {"type": "number"}}}))
    assert report.status == "rejected"
    assert "QUAL_SAMPLE_SIZE_REJECTED" in report.reason_codes
    # 与 V4 资格引擎同判（oracle 对齐）
    from app.services.gis_harness.workflow_v4.methodology import (
        qualify_method_candidates,
    )
    v4 = qualify_method_candidates(
        "spatial_statistics",
        profile={"geometryTypes": ["Point"], "featureCount": 6,
                 "fields": {"price": {"type": "number"}}})
    v4_by_id = {q.method_id: q for q in v4.qualifications}
    assert v4_by_id["stats.local_cluster"].status == "rejected"


def test_rate_without_denominator_rejected() -> None:
    """raw-count 归一化场景：分母 blocked → 率方法拒绝（不下人均结论）。"""
    report = qualify_method("density.admin_rate", QualificationFacts.from_profile(
        {"featureCount": 40},
        role_states={"subject": "eligible", "boundary": "eligible",
                     "denominator": "blocked"}))
    assert report.status == "rejected"
    assert "QUAL_DATA_ROLES_REJECTED" in report.reason_codes


# ── 维度语义 ────────────────────────────────────────────────────────────

def test_unknown_facts_do_not_reject() -> None:
    """unknown ≠ 不满足：画像缺事实时方法不因缺事实被拒。"""
    report = qualify_method("interp.ordinary_kriging", QualificationFacts.from_profile({}))
    assert report.status == "unknown"
    assert not report.has_facts or report.status != "rejected"
    assert any("unknown" in m for m in report.missing_requirements)


def test_null_heavy_fields_degrade_with_remediation() -> None:
    report = qualify_method("zonal.admin_stats", QualificationFacts.from_profile(
        {"featureCount": 100,
         "fields": {"pop": {"type": "number", "null_ratio": 0.8}}},
        role_states={"subject": "eligible", "boundary": "eligible"}))
    assert report.status == "degraded"
    assert "filter_null" in report.recommended_preprocessing


def test_confidence_is_fact_fraction() -> None:
    full = qualify_method("interp.ordinary_kriging", QualificationFacts.from_profile(
        {"geometryTypes": ["Point"], "featureCount": 60, "crs": "EPSG:32648",
         "fields": {"pm25": {"type": "number"}}}))
    empty = qualify_method("interp.ordinary_kriging", QualificationFacts.from_profile({}))
    assert full.confidence > empty.confidence
    assert 0.0 <= empty.confidence <= 1.0


def test_status_vocabulary_and_dimensions() -> None:
    facts = QualificationFacts.from_profile({"featureCount": 10})
    report = qualify_method("density.kernel_surface", facts)
    assert report.status in QUAL_METHOD_STATUSES + ("unknown",)
    for d in report.dimension_states:
        assert d.state in DIMENSION_STATES
        assert d.dimension in (
            "geometry", "sample_size", "crs_scale", "measure_semantics",
            "temporal", "nodata_quality", "data_roles",
            "scientific_precondition")


def test_batch_qualification_stable_order() -> None:
    facts = QualificationFacts.from_profile({"featureCount": 100,
                                             "geometryTypes": ["Point"]})
    ids = ["density.kernel_surface", "interp.idw", "stats.local_cluster"]
    reports = qualify_methods(ids, facts)
    assert [r.method_id for r in reports] == ids
    assert qualify_methods(ids, facts) == reports


def test_unknown_method_raises() -> None:
    with pytest.raises(ValueError):
        qualify_method("no.such_method", QualificationFacts.from_profile({}))


# ── oracle 对齐（防第二科学真相）────────────────────────────────────────

def test_precondition_normalization_matches_v4() -> None:
    """归一化语义与 V4 _evaluate_precondition_state 对齐（parity 锁定）。"""
    from app.lib.gis.scientific_preconditions import evaluate_precondition
    from app.services.gis_harness.workflow_v4.methodology import (
        _evaluate_precondition_state,
    )
    profile = {"geometryKinds": ["raster"], "crs": "EPSG:4326"}
    for pid in ("local_metric_crs_required", "numeric_field_required",
                "min_numeric_samples:8", "temporal_field_required"):
        v4 = _evaluate_precondition_state(pid, profile)
        mine = normalize_precondition_state(evaluate_precondition(pid, profile))
        assert mine == v4, pid


def test_null_ratio_threshold_matches_data_qualification() -> None:
    """退化阈值与 data_qualification._HIGH_NULL_RATIO 同口径（防漂移）。"""
    from app.services.gis_harness.data_qualification import (
        _HIGH_NULL_RATIO,
    )
    fields = {"pop": {"type": "number", "null_ratio": _HIGH_NULL_RATIO + 0.01}}
    report = qualify_method("zonal.admin_stats", QualificationFacts.from_profile(
        {"featureCount": 100, "fields": fields},
        role_states={"subject": "eligible", "boundary": "eligible"}))
    assert report.status == "degraded"
