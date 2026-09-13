"""制图前置质量门禁与数据剖析（ADR-0153，adaptive-cartography/04）。

把 ``SpatialQualityEngine.audit_dataset`` 的诊断、CRS 自动识别（P3）、
离群/值域剖析（P4）、修复编排计划（P2 的 ``plan_repair_ops``）与
Spatial Profile 契约扩展（P7）组装成**一次有界评估**，供 MapSpec
lifecycle 的 pre-commit 段挂载（P1）。

红线：
- **只评估不修复**：本模块绝不改写调用方的数据；修复执行走
  ``SpatialRepairPipeline``（非破坏 deepcopy + 证据）。
- **有界**：剖析采样有帽（sample_cap / field_cap / advisory_cap），证据
  全部 ≤16 条级别，绝不把全量数据倒进结论。
- **诚实**：推断不出 CRS → ``low_confidence`` 证据 + advisory，绝不静默
  假设 4326；无法修复的 blocking 码如实列出（拦得住、修不了的缺口格
  由修复建议的 ``skipped_ops`` 披露）。
- **重算离事件循环**：调用方（lifecycle hook）必须经 ``asyncio.to_thread``
  调用本模块（GeoJSON 剖析是 CPU 重活；仓库红线）。
"""
from __future__ import annotations

import logging
import math
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 有界剖析帽（证据是事实投影，不是数据转储）。
_SAMPLE_CAP = 2000          # CRS 推断采样的要素数上限
_FIELD_CAP = 8              # 离群剖析的数值字段上限
_ADVISORY_CAP = 16          # quality_advisories 上限（与修复证据同约定）
_MAX_FEATURES_DEFAULT = 5000  # 门禁审计的要素数上限（对齐 inline 载体 #687 门）

_WEB_MERCATOR_LIMIT = 20037508.342789244  # EPSG:3857 包络（±20037508.34 m）
_UTM_EASTING_RANGE = (100_000.0, 900_000.0)
_UTM_NORTHING_RANGE = (0.0, 10_000_000.0)
_GK_ZONE_PREFIXED_MIN = 16_000_000.0     # CGCS2000 3° 带号前推 easting（如 42_500_000）

# 中国经纬度范围（CGCS2000 候选判据；坐标量级与 4326 完全同域 —— 元数据级
# 区分必须依赖名称提示，纯数值推断只给候选注记，绝不拍板）。
_CHINA_LON_RANGE = (73.0, 135.5)
_CHINA_LAT_RANGE = (3.0, 54.0)

_BLOCKING_AUDIT_CODES = frozenset({
    "INVALID_FEATURE_FORMAT",
    "EMPTY_GEOMETRY",       # 仅 null/缺失几何分支为 blocking（audit 内部定级）
    "INVALID_GEOMETRY_SYNTAX",
    "SELF_INTERSECTION",    # 面自交 / Nested shells 分支
    "INVALID_GEOMETRY",     # Nested shells 分支
    "EXTREME_COORDINATES",
    "IMPOSSIBLE_LAT_LON",
})


# ─────────────────────────────────────────────────────────────────────────────
# P3：CRS 自动识别（4326 / 3857 / CGCS2000 / UTM 带号）
# ─────────────────────────────────────────────────────────────────────────────
def _sample_feature_bounds(geojson_data: Dict[str, Any]) -> Tuple[List[List[float]], int]:
    """有界采样：前 _SAMPLE_CAP 个可解析要素的 bounds 列表 + 总要素数。"""
    features: List[Any] = []
    if isinstance(geojson_data, dict):
        if geojson_data.get("type") == "FeatureCollection":
            features = geojson_data.get("features") or []
        elif geojson_data.get("type") == "Feature":
            features = [geojson_data]
        elif isinstance(geojson_data.get("features"), list):
            features = geojson_data["features"]
    total = len(features)
    bounds_list: List[List[float]] = []
    from shapely.geometry import shape as _shape

    for feat in features[:_SAMPLE_CAP]:
        if not isinstance(feat, dict):
            continue
        geom_raw = feat.get("geometry")
        if not isinstance(geom_raw, dict):
            continue
        try:
            geom = _shape(geom_raw)
        except Exception:  # noqa: BLE001 — 采样剖析跳过坏几何（audit 会如实报）
            continue
        if geom.is_empty:
            continue
        try:
            minx, miny, maxx, maxy = geom.bounds
        except Exception:  # noqa: BLE001
            continue
        if any(math.isnan(c) or math.isinf(c) for c in (minx, miny, maxx, maxy)):
            continue
        bounds_list.append([minx, miny, maxx, maxy])
    return bounds_list, total


