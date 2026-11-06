"""空间检查收据 V1 —— M3 单测（聚合透传 + 四类新证据 + 阻断语义）。

覆盖：QualityReport 透传与 severity 映射、AXIS_ORDER_SUSPECT 两种口径
（blocked 越界 / warn 中国 bbox 带）、PK 三态、DENOMINATOR_ABSENT 披露、
时区态、blocked 聚合属性、summary 有界。
"""
from __future__ import annotations

from app.lib.data.profile import (
    DatasetProfileV3,
    FieldProfile,
    TableProfileData,
    VectorProfileData,
)
from app.lib.data.quality import (
    QualityIssue,
    QualityIssueCode,
    QualityReport,
)
from app.services.data_preparation import (
    FINDING_AXIS_ORDER_SUSPECT,
    FINDING_DENOMINATOR_ABSENT,
    FINDING_PK_DUPLICATED,
    FINDING_PK_MISSING,
    FindingSeverity,
    InspectionFinding,
    build_spatial_receipt,
    propose_field_roles,
)


def _col(name, *, dtype="number", mn=None, mx=None, unique=None):
    return FieldProfile(name=name, dtype=dtype, min=mn, max=mx, unique_count=unique)


def _table(cols, rows=200, scanned=200, **kw) -> DatasetProfileV3:
    return DatasetProfileV3(
        target_ref="ref:test-src-1", crs="EPSG:4326",
        table=TableProfileData(row_count=rows, scanned_rows=scanned,
                               columns=cols, **kw),
    )


def _issue(code: QualityIssueCode, severity="warning", field="",
           evidence=None) -> QualityIssue:
    return QualityIssue(code=code, severity=severity, field=field,
                        evidence=evidence or {})


def _receipt(profile, quality=None, proposals=None, **kw):
    props = proposals if proposals is not None else propose_field_roles(
        profile.table.columns if profile.table else {})
    return build_spatial_receipt(profile, quality_report=quality,
                                 proposals=props, **kw)


class TestQualityPassthrough:
    def test_issue_severity_mapping(self):
        report = QualityReport(issues=[
            _issue(QualityIssueCode.IMPOSSIBLE_COORDINATES, "error", "纬度"),
            _issue(QualityIssueCode.TIMEZONE_MISSING, "warning", "时间"),
            _issue(QualityIssueCode.NODATA_SATURATION, "info"),
        ])
        r = _receipt(_table({"时间": _col("时间", dtype="string")}), quality=report)
        by_code = {f.code: f for f in r.findings}
        assert by_code["impossible_coordinates"].severity == FindingSeverity.BLOCKED
        assert by_code["impossible_coordinates"].suggestion.value == "filter_rows"
        assert by_code["timezone_missing"].severity == FindingSeverity.WARN
        assert by_code["timezone_missing"].suggestion.value == "pick_timezone"
        assert r.blocked

    def test_findings_sorted_blocked_first(self):
        report = QualityReport(issues=[
            _issue(QualityIssueCode.TIMEZONE_MISSING, "warning"),
            _issue(QualityIssueCode.CRS_MISSING, "error"),
        ])
        r = _receipt(_table({"x": _col("x")}), quality=report)
        assert r.findings[0].severity == FindingSeverity.BLOCKED


class TestAxisOrderSuspect:
    def _swapped_fields(self):
        # 名义纬度列值域 [73,135]（越 ±90），名义经度列 [18,54]
        return {
            "经度": _col("经度", mn=18.0, mx=54.0),
            "纬度": _col("纬度", mn=73.0, mx=135.0),
        }

    def test_blocked_when_lat_impossible(self):
        cols = self._swapped_fields()
        profile = _table(cols)
        r = _receipt(profile)
        sus = [f for f in r.findings if f.code == FINDING_AXIS_ORDER_SUSPECT]
        assert sus and sus[0].severity == FindingSeverity.BLOCKED
        assert sus[0].suggestion.value == "switch_xy"
        assert sus[0].evidence["lat_field"] == "纬度"
        assert r.blocked
        assert r.axis_order_guessed == "lon_lat"

    def test_warn_when_both_valid_but_china_bbox_swapped(self):
        # 两列都在合法带内（lat 不越界），但 lon∈[18,54]、lat∈[73,135]
        cols = {
            "经度": _col("经度", mn=20.0, mx=50.0),
            "纬度": _col("纬度", mn=80.0, mx=89.0),
        }
        r = _receipt(_table(cols))
        sus = [f for f in r.findings if f.code == FINDING_AXIS_ORDER_SUSPECT]
        assert sus and sus[0].severity == FindingSeverity.WARN

    def test_no_suspect_on_healthy_coords(self):
        cols = {
            "经度": _col("经度", mn=103.9, mx=104.2),
            "纬度": _col("纬度", mn=30.4, mx=30.8),
        }
        r = _receipt(_table(cols))
        assert not any(f.code == FINDING_AXIS_ORDER_SUSPECT for f in r.findings)
        assert r.axis_order_guessed == "lon_lat"


