"""Data Quality Contract —— 标准化质量诊断（§七）。

质量检查消费 Dataset Profile V3 的**已就位证据**（profile.py 产出），
绝不自行扫描数据本体 —— 深检（自相交、重复几何等需几何库/全量数据的
项）显式记入 ``not_run``，不做「没查 = 没问题」的静默假设。

输出：
- ``QualityIssue``：code（分类学枚举）+ severity + 字段定位 + 修复建议
  （recommended remediation）+ 有界证据；
- ``QualityReport``：issues + quality_status（valid/warning/repairable/
  blocked，§七四态）+ checks_run / checks_not_run（诚实披露）+
  ``summary()``（LLM 有界视图）。

severity 语义：``info``（披露）、``warning``（可用但有疑点）、
``error``（阻断专业分析 = blocked）。repairable 由 issue.repairable
标志驱动（可自动/半自动修复：缺 CRS 可问询补齐、编码可转码）。
"""
from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List

from pydantic import BaseModel, Field, field_validator

from app.lib.data.profile import DatasetProfileV3, FieldProfile, VectorProfileData
from app.lib.data.vocabulary import QualityStatus

# 诊断证据预算。
_MAX_ISSUES = 32
_MAX_EVIDENCE_KEYS = 8


class QualityIssueCode(str, enum.Enum):
    """质量诊断分类学（§七；扩展需受控新增枚举值，不许自由字符串）。"""

    # CRS / 坐标
    CRS_MISSING = "crs_missing"
    # CRS_SUSPICIOUS 为保留码（词表先行）：「范围 vs 声明 CRS」核查需要
    # 投影感知的 bbox 判定（pyproj 依赖边界），V3 以 IMPOSSIBLE_COORDINATES
    # 的地理 CRS 口径覆盖主场景；本码暂无生产者。
    CRS_SUSPICIOUS = "crs_suspicious"
    IMPOSSIBLE_COORDINATES = "impossible_coordinates"
    ZERO_COORDINATES = "zero_coordinates"
    # 几何
    INVALID_GEOMETRY = "invalid_geometry"
    SELF_INTERSECTION = "self_intersection"
    EMPTY_GEOMETRY = "empty_geometry"
    DUPLICATE_GEOMETRIES = "duplicate_geometries"
    # 行 / 字段
    DUPLICATE_ROWS = "duplicate_rows"
    MISSING_REQUIRED_FIELDS = "missing_required_fields"
    NULL_HEAVY_FIELD = "null_heavy_field"
    INCONSISTENT_UNIT = "inconsistent_unit"
    INVALID_DATES = "invalid_dates"
    ENCODING_ISSUES = "encoding_issues"
    # 栅格
    NODATA_SATURATION = "nodata_saturation"
    RESOLUTION_MISMATCH = "resolution_mismatch"
    EXTENT_MISMATCH = "extent_mismatch"
    # 时间
    TEMPORAL_GAPS = "temporal_gaps"
    # 通用
    EMPTY_PAYLOAD = "empty_payload"
    SCHEMA_TRUNCATED = "schema_truncated"
    PROFILE_INCOMPLETE = "profile_incomplete"


