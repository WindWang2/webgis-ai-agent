"""resolve_calculation_space —— 计算空间决策（GOAL 01 · M2）。

回答："这次计算应该发生在哪个 CRS / 用测地还是投影"——决策可解释
（每一步给 basis 证据）、确定性（同输入恒同输出）、复用单一权威：

- working_crs 推荐：``crs_safety.recommend_metric_crs``（UTM/UPS 纯计算）；
- 局部度量判定：``crs_safety.classify_crs``（含实测尺度失真门）；
- 大跨度/极区/跨 AM：与 aggregation._geodesic_area_if_wide 同口径
  （span>6° 或 |lat|>84 → 测地/极方位）。

模式选择（确定性规则，全部给 basis）：

1. 数据 crs_class=projected_local_metric → 原投影直接算（mode=
   projected_local，working_crs=source_crs）；
2. 地理 CRS：
   - 极区（中心 |lat|>84）→ UPS（3413 北 / 3031 南）；
   - 大跨度（经度跨度 >6°）→ 测地（距离）/ 测地面积（大洲级不该用单带）；
   - 否则 → recommend_metric_crs 的单带 UTM；
3. 非局部度量投影（3857 等）→ degraded：失真估计 1/cos(φ)−1，建议
   确定性安全变换到 UTM（决策 D6）；
4. unknown → unknown（不猜；validate 对度量类操作判 requires_clarification）。
"""
from __future__ import annotations

import math
from typing import Any, List, Optional, Sequence

from pydantic import BaseModel, Field

from app.services.gis_harness.spatial_semantics.contract import (
    ContractReasonCode,
    SpatialSemanticContract,
)

# 与 aggregation._SINGLE_ZONE_MAX_LON_SPAN 同口径（单 UTM 带可信跨度）。
_SINGLE_ZONE_MAX_LON_SPAN = 6.0
# 与 recommend_metric_crs 极区阈值同口径。
_POLAR_LAT = 84.0
_SOUTH_POLAR_LAT = -80.0


class CalculationSpace(BaseModel):
    """计算空间决策（typed、bounded、可序列化）。"""

    mode: str = "unknown"                     # CALCULATION_MODE_VOCABULARY
    working_crs: str = Field(default="", max_length=64)
    working_crs_basis: str = Field(default="", max_length=240)
    distortion_estimate: Optional[float] = None   # 工作 CRS 尺度失真估计（比例）
    antimeridian: bool = False
    suggested_transform: str = Field(default="", max_length=160)   # "EPSG:3857→EPSG:32648"
    reason_codes: List[ContractReasonCode] = Field(
        default_factory=list, max_length=8)


def _bbox_center_and_span(
    bbox: Optional[Sequence[float]],
) -> Optional[tuple]:
    if not bbox or len(bbox) != 4:
        return None
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(v) for v in (minx, miny, maxx, maxy)):
        return None
    # 经度跨度按环绕归一：raw 跨度 >180° 时，数据更可能是贴 ±180 的环绕
    # 簇（真实跨度 = 360 − raw），而非横跨 200°+ 的大洲（两者在 >6° 判定
    # 下同归测地/多带，中心只影响带号 —— ≤6° 跨度永不歧义）。
    raw_span = maxx - minx
    lon_span = raw_span % 360.0
    if lon_span > 180.0:
        lon_span = 360.0 - lon_span
        clon = (maxx + lon_span / 2.0 + 180.0) % 360.0 - 180.0
    else:
        clon = minx + lon_span / 2.0
    return (clon, (miny + maxy) / 2.0, lon_span, abs(maxy - miny))


