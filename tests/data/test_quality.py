"""Data Quality Contract —— 质量诊断测试。"""
import pytest

from app.lib.data.profile import (
    DatasetProfileV3,
    ProfileQuality,
    RasterBandStats,
    RasterProfileData,
    profile_features,
    profile_from_field_schema,
)
from app.lib.data.quality import (
    QualityIssue,
    QualityIssueCode,
    QualityReport,
    compose_status,
    run_quality_checks,
)
from app.lib.data.vocabulary import QualityStatus


def _profile_for(feats, crs=""):
    vp, q = profile_features(feats, crs=crs)
    return DatasetProfileV3(
        target_ref="ref:test", category="vector", crs=crs, extent=vp.extent,
        vector=vp, profile_quality=q,
    )


class TestComposeStatus:
    def test_empty_is_valid(self):
        assert compose_status([]) is QualityStatus.VALID

    def test_warning_only(self):
        i = QualityIssue(code=QualityIssueCode.CRS_MISSING)
        assert compose_status([i]) is QualityStatus.WARNING  # CRS 缺失可修复？

    def test_repairable_flag_drives(self):
        i = QualityIssue(code=QualityIssueCode.CRS_MISSING, repairable=True)
        assert compose_status([i]) is QualityStatus.REPAIRABLE

    def test_error_blocked_vs_repairable(self):
        err_fixed = QualityIssue(code=QualityIssueCode.IMPOSSIBLE_COORDINATES, severity="error", repairable=True)
        err_hard = QualityIssue(code=QualityIssueCode.EMPTY_PAYLOAD, severity="error")
        assert compose_status([err_fixed]) is QualityStatus.REPAIRABLE
        assert compose_status([err_hard]) is QualityStatus.BLOCKED
        assert compose_status([err_fixed, err_hard]) is QualityStatus.BLOCKED


class TestVectorChecks:
    def test_missing_crs_flagged(self):
        feats = [{"geometry": {"type": "Point", "coordinates": [1.0, 2.0]}, "properties": {}}]
        report = run_quality_checks(_profile_for(feats, crs=""))
        codes = {i.code for i in report.issues}
        assert QualityIssueCode.CRS_MISSING in codes
        issue = next(i for i in report.issues if i.code is QualityIssueCode.CRS_MISSING)
        assert issue.remediation  # §七：必须带修复建议
        assert issue.repairable
        assert report.status in (QualityStatus.WARNING, QualityStatus.REPAIRABLE)

    def test_clean_geodata_valid(self):
        feats = [
            {"geometry": {"type": "Point", "coordinates": [116.0 + i * 0.1, 39.0]},
             "properties": {"v": i}}
            for i in range(30)
        ]
        report = run_quality_checks(_profile_for(feats, crs="EPSG:4326"))
        assert report.status is QualityStatus.VALID
        assert "self_intersection" in report.checks_not_run   # 诚实披露未查项
        assert "duplicate_rows" in report.checks_not_run

    def test_impossible_coordinates_blocks(self):
        feats = [
            {"geometry": {"type": "Point", "coordinates": [500.0, 91.0]}, "properties": {}},
        ]
        report = run_quality_checks(_profile_for(feats, crs="EPSG:4326"))
        assert QualityIssueCode.IMPOSSIBLE_COORDINATES in {i.code for i in report.issues}
        assert report.status in (QualityStatus.BLOCKED, QualityStatus.REPAIRABLE)

    def test_null_heavy_field(self):
        feats = [
            {"geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
             "properties": {"ghost": None if i % 10 != 0 else 1, "solid": i}}
            for i in range(30)
        ]
        vp, q = profile_features(feats, crs="EPSG:4326")
        prof = DatasetProfileV3(target_ref="x", category="vector", vector=vp, profile_quality=q, crs="EPSG:4326")
        report = run_quality_checks(prof)
        null_issue = next((i for i in report.issues if i.code is QualityIssueCode.NULL_HEAVY_FIELD), None)
        assert null_issue is not None
        assert null_issue.field == "ghost"
        solid = next((i for i in report.issues if i.field == "solid"), None)
        assert solid is None

    def test_invalid_dates_detected(self):
        feats = [
            {"geometry": {"type": "Point", "coordinates": [1.0, 1.0]},
             "properties": {"when": "2024-13-45"}}  # 命名像日期但不可解析
            for _ in range(12)
        ]
        vp, q = profile_features(feats, crs="EPSG:4326")
        prof = DatasetProfileV3(target_ref="x", category="vector", vector=vp, profile_quality=q, crs="EPSG:4326")
        report = run_quality_checks(prof)
        assert QualityIssueCode.INVALID_DATES in {i.code for i in report.issues}

    def test_empty_payload_blocks(self):
        vp, q = profile_features([], crs="EPSG:4326")
        prof = DatasetProfileV3(target_ref="x", category="vector", vector=vp, profile_quality=q)
        report = run_quality_checks(prof)
        assert report.status is QualityStatus.BLOCKED
        assert report.issues[0].code is QualityIssueCode.EMPTY_PAYLOAD