# 修复建议表（§七 recommended remediation；code → 人/Agent 可执行动作）。
_REMEDIATIONS: Dict[QualityIssueCode, str] = {
    QualityIssueCode.CRS_MISSING: "为数据指定 CRS（上传参数或源元数据）；GeoJSON 按 RFC 7946 默认 WGS84，其他格式缺 CRS 会被拒绝，不应静默假设 EPSG:4326",
    QualityIssueCode.CRS_SUSPICIOUS: "坐标范围与声明的经纬度 CRS 不符：确认数据是否实为投影坐标系（如 CGCS2000/UTM 米制）并更正 CRS 声明",
    QualityIssueCode.IMPOSSIBLE_COORDINATES: "存在经纬度出界坐标：检查列映射（经纬度颠倒？投影坐标误标为经纬度？）并剔除/修复越界要素",
    QualityIssueCode.ZERO_COORDINATES: "大量 (0,0) 坐标通常是缺失值填充：将空值改为 null geometry 而非 0 坐标",
    QualityIssueCode.INVALID_GEOMETRY: "使用修复管线（spatial_repair_pipeline）或 make_valid 修复无效几何",
    QualityIssueCode.SELF_INTERSECTION: "对自相交面执行 unbuffer(0)/make_valid 修复后重查",
    QualityIssueCode.EMPTY_GEOMETRY: "剔除或补全空几何要素；空几何不能参与空间分析",
    QualityIssueCode.DUPLICATE_GEOMETRIES: "按几何去重（保留属性最全的一条）后重查",
    QualityIssueCode.DUPLICATE_ROWS: "按主键/全字段去重后重查",
    QualityIssueCode.MISSING_REQUIRED_FIELDS: "补齐分析所需字段或更换数据源",
    QualityIssueCode.NULL_HEAVY_FIELD: "该字段缺失率过高：剔除该字段、换列，或申明不可用于分析",
    QualityIssueCode.INCONSISTENT_UNIT: "统一计量单位（字段名提示与值域不一致）并在分析参数中显式声明单位",
    QualityIssueCode.INVALID_DATES: "修复无法解析的日期值或统一日期格式（ISO 8601）",
    QualityIssueCode.ENCODING_ISSUES: "以正确编码重新导入（中文场景常见 GBK/GB18030 CSV）",
    QualityIssueCode.NODATA_SATURATION: "栅格有效像元占比过低：检查 nodata 设置或裁剪有效区",
    QualityIssueCode.RESOLUTION_MISMATCH: "多源分辨率不一致：重采样到统一分辨率后再叠加分析",
    QualityIssueCode.EXTENT_MISMATCH: "数据范围与期望区域不符：检查裁剪范围/坐标换算",
    QualityIssueCode.TEMPORAL_GAPS: "时间序列存在缺口：补齐缺失时段或在分析中声明时间覆盖",
    QualityIssueCode.EMPTY_PAYLOAD: "数据为空：检查查询条件/过滤参数或数据源可用性",
    QualityIssueCode.SCHEMA_TRUNCATED: "字段数超出剖析上限，部分字段证据缺失：显式指定分析所需字段",
    QualityIssueCode.PROFILE_INCOMPLETE: "剖析证据不完整（仅描述符级）：需要更强证据时执行 deep profile",
}