def resolve_calculation_space(
    contract: SpatialSemanticContract,
    *,
    purpose: str = "distance",
) -> CalculationSpace:
    """契约 → 计算空间决策。

    ``purpose``: distance（buffer/近邻/带宽）| area（面积/密度分母）|
    topology（overlay/join，CRS 无关拓扑）—— 决定大跨度时选测地还是
    披露降级。未知 purpose 按 distance（最严口径）。
    """
    from app.lib.gis.crs_safety import recommend_metric_crs

    cls = contract.crs_class
    bbox = contract.data_bbox
    geo = _bbox_center_and_span(bbox)

    def _result(mode: str, crs: str, basis: str, **kw: Any) -> CalculationSpace:
        return CalculationSpace(mode=mode, working_crs=crs, working_crs_basis=basis, **kw)

    # — topology 目的：拓扑谓词与 CRS 无关（overlay/sjoin 走归一帧）—
    if purpose == "topology":
        return _result(
            "projected_local" if cls in ("projected", "projected_local_metric")
            else "geodesic",
            contract.source_crs if cls in ("projected", "projected_local_metric") else "",
            f"topology 目的 —— 谓词 CRS 无关（crs_class={cls}），"
            "归一帧下执行（geo_processor 工作帧）")

    # — 局部度量投影：直接用（含极方位 3413/3031）—
    if cls == "projected_local_metric":
        return _result(
            "projected_local", contract.source_crs,
            f"数据 CRS {contract.source_crs} 为局部度量投影"
            "（crs_safety 实测尺度失真门通过）—— 原投影直接计算")

    # — 非局部度量投影（3857/世界级）：degraded + 失真估计 + 安全变换建议 —
    if cls == "projected":
        distortion: Optional[float] = None
        rec_crs = ""
        if geo is not None:
            clon_x, clat_y, _span, _latspan = geo
            if contract.source_crs.strip().upper() in ("EPSG:3857", "3857",
                                                       "EPSG:900913"):
                # Web Mercator 平面中心 → 纬度用闭式反算（sphere 版，与
                # EPSG:3857 球体定义一致；确定性、零依赖）：
                # φ = atan(sinh(y/R))，R = 6378137。
                r = 6378137.0
                lat_deg = math.degrees(math.atan(math.sinh(clat_y / r)))
                distortion = (1.0 / math.cos(math.radians(
                    min(abs(lat_deg), 89.0))) - 1.0)
            # 推荐带用数据 bbox 中心（投影平面坐标不能直接喂地理 bbox 推荐器；
            # 有 pyproj 时反算，否则留空由修复建议兜底）。
            rec_crs = _recommend_from_projected(contract, geo)
        suggestion = (
            f"{contract.source_crs}→{rec_crs}" if rec_crs else "")
        reasons = [ContractReasonCode(
            code="SSC_WEB_MERCATOR_SCALE_DISTORTION",
            detail=(f"EPSG:3857 中心纬度失真 ≈{distortion * 100:.1f}%"
                    if distortion is not None
                    else f"{contract.source_crs} 为非局部度量投影"),
            field="working_crs")]
        return _result(
            "projected_local", contract.source_crs,
            f"数据 CRS {contract.source_crs} 非局部度量 —— 直接度量将带尺度失真；"
            f"建议确定性安全变换 {suggestion}" if suggestion
            else f"数据 CRS {contract.source_crs} 非局部度量（无 bbox，失真未估计）",
            distortion_estimate=distortion,
            suggested_transform=suggestion,
            reason_codes=reasons)

    # — 地理 CRS：极区 / 大跨度 / 单带 —
    if cls == "geographic":
        if geo is None:
            return _result(
                "unknown", "",
                "地理 CRS 但无 bbox —— 无法推荐计算投影（不猜）")
        clon, clat, lon_span, _latspan = geo
        # 跨 AM 环绕证据：#1737 bbox 判定 ∪ 契约侧采样跳变证据（inspect 已判）。
        antimeridian = _lon_crosses(bbox) or any(
            rc.code == "SSC_ANTIMERIDIAN_CROSSED" for rc in contract.reason_codes)
        if clat > _POLAR_LAT:
            return _result(
                "projected_polar", "EPSG:3413",
                f"中心纬度 {clat:.1f}°N > 84° —— 北极 UPS 极方位（与 "
                "recommend_metric_crs 同口径）",
                antimeridian=antimeridian,
                reason_codes=[ContractReasonCode(
                    code="SSC_POLAR_PROJECTION_SELECTED",
                    detail="|lat|>84° —— 极方位度量投影 EPSG:3413",
                    field="working_crs")])
        if clat < _SOUTH_POLAR_LAT:
            return _result(
                "projected_polar", "EPSG:3031",
                f"中心纬度 {clat:.1f}°S < -80° —— 南极极方位（与 "
                "recommend_metric_crs 同口径）",
                antimeridian=antimeridian,
                reason_codes=[ContractReasonCode(
                    code="SSC_POLAR_PROJECTION_SELECTED",
                    detail="|lat|<-80° —— 极方位度量投影 EPSG:3031",
                    field="working_crs")])
        if lon_span > _SINGLE_ZONE_MAX_LON_SPAN:
            reasons = [ContractReasonCode(
                code="SSC_MULTIZONE_SPAN",
                detail=f"经度跨度 {lon_span:.1f}° > 单 UTM 带可信跨度 6°",
                field="working_crs")]
            return _result(
                "geodesic", "WGS84",
                f"经度跨度 {lon_span:.1f}° 超单带 —— 椭球测地"
                f"（purpose={purpose}；与 aggregation._geodesic_area_if_wide 同口径）",
                antimeridian=antimeridian,
                reason_codes=reasons)
        # 选带用环绕归一后的中心（#709：跨 AM 数据不得按原始 min/max 中心
        # 选到格林尼治带）—— 传中心微 bbox 而非原始 bbox。
        rec = recommend_metric_crs([
            clon - 0.05, (float(bbox[1]) if bbox else clat) , clon + 0.05,
            (float(bbox[3]) if bbox else clat)])
        if not rec:
            return _result("unknown", "", "bbox 无法推荐 UTM 带 —— 不猜")
        return _result(
            "projected_local", rec,
            f"地理数据自动投影 {rec}（中心 {clon:.1f}°E/{clat:.1f}° 单带，"
            "recommend_metric_crs 纯计算）",
            antimeridian=antimeridian)

    # — unknown —
    return _result(
        "unknown", "",
        f"数据 crs_class=unknown（crs_source={contract.crs_source}）—— "
        "无 CRS 证据不选计算空间")