def _bbox_of(bounds_list: List[List[float]]) -> Optional[List[float]]:
    if not bounds_list:
        return None
    return [
        min(b[0] for b in bounds_list),
        min(b[1] for b in bounds_list),
        max(b[2] for b in bounds_list),
        max(b[3] for b in bounds_list),
    ]


def _parse_declared_crs(geojson_data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(geojson_data, dict):
        return None
    crs_member = geojson_data.get("crs")
    if isinstance(crs_member, dict):
        props = crs_member.get("properties") or {}
        name = props.get("name") or props.get("code")
        if name:
            return str(name)
    return None


def _is_geographic_crs(crs: Optional[str]) -> Optional[bool]:
    """CRS 数据库口径的地理/投影判定；无法解析 → None（绝不猜）。"""
    if not crs:
        return None
    try:
        import pyproj
    except ImportError:
        return str(crs).upper() in {"EPSG:4326", "WGS84", "CRS84", "EPSG:4490"}
    try:
        return bool(pyproj.CRS.from_user_input(str(crs)).is_geographic)
    except Exception:  # noqa: BLE001 — 未知/速记串
        return None


def _coord_shape_kind(bbox: List[float]) -> str:
    """bbox 数值形态分类（degree / web_mercator / utm_or_gk / gk_zone_prefixed / extreme）。

    判定顺序即歧义消解顺序：度域 → GK 带号前推（eastings >16×10⁶ 只可能是
    CGCS2000 带号法）→ UTM/GK easting 窗（x∈[100k,900k] ∧ y∈[0,10M]）→
    Web Mercator 包络 → 未知米制。UTM 窗与 Web Mercator 在 |lon|<8° 的小
    范围数据上数值重叠 —— 这类歧义交由 lon_hint 锚点消解，无锚点时按
    utm_or_gk 走 low_confidence（宁可低置信不冒认）。
    """
    minx, miny, maxx, maxy = bbox
    span_x = max(abs(minx), abs(maxx))
    span_y = max(abs(miny), abs(maxy))
    if span_x > 1e10 or span_y > 1e10:
        return "extreme"
    if span_x <= 180.0 and span_y <= 90.0:
        return "degree"
    if (
        minx >= _GK_ZONE_PREFIXED_MIN
        and maxx <= 50_000_000.0
        and _UTM_NORTHING_RANGE[0] <= miny
        and maxy <= _UTM_NORTHING_RANGE[1]
    ):
        return "gk_zone_prefixed"
    if (
        _UTM_EASTING_RANGE[0] <= minx
        and maxx <= _UTM_EASTING_RANGE[1]
        and _UTM_NORTHING_RANGE[0] <= miny
        and maxy <= _UTM_NORTHING_RANGE[1]
    ):
        return "utm_or_gk"
    if span_x <= _WEB_MERCATOR_LIMIT and span_y <= _WEB_MERCATOR_LIMIT:
        return "web_mercator"
    return "unknown_metric"


def _resolve_projected_zone(
    shape_kind: str,
    lon_hint: Optional[float],
    lat_hint: Optional[float],
    crs_name_hint: str,
) -> Tuple[Optional[str], str, str]:
    """GK/UTM 形态坐标的带号消解（确定性；返回 (epsg, method, note)）。

    优先级：
    1. 显式名称提示含 UTM/WGS84 → UTM 带（即使 lon 落在中国域 —— 显式
       提示压过地理先验）；
    2. 带号前推 easting（>16×10⁶）→ CGCS2000 带（中国独有惯例；中国 lon
       锚点下 medium，无锚点 low）；
    3. 中国经度域 lon_hint → CGCS2000 3° 带（国家基准先验；note 披露 UTM
       候选），显式 CGCS/2000 提示时同此路径；
    4. 非中国 lon_hint → UTM 带；
    5. 无锚点 → (None, low)（绝不猜带号）。
    """
    hint = (crs_name_hint or "").upper()
    in_china = lon_hint is not None and _CHINA_LON_RANGE[0] <= lon_hint <= _CHINA_LON_RANGE[1]

    if "UTM" in hint or "WGS84" in hint or "WGS 84" in hint:
        epsg = _utm_zone_epsg(lon_hint, lat_hint) if lon_hint is not None else None
        if epsg:
            return epsg, "utm_zone_by_hint", "explicit UTM/WGS hint overrides geographic prior"
    if shape_kind == "gk_zone_prefixed":
        if in_china:
            epsg = _cgcs2000_zone_epsg(lon_hint or 0.0)
            if epsg:
                return epsg, "gk_zone_prefixed_cgcs2000_zone", \
                    "zone-prefixed GK eastings (China-only convention) + China lon hint"
        return None, "gk_zone_prefixed_indeterminate", \
            "zone-prefixed GK eastings but no China lon hint; zone indeterminate"
    if in_china:
        epsg = _cgcs2000_zone_epsg(lon_hint or 0.0)
        if epsg:
            return epsg, "gk_shape_cgcs2000_zone", \
                "GK-shaped coords + China lon hint -> CGCS2000 3-degree zone (UTM candidate disclosed)"
        return None, "gk_shape_china_indeterminate", "China lon hint outside defined CGCS2000 zones"
    if lon_hint is not None:
        epsg = _utm_zone_epsg(lon_hint, lat_hint)
        if epsg:
            return epsg, "utm_shape_zone", "GK/UTM-shaped coords + non-China lon hint -> UTM zone"
    return None, "gk_utm_indeterminate", \
        "UTM/GK-shaped coords but no geographic anchor (lon_hint); zone indeterminate"


def _cgcs2000_zone_epsg(lon_hint: float) -> Optional[str]:
    """lon_hint → CGCS2000 3° 带的 EPSG 代码。

    3° 带号 n（中央经线 = 3n）覆盖中国 25..45 → EPSG 4513..4533（带号前推
    easting）。CM 表示法（easting ≈ 5×10⁵，EPSG 4534..4554）数值不可分 ——
    缺省统一返回带号法代码并在调用侧注记候选（见 ac-04-decisions §6）。
    """
    n = int(round(lon_hint / 3.0))
    if not (25 <= n <= 45):
        return None
    return f"EPSG:{4513 + (n - 25)}"


def _utm_zone_epsg(lon_hint: float, lat_hint: Optional[float] = None) -> Optional[str]:
    zone = int((math.floor((lon_hint + 180.0) / 6.0)) % 60) + 1
    south = lat_hint is not None and lat_hint < 0
    return f"EPSG:{'327' if south else '326'}{zone:02d}"


def infer_crs(
    geojson_data: Dict[str, Any],
    *,
    declared_crs: Optional[str] = None,
    crs_name_hint: str = "",
    lon_hint: Optional[float] = None,
    lat_hint: Optional[float] = None,
) -> Dict[str, Any]:
    """按 bbox 量级 + 坐标范围 + 常见投影特征推断 CRS（任务书 §2 P3）。

    返回（有界 dict，契约字段稳定）::
        {crs, confidence: high|medium|low, low_confidence: bool,
         method: <机器可读证据码>, evidence: {bbox, coord_shape, sampled_features, note}}

    规则（确定性，同输入同输出）：
    1. 显式声明且可解析 → 声明优先；声明为地理 CRS 而坐标超度域 ⇒ 正矛盾，
       转入投影候选推断（SUSPICIOUS_CRS / IMPOSSIBLE_LAT_LON 的根因）。
    2. 度域内 → EPSG:4326（high）；bbox 落在中国范围 → 附 cgcs2000_candidate
       注记（坐标量级 4326/4490 完全同域，元数据区分需名称提示）。
    3. Web Mercator 包络内的米制量级 → EPSG:3857（medium）。
    4. UTM/GK 形态（easting/northing 量级）→ 有经度锚点时给带号：
       中国经度域 → CGCS2000 3° 带（EPSG 4513 段），否则 UTM（EPSG 326/327
       段）；无锚点 → low + low_confidence（绝不猜带号）。
    5. 退化/极端（全 (0,0)、>1e10、NaN）→ crs=None + low_confidence。
    """
    bounds_list, total = _sample_feature_bounds(geojson_data)
    bbox = _bbox_of(bounds_list)
    declared = declared_crs or _parse_declared_crs(geojson_data)
    hint = str(crs_name_hint or "").upper()

    evidence: Dict[str, Any] = {
        "sampled_features": len(bounds_list),
        "total_features": total,
    }
    if bbox is None:
        return {
            "crs": None,
            "confidence": "low",
            "low_confidence": True,
            "method": "no_parseable_geometry",
            "evidence": evidence,
        }
    evidence["bbox"] = [round(c, 6) for c in bbox]
    shape_kind = _coord_shape_kind(bbox)
    evidence["coord_shape"] = shape_kind

    # 退化 bbox 且整体压在原点：Null Island 缺失值填充形态 —— CRS 判定
    # 没有证据，绝不给 high（audit 的 NULL_ISLAND 警告与本推断互为印证）。
    if all(abs(c) < 1e-6 for c in bbox):
        return {
            "crs": None,
            "confidence": "low",
            "low_confidence": True,
            "method": "null_island_degenerate",
            "evidence": evidence,
        }

    def _result(crs: Optional[str], confidence: str, method: str, note: str = "") -> Dict[str, Any]:
        if note:
            evidence["note"] = note
        return {
            "crs": crs,
            "confidence": confidence,
            "low_confidence": confidence == "low",
            "method": method,
            "evidence": evidence,
        }

    # 规则 1：显式声明优先。
    if declared:
        declared_geo = _is_geographic_crs(declared)
        if declared_geo is True:
            if shape_kind == "degree":
                return _result(declared, "high", "declared_consistent")
            if shape_kind == "extreme":
                return _result(
                    None, "low", "declared_contradicted_extreme",
                    note=f"declared {declared} (geographic) contradicted by extreme coordinates",
                )
            # 正矛盾：声明地理而坐标是米制量级 —— 推断真实投影源。
            if shape_kind == "web_mercator":
                return _result(
                    "EPSG:3857", "medium", "declared_geographic_contradicted_web_mercator",
                    note=f"declared {declared} contradicted by metric coordinates in Web Mercator envelope",
                )
            if shape_kind in ("utm_or_gk", "gk_zone_prefixed"):
                epsg, method, note = _resolve_projected_zone(
                    shape_kind, lon_hint, lat_hint, crs_name_hint
                )
                if epsg:
                    return _result(
                        epsg, "medium", f"declared_contradicted_{method}",
                        note=f"declared {declared} contradicted; {note}",
                    )
                return _result(
                    None, "low", "declared_contradicted_gk_indeterminate",
                    note=f"declared {declared} contradicted by GK/UTM-shaped coords; "
                         "zone indeterminate without lon_hint",
                )
            return _result(declared, "medium", "declared_projected_unverified")
        if declared_geo is False:
            return _result(declared, "high", "declared_projected")
        # 声明串无法解析 → 按无声明继续（但保留痕迹）。
        evidence["note"] = f"declared CRS '{declared}' unparseable; falling back to numeric inference"

    # 规则 2..5：无有效声明的数值推断。
    if shape_kind == "extreme":
        return _result(None, "low", "extreme_coordinates",
                       note="coordinates beyond 1e10 or unparsable magnitude; CRS not inferable")
    if shape_kind == "degree":
        in_china = (
            _CHINA_LON_RANGE[0] <= bbox[0]
            and bbox[2] <= _CHINA_LON_RANGE[1]
            and _CHINA_LAT_RANGE[0] <= bbox[1]
            and bbox[3] <= _CHINA_LAT_RANGE[1]
        )
        if ("CGCS" in hint) or ("4490" in hint) or ("2000" in hint and "3857" not in hint):
            return _result("EPSG:4490", "high", "hint_cgcs2000_geographic")
        if in_china:
            return _result(
                "EPSG:4326", "high", "degree_range",
                note="bbox within China extent: EPSG:4490 (CGCS2000) is coordinate-wise "
                     "identical to EPSG:4326; pick 4490 only with an explicit metadata hint",
            )
        return _result("EPSG:4326", "high", "degree_range")
    if shape_kind == "web_mercator":
        return _result("EPSG:3857", "medium", "web_mercator_envelope")
    if shape_kind in ("gk_zone_prefixed", "utm_or_gk"):
        epsg, method, note = _resolve_projected_zone(
            shape_kind, lon_hint, lat_hint, crs_name_hint
        )
        if epsg:
            return _result(epsg, "medium", method, note=note)
        return _result(None, "low", method, note=note)
    return _result(None, "low", "unknown_coord_shape")


# ─────────────────────────────────────────────────────────────────────────────
# P4：离群值与值域剖析（只剖析不裁剪；裁剪由 03 线按 outlier_policy 执行）
# ─────────────────────────────────────────────────────────────────────────────
def profile_outlier_policy(values: List[float]) -> Dict[str, Any]:
    """单数值字段的离群/分布剖析 → ``outlier_policy`` 契约（确定性）。

    返回 {field_policy: none|clip_p99|head_tail|log, outlier_ratio, skew,
    zero_ratio, p50, p90, p99, min, max, suggested_clip, reason}。

    规则（任务书 §2 P4 的封闭词表）：
    - 样本 <8 或 zero_ratio>0.5 → none（证据不足 / 缺失值为主，不是分布问题）；
    - |skew|<1 且无 >3σ 点 → none；
    - skew≥2 且 min≥0 → log（正重尾；min==0 时建议 log1p，见 reason）；
    - 相异值 ≤12 且 max/min>100 → head_tail（序数长尾，类断点按首尾处理）；
    - 其余 |skew|≥1 或存在 >3σ 点 → clip_p99（suggested_clip=p99）。
    """
    out: Dict[str, Any] = {
        "field_policy": "none",
        "outlier_ratio": 0.0,
        "skew": 0.0,
        "zero_ratio": 0.0,
        "p50": None,
        "p90": None,
        "p99": None,
        "min": None,
        "max": None,
        "suggested_clip": None,
        "reason": "",
    }
    vals = [float(v) for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)
            and math.isfinite(float(v))]
    if not vals:
        out["reason"] = "no finite numeric values"
        return out
    n = len(vals)
    svals = sorted(vals)
    out["min"] = svals[0]
    out["max"] = svals[-1]

    def _pct(q: float) -> float:
        # 线性插值分位（numpy 'linear' 同法；不引入 numpy 依赖 —— 有界样本）
        pos = q * (n - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return svals[lo]
        frac = pos - lo
        return svals[lo] * (1.0 - frac) + svals[hi] * frac

    p50, p90, p99 = _pct(0.50), _pct(0.90), _pct(0.99)
    out["p50"], out["p90"], out["p99"] = p50, p90, p99
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    std = math.sqrt(var)
    zeros = sum(1 for v in vals if v == 0.0)
    out["zero_ratio"] = round(zeros / n, 6)

    # Pearson 偏度（std=0 → 0）
    if std > 1e-12:
        skew = sum(((v - mean) / std) ** 3 for v in vals) / n
    else:
        skew = 0.0
    out["skew"] = round(skew, 6)

    if std > 1e-12:
        outliers = [v for v in vals if abs(v - mean) / std > 3.0]
    else:
        outliers = []
    out["outlier_ratio"] = round(len(outliers) / n, 6)

    if n < 8:
        out["reason"] = f"insufficient samples (n={n})"
        return out
    if out["zero_ratio"] > 0.5:
        out["reason"] = f"zero-heavy column (zero_ratio={out['zero_ratio']}); treat nulls, not outliers"
        return out

    distinct = len(set(svals))
    integral = all(float(v).is_integer() for v in svals)
    if integral and distinct <= 12 and out["min"] >= 0 and out["max"] / max(out["min"], 1e-9) > 100:
        out["field_policy"] = "head_tail"
        out["reason"] = f"low-cardinality long tail (distinct={distinct}, max/min>100)"
        return out
    if abs(skew) < 1.0 and not outliers:
        out["reason"] = "symmetric distribution without >3sigma points"
        return out
    if outliers and abs(skew) >= 1.0:
        # >3σ 拉爆色带场景：建议值 = 剔除 >3σ 点后的 p99（对主体分布的
        # 稳健分位）—— 原始 p99 被极值本身吞掉时才是「明确可用」的建议。
        inliers = sorted(v for v in vals if abs(v - mean) / std <= 3.0)
        n_in = len(inliers)
        pos = 0.99 * (n_in - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        clip_value = inliers[lo] if lo == hi else (
            inliers[lo] * (1.0 - (pos - lo)) + inliers[hi] * (pos - lo)
        )
        out["field_policy"] = "clip_p99"
        out["suggested_clip"] = clip_value
        out["reason"] = (
            f">{3}sigma points present (outlier_ratio={out['outlier_ratio']}, "
            f"skew={out['skew']}); suggest clipping at p99 of inlier mass "
            f"({clip_value:.6g}) for color-band stability (line-03 executes)"
        )
        return out
    if skew >= 2.0 and out["min"] >= 0.0:
        out["field_policy"] = "log"
        out["reason"] = (
            "positive heavy tail (skew>=2, min>=0, no hard >3sigma outlier); suggest log1p transform "
            if out["min"] == 0.0 else
            "positive heavy tail (skew>=2, min>0, no hard >3sigma outlier); suggest log transform"
        )
        return out
    out["field_policy"] = "clip_p99"
    out["suggested_clip"] = p99
    out["reason"] = (
        f"heavy tail (skew={out['skew']}); "
        "suggest clipping at p99 for color-band stability (line-03 executes)"
    )
    return out


def profile_numeric_fields(
    features: List[Dict[str, Any]],
    *,
    field_cap: int = _FIELD_CAP,
) -> Dict[str, Dict[str, Any]]:
    """数据集级数值字段剖析（有界：取非空样本最多的前 field_cap 个字段）。"""
    values_by_field: Dict[str, List[float]] = {}
    for feat in features:
        if not isinstance(feat, dict):
            continue
        props = feat.get("properties")
        if not isinstance(props, dict):
            continue
        for k, v in props.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(float(v)):
                values_by_field.setdefault(str(k), []).append(float(v))
    ranked = sorted(values_by_field.items(), key=lambda kv: len(kv[1]), reverse=True)[:field_cap]
    return {k: profile_outlier_policy(v) for k, v in ranked}


# ─────────────────────────────────────────────────────────────────────────────
# P1/P7：门禁评估 + Spatial Profile 契约扩展
# ─────────────────────────────────────────────────────────────────────────────
def default_quality_profile() -> Dict[str, Any]:
    """P7 契约的默认值兜底 —— 02/03 线在本线合入前即可按此消费。"""
    return {
        "geometry_mix": {"types": {}, "dominant": None, "mix_ratio": 0.0},
        "n_valid": None,
        "extent": None,
        "crs_confidence": {
            "crs": None,
            "confidence": "unknown",
            "low_confidence": False,
            "method": "not_evaluated",
        },
        "outlier_policy": {},
        "quality_advisories": [],
    }


def _geometry_mix(features: List[Dict[str, Any]]) -> Dict[str, Any]:
    types: Dict[str, int] = {}
    for feat in features:
        if not isinstance(feat, dict):
            continue
        g = feat.get("geometry")
        if not isinstance(g, dict):
            continue
        t = str(g.get("type", ""))
        if t:
            types[t] = types.get(t, 0) + 1
    if not types:
        return {"types": {}, "dominant": None, "mix_ratio": 0.0}
    dominant = max(types.items(), key=lambda kv: kv[1])[0]
    total = sum(types.values())
    minority = total - types[dominant]
    return {
        "types": types,
        "dominant": dominant,
        "mix_ratio": round(minority / total, 6),
    }


def _extent_of(features: List[Dict[str, Any]]) -> Optional[List[float]]:
    from shapely.geometry import shape as _shape

    minx = miny = maxx = maxy = None
    seen = 0
    for feat in features[:_SAMPLE_CAP]:
        if not isinstance(feat, dict):
            continue
        g = feat.get("geometry")
        if not isinstance(g, dict):
            continue
        try:
            geom = _shape(g)
        except Exception:  # noqa: BLE001
            continue
        if geom.is_empty:
            continue
        b = geom.bounds
        if any(math.isnan(c) or math.isinf(c) for c in b):
            continue
        minx = b[0] if minx is None else min(minx, b[0])
        miny = b[1] if miny is None else min(miny, b[1])
        maxx = b[2] if maxx is None else max(maxx, b[2])
        maxy = b[3] if maxy is None else max(maxy, b[3])
        seen += 1
    if not seen:
        return None
    return [minx, miny, maxx, maxy]


def build_quality_advisories(
    report: Any,
    *,
    crs_inference: Optional[Dict[str, Any]] = None,
    outlier_fields: Optional[Dict[str, Dict[str, Any]]] = None,
    extra: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """audit 报告 + 剖析 → 有界 quality_advisories（≤16 条，07/09 线消费）。"""
    advisories: List[Dict[str, Any]] = []
    issues = list(getattr(report, "issues", None) or [])
    for issue in issues:
        if issue.level == "info":
            continue
        advisories.append({
            "code": str(issue.code)[:64],
            "level": str(issue.level)[:16],
            "message": str(issue.message)[:200],
            "feature_index": issue.feature_index if issue.feature_index is not None else None,
        })
        if len(advisories) >= _ADVISORY_CAP:
            break
    if crs_inference and crs_inference.get("method") not in (None, "not_evaluated"):
        advisories.append({
            "code": "CRS_CONFIDENCE",
            "level": "info" if not crs_inference.get("low_confidence") else "warning",
            "message": (
                f"CRS 判定：{crs_inference.get('crs') or '未知'}"
                f"（confidence={crs_inference.get('confidence')}, method={crs_inference.get('method')}）"
            )[:200],
        })
    for fname, prof in list((outlier_fields or {}).items())[:4]:
        if prof.get("field_policy") and prof["field_policy"] != "none":
            advisories.append({
                "code": "OUTLIER_POLICY",
                "level": "info",
                "message": (
                    f"字段 {fname} 建议 outlier_policy={prof['field_policy']}"
                    + (f"（clip@p99={prof.get('suggested_clip')}）" if prof.get("suggested_clip") is not None else "")
                )[:200],
            })
    for item in extra or []:
        if len(advisories) >= _ADVISORY_CAP - 1:
            # 截断标记必须留在 ≤16 的预算内：预留最后一位给标记本身。
            advisories.append({
                "code": "ADVISORIES_TRUNCATED",
                "level": "info",
                "message": "advisory budget exhausted",
            })
            break
        advisories.append(item)
    return advisories[:_ADVISORY_CAP]


def evaluate_quality_gate(
    geojson_data: Any,
    *,
    declared_crs: Optional[str] = None,
    dataset_id: str = "",
    max_features: int = _MAX_FEATURES_DEFAULT,
) -> Dict[str, Any]:
    """一次有门禁判定（纯同步 CPU —— 调用方必须放线程池）。

    返回（封闭键集）::
        {dataset_id, verdict: pass|warn|block, mode, blocking_codes,
         error_codes, issue_summary, advisories, repair_plan,
         crs_inference, outlier_fields, null_heavy_fields,
         profile_extension, audit_truncated, feature_count}

    ``verdict`` 只反映数据本身；是否拒绝提交由 lifecycle hook 按 settings
    的门禁模式（enforce/advisory/off）裁决。
    """
    from app.services.spatial_quality_service import SpatialQualityEngine
    from app.services.spatial_repair_pipeline import plan_repair_ops

    base = default_quality_profile()
    result: Dict[str, Any] = {
        "dataset_id": str(dataset_id)[:80],
        "verdict": "pass",
        "mode": "enforce",
        "blocking_codes": [],
        "error_codes": [],
        "issue_summary": {},
        "advisories": [],
        "repair_plan": None,
        "crs_inference": base["crs_confidence"],
        "outlier_fields": {},
        "null_heavy_fields": [],
        "profile_extension": dict(base),
        "audit_truncated": False,
        "feature_count": 0,
    }
    if not isinstance(geojson_data, dict):
        # 非矢量载荷（raster / url 字符串 / None）：门禁不适用，如实说明。
        result["profile_extension"]["crs_confidence"]["method"] = "not_vector_payload"
        return result

    features: List[Any] = []
    if geojson_data.get("type") == "FeatureCollection":
        features = geojson_data.get("features") or []
    elif geojson_data.get("type") == "Feature":
        features = [geojson_data]
    elif isinstance(geojson_data.get("features"), list):
        features = geojson_data["features"]
    result["feature_count"] = len(features)

    if not features:
        result["verdict"] = "warn"
        result["advisories"] = [{
            "code": "EMPTY_FEATURE_COLLECTION",
            "level": "warning",
            "message": "数据集没有可上图要素：上图产生空图层。",
        }]
        result["profile_extension"]["quality_advisories"] = result["advisories"]
        return result

    if len(features) > max_features:
        # 门禁审计有界：超帽**立即返回** advisory，绝不逐要素审计/剖析全量
        # 载荷（audit + outlier 剖析是 O(n) CPU 重活 —— 超帽还硬跑等于把
        # 门禁变成 UpsertSourceIntent 上的 DoS 面）。审计与 CRS/离群推断
        # 全部让位给 ingest/Celery 路径；本判定只是诚实披露 + 有界放行：
        # verdict=warn（未审计不得谎称 pass）、P7 契约六键全在（值取
        # default_quality_profile 的「未评估」兜底，crs_confidence.method
        # 如实标注 skipped_over_budget），下游 hook/profile 合并逻辑零改动。
        result["audit_truncated"] = True
        result["verdict"] = "warn"
        over_budget_advisory = {
            "code": "QUALITY_AUDIT_SKIPPED_OVER_BUDGET",
            "level": "warning",
            "message": (
                f"{len(features)} features exceed gate audit budget ({max_features}); "
                "full audit should run on the ingest/Celery path"
            ),
        }
        result["advisories"] = [over_budget_advisory]
        result["profile_extension"]["crs_confidence"]["method"] = "skipped_over_budget"
        result["profile_extension"]["quality_advisories"] = [over_budget_advisory]
        return result

    effective_crs = declared_crs or _parse_declared_crs(geojson_data)
    report = SpatialQualityEngine.audit_dataset(
        geojson_data, crs=effective_crs, dataset_id=dataset_id or None
    )
    result["issue_summary"] = dict(report.issue_summary)
    result["audit_truncated"] = result["audit_truncated"] or bool(report.truncated)

    seen_blocking: set = set()
    seen_error: set = set()
    for issue in report.issues:
        if issue.level == "blocking":
            seen_blocking.add(issue.code)
        elif issue.level == "error":
            seen_error.add(issue.code)
    result["blocking_codes"] = sorted(seen_blocking)
    result["error_codes"] = sorted(seen_error)

    # P3 CRS 推断（声明优先；矛盾时给投影候选）。
    crs_inf = infer_crs(geojson_data, declared_crs=declared_crs)
    result["crs_inference"] = {
        "crs": crs_inf.get("crs"),
        "confidence": crs_inf.get("confidence"),
        "low_confidence": bool(crs_inf.get("low_confidence")),
        "method": crs_inf.get("method"),
    }

    # P4 离群剖析（只剖析不裁剪）。
    outlier_fields = profile_numeric_fields(features)
    result["outlier_fields"] = {
        k: {
            "field_policy": p.get("field_policy"),
            "outlier_ratio": p.get("outlier_ratio"),
            "skew": p.get("skew"),
            "zero_ratio": p.get("zero_ratio"),
            "p99": p.get("p99"),
            "suggested_clip": p.get("suggested_clip"),
        }
        for k, p in outlier_fields.items()
    }
    null_heavy_fields = sorted({
        str(getattr(i, "details", {}).get("attribute", ""))
        for i in report.issues
        if i.code == "HIGH_NULL_RATIO" and getattr(i, "details", {}).get("attribute")
    })

    # 裁决输入比率（有界近似：由 issue 计数 / 要素数推导）。
    total = max(report.total_features, 1)
    dup_count = sum(1 for i in report.issues if i.code in ("DUPLICATE_GEOMETRY", "DUPLICATE_FEATURE"))
    overlap_count = sum(1 for i in report.issues if i.code == "TOPOLOGY_OVERLAP")
    mix = _geometry_mix(features)

    repair_plan = plan_repair_ops(
        report,
        total_features=report.total_features,
        duplicate_ratio=dup_count / total,
        geometry_mix_ratio=mix["mix_ratio"],
        attribute_type_mix_ratio=sum(1 for i in report.issues if i.code == "TYPE_INCONSISTENCY") / total,
        overlap_pair_ratio=overlap_count / total,
        outlier_fields=[
            {"field": k, **{kk: vv for kk, vv in p.items() if kk in ("field_policy", "outlier_ratio", "suggested_clip")}}
            for k, p in outlier_fields.items()
            if p.get("field_policy") not in (None, "none")
        ],
        null_heavy_fields=null_heavy_fields,
        crs_inference=crs_inf,
        declared_crs=effective_crs if _is_geographic_crs(effective_crs) is False else None,
    )
    result["repair_plan"] = repair_plan.to_bounded_dict()

    if seen_blocking:
        result["verdict"] = "block"
    elif seen_error or report.overall_status != "passed":
        # error 级（拓扑重叠 / 主键重复 / 环检查失败）与 warning 级同走
        # advisory：携带修复计划放行，由裁决/auto-repair 闭环 —— 拦截面
        # 与 audit 自身的 blocking 分级严格一致（14 码验收口径：blocking
        # 类 100% 拦截或自动修复；error/warning 不静默，advisory 必达）。
        result["verdict"] = "warn"

    # P7 profile 契约扩展（02/03 线消费；默认值兜底见 default_quality_profile）。
    n_valid = sum(
        1
        for f in features[:_SAMPLE_CAP]
        if isinstance(f, dict)
        and isinstance(f.get("geometry"), dict)
        and f["geometry"].get("type")
    )
    profile_extension = {
        "geometry_mix": mix,
        "n_valid": n_valid,
        "extent": _extent_of(features),
        "crs_confidence": dict(result["crs_inference"]),
        "outlier_policy": dict(result["outlier_fields"]),
        "quality_advisories": [],
    }
    profile_extension["quality_advisories"] = build_quality_advisories(
        report, crs_inference=result["crs_inference"], outlier_fields=outlier_fields
    )
    result["profile_extension"] = profile_extension
    result["advisories"] = profile_extension["quality_advisories"]
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 审计事件（逃生舱使用必须留痕；失败绝不影响主流程）
# ─────────────────────────────────────────────────────────────────────────────
_gate_metrics: Dict[str, Any] = {}


def record_gate_event(
    event: str,
    *,
    verdict: str = "",
    mode: str = "",
    dataset_id: str = "",
    codes: Optional[List[str]] = None,
) -> None:
    """门禁审计事件：结构化日志 + Prometheus 计数（best-effort，双写 fail-open）。"""
    codes = [str(c)[:64] for c in (codes or [])][:8]
    logger.info(
        "[quality-gate] event=%s verdict=%s mode=%s dataset=%s codes=%s",
        event, verdict, mode, dataset_id, ",".join(codes) if codes else "-",
    )
    try:
        from prometheus_client import Counter

        if "counter" not in _gate_metrics:
            _gate_metrics["counter"] = Counter(
                "mapspec_quality_gate_events_total",
                "MapSpec pre-commit quality gate events (ADR-0153)",
                ["event", "verdict", "mode"],
            )
        _gate_metrics["counter"].labels(
            event=event, verdict=verdict or "-", mode=mode or "-",
        ).inc()
    except Exception:  # noqa: BLE001 — 指标注册失败不影响门禁判定
        pass


__all__ = [
    "default_quality_profile",
    "infer_crs",
    "profile_outlier_policy",
    "profile_numeric_fields",
    "build_quality_advisories",
    "evaluate_quality_gate",
    "record_gate_event",
    "BLOCKING_AUDIT_CODES",
    "_MAX_FEATURES_DEFAULT",
]

#: ``_BLOCKING_AUDIT_CODES`` 的公开别名（文档口径：audit 内部 blocking 级码）。
BLOCKING_AUDIT_CODES = _BLOCKING_AUDIT_CODES