class QualityIssue(BaseModel):
    """单条质量诊断（有界证据 + 修复建议）。"""

    code: QualityIssueCode
    severity: str = "warning"                 # info / warning / error
    message: str = ""
    field: str = ""                           # 字段/波段定位（可空）
    repairable: bool = False                  # 可（半）自动修复
    remediation: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("severity")
    @classmethod
    def _severity_enum(cls, v: str) -> str:
        v = str(v).lower()
        if v not in ("info", "warning", "error"):
            raise ValueError("severity must be info|warning|error")
        return v

    @field_validator("message")
    @classmethod
    def _bounded_message(cls, v: str) -> str:
        return str(v)[:300]

    @field_validator("evidence")
    @classmethod
    def _bounded_evidence(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        return dict(list(v.items())[:_MAX_EVIDENCE_KEYS])

    def model_post_init(self, __context: Any) -> None:
        if not self.remediation:
            self.remediation = _REMEDIATIONS.get(self.code, "")


class QualityReport(BaseModel):
    """质量报告（§七输出契约）。"""

    target_ref: str = ""
    status: QualityStatus = QualityStatus.UNCHECKED
    issues: List[QualityIssue] = Field(default_factory=list)
    checks_run: List[str] = Field(default_factory=list)
    checks_not_run: List[str] = Field(default_factory=list)   # 证据不足未执行的检查（诚实披露）
    checked_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @field_validator("issues")
    @classmethod
    def _bounded_issues(cls, v: List[QualityIssue]) -> List[QualityIssue]:
        # error 优先保留，其次 repairable，再按代码稳定排序 —— 截断可预期。
        rank = {"error": 0, "warning": 1, "info": 2}
        ordered = sorted(v, key=lambda i: (rank.get(i.severity, 3), i.code.value))
        return ordered[:_MAX_ISSUES]

    @field_validator("checks_run", "checks_not_run")
    @classmethod
    def _bounded_check_lists(cls, v: List[str]) -> List[str]:
        return [str(c)[:64] for c in v[:32]]

    def summary(self, *, max_issues: int = 8) -> Dict[str, Any]:
        """LLM 有界视图：状态 + 按严重度截断的问题清单。"""
        return {
            "target": self.target_ref,
            "quality_status": self.status.value,
            "issues": [
                {
                    "code": i.code.value,
                    "severity": i.severity,
                    "field": i.field or None,
                    "message": i.message or None,
                    "remediation": i.remediation or None,
                }
                for i in self.issues[:max_issues]
            ],
            "issues_total": len(self.issues),
            "not_checked": self.checks_not_run or None,
        }


def compose_status(issues: List[QualityIssue]) -> QualityStatus:
    """issues → §七四态。repairable：存在可修复的 warning/error。"""
    if any(i.severity == "error" for i in issues):
        return (
            QualityStatus.REPAIRABLE
            if all(i.repairable for i in issues if i.severity == "error")
            else QualityStatus.BLOCKED
        )
    if any(i.severity == "warning" for i in issues):
        return (
            QualityStatus.REPAIRABLE
            if any(i.repairable for i in issues)
            else QualityStatus.WARNING
        )
    return QualityStatus.VALID


# ── 检查实现（输入 = profile 证据；输出 = issues + 检查披露）──────────


def _vector_checks(vp: VectorProfileData, *, crs: str) -> List[QualityIssue]:
    issues: List[QualityIssue] = []

    # EMPTY_PAYLOAD
    if vp.row_count == 0:
        issues.append(QualityIssue(
            code=QualityIssueCode.EMPTY_PAYLOAD,
            severity="error",
            message="数据集为空（0 行）",
            repairable=False,
        ))
        return issues

    # EMPTY_GEOMETRY
    if vp.empty_geometry_count > 0:
        ratio = vp.empty_geometry_count / max(vp.scanned_rows, 1)
        issues.append(QualityIssue(
            code=QualityIssueCode.EMPTY_GEOMETRY,
            severity="warning" if ratio < 0.5 else "error",
            message=f"空/缺失几何占比 {ratio:.1%}（扫描 {vp.scanned_rows} 行中 {vp.empty_geometry_count} 行）",
            repairable=True,
            evidence={"empty_count": vp.empty_geometry_count, "scanned": vp.scanned_rows},
        ))

    # CRS_MISSING（按证据链：profile.crs 为空）
    if not crs:
        issues.append(QualityIssue(
            code=QualityIssueCode.CRS_MISSING,
            severity="warning",
            message="CRS 未知：不会静默按 EPSG:4326 处理",
            repairable=True,
        ))

    # IMPOSSIBLE_COORDINATES / ZERO_COORDINATES（经纬度口径）
    if vp.impossible_coordinate_count > 0:
        issues.append(QualityIssue(
            code=QualityIssueCode.IMPOSSIBLE_COORDINATES,
            severity="error",
            message=f"发现 {vp.impossible_coordinate_count} 个经纬度出界坐标（扫描集内）",
            repairable=True,
            evidence={"count": vp.impossible_coordinate_count},
        ))
    if vp.zero_zero_coordinate_count > max(3, vp.scanned_rows // 100):
        issues.append(QualityIssue(
            code=QualityIssueCode.ZERO_COORDINATES,
            severity="warning",
            message=f"{vp.zero_zero_coordinate_count} 个 (0,0) 坐标，疑似缺失值填充",
            repairable=True,
            evidence={"count": vp.zero_zero_coordinate_count},
        ))

    # 字段级检查
    for name, fp in vp.fields.items():
        issues.extend(_field_checks(name, fp, vp.scanned_rows))

    # SCHEMA_TRUNCATED
    if vp.fields_truncated:
        issues.append(QualityIssue(
            code=QualityIssueCode.SCHEMA_TRUNCATED,
            severity="info",
            message="字段数超剖析上限，证据被截断",
        ))
    return issues


def _field_checks(name: str, fp: FieldProfile, scanned: int) -> List[QualityIssue]:
    issues: List[QualityIssue] = []
    # NULL_HEAVY_FIELD
    if fp.null_rate is not None and scanned >= 10 and fp.null_rate >= 0.9:
        issues.append(QualityIssue(
            code=QualityIssueCode.NULL_HEAVY_FIELD,
            severity="warning" if fp.null_rate < 0.99 else "error",
            field=name,
            message=f"字段 {name} 缺失率 {fp.null_rate:.1%}",
            repairable=True,
            evidence={"null_rate": round(fp.null_rate, 4)},
        ))
    # INVALID_DATES（时间语义字段的样本解析失败）
    if fp.temporal_hint and fp.dtype == "string" and fp.samples:
        bad = sum(
            1 for s in fp.samples
            if not isinstance(s, (int, float)) and not _parses_as_date(s)
        )
        if bad:
            issues.append(QualityIssue(
                code=QualityIssueCode.INVALID_DATES,
                severity="warning",
                field=name,
                message=f"时间字段 {name} 有 {bad}/{len(fp.samples)} 个样本无法按日期解析",
                repairable=True,
                evidence={"samples": len(fp.samples), "bad": bad},
            ))
    return issues


def _parses_as_date(value: str) -> bool:
    """严格日期判定：形状像日期不够（2024-13-45 能过正则）——必须可解析。"""
    s = str(value)
    from app.lib.data.profile import _YEAR_ONLY_RE

    if _YEAR_ONLY_RE.match(s):
        try:
            y = int(s)
            return 1000 <= y <= 9999
        except ValueError:
            return False
    try:
        datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1) if " " in s and "T" not in s else s)
        return True
    except ValueError:
        return False


