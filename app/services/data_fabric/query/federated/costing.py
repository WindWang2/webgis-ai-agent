"""V6 空间成本（ADR-0118 W3）：空间选择性估计 + CRS 变换决策。

纯函数（无 IO、无 shapely/pyproj 依赖）—— 规划期只做估计与决策；
真实几何变换在执行期（executor）用 pyproj 落地。

诚实口径：
- measured 直方图 → ``statistics``；均匀合成直方图 → ``assumption``；
  无统计 → ``default``（与 V2 planner 面积比常数逐位一致）；
- 非等分几何 op（intersects/within/...）= bbox 足迹比 × 收缩因子
  （公开常数，EXPLAIN 可复述），绝不冒充测量；
- dwithin 在地理 CRS 上是**度近似**（equator 口径），detail 恒标注
  ``geographic_approximation`` —— 正确性约束由编译层（PostGIS geography
  cast / 本地 haversine）承载，这里只影响排序。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.services.data_fabric.query.federated.spatial_stats import (
    SpatialGridHistogram,
    estimate_bbox_selectivity,
)
from app.services.data_fabric.query.selectivity import SelectivityEstimate
from app.services.data_fabric.query.statistics import DatasetStatistics

# ── 公开常数（权重/收缩因子；仅为相对排序服务）───────────────────────────
_GEO_DEG_PER_METER = 1.0 / 111_320.0
_SHRINK_INTERSECTS = 0.6
_SHRINK_WITHIN = 0.4
_SHRINK_CONTAINS = 0.4
_SHRINK_TOUCHES = 0.05
_SHRINK_OVERLAPS = 0.4
_MIN_SEL = 1e-9
_W_LOCAL_REPROJECT_PER_ROW = 1.5
_W_SERVER_REPROJECT_PER_ROW = 0.1
_COMPLEXITY_NORM_VERTICES = 60.0
#: 与 V5 链枚举同方向的「无估算」占位（federation.py _UNESTIMATED_ROWS）。
_UNESTIMATED_ROWS = 1_000_000

# ── V7（ADR-0119 W7）：估计不确定性与 rate-limit 成本 ────────────────────────
#: 估计不确定性乘子（按统计置信度；确定性、EXPLAIN 可复述）。
#: measured=实测 → 1.0；estimated=估计 → 轻度上调；assumption/无统计 →
#: 显著上调（乐观估计不再是免费午餐，比较时偏向有统计的源）。
_UNCERTAINTY_BY_CONFIDENCE = {
    "measured": 1.0,
    "estimated": 1.15,
    "assumption": 1.5,
}
_DEFAULT_UNCERTAINTY = 1.5

#: rate-limit 每请求惩罚基（已知限率 → penalty = base / requests_per_window；
#: 未知 = 0 —— 诚实：不猜）。并入源扫描成本，使同基数下限流源排名靠后。
_W_RATE_LIMIT_PENALTY_PER_REQUEST = 25.0


def estimate_uncertainty_multiplier(stats: Optional[Any]) -> "tuple[float, str]":
    """统计置信度 → (乘子, basis)（纯函数；EXPLAIN 披露 basis）。"""
    confidence = getattr(stats, "confidence", None) if stats is not None else None
    if not isinstance(confidence, str) or not confidence:
        confidence = "assumption"
    return _UNCERTAINTY_BY_CONFIDENCE.get(confidence, _DEFAULT_UNCERTAINTY), confidence


def rate_limit_request_penalty(hints: Optional[Any]) -> float:
    """RateLimitHints → 每请求惩罚（未知限率 = 0，绝不虚构）。"""
    if hints is None:
        return 0.0
    rate = getattr(hints, "requests_per_window", None)
    if not isinstance(rate, int) or rate <= 0:
        return 0.0
    return _W_RATE_LIMIT_PENALTY_PER_REQUEST / float(rate)

_SHRINK_BY_OP = {
    "intersects": _SHRINK_INTERSECTS,
    "within": _SHRINK_WITHIN,
    "contains": _SHRINK_CONTAINS,
    "touches": _SHRINK_TOUCHES,
    "overlaps": _SHRINK_OVERLAPS,
}


def _coords_iter(node: Any):
    if isinstance(node, (list, tuple)):
        for x in node:
            yield from _coords_iter(x)
    else:
        yield node


def geojson_bbox(geometry: Any) -> Optional[List[float]]:
    """GeoJSON geometry → 足迹 bbox（纯坐标遍历；形状不合法 → None）。"""
    if not isinstance(geometry, dict) or not isinstance(
        geometry.get("coordinates"), (list, tuple)
    ):
        return None
    vals = [
        v for v in _coords_iter(geometry["coordinates"]) if isinstance(v, (int, float))
    ]
    if len(vals) < 2 or len(vals) % 2 != 0:
        return None
    xs = vals[0::2]
    ys = vals[1::2]
    return [min(xs), min(ys), max(xs), max(ys)]


def _area_ratio(footprint: List[float], extent: List[float]) -> Optional[float]:
    """V5 planner 同款面积比（planner.py:_desc_bbox_area 口径）。"""
    if not extent or len(extent) != 4:
        return None
    q = max(0.0, footprint[2] - footprint[0]) * max(0.0, footprint[3] - footprint[1])
    d = max(0.0, extent[2] - extent[0]) * max(0.0, extent[3] - extent[1])
    if d <= 0 or q <= 0:
        return None
    return max(1e-6, min(1.0, q / d))


def _parse_srid(crs: Optional[str]) -> Optional[int]:
    if not crs:
        return None
    s = str(crs).strip().upper()
    if s.endswith("CRS84") or s == "OGC:CRS84":
        return 4326
    if s.startswith("EPSG:"):
        body = s[5:]
        return int(body) if body.isdigit() else None
    return int(s) if s.isdigit() else None


def is_geographic_srid(srid: Optional[int]) -> bool:
    return srid == 4326


def _histogram_of(stats: Optional[DatasetStatistics]) -> Optional[SpatialGridHistogram]:
    meta = getattr(stats, "spatial_histogram", None)
    return SpatialGridHistogram.from_meta(meta) if meta else None


def estimate_spatial_selectivity(
    spatial: Optional[Any], stats: Optional[DatasetStatistics] = None
) -> SelectivityEstimate:
    """空间谓词选择率：直方图优先 → 面积比兜底 → op 收缩因子。"""
    if spatial is None:
        return SelectivityEstimate(value=1.0, basis="default")
    op = getattr(spatial, "op", None)

    footprint: Optional[List[float]] = (
        list(spatial.bbox)
        if op == "bbox"
        else geojson_bbox(getattr(spatial, "geometry", None))
    )
    extent = getattr(stats, "extent", None) if stats is not None else None
    hist = _histogram_of(stats)

    ratio: Optional[float] = None
    basis = "default"
    if footprint is not None:
        ratio = estimate_bbox_selectivity(hist, footprint)
        if ratio is not None:
            basis = (
                "statistics" if (hist and hist.basis == "measured") else "assumption"
            )
        elif extent:
            ratio = _area_ratio(footprint, extent)
    if ratio is None:
        ratio = 1.0
        basis = "default"

    detail: Dict[str, Any] = {"op": op, "footprint_ratio": round(ratio, 8)}

    if op in ("bbox", None):
        value = ratio
    elif op == "dwithin":
        distance = float(getattr(spatial, "distance", 0.0) or 0.0)
        crs = getattr(spatial, "crs", "EPSG:4326")
        srid = _parse_srid(crs)
        geographic = is_geographic_srid(srid)
        if geographic:
            detail["geographic_approximation"] = True
            pad = distance * _GEO_DEG_PER_METER
        else:
            pad = distance  # 投影 CRS：units 直接是坐标单位（近似）
        if footprint is not None:
            footprint = [
                footprint[0] - pad,
                footprint[1] - pad,
                footprint[2] + pad,
                footprint[3] + pad,
            ]
            buffered = estimate_bbox_selectivity(hist, footprint)
            if buffered is not None:
                value = buffered
                basis = (
                    "statistics"
                    if (hist and hist.basis == "measured")
                    else "assumption"
                )
            elif extent:
                value = _area_ratio(footprint, extent) or ratio
                basis = "default"
            else:
                value = ratio
                basis = "default"
        else:
            value = 0.001  # 无足迹：极保守（dwithin 半径通常很小）
            basis = "assumption"
    else:
        shrink = _SHRINK_BY_OP.get(op, 1.0)
        value = ratio * shrink
        detail["shrink"] = shrink

    detail["footprint"] = footprint
    return SelectivityEstimate(
        value=max(_MIN_SEL, min(1.0, value)),
        basis=basis,
        detail=detail,
    )


# ── CRS 变换决策 ────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CrsTransformDecision:
    """一次 CRS 对齐决策（EXPLAIN 可复述）。"""

    placement: str  # none | server | local
    transform_side: Optional[str]  # left | right | None
    reason: str
    per_row_cost: float = 0.0
    correctness_note: Optional[str] = None


def decide_crs_transform(
    *,
    left_crs_srid: Optional[int],
    right_crs_srid: Optional[int],
    join_kind: str,
    caps_left: Any,
    caps_right: Any,
    est_left_rows: Optional[int],
    est_right_rows: Optional[int],
    stats_left: Optional[DatasetStatistics] = None,
    stats_right: Optional[DatasetStatistics] = None,
    allow_server: bool = False,
    server_feasible: "tuple[bool, bool]" = (True, True),
) -> CrsTransformDecision:
    """join 两侧 CRS 对齐决策：两侧分别估价，取总变换成本最小的一侧。

    - 任一 CRS 未知 → 不变换（诚实 unknown；V5 语义：未知 CRS 视为兼容）；
    - 空间 join 且 CRS 不同 → 必须对齐。server 变换每行成本 ≈ 本地的
      1/15，但 server placement 需要跨 adapter 的 output.crs 下推管道
      （legacy QuerySpec 无该字段）—— ``allow_server=False``（当前默认）
      时只产本地变换决策；server 是显式 follow-up（ADR-0118 Known
      Limitations），计划绝不声称执行不了的 placement；
    - 属性 join 不比较几何 → 只记录 correctness note（几何列随行输出，
      结果 CRS 混合如实披露），不做变换。

    V7（ADR-0119 W8）：``allow_server=True`` 时 server placement 还要求
    该侧 caps 声明 ``output_crs_pushdown``（已验证的扫描通道 —— PostGIS
    ST_Transform 输出包裹 / ArcGIS outSR + spatialReference 回读）；
    仅 server_reprojection（服务器能变换）不等于通道可靠。
    """
    if left_crs_srid is None or right_crs_srid is None:
        return CrsTransformDecision(
            placement="none",
            transform_side=None,
            reason="CRS unknown on at least one side; no transform planned",
            correctness_note=(
                "mixed/unknown CRS: geometry columns are carried as-is; "
                "spatial semantics assume aligned CRS"
            )
            if left_crs_srid != right_crs_srid
            else None,
        )
    if left_crs_srid == right_crs_srid:
        return CrsTransformDecision(
            placement="none", transform_side=None, reason="CRS already aligned"
        )

    # 两侧分别估价（allow_server 时 server 每行成本 ≈ 本地 1/15），
    # 取总变换成本最小的一侧；平局偏 build 侧（右）。
    rows_left = est_left_rows if est_left_rows is not None else _UNESTIMATED_ROWS
    rows_right = est_right_rows if est_right_rows is not None else _UNESTIMATED_ROWS
    complexity_left = max(1.0, _complexity(stats_left)) / _COMPLEXITY_NORM_VERTICES
    complexity_right = max(1.0, _complexity(stats_right)) / _COMPLEXITY_NORM_VERTICES
    server_left = (
        allow_server
        and server_feasible[0]
        and getattr(caps_left, "server_reprojection", False)
        and getattr(caps_left, "output_crs_pushdown", False)
    )
    server_right = (
        allow_server
        and server_feasible[1]
        and getattr(caps_right, "server_reprojection", False)
        and getattr(caps_right, "output_crs_pushdown", False)
    )
    cost_left = rows_left * (
        _W_SERVER_REPROJECT_PER_ROW
        if server_left
        else _W_LOCAL_REPROJECT_PER_ROW * complexity_left
    )
    cost_right = rows_right * (
        _W_SERVER_REPROJECT_PER_ROW
        if server_right
        else _W_LOCAL_REPROJECT_PER_ROW * complexity_right
    )
    if cost_right <= cost_left:
        side, src_srid, dst_srid = "right", right_crs_srid, left_crs_srid
        rows = est_right_rows
        side_server = server_right
    else:
        side, src_srid, dst_srid = "left", left_crs_srid, right_crs_srid
        rows = est_left_rows
        side_server = server_left

    note = None
    if join_kind != "spatial_join":
        return CrsTransformDecision(
            placement="none",
            transform_side=None,
            reason=f"CRS differs (EPSG:{left_crs_srid} vs EPSG:{right_crs_srid}) "
            "but join does not compare geometry",
            correctness_note=(
                "output mixes EPSG:{a} and EPSG:{b} geometry columns".format(
                    a=left_crs_srid, b=right_crs_srid
                )
            ),
        )
    server = side_server
    per_row = (
        _W_SERVER_REPROJECT_PER_ROW
        if server
        else (
            _W_LOCAL_REPROJECT_PER_ROW
            * max(1.0, _complexity(stats_left if side == "left" else stats_right))
            / _COMPLEXITY_NORM_VERTICES
        )
    )
    side_rows = rows if rows is not None else _UNESTIMATED_ROWS
    if server:
        reason = (
            f"server-side ST_Transform EPSG:{src_srid}→EPSG:{dst_srid} on "
            f"{side} (smaller total transform cost, ~{int(side_rows)} rows)"
        )
    else:
        reason = (
            f"local pyproj transform EPSG:{src_srid}→EPSG:{dst_srid} on {side} "
            "(source cannot reproject; smaller total-cost side transformed "
            "once into build cache)"
        )
    return CrsTransformDecision(
        placement="server" if server else "local",
        transform_side=side,
        reason=reason,
        per_row_cost=per_row,
        correctness_note=note,
    )


def _complexity(stats: Optional[DatasetStatistics]) -> float:
    from app.services.data_fabric.query.federated.spatial_stats import (
        geometry_complexity,
    )

    return geometry_complexity(stats)


__all__ = [
    "CrsTransformDecision",
    "decide_crs_transform",
    "estimate_spatial_selectivity",
    "estimate_uncertainty_multiplier",
    "geojson_bbox",
    "is_geographic_srid",
    "rate_limit_request_penalty",
]
