"""validate_spatial_operation —— 操作 × 契约 → 五态判定（GOAL 01 · M2/M3）。

判定词表（VERDICT_VOCABULARY，与科学门 pass/warning/not_evaluated 哲学
对齐）：

- ``pass``：语义齐备，无披露必要；
- ``pass_with_disclosure``：可执行，附非阻塞披露（rfc7946 缺省/归一链/换算链）；
- ``degraded_with_disclosure``：可执行但科学口径降级（3857 失真/跨带/栅格
  分辨率单位失配）—— 结论可用，须随证据披露；
- ``requires_clarification``：语义歧义（CRS 未知但结构合法/分母口径冲突/
  AOI 与数据 CRS 矛盾）—— Agent 应向用户澄清，不执行；
- ``blocked``：确定性错误（未声明 CRS 投影样/无效范围/单位越词表/AOI 与
  数据不相交）—— 拒绝执行，绝不改写成成功；
- ``not_evaluated``：该操作面无对应证据（如 topology 目的下的距离参数）。

修复建议分级（REPAIR_AUTHORIZATION_VOCABULARY，决策 D4）：

- ``explicit_required``（默认）：须用户/上层显式授权才执行；
- ``deterministic_safe``：双方显式声明的 EPSG↔EPSG 可逆变换（或词表内
  单位换算）—— 可自动执行并留链。
"""
from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field

from app.services.gis_harness.spatial_semantics.contract import (
    ContractReasonCode,
    SpatialSemanticContract,
)
from app.services.gis_harness.spatial_semantics.resolve import CalculationSpace
from app.services.gis_harness.spatial_semantics.units import (
    DISTANCE_UNIT_TO_M,
    normalize_distance_params,
)
from app.services.gis_harness.spatial_semantics.vocabulary import (
    DENSITY_DENOMINATOR_UNIT,
    reason_code_metadata,
)

_MAX_REASON_CODES = 12
_MAX_REPAIRS = 4

_OPERATION_VOCABULARY = (
    "buffer", "multi_ring_buffer", "spatial_join", "overlay",
    "spatial_aggregate", "zonal_statistics", "density", "measure",
    "topology", "generic",
)
# 度量敏感操作（距离/面积/密度语义进入结果数值）。
_METRIC_OPERATIONS = frozenset({
    "buffer", "multi_ring_buffer", "spatial_aggregate",
    "zonal_statistics", "density", "measure",
})


class SpatialOperationSpec(BaseModel):
    """被验证的操作声明（bounded；只带语义参数，不带数据）。"""

    operation: str = "generic"
    distance: Optional[float] = None
    distance_unit: Optional[str] = None          # m/km（缺省 None = 未声明）
    denominator_kind: Optional[str] = None       # area/population/count/none
    requested_density_denominator: Optional[str] = None   # per_km2/per_10k_persons/…
    raster_crs: Optional[str] = None
    raster_resolution: Optional[float] = None
    raster_resolution_unit: Optional[str] = None # degree/m
    numerator_display: Optional[str] = None      # 图例分子显示（如 "个"/"所"）
    scope_crs: Optional[str] = None              # AOI bbox 的 CRS（对账用）


class RepairSuggestion(BaseModel):
    action: str = Field(default="", max_length=48)
    detail: str = Field(default="", max_length=240)
    authorization: str = "explicit_required"
    transform: str = Field(default="", max_length=160)


class SpatialOperationVerdict(BaseModel):
    status: str = "not_evaluated"
    reason_codes: List[ContractReasonCode] = Field(
        default_factory=list, max_length=_MAX_REASON_CODES)
    repair_suggestions: List[RepairSuggestion] = Field(
        default_factory=list, max_length=_MAX_REPAIRS)
    blocked_fields: List[str] = Field(default_factory=list, max_length=8)


def _blocking(rc: ContractReasonCode) -> bool:
    return reason_code_metadata(rc.code).severity == "blocking"


