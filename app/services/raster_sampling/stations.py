"""G39 站位生成 — 点集身份保持与线路站位/距离轴（纯几何，无 I/O）。

纪律（WP04）：
- 点集：每个输入点保留为一站位（先不去重后丢站），父 locator 用输入
  feature id（缺失 → 生成序号并披露），属性有界复制；
- 线路：站位按声明 CRS 的距离模式生成 —— 地理 CRS 用 pyproj Geod 测地，
  投影米制 CRS 用平面米制；每 part 独立 chainage，断口如实保留；
- 不可靠组合 typed 拒绝：度数平面距离、跨经线 >180° 跳变、极点顶点、
  零长线路。顶点恰为站位（段边界不重复计），末顶点必在场。
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.services.raster_sampling.contracts import (
    DISTANCE_MODE_GEODESIC,
    DISTANCE_MODE_PLANAR_METRIC,
    MAX_STATIONS,
    RasterSamplingError,
    STATION_MODE_LINE_STATIONS,
    STATION_MODE_SOURCE_POINTS,
)

#: 点站位父属性有界复制（键数与值长度）
_MAX_PARENT_ATTRS = 8
_MAX_ATTR_LEN = 128
#: 线路每 part 顶点上限
_MAX_LINE_VERTICES = 1024
#: 测地插值相邻顶点经度跳变护栏（>180° = 绕远弧，typed 拒绝）
_MAX_LON_JUMP_DEG = 180.0
#: 链距比较容差（米；同位顶点/规则站位去重）
_CHAIN_EPS = 1e-9


@dataclass
class Station:
    """一个采样站位（确定性：由 plan seed + 序列 mint 身份）。"""

    sample_id: str
    station_index: int
    x: float                      # 站位 CRS 坐标
    y: float
    parent_locator: str = ""      # 点模式父 feature id；线路模式线路定位
    parent_attrs: Dict[str, str] = field(default_factory=dict)
    part_id: str = ""             # 线路 part（点模式空）
    chainage_m: Optional[float] = None
    is_vertex: bool = False
    segment_index: int = -1       # 线路：所在线段序号（0 基；-1 = 非线路）
    breaks_after: bool = False    # 本站位后存在 part 断口（曲线不连线）


@dataclass
class StationSet:
    """站位集 + 几何摘要（digest 用于计划指纹与预览失效）。"""

    mode: str
    crs: str
    stations: List[Station] = field(default_factory=list)
    part_ids: List[str] = field(default_factory=list)
    part_break_indices: List[int] = field(default_factory=list)
    distance_mode: str = ""
    line_length_m: float = 0.0
    notes: List[str] = field(default_factory=list)

    def digest(self) -> str:
        """站位几何摘要（坐标/链距/断口；不含显示属性）。"""
        h = hashlib.sha256()
        h.update(f"{self.mode}|{self.crs}|{self.distance_mode}".encode())
        for s in self.stations:
            chain = "" if s.chainage_m is None else format(s.chainage_m, ".6f")
            h.update(
                f"|{s.station_index}:{s.x:.9f}:{s.y:.9f}:{s.part_id}:"
                f"{chain}:{int(s.is_vertex)}:{int(s.breaks_after)}".encode()
            )
        return h.hexdigest()[:24]

    def geometry_hash(self) -> str:
        """输入几何身份（父 locator + 坐标序列；绑定血缘用）。"""
        h = hashlib.sha256()
        h.update(f"{self.mode}|{self.crs}".encode())
        for s in self.stations:
            h.update(f"|{s.parent_locator}:{s.x:.9f}:{s.y:.9f}".encode())
        return h.hexdigest()[:24]


def _station_id(plan_seed: str, index: int) -> str:
    return f"sp_{plan_seed}_{index:06d}"


def _plan_seed(plan_id: str) -> str:
    return hashlib.sha256(f"g39:{plan_id}".encode()).hexdigest()[:8]


def _bounded_attrs(props: Optional[Dict[str, Any]]) -> Dict[str, str]:
    if not isinstance(props, dict):
        return {}
    out: Dict[str, str] = {}
    for k, v in props.items():
        if len(out) >= _MAX_PARENT_ATTRS:
            break
        if isinstance(v, (str, int, float, bool)):
            out[str(k)[:32]] = str(v)[:_MAX_ATTR_LEN]
    return out


def _finite(x: float, y: float, *, where: str) -> Tuple[float, float]:
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError) as exc:
        raise RasterSamplingError(
            "INVALID_GEOMETRY", f"non-numeric coordinates in {where}",
            field="stations") from exc
    if not (math.isfinite(fx) and math.isfinite(fy)):
        raise RasterSamplingError(
            "INVALID_GEOMETRY", f"non-finite coordinates in {where}",
            field="stations")
    return fx, fy


# ── CRS 距离能力（真实 pyproj 判定；不猜） ───────────────────────────

def crs_distance_capability(crs: str) -> Dict[str, Any]:
    """解析 CRS 的可用距离模式（真实 pyproj CRS 轴/单位；失败 typed）。"""
    if not crs or not isinstance(crs, str):
        raise RasterSamplingError("NO_CRS", "station CRS is required",
                                  field="crs")
    try:
        from pyproj import CRS
        parsed = CRS.from_user_input(crs)
    except Exception as exc:  # noqa: BLE001
        raise RasterSamplingError(
            "INVALID_CRS", f"station CRS {crs!r} could not be parsed",
            field="crs") from exc
    is_geographic = bool(parsed.is_geographic)
    unit = None
    try:
        unit = str(parsed.axis_info[0].unit_name or "").lower()
    except Exception:  # noqa: BLE001
        unit = None
    modes: List[str] = []
    if is_geographic:
        modes.append(DISTANCE_MODE_GEODESIC)
    elif unit in {"metre", "meter", "m"}:
        # 投影系：只有米制单位才允许平面米制距离
        modes.append(DISTANCE_MODE_PLANAR_METRIC)
    is_web_mercator = False
    try:
        is_web_mercator = bool(parsed.to_epsg() in (3857, 900913, 3785))
    except Exception:  # noqa: BLE001
        is_web_mercator = False
    return {
        "is_geographic": is_geographic,
        "unit": unit,
        "allowed_modes": modes,
        "is_web_mercator": is_web_mercator,
        "epsg": parsed.to_epsg(),
    }


class _DistanceEngine:
    """按声明模式的段长/插值（geodesic | planar_metric）。"""

    def __init__(self, mode: str, crs: str) -> None:
        self.mode = mode
        self.crs = crs
        self._geod = None
        if mode == DISTANCE_MODE_GEODESIC:
            from pyproj import CRS, Geod

            parsed = CRS.from_user_input(crs)
            ellps = parsed.ellipsoid
            self._geod = Geod(a=ellps.semi_major_metre, b=ellps.semi_minor_metre)

    def segment_length(self, x0: float, y0: float, x1: float, y1: float) -> float:
        if self.mode == DISTANCE_MODE_GEODESIC:
            if abs(x1 - x0) > _MAX_LON_JUMP_DEG:
                raise RasterSamplingError(
                    "UNSUPPORTED_GEODESY",
                    "line vertices jump more than 180° in longitude — split "
                    "the line at the antimeridian instead of sampling the "
                    "long way around the Earth",
                    field="stations",
                    correction_hint="Split the route into parts at ±180° "
                                    "and sample each side separately.")
            _, _, dist = self._geod.inv(x0, y0, x1, y1)
            return float(dist)
        return float(math.hypot(x1 - x0, y1 - y0))

    def interpolate(
        self, x0: float, y0: float, x1: float, y1: float, frac: float
    ) -> Tuple[float, float]:
        """段内 frac∈[0,1] 处坐标（测地 fwd / 平面线性）。"""
        if self.mode == DISTANCE_MODE_GEODESIC:
            az, _, seg_len = self._geod.inv(x0, y0, x1, y1)
            lon, lat, _ = self._geod.fwd(x0, y0, az, seg_len * frac)
            return float(lon), float(lat)
        return x0 + (x1 - x0) * frac, y0 + (y1 - y0) * frac


# ── 点集站位 ─────────────────────────────────────────────────────────

def build_point_stations(
    fc: Dict[str, Any], *, crs: str, plan_id: str
) -> StationSet:
    """点 FeatureCollection → 站位（保身份、不去重、有界属性）。"""
    if not isinstance(fc, dict) or fc.get("type") != "FeatureCollection":
        raise RasterSamplingError(
            "INVALID_GEOMETRY",
            "points input must be a GeoJSON FeatureCollection of Point features",
            field="points_ref")
    features = fc.get("features")
    if not isinstance(features, list) or not features:
        raise RasterSamplingError(
            "INVALID_GEOMETRY", "points FeatureCollection has no features",
            field="points_ref")
    if len(features) > MAX_STATIONS:
        raise RasterSamplingError(
            "BUDGET_EXCEEDED",
            f"point set exceeds station budget ({len(features)} > {MAX_STATIONS})",
            field="points_ref", status_code=413,
            correction_hint="Split the point set or select a subset (e.g. via "
                            "a selection handle) before planning.")
    seed = _plan_seed(plan_id)
    stations: List[Station] = []
    missing_id = 0
    id_counts: Dict[str, int] = {}
    for i, f in enumerate(features):
        if not isinstance(f, dict):
            raise RasterSamplingError(
                "INVALID_GEOMETRY", f"feature[{i}] is not an object",
                field="points_ref")
        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        if gtype == "Point" and isinstance(coords, (list, tuple)) and len(coords) >= 2:
            x, y = _finite(coords[0], coords[1], where=f"point[{i}]")
        else:
            raise RasterSamplingError(
                "INVALID_GEOMETRY",
                f"feature[{i}] is not a Point geometry — explode MultiPoint or "
                "other collections first",
                field="points_ref")
        props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
        fid = f.get("id")
        if fid is None and isinstance(props, dict):
            fid = props.get("id")
        if fid is None or isinstance(fid, (dict, list)):
            fid = f"point[{i}]"
            missing_id += 1
        locator = str(fid)
        id_counts[locator] = id_counts.get(locator, 0) + 1
        stations.append(Station(
            sample_id=_station_id(seed, i),
            station_index=i,
            x=x, y=y,
            parent_locator=locator,
            parent_attrs=_bounded_attrs(props),
        ))
    notes: List[str] = []
    if missing_id:
        notes.append(f"missing_parent_ids:{missing_id}")
    dups = {k: v for k, v in id_counts.items() if v > 1}
    if dups:
        # 不去重、不丢站 —— 重复父身份如实披露（站位 id 仍唯一）
        top = sorted(dups.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
        notes.append("duplicate_parent_ids:" + ",".join(
            f"{k}×{v}" for k, v in top))
    return StationSet(mode=STATION_MODE_SOURCE_POINTS, crs=crs,
                      stations=stations, notes=notes)


# ── 线路站位 ─────────────────────────────────────────────────────────

def _iter_line_parts(
    feature: Dict[str, Any]
) -> List[Tuple[str, List[Tuple[float, float]]]]:
    """LineString/MultiLineString → [(part_id, [顶点])]（顺序保留）。"""
    geom = feature.get("geometry") or {}
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    parts: List[Tuple[str, List[Tuple[float, float]]]] = []

    def _vertex(part_id: str, vi: int, c: Any) -> Tuple[float, float]:
        if not isinstance(c, (list, tuple)) or len(c) < 2:
            raise RasterSamplingError(
                "INVALID_GEOMETRY",
                f"{part_id} vertex[{vi}] must be an [x, y] coordinate pair",
                field="lines_ref")
        return tuple(c)  # type: ignore[return-value]

    if gtype == "LineString":
        if not isinstance(coords, list) or len(coords) < 2:
            raise RasterSamplingError(
                "INVALID_GEOMETRY", "LineString needs ≥2 vertices",
                field="lines_ref")
        parts.append(("part-0", [_vertex("part-0", vi, c)
                                 for vi, c in enumerate(coords)]))
    elif gtype == "MultiLineString":
        if not isinstance(coords, list) or not coords:
            raise RasterSamplingError(
                "INVALID_GEOMETRY", "MultiLineString has no parts",
                field="lines_ref")
        for pi, part in enumerate(coords):
            if not isinstance(part, list) or len(part) < 2:
                raise RasterSamplingError(
                    "INVALID_GEOMETRY",
                    f"MultiLineString part[{pi}] needs ≥2 vertices",
                    field="lines_ref")
            parts.append((f"part-{pi}",
                          [_vertex(f"part-{pi}", vi, c)
                           for vi, c in enumerate(part)]))
    else:
        raise RasterSamplingError(
            "INVALID_GEOMETRY",
            f"line input must be LineString/MultiLineString, got {gtype!r}",
            field="lines_ref")
    return parts


def _locate_chainage(
    seg_lens: Sequence[float], chain: float
) -> Tuple[int, float]:
    """链距 → (线段序号, 段内分数)。零长段不承载站位。"""
    acc = 0.0
    last_nonzero = 0
    for i, sl in enumerate(seg_lens):
        if sl > 0.0:
            last_nonzero = i
        if chain <= acc + sl + _CHAIN_EPS:
            if sl <= 0.0:
                # 零长段：站位归到段首（与前段末同点）
                return i, 0.0
            return i, min(1.0, max(0.0, (chain - acc) / sl))
        acc += sl
    return last_nonzero, 1.0


def build_line_stations(
    feature: Dict[str, Any],
    *,
    crs: str,
    distance_mode: str,
    station_step: Optional[float],
    include_vertices: bool,
    plan_id: str,
    line_feature_id: str = "",
) -> StationSet:
    """线路 → 站位（每 part 独立 chainage；顶点/步长/端点策略显式）。

    规则站位落在步长网格（0, step, 2·step, …）；``include_vertices`` 时
    顶点也是站位；同链距只计一次（顶点身份优先）；末顶点必在场（端点
    语义）；重复顶点不制造额外里程（零长段跳过）；part 断口标记在
    part 末站位上，曲线绘制不跨界连线。
    """
    cap = crs_distance_capability(crs)
    allowed = cap["allowed_modes"]
    if distance_mode not in allowed:
        hint = ("Use distance_mode='geodesic' with a geographic CRS."
                if distance_mode == DISTANCE_MODE_GEODESIC
                else "Provide a projected metric CRS (axis unit = metre) for "
                     "planar_metric distances.")
        extra = (" Web Mercator lengths are not ground distances."
                 if cap["is_web_mercator"] else "")
        raise RasterSamplingError(
            "UNSUPPORTED_GEODESY",
            f"distance_mode={distance_mode!r} is not supported for CRS {crs!r} "
            f"(allowed: {sorted(allowed) or 'none'}).{extra}",
            field="distance_mode",
            correction_hint=hint)
    if station_step is None or not math.isfinite(station_step) or station_step <= 0:
        raise RasterSamplingError(
            "INVALID_PLAN", "line_stations requires a positive station_step",
            field="station_step")
    parts = _iter_line_parts(feature)
    engine = _DistanceEngine(distance_mode, crs)
    seed = _plan_seed(plan_id)
    stations: List[Station] = []
    part_ids: List[str] = []
    breaks: List[int] = []
    total_len = 0.0
    notes: List[str] = []
    zero_segs_total = 0

    for part_id, vertices in parts:
        if len(vertices) > _MAX_LINE_VERTICES:
            raise RasterSamplingError(
                "BUDGET_EXCEEDED",
                f"line part {part_id} has {len(vertices)} vertices "
                f"(cap {_MAX_LINE_VERTICES})",
                field="lines_ref", status_code=413)
        part_ids.append(part_id)
        pts: List[Tuple[float, float]] = []
        for vi, v in enumerate(vertices):
            x, y = _finite(v[0], v[1], where=f"{part_id} vertex[{vi}]")
            if distance_mode == DISTANCE_MODE_GEODESIC and abs(y) >= 90.0:
                raise RasterSamplingError(
                    "UNSUPPORTED_GEODESY",
                    f"line vertex at latitude {y} — geodesic sampling through "
                    "the poles is not supported",
                    field="lines_ref",
                    correction_hint="Densify or clip the route away from ±90°.")
            pts.append((x, y))
        seg_lens = [engine.segment_length(pts[i][0], pts[i][1],
                                          pts[i + 1][0], pts[i + 1][1])
                    for i in range(len(pts) - 1)]
        zero_segs_total += sum(1 for sl in seg_lens if sl <= 0.0)
        part_len = sum(seg_lens)
        if part_len <= 0.0:
            raise RasterSamplingError(
                "INVALID_GEOMETRY",
                f"line part {part_id} has zero total length "
                "(all vertices coincide)",
                field="lines_ref",
                correction_hint="Remove duplicate vertices or provide a "
                                "non-degenerate route.")
        total_len += part_len
        # 分配前预算（规则站位 + 顶点 + 端点）
        est = int(math.ceil(part_len / station_step)) + 1 + len(pts)
        if len(stations) + est > MAX_STATIONS:
            raise RasterSamplingError(
                "BUDGET_EXCEEDED",
                f"station budget exceeded while planning {part_id} "
                f"(~{len(stations) + est} > {MAX_STATIONS})",
                field="station_step", status_code=413,
                correction_hint="Increase station_step or split the route.")

        # 事件收集：(链距, 线段, 是否顶点)。顶点链距 = 前序非零段累计。
        events: List[Tuple[float, int, bool]] = []
        t = 0.0
        while t < part_len - _CHAIN_EPS:
            events.append((t, -1, False))
            t += station_step
        acc = 0.0
        for si, sl in enumerate(seg_lens):
            if sl > 0.0:
                acc += sl
                if include_vertices and si + 1 < len(pts):
                    events.append((acc, si, True))
        # 首顶点（链距 0，顶点身份）与末顶点在去重/收尾中保证
        events.append((0.0, -1, True))
        events.append((part_len, -1, True))
        events.sort(key=lambda e: (e[0], not e[2]))

        last_chain: Optional[float] = None
        for ch, seg_hint, is_v in events:
            if last_chain is not None and abs(ch - last_chain) < _CHAIN_EPS:
                continue  # 同链距去重（顶点身份优先已由排序保证）
            seg, frac = _locate_chainage(seg_lens, ch)
            v0, v1 = pts[seg], pts[seg + 1]
            x, y = engine.interpolate(v0[0], v0[1], v1[0], v1[1], frac)
            stations.append(Station(
                sample_id=_station_id(seed, len(stations)),
                station_index=len(stations),
                x=x, y=y,
                parent_locator=line_feature_id or part_id,
                part_id=part_id,
                chainage_m=ch,
                is_vertex=is_v,
                segment_index=seg,
            ))
            last_chain = ch
        # 端点必须恰为末站位（浮点尾差兜底）
        end = stations[-1]
        if abs((end.chainage_m or 0.0) - part_len) > _CHAIN_EPS:
            stations.append(Station(
                sample_id=_station_id(seed, len(stations)),
                station_index=len(stations),
                x=pts[-1][0], y=pts[-1][1],
                parent_locator=line_feature_id or part_id,
                part_id=part_id,
                chainage_m=part_len,
                is_vertex=True,
                segment_index=len(pts) - 2,
            ))
        breaks.append(len(stations) - 1)

    if zero_segs_total:
        notes.append(f"duplicate_zero_length_segments:{zero_segs_total}")
    # 断口标记：part 末站位之后为断口（曲线不连线）
    for b in breaks:
        if 0 <= b < len(stations):
            stations[b].breaks_after = True
    return StationSet(
        mode=STATION_MODE_LINE_STATIONS, crs=crs, stations=stations,
        part_ids=part_ids, part_break_indices=breaks,
        distance_mode=distance_mode, line_length_m=total_len, notes=notes,
    )