def _raster_checks(raster: Any, *, crs: str) -> List[QualityIssue]:
    from app.lib.data.profile import RasterProfileData

    issues: List[QualityIssue] = []
    r: RasterProfileData = raster  # type: ignore[assignment]
    if not crs and not (r.crs or ""):
        issues.append(QualityIssue(
            code=QualityIssueCode.CRS_MISSING,
            severity="warning",
            message="栅格 CRS 未知",
            repairable=True,
        ))
    # NODATA_SATURATION
    for bs in r.band_stats:
        if bs.valid_pixel_ratio is not None and bs.valid_pixel_ratio < 0.01:
            issues.append(QualityIssue(
                code=QualityIssueCode.NODATA_SATURATION,
                severity="warning",
                field=f"band_{bs.band}",
                message=f"波段 {bs.band} 有效像元占比 {bs.valid_pixel_ratio:.2%}",
                repairable=False,
                evidence={"valid_ratio": bs.valid_pixel_ratio},
            ))
    return issues


def run_quality_checks(
    profile: DatasetProfileV3,
    *,
    deep_evidence: bool = False,
) -> QualityReport:
    """剖析证据 → 质量报告（§七）。不读数据本体。

    ``deep_evidence=False``（仅剖析证据）时，需要几何库/全量数据的检查
    （自相交、重复几何/行）列入 checks_not_run。
    """
    issues: List[QualityIssue] = []
    checks_run: List[str] = []
    checks_not_run: List[str] = []

    if profile.profile_quality == "failed":
        issues.append(QualityIssue(
            code=QualityIssueCode.PROFILE_INCOMPLETE,
            severity="warning",
            message="剖析失败，质量结论不可用",
        ))
        report = QualityReport(target_ref=profile.target_ref, issues=issues)
        report.status = QualityStatus.UNCHECKED
        return report

    if profile.vector is not None:
        issues.extend(_vector_checks(profile.vector, crs=profile.crs))
        checks_run += [
            "empty_payload", "empty_geometry", "crs_missing", "impossible_coordinates",
            "null_heavy_field", "invalid_dates", "schema_truncated",
        ]
        if not deep_evidence:
            checks_not_run += ["self_intersection", "duplicate_geometries", "duplicate_rows"]
    if profile.raster is not None:
        issues.extend(_raster_checks(profile.raster, crs=profile.crs))
        checks_run += ["crs_missing", "nodata_saturation"]
        if not deep_evidence:
            checks_not_run += ["resolution_mismatch", "extent_mismatch"]
    if profile.table is not None:
        for name, fp in profile.table.columns.items():
            issues.extend(_field_checks(name, fp, profile.table.scanned_rows))
        if profile.table.coordinate_candidates and not profile.crs:
            issues.append(QualityIssue(
                code=QualityIssueCode.CRS_MISSING,
                severity="warning",
                message="表含坐标候选列但 CRS 未知（无法安全空间化）",
                repairable=True,
                evidence={"columns": profile.table.coordinate_candidates},
            ))
        checks_run += ["null_heavy_field", "crs_missing", "invalid_dates"]

    if not checks_run:
        issues.append(QualityIssue(
            code=QualityIssueCode.PROFILE_INCOMPLETE,
            severity="info",
            message="无可用剖析证据（未执行任何检查）",
        ))

    report = QualityReport(
        target_ref=profile.target_ref,
        issues=issues,
        checks_run=checks_run,
        checks_not_run=checks_not_run,
    )
    report.status = compose_status(issues)
    return report
