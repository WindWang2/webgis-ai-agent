"""空间语义检查收据（M3；M2 契约 §3）。

回答的问句：「这份数据的空间语义健康吗 —— 几何/CRS/坐标轴序/主键/时区/
分母，哪里 ok、哪里 warn、哪里必须阻断？」

聚合纪律（绝不重复造检查）：
- 既有质量检查（app/lib/data/quality.py::run_quality_checks）产出的
  QualityReport **逐条透传**为 InspectionFinding（severity 映射：
  error→blocked、warning/info→warn/ok），码与证据原样保留；
- 本模块**新增**的只有 quality 词表没有的四种证据：
  AXIS_ORDER_SUSPECT（经纬度列对调嫌疑 —— 需要列对联合判定，逐列检查
  做不到）、PK_DUPLICATED / PK_MISSING（主键唯一性 —— 结合提案引擎的
  id 角色与画像唯一度）、DENOMINATOR_ABSENT（角色级缺口：有度量值但
  无归一化分母 —— 提案层的联合视野）；
- 全部输入是已就位证据（DatasetProfileV3 + QualityReport + 提案列表 +
  解析侧 CRS 披露），零 IO、纯函数。

阻断语义（与准备计划联动）：severity=blocked 的 finding 阻止自动执行
（auto_execute / 高置信直通）；SWITCH_XY 建议必须先有 AXIS_ORDER_SUSPECT
finding 才允许构造 AxisSwapStep（plan.py 校验）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field, field_validator

from app.lib.data.profile import DatasetProfileV3, FieldProfile
from app.lib.data.quality import QualityIssueCode, QualityReport
from app.services.data_preparation.field_proposal import (
    FieldProposal,
    name_role_hit,
)
from app.services.data_preparation.vocabulary import (
    FINDING_AXIS_ORDER_SUSPECT,
    FINDING_DENOMINATOR_ABSENT,
    FINDING_PK_DUPLICATED,
    FINDING_PK_MISSING,
    FindingSeverity,
    SuggestionCode,
)

_MAX_FINDINGS = 32
_MAX_EVIDENCE_KEYS = 8
_LAT_ABS_MAX = 90.0
_LON_ABS_MAX = 180.0

#: quality severity → 收据 severity 映射（§七四态：error 即 blocked）。
_QC_SEVERITY_MAP = {"error": FindingSeverity.BLOCKED,
                    "warning": FindingSeverity.WARN,
                    "info": FindingSeverity.OK}
#: quality code → 建议码（既有 _REMEDIATIONS 是长文案；这里是计划可执行桥）。
_QC_SUGGESTION_MAP: Dict[str, SuggestionCode] = {
    QualityIssueCode.IMPOSSIBLE_COORDINATES.value: SuggestionCode.FILTER_ROWS,
    QualityIssueCode.CRS_MISSING.value: SuggestionCode.DECLARE_CRS,
    QualityIssueCode.CRS_SUSPICIOUS.value: SuggestionCode.DECLARE_CRS,
    QualityIssueCode.TIMEZONE_MISSING.value: SuggestionCode.PICK_TIMEZONE,
    QualityIssueCode.FIELD_ROLE_AMBIGUOUS.value: SuggestionCode.CONFIRM_ROLE,
    QualityIssueCode.DUPLICATE_ROWS.value: SuggestionCode.DESIGNATE_KEY,
    QualityIssueCode.INVALID_GEOMETRY.value: SuggestionCode.REPAIR_GEOMETRY,
    QualityIssueCode.SELF_INTERSECTION.value: SuggestionCode.REPAIR_GEOMETRY,
}
#: 收据新增码 → 建议码。
_NEW_CODE_SUGGESTION = {
    FINDING_AXIS_ORDER_SUSPECT: SuggestionCode.SWITCH_XY,
    FINDING_PK_DUPLICATED: SuggestionCode.DESIGNATE_KEY,
    FINDING_PK_MISSING: SuggestionCode.DESIGNATE_KEY,
    FINDING_DENOMINATOR_ABSENT: SuggestionCode.PROVIDE_DENOMINATOR,
}


class InspectionFinding(BaseModel):
    """单条检查发现（code + severity + 有界证据 + 建议码）。"""

    code: str
    severity: FindingSeverity = FindingSeverity.OK
    message_zh: str = ""
    evidence: Dict[str, Any] = Field(default_factory=dict)
    suggestion: SuggestionCode = SuggestionCode.NONE
    field: str = ""          # 定位字段（可空 = 数据集级）

    @field_validator("code")
    @classmethod
    def _code(cls, v: str) -> str:
        return str(v)[:48]

    @field_validator("message_zh")
    @classmethod
    def _msg(cls, v: str) -> str:
        return str(v)[:240]

    @field_validator("evidence")
    @classmethod
    def _ev(cls, v: Dict[str, Any]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, val in list(v.items())[:_MAX_EVIDENCE_KEYS]:
            out[str(k)[:48]] = val if isinstance(val, (int, float, str, bool)) else str(val)[:200]
        return out


class SpatialInspectionReceipt(BaseModel):
    """一次 inspect 的空间语义收据（全部有界；不含任何行数据）。"""

    input_ref: str = ""
    geometry_type: str = ""
    srs: str = ""
    crs_source: str = ""            # declared/source/assumed/rfc7946_default
    axis_order_guessed: str = ""    # lon_lat / lat_lon / ""（无列对证据）
    coordinate_range: Optional[List[float]] = None   # 扫描集 bbox
    encoding: str = ""
    time_fields: Tuple[str, ...] = ()
    tz_status: str = ""             # ok / missing / ""（无时间字段）
    pk_field: str = ""
    pk_status: str = ""             # unique / duplicated / missing
    row_count: int = 0
    sample_rows_scanned: int = 0
    findings: Tuple[InspectionFinding, ...] = ()

    @field_validator("findings")
    @classmethod
    def _findings(cls, v: Tuple[InspectionFinding, ...]) -> Tuple[InspectionFinding, ...]:
        return tuple(v)[:_MAX_FINDINGS]

    @property
    def blocked(self) -> bool:
        return any(f.severity == FindingSeverity.BLOCKED for f in self.findings)

    def summary(self) -> Dict[str, Any]:
        """LLM/工具面有界摘要（状态先行，findings 只计码）。"""
        return {
            "input_ref": self.input_ref,
            "geometry_type": self.geometry_type or None,
            "srs": self.srs or None,
            "crs_source": self.crs_source or None,
            "row_count": self.row_count,
            "blocked": self.blocked,
            "finding_codes": [f.code for f in self.findings],
            "pk_status": self.pk_status or None,
            "tz_status": self.tz_status or None,
        }


def _fp_of(proposals: Sequence[FieldProposal], field: str) -> Optional[FieldProposal]:
    for p in proposals:
        if p.field == field:
            return p
    return None


def _range(fp: Optional[FieldProfile]) -> Optional[Tuple[float, float]]:
    if fp is None or fp.min is None or fp.max is None:
        return None
    return (float(fp.min), float(fp.max))


def _fields_of(profile: DatasetProfileV3) -> Dict[str, FieldProfile]:
    if profile.table is not None:
        return dict(profile.table.columns)
    if profile.vector is not None:
        return dict(profile.vector.fields)
    return {}


def _qc_findings(report: Optional[QualityReport]) -> List[InspectionFinding]:
    """QualityReport → 收据 finding（逐条透传；码/证据/字段定位保留）。"""
    if report is None:
        return []
    out: List[InspectionFinding] = []
    for issue in report.issues[:_MAX_FINDINGS]:
        ev = dict(getattr(issue, "evidence", None) or {})
        out.append(InspectionFinding(
            code=str(issue.code.value if isinstance(issue.code, QualityIssueCode) else issue.code),
            severity=_QC_SEVERITY_MAP.get(str(issue.severity), FindingSeverity.WARN),
            message_zh=str(getattr(issue, "message", "") or issue.code),
            evidence=ev,
            suggestion=_QC_SUGGESTION_MAP.get(
                str(issue.code.value if isinstance(issue.code, QualityIssueCode) else issue.code),
                SuggestionCode.NONE),
            field=str(getattr(issue, "field", "") or ""),
        ))
    return out


def _axis_order_check(
    fields: Dict[str, FieldProfile],
    proposals: Sequence[FieldProposal],
) -> Tuple[List[InspectionFinding], str]:
    """经纬度列对联合判定（quality 逐列检查做不到的新证据）。

    列身份用**名称事实**（name_role_hit，与提案置信度无关 —— 对调场景中
    名义 lat 列已被值域反证拒绝、不在候选里，但名称身份仍在）；矛盾证据
    来自两列值域的联合比较。

    判定口径（确定性，双向都看 —— 只报嫌疑，修正必须经用户确认的
    AxisSwapStep）：
    - 名义 lat 列值域越过 ±90（lat 不可能）且名义 lon 列值域 ⊆ [-90,90]
      （lon 可以是 lat 值）→ AXIS_ORDER_SUSPECT（blocked）；
    - 名义 lon 列 ⊆ [18,54] 且名义 lat 列 ⊆ [73,135]（中国 bbox 经纬带）
      → 同码（warn 补充信号，与上一条独立成立即可报）。
    axis_order_guessed：列名身份成立且无嫌疑 → lon_lat（约定序，猜测口径）。
    """
    lon_f = lat_f = None
    for name in fields:
        if lon_f is None and name_role_hit(name, "lon") is not None:
            lon_f = name
        elif lat_f is None and name_role_hit(name, "lat") is not None:
            lat_f = name
    if not lon_f or not lat_f:
        return [], ""
    lon_r = _range(fields.get(lon_f))
    lat_r = _range(fields.get(lat_f))
    if lon_r is None or lat_r is None:
        return [], "lon_lat"
    out: List[InspectionFinding] = []
    lat_impossible = lat_r[0] < -_LAT_ABS_MAX or lat_r[1] > _LAT_ABS_MAX
    lon_latlike = -_LAT_ABS_MAX <= lon_r[0] and lon_r[1] <= _LAT_ABS_MAX
    if lat_impossible and lon_latlike:
        out.append(InspectionFinding(
            code=FINDING_AXIS_ORDER_SUSPECT,
            severity=FindingSeverity.BLOCKED,
            message_zh=f"疑似经纬度列对调：『{lat_f}』值域越出 ±90 而『{lon_f}』"
                       f"值域落在纬度带 —— 自动执行已阻断，请确认后构造坐标轴对调步",
            evidence={"lat_field": lat_f, "lon_field": lon_f,
                      "lat_range": [lat_r[0], lat_r[1]],
                      "lon_range": [lon_r[0], lon_r[1]]},
            suggestion=SuggestionCode.SWITCH_XY,
        ))
    elif (18.0 <= lon_r[0] and lon_r[1] <= 54.0
          and 73.0 <= lat_r[0] and lat_r[1] <= 135.0):
        out.append(InspectionFinding(
            code=FINDING_AXIS_ORDER_SUSPECT,
            severity=FindingSeverity.WARN,
            message_zh=f"『{lon_f}』值域在中国纬度带、『{lat_f}』值域在中国经度带 —— "
                       "疑似对调（中国 bbox 口径），请复核",
            evidence={"lat_field": lat_f, "lon_field": lon_f,
                      "lat_range": [lat_r[0], lat_r[1]],
                      "lon_range": [lon_r[0], lon_r[1]]},
            suggestion=SuggestionCode.SWITCH_XY,
        ))
    return out, "lon_lat"


def _pk_check(
    fields: Dict[str, FieldProfile],
    proposals: Sequence[FieldProposal],
    row_count: int,
) -> Tuple[List[InspectionFinding], str, str]:
    """主键唯一性（提案 id 角色 × 画像唯一度；无 id 角色列 = missing 警示）。"""
    id_fields = [p.field for p in proposals
                 if p.top is not None and p.top.role == "id"]
    if not id_fields:
        return [InspectionFinding(
            code=FINDING_PK_MISSING,
            severity=FindingSeverity.WARN,
            message_zh="未发现主键候选列：去重/跨会话对账将退化为行序口径，"
                       "建议指定业务主键或接受行序语义",
            evidence={"id_candidates": 0},
            suggestion=SuggestionCode.DESIGNATE_KEY,
        )], "", "missing"
    pk = id_fields[0]
    fp = fields.get(pk)
    if fp is None or fp.unique_count is None:
        return [], pk, ""   # 证据不足：诚实空（不虚构判定）
    if fp.unique_capped:
        return [], pk, "capped"
    scanned = max(row_count, 1)
    if fp.unique_count >= scanned:
        return [], pk, "unique"
    dup = max(scanned - fp.unique_count, 0)
    return [InspectionFinding(
        code=FINDING_PK_DUPLICATED,
        severity=FindingSeverity.WARN,
        message_zh=f"主键候选『{pk}』存在重复（约 {dup} 行）—— 去重步前不可作对账键",
        evidence={"pk_field": pk, "duplicate_rows": dup,
                  "unique_count": fp.unique_count, "row_count": scanned},
        suggestion=SuggestionCode.DESIGNATE_KEY,
        field=pk,
    )], pk, "duplicated"


def _denominator_check(proposals: Sequence[FieldProposal]) -> List[InspectionFinding]:
    """角色级缺口：有度量值但无分母候选（人均类分析的诚实降级披露）。"""
    roles = {p.top.role for p in proposals if p.top is not None}
    if ("value" in roles or "weight" in roles) and "denominator" not in roles:
        return [InspectionFinding(
            code=FINDING_DENOMINATOR_ABSENT,
            severity=FindingSeverity.OK,   # 披露级：不是数据本身的错误
            message_zh="存在度量值但未见归一化分母候选（人口/面积类）—— 人均、"
                       "密度类分析将以无分母降级执行或要求补充数据",
            evidence={"measure_fields": sorted(
                p.field for p in proposals
                if p.top is not None and p.top.role in ("value", "weight"))[:8]},
            suggestion=SuggestionCode.PROVIDE_DENOMINATOR,
        )]
    return []


def build_spatial_receipt(
    profile: DatasetProfileV3,
    *,
    quality_report: Optional[QualityReport] = None,
    proposals: Optional[Sequence[FieldProposal]] = None,
    crs_source: str = "",
    encoding: str = "",
) -> SpatialInspectionReceipt:
    """已就位证据 → 空间收据（纯函数；零扫描）。"""
    proposals = list(proposals or [])
    fields = _fields_of(profile)
    vec = profile.vector
    tab = profile.table

    findings: List[InspectionFinding] = list(_qc_findings(quality_report))
    axis_findings, axis_guess = _axis_order_check(fields, proposals)
    pk_findings, pk_field, pk_status = _pk_check(
        fields, proposals, row_count=(tab or vec).row_count if (tab or vec) else 0)
    findings.extend(axis_findings)
    findings.extend(pk_findings)
    findings.extend(_denominator_check(proposals))

    # 时间字段/时区态（画像 temporal 证据 + quality TIMEZONE_MISSING 透传）。
    time_fields: Tuple[str, ...] = ()
    if tab is not None:
        time_fields = tuple(tab.time_candidates[:8])
    elif vec is not None:
        time_fields = tuple(vec.temporal_fields[:8])
    tz_status = ""
    if time_fields:
        tz_missing = any(
            f.code == QualityIssueCode.TIMEZONE_MISSING.value for f in findings)
        tz_status = "missing" if tz_missing else "ok"

    # 有界排序：blocked > warn > ok（同类稳定序）。
    sev_rank = {FindingSeverity.BLOCKED: 0, FindingSeverity.WARN: 1,
                FindingSeverity.OK: 2}
    findings.sort(key=lambda f: (sev_rank[f.severity], f.code))

    geometry_type = ""
    if vec is not None and vec.geometry_types:
        geometry_type = vec.geometry_types[0]
    return SpatialInspectionReceipt(
        input_ref=str(profile.target_ref or "")[:200],
        geometry_type=geometry_type,
        srs=str(profile.crs or "")[:64],
        crs_source=str(crs_source or "")[:32],
        axis_order_guessed=axis_guess,
        coordinate_range=list(vec.extent) if vec is not None and vec.extent else None,
        encoding=str(encoding or "")[:32],
        time_fields=time_fields,
        tz_status=tz_status,
        pk_field=pk_field[:128],
        pk_status=pk_status,
        row_count=(tab.row_count if tab is not None
                   else (vec.row_count if vec is not None else 0)),
        sample_rows_scanned=(tab.scanned_rows if tab is not None
                             else (vec.scanned_rows if vec is not None else 0)),
        findings=tuple(findings),
    )
