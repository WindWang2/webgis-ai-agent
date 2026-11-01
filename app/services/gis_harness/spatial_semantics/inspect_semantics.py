"""inspect_spatial_contract —— 数据证据 → 空间语义契约（GOAL 01 · M2）。

纯函数、零 IO、descriptor 级（决策 D7）：坐标采样 ≤ ``_MAX_SAMPLES``、
特征扫描 ≤ ``_MAX_FEATURES_SCAN`` —— 50k feature 输入也只做 O(bounded)
结构判定，几何全量绝不进契约（不进 prompt/LLM）。

证据链（全部复用既有单一权威，不复制实现）：

- CRS 分类：``crs_safety.classify_crs``（地理/投影/局部度量/unknown）；
- 中国偏移帧身份：``utils.coord_transform.normalize_chinese_crs``（单一权威）；
- 经度约定/跨 AM：``longitude.describe_longitude_semantics``；
- 轴序嫌疑：data_preparation/spatial_receipt 同款中国 bbox 口径
  （经度带 73-135 / 纬度带 18-54；坐标对版本）。

未知不猜（决策 D3）：无声明 + 非度样 → ``crs_source=unknown`` + blocking
理由码，由 validate 判 blocked；无声明 + 度样 → rfc7946_default 披露放行。
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import BaseModel, Field

from app.services.gis_harness.spatial_semantics.contract import (
    ContractReasonCode,
    ScopeEvidence,
    SpatialSemanticContract,
)

#: 采样与扫描上限（M7 性能预算：50k feature 也只看这些）。
_MAX_SAMPLES = 200
_MAX_FEATURES_SCAN = 2000

# 中国 bbox（与 data_preparation/spatial_receipt._axis_order_check 同口径）。
_CHINA_LON_MIN, _CHINA_LON_MAX = 73.0, 135.5
_CHINA_LAT_MIN, _CHINA_LAT_MAX = 18.0, 54.0

# WGS84 经纬包络（判"度样"的边界；越界 = 投影样或矛盾）。
_LON_ABS_MAX = 180.0
_LAT_ABS_MAX = 90.0

# 度量级尺度分桶边界（km；描述性 scale_level）。
_SCALE_LOCAL_KM = 50.0
_SCALE_REGIONAL_KM = 500.0
_SCALE_CONTINENTAL_KM = 5000.0

_KM_PER_DEG_LAT = 111.32


class DatasetSpatialEvidence(BaseModel):
    """契约输入信封（descriptor 级；由 FC 扫描或 ref descriptor 组装）。"""

    crs: str = Field(default="", max_length=64)          # 声明 CRS（原样）
    crs_source_hint: str = Field(                        # 上游已知的来源
        default="", max_length=32)                       # declared/source/assumed/""
    ref: str = Field(default="", max_length=128)
    fingerprint: str = Field(default="", max_length=128)
    feature_count: int = 0
    geometry_kinds: Dict[str, int] = Field(default_factory=dict)   # kind→count（有界）
    coord_samples: List[Tuple[float, float]] = Field(default_factory=list)
    bbox: Optional[List[float]] = None                   # 数据 bbox（原始坐标）
    longitude_convention: str = ""                       # pm180/e360/ambiguous/""
    scan_truncated: bool = False                         # 扫描触上限（bbox 是部分口径）


def _iter_coords(geom: Any) -> List[Tuple[float, float]]:
    """几何 → 坐标对（有界；不构造 shapely —— 结构扫描而已）。"""
    out: List[Tuple[float, float]] = []

    def _walk(node: Any) -> None:
        if len(out) >= _MAX_SAMPLES:
            return
        if (isinstance(node, (list, tuple)) and len(node) >= 2
                and isinstance(node[0], (int, float))
                and isinstance(node[1], (int, float))):
            out.append((float(node[0]), float(node[1])))
            return
        if isinstance(node, (list, tuple)):
            for child in node:
                _walk(child)

    _walk(geom)
    return out


def evidence_from_feature_collection(
    fc: Any, *, ref: str = "", fingerprint: str = "",
) -> DatasetSpatialEvidence:
    """FeatureCollection → 证据信封（O(bounded)：≤2000 要素 / ≤200 采样点）。

    提取：顶层 crs member（geo_processor.extract_declared_crs 同规），
    几何类型计数，数据 bbox（采样口径），坐标采样。
    """
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        return DatasetSpatialEvidence(ref=ref, fingerprint=fingerprint)
    # R1 P1：crs member 解析复用 geo_processor.extract_declared_crs（单一
    # 真相 —— 此前本地重实现漏掉 {"properties":{"code":N}} 形态，合法声明
    # 数据被误判 blocked）。
    from app.lib.geo_processor.core import extract_declared_crs

    crs = (extract_declared_crs(fc) or "")[:64]

    features = fc.get("features")
    if not isinstance(features, list):
        return DatasetSpatialEvidence(crs=crs, ref=ref, fingerprint=fingerprint)

    kinds: Dict[str, int] = {}
    samples: List[Tuple[float, float]] = []
    bbox: Optional[List[float]] = None
    scanned = 0
    scan_truncated = False
    for feat in features:
        if scanned >= _MAX_FEATURES_SCAN or len(samples) >= _MAX_SAMPLES:
            # R1 P2：bbox/结构是前缀口径 —— 消费方不得据此判 disjoint
            scan_truncated = scanned >= _MAX_FEATURES_SCAN or (
                len(samples) >= _MAX_SAMPLES and scanned < len(features))
            break
        scanned += 1
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry")
        if not isinstance(geom, dict) or not geom.get("type"):
            continue
        gtype = str(geom["type"])
        kind = ("point" if "Point" in gtype
                else "line" if "LineString" in gtype or "Circular" in gtype
                else "polygon" if "Polygon" in gtype or "Region" in gtype
                else "other")
        kinds[kind] = kinds.get(kind, 0) + 1
        for x, y in _iter_coords(geom.get("coordinates")):
            samples.append((x, y))
            if bbox is None:
                bbox = [x, y, x, y]
            else:
                bbox[0] = min(bbox[0], x)
                bbox[1] = min(bbox[1], y)
                bbox[2] = max(bbox[2], x)
                bbox[3] = max(bbox[3], y)
            if len(samples) >= _MAX_SAMPLES:
                break
    return DatasetSpatialEvidence(
        crs=crs,
        ref=ref,
        fingerprint=fingerprint,
        feature_count=len(features),
        geometry_kinds=dict(sorted(kinds.items())),
        coord_samples=[(round(x, 7), round(y, 7)) for x, y in samples[:_MAX_SAMPLES]],
        bbox=bbox,
        scan_truncated=scan_truncated or scanned < len(features),
    )


def _decimal_places(samples: Sequence[Tuple[float, float]]) -> int:
    """采样坐标的最大小数位数（粗糙精度信号）。"""
    best = 0
    for x, y in samples:
        for v in (x, y):
            s = repr(float(v))
            if "." in s and "e" not in s and "E" not in s:
                best = max(best, len(s.split(".", 1)[1]))
    return min(best, 10)


def _precision_from_decimals(decimals: int) -> str:
    # 度坐标小数位 ↔ 米量级：5 位 ≈ 1.1 m，3 位 ≈ 110 m，2 位 ≈ 1.1 km。
    if decimals >= 5:
        return "fine"
    if decimals >= 3:
        return "medium"
    return "coarse"


def _coordinate_structure(
    samples: Sequence[Tuple[float, float]],
) -> Dict[str, Any]:
    """坐标结构判定（确定性）：

    - ``out_of_envelope``：任一 |x|>180 或 |y|>90（投影样或矛盾）；
    - ``degree_like``：包络内且存在带小数坐标（度的典型形态）；
    - ``integer_grid``：包络内且全整数（精度粗糙，不构成投影证据）。
    """
    if not samples:
        return {"out_of_envelope": False, "degree_like": False,
                "integer_grid": False, "has_fractional": False}
    # R1 P2：NaN/Inf 坐标（nan 比较恒 False 会逃过包络检查）先显式标记。
    nonfinite = any(
        not (math.isfinite(x) and math.isfinite(y)) for x, y in samples)
    out = nonfinite or any(
        abs(x) > _LON_ABS_MAX or abs(y) > _LAT_ABS_MAX for x, y in samples)
    has_frac = any(
        not float(x).is_integer() or not float(y).is_integer()
        for x, y in samples
    )
    return {
        "out_of_envelope": out,
        "degree_like": (not out) and has_frac,
        "integer_grid": (not out) and not has_frac,
        "has_fractional": has_frac,
        "nonfinite": nonfinite,
    }


def _occupied_lon_span_center(
    samples: Sequence[Tuple[float, float]],
) -> Tuple[Optional[float], Optional[float]]:
    """采样经度的占据跨度与弧心（R1 P1：global/近全球数据修复）。

    bbox min/max 反推在 raw_span>180 时无法区分「环绕簇」与「真大洲」——
    用采样经度在单位圆上的**最大空隙**求占据弧：占据弧 = 360° − 最大空隙，
    弧心取空隙后沿。全球散布数据最大空隙小 → 跨度大（→测地）；跨 AM 簇
    最大空隙 ~360°-簇宽 → 跨度 = 簇宽（→单带）。采样 <2 → (None, None)。
    """
    lons = sorted(
        float(x) % 360.0 for x, _ in samples
        if math.isfinite(x))
    if len(lons) < 2:
        return None, None
    gaps = [(lons[i + 1] - lons[i], i) for i in range(len(lons) - 1)]
    gaps.append((lons[0] + 360.0 - lons[-1], len(lons) - 1))  # 环绕空隙
    max_gap, gap_idx = max(gaps)
    occupied = 360.0 - max_gap
    if occupied <= 0.0:
        return 0.0, lons[0]
    arc_start = lons[(gap_idx + 1) % len(lons)]
    center = (arc_start + occupied / 2.0 + 180.0) % 360.0 - 180.0
    return occupied, center


def _axis_order_guess(
    samples: Sequence[Tuple[float, float]],
) -> Tuple[str, str]:
    """坐标对轴序猜测 + 嫌疑证据（spatial_receipt 中国 bbox 同口径）。

    - 任一 y 越出 ±90 而 x 包络内 → lat_lon_suspect（y 不可能是纬度；
      与 receipt 的"名义 lat 列越 ±90"判定同构，坐标对版本）；
    - x ⊆ 纬度带且 y ⊆ 经度带（中国）→ 中国 bbox 对调嫌疑（warn）；
    - 其余 → lon_lat（GeoJSON/RFC7946 约定序，猜测口径）。
    """
    if not samples:
        return "unknown", ""
    xs = [x for x, _ in samples]
    ys = [y for _, y in samples]
    xr = (min(xs), max(xs))
    yr = (min(ys), max(ys))
    if (yr[0] < -_LAT_ABS_MAX or yr[1] > _LAT_ABS_MAX) and (
            -_LAT_ABS_MAX <= xr[0] and xr[1] <= _LAT_ABS_MAX):
        return "lat_lon", (
            f"y 值域 [{yr[0]:.4g},{yr[1]:.4g}] 越 ±90 而 x 值域在纬度带 —— "
            "疑似 (lat,lon) 序")
    if (_CHINA_LAT_MIN <= xr[0] and xr[1] <= _CHINA_LAT_MAX
            and _CHINA_LON_MIN <= yr[0] and yr[1] <= _CHINA_LON_MAX):
        return "lat_lon", (
            "x 值域落在纬度带、y 值域落在经度带（中国 bbox 口径）—— "
            "疑似 (lat,lon) 序，请复核")
    return "lon_lat", "GeoJSON/RFC 7946 约定序（lon,lat）—— 未发现反证"


def _bbox_span_km(bbox: Sequence[float], crs_class: str) -> Optional[float]:
    """bbox 对角跨度（km，粗量级；仅用于 scale_level 分桶）。"""
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox[:4])
    except (TypeError, ValueError, IndexError):
        return None
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        return None
    if crs_class == "geographic":
        mid_lat = min(max((miny + maxy) / 2.0, -89.0), 89.0)
        dx = abs(maxx - minx) * _KM_PER_DEG_LAT * math.cos(math.radians(mid_lat))
        dy = abs(maxy - miny) * _KM_PER_DEG_LAT
        return math.hypot(dx, dy)
    if crs_class in ("projected", "projected_local_metric"):
        return math.hypot(abs(maxx - minx), abs(maxy - miny)) / 1000.0
    return None


def _scale_level(span_km: Optional[float]) -> str:
    if span_km is None:
        return "unknown"
    if span_km < _SCALE_LOCAL_KM:
        return "local"
    if span_km < _SCALE_REGIONAL_KM:
        return "regional"
    if span_km < _SCALE_CONTINENTAL_KM:
        return "continental"
    return "global"


def _china_aoi(bbox: Optional[Sequence[float]]) -> bool:
    if not bbox or len(bbox) < 4:
        return False
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return False
    return (minx >= _CHINA_LON_MIN - 0.5 and maxx <= _CHINA_LON_MAX + 0.5
            and miny >= _CHINA_LAT_MIN - 0.5 and maxy <= _CHINA_LAT_MAX + 0.5)


def inspect_spatial_contract(
    evidence: DatasetSpatialEvidence,
    *,
    scope: Optional[ScopeEvidence] = None,
) -> SpatialSemanticContract:
    """证据信封 → 契约（只判定数据/范围侧；计算语义由 resolve 填充）。"""
    from app.lib.gis.crs_safety import classify_crs
    from app.lib.gis.longitude import describe_longitude_semantics
    from app.utils.coord_transform import normalize_chinese_crs

    reasons: List[ContractReasonCode] = []
    assumptions: List[str] = []
    samples = list(evidence.coord_samples[:_MAX_SAMPLES])
    structure = _coordinate_structure(samples)

    # — CRS 事实来源四态（与 spatial_receipt 一字不差 + unknown）—
    declared = (evidence.crs or "").strip()
    crs_source = "unknown"
    if declared:
        crs_source = "declared"
    elif evidence.crs_source_hint in ("declared", "source", "assumed"):
        crs_source = evidence.crs_source_hint
        declared = evidence.crs if evidence.crs else ""
    elif structure["degree_like"]:
        crs_source = "rfc7946_default"
    # integer_grid / 空采样 → unknown（不猜）

    if crs_source == "rfc7946_default":
        reasons.append(ContractReasonCode(
            code="SSC_CRS_RFC7946_DEFAULT",
            detail="未声明 CRS，坐标呈度样 —— 按 RFC 7946 §4 规范缺省 WGS84",
            field="source_crs"))
        assumptions.append("source_crs=EPSG:4326（rfc7946_default 规范缺省，非猜测）")
    elif crs_source == "assumed":
        reasons.append(ContractReasonCode(
            code="SSC_CRS_ASSUMED",
            detail="CRS 为启发式假设 —— 不得作为度量/重投影依据",
            field="source_crs"))
    elif crs_source == "unknown" and not declared:
        if structure["out_of_envelope"]:
            reasons.append(ContractReasonCode(
                code="SSC_CRS_UNDECLARED_NON_DEGREE",
                detail="未声明 CRS 且坐标越 WGS84 经纬包络（投影样量级）"
                       "—— 禁止默认 4326，须先 declare_crs",
                field="source_crs"))
        elif evidence.feature_count > 0:
            reasons.append(ContractReasonCode(
                code="SSC_UNKNOWN_CRS",
                detail="未声明 CRS 且结构证据不足（非度样/无坐标采样）—— 不猜",
                field="source_crs"))

    # — 中国偏移帧身份（声明帧归一链事实；未声明风险在 validate 判）—
    datum_normalization: List[str] = []
    chinese_frame = normalize_chinese_crs(declared) if declared else None
    if crs_source == "declared" and chinese_frame in ("gcj02", "bd09"):
        # 归一链与 utils/coord_transform 的实际路径一致：
        # gcj02 → wgs84；bd09 → gcj02 → wgs84。
        datum_normalization.append(
            "gcj02→wgs84" if chinese_frame == "gcj02"
            else "bd09→gcj02→wgs84")
        reasons.append(ContractReasonCode(
            code="SSC_DATUM_NORMALIZED",
            detail=f"声明偏移帧 {chinese_frame} —— 分析前归一到 WGS84",
            field="datum_normalization"))

    # — CRS 分类（crs_safety 权威；GCJ-02/BD-09 是偏移**地理帧**，归
    # geographic —— 执行层归一 WGS84 后即标准地理数据，不是 unknown）—
    if crs_source == "declared" and chinese_frame in ("gcj02", "bd09"):
        classified = "geographic"
    else:
        classified = classify_crs(
            declared if crs_source == "declared" else
            ("EPSG:4326" if crs_source == "rfc7946_default" else ""))
    if (crs_source == "declared" and classified == "geographic"
            and structure["out_of_envelope"]):
        reasons.append(ContractReasonCode(
            code="SSC_COORD_OUT_OF_ENVELOPE",
            detail=f"声明地理 CRS（{declared}）但采样坐标越 ±180/±90 —— "
                   "CRS 声明与数据矛盾",
            field="source_crs"))

    # — 轴序 —
    axis_order, axis_evidence = _axis_order_guess(samples)
    if axis_order == "lat_lon":
        reasons.append(ContractReasonCode(
            code="SSC_AXIS_ORDER_SUSPECT", detail=axis_evidence, field="axis_order"))

    # — 几何类型（dominant/mixed）—
    kinds = evidence.geometry_kinds
    real_kinds = {k: v for k, v in kinds.items() if k != "other"}
    if not real_kinds:
        geometry_type = "unknown"
    elif len(real_kinds) == 1:
        geometry_type = next(iter(real_kinds))
    else:
        geometry_type = "mixed"

    # — 经度约定 / 跨 AM（bbox + 采样坐标伪几何，#1737 口径由 longitude 权威判）—
    lon_sem: Dict[str, Any] = {}
    if evidence.bbox:
        pseudo_geom = (
            {"coordinates": [[x, y] for x, y in samples]} if samples else None)
        lon_sem = describe_longitude_semantics(evidence.bbox, pseudo_geom)
        if lon_sem.get("crosses_antimeridian"):
            reasons.append(ContractReasonCode(
                code="SSC_ANTIMERIDIAN_CROSSED",
                detail="数据跨 180° 经线（采样坐标跳变 >180° 证据）—— "
                       "选带按 rewrap 口径，几何级拆分由分析入口负责",
                field="data_bbox"))
    longitude_convention = str(lon_sem.get("convention") or "unknown")

    # — 采样占据跨度（R1 P1：resolve 消费，防近全球 bbox 被误判单带）—
    span_occupied, center_hint = _occupied_lon_span_center(samples)

    # — 精度 / 尺度 —
    decimals = _decimal_places(samples) if samples else 0
    precision = _precision_from_decimals(decimals) if (
        samples and classified == "geographic") else "unknown"
    span_km = _bbox_span_km(evidence.bbox or [], classified) if evidence.bbox else None
    scale_level = _scale_level(span_km)

    # — AOI × 数据重叠（同帧才可比；跨帧判 unknown，帧矛盾由 validate 另判）—
    scope_out = scope or ScopeEvidence()
    overlap = _scope_overlap_comparable(
        scope_out, declared, crs_source, evidence.bbox,
        getattr(evidence, "scan_truncated", False))

    return SpatialSemanticContract(
        dataset_ref=evidence.ref,
        dataset_fingerprint=evidence.fingerprint,
        source_crs=declared if crs_source != "rfc7946_default" else "EPSG:4326",
        crs_source=crs_source,
        crs_class=classified,
        axis_order=axis_order,
        axis_order_evidence=axis_evidence[:120],
        geometry_type=geometry_type,
        feature_count=evidence.feature_count,
        data_bbox=evidence.bbox,
        longitude_convention=longitude_convention,
        lon_span_occupied=span_occupied,
        lon_center_hint=center_hint,
        requested_scope=ScopeEvidence(
            name=scope_out.name, level=scope_out.level, bbox=scope_out.bbox,
            bbox_crs=scope_out.bbox_crs, source=scope_out.source,
            overlap_with_data=overlap,
        ),
        spatial_precision=precision,
        scale_level=scale_level,
        datum_normalization=datum_normalization,
        assumptions=assumptions,
        reason_codes=reasons,
    )


def _scope_overlap_comparable(
    scope: ScopeEvidence, data_crs: str, crs_source: str,
    data_bbox: Optional[List[float]], scan_truncated: bool = False,
) -> str:
    """AOI × 数据重叠 —— 仅在同帧（或等价地理帧）下判定。

    跨 CRS 帧的 bbox 不可直接比大小（TEST_MATRIX S11：AOI 4326 vs 数据
    3857 的米 bbox 会假性 disjoint）—— 帧不可比 → unknown，CRS 一致性
    矛盾由 validate 的 SSC_SCOPE_DATA_CRS_MISMATCH 通道处理。
    """
    scope_frame = (scope.bbox_crs or "").strip().upper()
    data_frame = (data_crs or "").strip().upper()
    geo_equivalence = {"EPSG:4326", "CRS84", "OGC:CRS84", "WGS84"}
    comparable = (
        scope_frame == data_frame
        or (scope_frame in geo_equivalence and data_frame in geo_equivalence)
        or (scope_frame in geo_equivalence and crs_source == "rfc7946_default"))
    if not comparable:
        return "unknown"
    overlap = _scope_overlap(scope, data_bbox)
    if overlap == "disjoint" and scan_truncated:
        # R1 P2：bbox 是前缀采样口径 —— 未扫到的尾部要素可能落在 AOI 内，
        # disjoint（blocking）不可凭部分证据下结论。
        return "unknown"
    return overlap


def _scope_overlap(
    scope: ScopeEvidence, data_bbox: Optional[List[float]],
) -> str:
    """AOI bbox × 数据 bbox 重叠态（无几何证据 → unknown；判词表）。"""
    sb = scope.bbox
    if not sb or len(sb) != 4:
        return "empty" if (scope.name or scope.level) else "unknown"
    if not data_bbox or len(data_bbox) != 4:
        return "unknown"
    try:
        sminx, sminy, smaxx, smaxy = (float(v) for v in sb[:4])
        dminx, dminy, dmaxx, dmaxy = (float(v) for v in data_bbox[:4])
    except (TypeError, ValueError):
        return "unknown"
    if sminx > smaxx or sminy > smaxy:
        return "empty"
    # 同 CRS 前提下判重叠（AOI bbox_crs 与数据 CRS 一致性由 validate 另判）。
    if dminx >= sminx and dmaxx <= smaxx and dminy >= sminy and dmaxy <= smaxy:
        return "contains"
    if sminx > dmaxx or smaxx < dminx or sminy > dmaxy or smaxy < dminy:
        return "disjoint"
    return "intersects"


def evidence_for_china_offset_risk(contract: SpatialSemanticContract) -> bool:
    """未声明帧的中国偏移风险证据（validate 消费；确定性谓词）。

    风险仅在坐标帧**未显式声明**时成立（unknown/rfc7946_default/assumed）：
    显式声明 gcj02/bd09（已带归一链）或 wgs84 的数据不猜偏移。
    """
    if contract.datum_normalization:
        return False
    if contract.crs_source == "declared":
        return False
    bbox = contract.data_bbox
    if _china_aoi(bbox):
        return True
    return _china_aoi(contract.requested_scope.bbox)


__all__ = [
    "DatasetSpatialEvidence",
    "evidence_from_feature_collection",
    "inspect_spatial_contract",
    "evidence_for_china_offset_risk",
    "_MAX_SAMPLES",
    "_MAX_FEATURES_SCAN",
]