class TestPrimaryKey:
    def test_unique_pk_ok(self):
        cols = {
            "学校编号": _col("学校编号", dtype="string", unique=200),
            "学校名称": _col("学校名称", dtype="string"),
        }
        r = _receipt(_table(cols))
        assert r.pk_field == "学校编号"
        assert r.pk_status == "unique"
        assert not any(f.code == FINDING_PK_DUPLICATED for f in r.findings)

    def test_duplicated_pk_warns(self):
        cols = {"学校编号": _col("学校编号", dtype="string", unique=180)}
        r = _receipt(_table(cols))
        dup = [f for f in r.findings if f.code == FINDING_PK_DUPLICATED]
        assert dup and dup[0].severity == FindingSeverity.WARN
        assert dup[0].evidence["duplicate_rows"] == 20
        assert r.pk_status == "duplicated"

    def test_missing_pk_warns(self):
        cols = {"备注": _col("备注", dtype="string")}
        r = _receipt(_table(cols))
        miss = [f for f in r.findings if f.code == FINDING_PK_MISSING]
        assert miss and miss[0].suggestion.value == "designate_key"
        assert r.pk_status == "missing"

    def test_capped_uniqueness_is_honest_unknown(self):
        cols = {"编号": FieldProfile(name="编号", dtype="string",
                                     unique_count=10_000, unique_capped=True)}
        r = _receipt(_table(cols))
        assert r.pk_status == "capped"
        assert r.pk_field == "编号"


class TestDenominatorAndTimezone:
    def test_denominator_absent_disclosure(self):
        cols = {
            "学校名称": _col("学校名称", dtype="string"),
            "在校生数": _col("在校生数", mn=100, mx=3000),
        }
        r = _receipt(_table(cols))
        den = [f for f in r.findings if f.code == FINDING_DENOMINATOR_ABSENT]
        assert den and den[0].severity == FindingSeverity.OK
        assert den[0].suggestion.value == "provide_denominator"

    def test_denominator_present_no_finding(self):
        cols = {
            "在校生数": _col("在校生数", mn=100, mx=3000),
            "常住人口": _col("常住人口", mn=50000, mx=900000),
        }
        r = _receipt(_table(cols))
        assert not any(f.code == FINDING_DENOMINATOR_ABSENT for f in r.findings)

    def test_tz_missing_from_quality(self):
        cols = {"时间": _col("时间", dtype="string")}
        profile = _table(cols, time_candidates=["时间"])
        r = _receipt(profile, quality=QualityReport(issues=[
            _issue(QualityIssueCode.TIMEZONE_MISSING, "warning", "时间")]))
        assert r.tz_status == "missing"
        assert r.time_fields == ("时间",)

    def test_tz_ok_when_no_issue(self):
        cols = {"时间": _col("时间", dtype="string")}
        profile = _table(cols, time_candidates=["时间"])
        r = _receipt(profile)
        assert r.tz_status == "ok"


class TestSummaryAndVectorPath:
    def test_summary_shape(self):
        cols = {"经度": _col("经度", mn=104.0, mx=104.1),
                "纬度": _col("纬度", mn=30.0, mx=30.7)}
        r = _receipt(_table(cols), crs_source="declared", encoding="utf-8")
        s = r.summary()
        assert s["srs"] == "EPSG:4326"
        assert s["crs_source"] == "declared"
        assert s["blocked"] is False
        assert isinstance(s["finding_codes"], list)

    def test_vector_profile_path(self):
        vec = VectorProfileData(
            row_count=50, scanned_rows=50, geometry_types=["Polygon"],
            extent=[104.0, 30.0, 104.1, 30.1],
            fields={"NAME": _col("NAME", dtype="string")},
        )
        profile = DatasetProfileV3(target_ref="ref:vec-1", crs="EPSG:4326",
                                   vector=vec)
        r = build_spatial_receipt(profile)
        assert r.geometry_type == "Polygon"
        assert r.coordinate_range == [104.0, 30.0, 104.1, 30.1]
        assert r.row_count == 50

    def test_finding_evidence_bounded(self):
        f = InspectionFinding(code="x" * 100, message_zh="y" * 500,
                              evidence={"k" * 100: "v" * 500,
                                        "nested": {"deep": 1}})
        assert len(f.code) <= 48
        assert len(f.message_zh) <= 240
        assert len(f.evidence) <= 8
        assert isinstance(f.evidence["nested"], str)