class TestRasterChecks:
    def test_nodata_saturation(self):
        prof = DatasetProfileV3(
            target_ref="ref:r", category="raster", crs="EPSG:4326",
            raster=RasterProfileData(
                width=100, height=100, band_count=1, crs="EPSG:4326",
                band_stats=[RasterBandStats(band=1, valid_pixel_ratio=0.001, min=0, max=1)],
            ),
            profile_quality=ProfileQuality.COMPLETE,
        )
        report = run_quality_checks(prof)
        assert QualityIssueCode.NODATA_SATURATION in {i.code for i in report.issues}
        assert report.status is QualityStatus.WARNING

    def test_raster_crs_missing(self):
        prof = DatasetProfileV3(
            target_ref="ref:r", category="raster",
            raster=RasterProfileData(width=10, height=10, band_count=1, crs=""),
            profile_quality=ProfileQuality.COMPLETE,
        )
        report = run_quality_checks(prof)
        assert QualityIssueCode.CRS_MISSING in {i.code for i in report.issues}


class TestReportHygiene:
    def test_failed_profile_unchecked(self):
        prof = DatasetProfileV3(
            target_ref="x", category="unknown",
            profile_quality=ProfileQuality.FAILED, diagnostics=["boom"],
        )
        report = run_quality_checks(prof)
        assert report.status is QualityStatus.UNCHECKED

    def test_issues_bounded_with_error_priority(self):
        issues = [
            QualityIssue(code=QualityIssueCode.CRS_MISSING, severity="warning")
            for _ in range(50)
        ] + [QualityIssue(code=QualityIssueCode.EMPTY_PAYLOAD, severity="error")]
        report = QualityReport(target_ref="x", issues=issues)
        assert len(report.issues) <= 32
        assert report.issues[0].severity == "error"  # error 优先保留

    def test_summary_shape(self):
        feats = [{"geometry": None, "properties": {}}]
        report = run_quality_checks(_profile_for(feats, crs=""))
        s = report.summary()
        assert s["quality_status"] in {"valid", "warning", "repairable", "blocked"}
        assert isinstance(s["issues"], list)
        assert "not_checked" in s


def test_table_profile_crs_warning():
    from app.lib.data.profile import TableProfileData, profile_rows

    rows = [{"lng": 116.0, "lat": 39.9, "v": 1} for _ in range(20)]
    tp, q = profile_rows(rows)
    prof = DatasetProfileV3(target_ref="t", category="table", table=tp, profile_quality=q)
    report = run_quality_checks(prof)
    crs_issues = [i for i in report.issues if i.code is QualityIssueCode.CRS_MISSING]
    assert crs_issues and "坐标候选列" in crs_issues[0].message