def validate_spatial_operation(
    contract: SpatialSemanticContract,
    space: CalculationSpace,
    spec: SpatialOperationSpec,
) -> SpatialOperationVerdict:
    """统一判定（确定性；同输入恒同输出；理由码 ≤12、修复建议 ≤4）。"""
    reasons: List[ContractReasonCode] = []
    repairs: List[RepairSuggestion] = []
    blocked_fields: List[str] = []

    def _add(code: str, detail: str, field: str = "") -> None:
        if len(reasons) < _MAX_REASON_CODES and not any(
                r.code == code and r.field == field for r in reasons):
            reasons.append(ContractReasonCode(
                code=code, detail=detail[:200], field=field[:48]))

    def _repair(action: str, detail: str, *,
                authorization: str = "explicit_required",
                transform: str = "") -> None:
        if len(repairs) < _MAX_REPAIRS and not any(
                r.action == action for r in repairs):
            repairs.append(RepairSuggestion(
                action=action, detail=detail[:240],
                authorization=authorization, transform=transform[:160]))

    op = spec.operation if spec.operation in _OPERATION_VOCABULARY else "generic"
    metric_sensitive = op in _METRIC_OPERATIONS

    # ══ 1) blocking 级：CRS 事实 ════════════════════════════════════════
    for rc in contract.reason_codes:
        if _blocking(rc):
            _add(rc.code, rc.detail, rc.field)
            blocked_fields.append(rc.field)
        else:
            # 契约侧披露（rfc7946 缺省/归一链/跨 AM/轴序嫌疑…）透传进判定，
            # 消费方只需看 verdict（pass_with_disclosure 的依据来源）。
            _add(rc.code, rc.detail, rc.field)
    if any(r.code == "SSC_CRS_UNDECLARED_NON_DEGREE" for r in reasons):
        _repair("declare_crs",
                "数据坐标越 WGS84 经纬包络（投影样）但未声明 CRS —— 请声明真实 "
                "CRS（如 EPSG:32648）或改用已声明数据；禁止默认 EPSG:4326")
    if any(r.code == "SSC_COORD_OUT_OF_ENVELOPE" for r in reasons):
        _repair("clarify_crs",
                "声明的地理 CRS 与数据坐标矛盾 —— 核对 crs member 或数据坐标系")

    # ══ 2) 度量敏感操作 × CRS 证据 ═════════════════════════════════════
    if metric_sensitive and not blocked_fields:
        if contract.crs_source == "unknown" and not any(
                r.code == "SSC_CRS_UNDECLARED_NON_DEGREE" for r in reasons):
            _add("SSC_UNKNOWN_CRS",
                 "度量敏感操作但 CRS 未知（结构证据不足）—— 不猜", "source_crs")
            _repair("declare_crs", "声明数据 CRS 后重试（度量语义依赖 CRS）")
        elif contract.crs_source == "assumed":
            _add("SSC_CRS_ASSUMED",
                 "CRS 为启发式假设 —— 度量结论不可信", "source_crs")
            _repair("declare_crs", "将假设 CRS 升格为显式声明（declare_crs）")

    # ══ 3) 距离参数单位（M3：m vs degree 混用防线）═════════════════════
    distance_m: Optional[float] = None
    if spec.distance is not None:
        unit = (spec.distance_unit or "").strip().lower()
        if unit not in DISTANCE_UNIT_TO_M:
            _add("SSC_DISTANCE_UNIT_INVALID",
                 f"距离单位 {spec.distance_unit!r} 不在词表 m/km —— 拒绝执行",
                 "distance_unit")
            blocked_fields.append("distance_unit")
            _repair("clarify_distance_unit",
                    "显式声明单位 m 或 km（如 unit=\"m\"）")
        else:
            distance_m, chain = normalize_distance_params(spec.distance, unit)
            if chain:
                _add("SSC_DISTANCE_UNIT_CONVERTED",
                     "距离换算链 " + "; ".join(chain), "distance")
        # 度级量值 + 无单位 → 不得当米（"buffer 0.005 度" 类典型错误）
        if (spec.distance_unit is None and spec.distance is not None
                and 0.0 < abs(spec.distance) <= 0.05):
            _add("SSC_DEGREE_DISTANCE_REQUEST",
                 f"距离 {spec.distance} 呈度级量值且单位未声明 —— 不得当米用",
                 "distance")
            blocked_fields.append("distance")

    # ══ 4) AOI（范围）对账 ═════════════════════════════════════════════
    scope = contract.requested_scope
    if scope.bbox:
        try:
            sminx, sminy, smaxx, smaxy = (float(v) for v in scope.bbox[:4])
            if sminx > smaxx or sminy > smaxy:
                raise ValueError("min>max")
        except (TypeError, ValueError):
            _add("SSC_SCOPE_INVALID", "AOI bbox min>max / 非数值", "requested_scope")
            blocked_fields.append("requested_scope")
            _repair("clarify_scope", "AOI 范围无效 —— 给出 [minx,miny,maxx,maxy]")
        else:
            # AOI CRS × 数据 CRS 对账（同帧才可直接判 overlap/disjoint）
            scope_crs = (spec.scope_crs or scope.bbox_crs or "").strip().upper()
            data_crs = (contract.source_crs or "").strip().upper()
            normalized = {"EPSG:4326", "CRS84", "OGC:CRS84", "WGS84"}
            if (scope_crs and data_crs
                    and scope_crs != data_crs
                    and not (scope_crs in normalized and data_crs in normalized)):
                _add("SSC_SCOPE_DATA_CRS_MISMATCH",
                     f"AOI CRS {scope_crs} ≠ 数据 CRS {data_crs} —— 拒绝混合 CRS 执行",
                     "requested_scope")
                _repair("clarify_scope_crs",
                        "统一 AOI 与数据的 CRS（显式声明其一或 transform）")
            if contract.scope_overlap == "disjoint":
                _add("SSC_SCOPE_DISJOINT_FROM_DATA",
                     "AOI 与数据 bbox 完全不相交 —— 连接/聚合结果必然为空",
                     "requested_scope")
                blocked_fields.append("requested_scope")
                _repair("clarify_scope",
                        "AOI 与数据范围不相交 —— 核对研究区或换数据")

    # ══ 5) AOI bbox × 数值合理性（S12：无效范围，独立于 overlap）════════
    if scope.bbox and scope.bbox_crs.upper() in ("EPSG:4326", "CRS84", "WGS84", ""):
        try:
            sminx, sminy, smaxx, smaxy = (float(v) for v in scope.bbox[:4])
        except (TypeError, ValueError):
            sminx = None  # 已在上方判 SSC_SCOPE_INVALID
        if sminx is not None and not blocked_fields:
            if not (-180.0 <= sminx <= 180.0 and -90.0 <= sminy <= 90.0
                    and -180.0 <= smaxx <= 180.0 and -90.0 <= smaxy <= 90.0):
                _add("SSC_SCOPE_INVALID",
                     "AOI bbox 越 WGS84 经纬包络（且 bbox_crs 声明为地理）",
                     "requested_scope")
                blocked_fields.append("requested_scope")

    # ══ 6) 分母/密度口径（S6/S7）══════════════════════════════════════
    if spec.requested_density_denominator:
        requested = spec.requested_density_denominator
        want_unit = DENSITY_DENOMINATOR_UNIT.get(requested)
        actual = spec.denominator_kind or "none"
        if want_unit == "population":
            if actual != "population":
                _add("SSC_DENOMINATOR_KIND_MISMATCH",
                     f"请求口径 {requested}（人口分母）≠ 分母类型 {actual}",
                     "stat_denominator")
                _repair("switch_denominator",
                        "二选一：分母换人口字段（per_capita 口径）或请求改 per_km²")
        else:
            if actual not in ("area", None):
                _add("SSC_DENOMINATOR_KIND_MISMATCH",
                     f"请求口径 {requested}（面积分母 {want_unit}）≠ 分母类型 {actual}",
                     "stat_denominator")
                _repair("switch_denominator",
                        "二选一：分母换聚合单元面积（area）或请求改对应口径")

    # ══ 7) 计算空间降级披露（3857/跨带/极区 —— resolve 已给证据）══════
    for rc in space.reason_codes:
        _add(rc.code, rc.detail, rc.field)
    if (space.distortion_estimate is not None
            and space.distortion_estimate > 0.01):
        # >1% 失真在 degraded 而非 pass 上披露
        _repair("transform",
                f"确定性安全变换 {space.suggested_transform} 后重算"
                "（双方显式 EPSG，可逆）",
                authorization="deterministic_safe",
                transform=space.suggested_transform)
    if space.mode == "unknown" and metric_sensitive and not blocked_fields:
        _add("SSC_UNKNOWN_CRS",
             "计算空间未知（无 CRS 证据）—— 度量操作无法合规执行", "working_crs")
        _repair("declare_crs", "声明数据 CRS 后重试")

    # ══ 8) 栅格分辨率单位（S12/S14 面）════════════════════════════════
    if (op == "zonal_statistics" and spec.raster_resolution is not None
            and spec.raster_resolution_unit):
        r_unit = spec.raster_resolution_unit.strip().lower()
        if r_unit == "degree" and (distance_m or 0) > 0:
            _add("SSC_RASTER_CELL_UNIT_MISMATCH",
                 f"栅格分辨率 {spec.raster_resolution}{r_unit}（度）与米制请求"
                 "并存 —— 换算依赖纬度，须附披露",
                 "raster_resolution")
        elif r_unit not in ("degree", "m", "meter", "metre"):
            _add("SSC_RASTER_CELL_UNIT_MISMATCH",
                 f"栅格分辨率单位 {r_unit!r} 不在 degree/m 词表", "raster_resolution")
            blocked_fields.append("raster_resolution")

    # ══ 9) 中国偏移帧风险（S9：未声明帧，不自动归一）══════════════════
    from app.services.gis_harness.spatial_semantics.inspect_semantics import (
        evidence_for_china_offset_risk,
    )
    if (evidence_for_china_offset_risk(contract)
            and op in ("spatial_join", "overlay", "buffer", "generic")):
        _add("SSC_CHINA_OFFSET_FRAME_RISK",
             "AOI 位于中国且坐标帧未显式声明 —— 存在 GCJ-02/BD-09 偏移风险"
             "（~100-600m；未声明数据不自动归一）", "source_crs")
        _repair("clarify_chinese_frame",
                "确认数据帧（wgs84/gcj02/bd09）；确属 gcj02/bd09 时用 "
                "transform_coordinates 归一后重试")

    # ══ 判定收敛（最严者胜，确定性顺序）════════════════════════════════
    severities = {reason_code_metadata(r.code).severity for r in reasons}
    if blocked_fields or any(_blocking(r) for r in reasons):
        status = "blocked"
    elif "requires_clarification" in severities or any(
            r.code in ("SSC_DENOMINATOR_KIND_MISMATCH",
                       "SSC_SCOPE_DATA_CRS_MISMATCH") for r in reasons):
        status = "requires_clarification"
    elif ("degraded" in severities
            or (space.distortion_estimate is not None
                and space.distortion_estimate > 0.01)):
        status = "degraded_with_disclosure"
    elif reasons or contract.datum_normalization:
        status = "pass_with_disclosure"
    else:
        status = "pass"
    if op not in _METRIC_OPERATIONS and op not in ("spatial_join", "overlay",
                                                   "topology"):
        status = "not_evaluated" if status == "pass" else status

    return SpatialOperationVerdict(
        status=status,
        reason_codes=reasons,
        repair_suggestions=repairs,
        blocked_fields=blocked_fields,
    )


__all__ = [
    "SpatialOperationSpec",
    "RepairSuggestion",
    "SpatialOperationVerdict",
    "validate_spatial_operation",
]