def _recommend_from_projected(
    contract: SpatialSemanticContract, geo: tuple,
) -> str:
    """投影数据 → 局部度量建议带。

    投影平面坐标不能直接喂地理 bbox 推荐器；用 pyproj 反算中心经纬度
    （确定性、本地），pyproj 缺席 → 空串（诚实缺省，建议层兜底）。
    """
    try:
        from pyproj import Transformer

        clon_x, clat_y = geo[0], geo[1]
        transformer = Transformer.from_crs(
            contract.source_crs, "EPSG:4326", always_xy=True)
        lon, lat = transformer.transform(clon_x, clat_y)
        from app.lib.gis.crs_safety import recommend_metric_crs

        return recommend_metric_crs([lon - 0.05, lat - 0.05, lon + 0.05, lat + 0.05])
    except Exception:  # noqa: BLE001 — 不可推荐 → 空串由调用方兜底
        return ""


def _lon_crosses(bbox: Optional[Sequence[float]]) -> bool:
    from app.lib.gis.longitude import bbox_crosses_antimeridian

    if not bbox or len(bbox) != 4:
        return False
    try:
        minx, miny, maxx, maxy = (float(v) for v in bbox[:4])
    except (TypeError, ValueError):
        return False
    return bool(bbox_crosses_antimeridian(minx, miny, maxx, maxy))


def apply_calculation_space(
    contract: SpatialSemanticContract,
    space: CalculationSpace,
) -> SpatialSemanticContract:
    """把计算空间决策写回契约（copy-on-write；reason_codes 有界合并）。"""
    merged = list(contract.reason_codes)
    for rc in space.reason_codes:
        if len(merged) >= 12:
            break
        if not any(r.code == rc.code and r.field == rc.field for r in merged):
            merged.append(rc)
    return contract.model_copy(update={
        "calculation_mode": space.mode,
        "working_crs": space.working_crs,
        "working_crs_basis": space.working_crs_basis[:200],
        "reason_codes": merged,
    })


__all__ = [
    "CalculationSpace",
    "resolve_calculation_space",
    "apply_calculation_space",
    "_SINGLE_ZONE_MAX_LON_SPAN",
]
