"""经度约定与 antimeridian 处理（Harness V5 W3 — ADR-0118 决策 D3）。

V4 基线：0-360 / antimeridian 的真实处理只存在于 MVT 瓦片切分
（``app/services/mvt.py``），分析/数据面没有任何通用几何级归一 ——
0-360 约定的数据集（常见于全球栅格/部分国家数据源）在 ±180 面向的
分析管道里产生镜像错位或跨 AM 环绕的病态几何。

本模块提供**纯函数、确定性**的经度/AM 语义，供 DatasetProfile（约定
事实）、planner（remediation 提示）与分析入口（归一）复用：

- ``normalize_lon_pm180`` / ``normalize_lon_e360``：标量经度换算；
- ``detect_longitude_convention``：从经度序列判定 ``pm180`` / ``e360``
  / ``ambiguous``（全部落在 0-180 时两种约定等价，不猜）；
- ``bbox_crosses_antimeridian``：±180 bbox 判定（V5 语义：pm180 表示
  下 span>180 即跨 AM）；
- ``split_geometry_at_antimeridian``：GeoJSON 几何级 AM 拆分 —— 平移
  进 0-360 连续帧、按 180 经线切分、再映射回 pm180；
- ``normalize_geojson_geometry``：约定转换入口（e360 → pm180 时自动
  拆分 AM 穿越几何）。

不做的事：不替换 MVT 瓦片内部实现（渲染面归 renderer Epic）；不做
投影变换（CRS 变换走 geo_processor/pyproj）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence

LongitudeConvention = str  # "pm180" | "e360" | "ambiguous"


def normalize_lon_pm180(lon: float) -> float:
    """任一经度换算到 [-180, 180)。"""
    return (float(lon) + 180.0) % 360.0 - 180.0


def normalize_lon_e360(lon: float) -> float:
    """任一经度换算到 [0, 360)。"""
    return float(lon) % 360.0


def detect_longitude_convention(lons: Sequence[float]) -> LongitudeConvention:
    """从经度采样判定约定（确定性；不猜等价情形）。

    - 任一 lon < 0 → ``pm180``（e360 无负值）；
    - 任一 lon > 180 → ``e360``（pm180 表示下不可能）；
    - 全部落在 [0, 180] → 两约定等价 → ``ambiguous``（诚实不猜）；
    - 空序列 → ``ambiguous``。
    """
    vals = [float(v) for v in lons if v is not None]
    if not vals:
        return "ambiguous"
    if any(v < 0 for v in vals):
        return "pm180"
    if any(v > 180 for v in vals):
        return "e360"
    return "ambiguous"


def bbox_crosses_antimeridian(minx: float, miny: float,
                              maxx: float, maxy: float) -> bool:
    """bbox 是否跨越 antimeridian。

    - 环绕表示（``maxx < minx``，如 177 … -179）：本身就是跨 AM 表示；
    - 规范表示（``maxx ≥ minx``）：pm180 下 span > 180 必然是「绕远路」
      的环绕数据。
    """
    if float(maxx) < float(minx):
        return True
    return (float(maxx) - float(minx)) > 180.0


def _shift_coords_to_e360(coords: Any) -> Any:
    """递归把坐标数组的经度平移进 [0, 360)。"""
    if not isinstance(coords, (list, tuple)) or not coords:
        return coords
    first = coords[0]
    if isinstance(first, (int, float)):
        # 坐标点 [lon, lat, ...]
        return [normalize_lon_e360(float(coords[0]))] + [
            c for c in coords[1:]]
    return [_shift_coords_to_e360(c) for c in coords]


def _shift_coords_to_pm180(coords: Any) -> Any:
    if not isinstance(coords, (list, tuple)) or not coords:
        return coords
    first = coords[0]
    if isinstance(first, (int, float)):
        return [normalize_lon_pm180(float(coords[0]))] + [
            c for c in coords[1:]]
    return [_shift_coords_to_pm180(c) for c in coords]


def _split_e360_geometry(geom: Dict[str, Any]) -> List[Dict[str, Any]]:
    """0-360 帧内的几何按 180 经线二分（west: [0,180] / east: (180,360]）。

    用 shapely 矩形裁剪（确定性；无第三方 split 语义依赖）。点/多点按
    经度阈值直接分组，不裁剪。
    """
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if coords is None:
        return [geom]
    try:
        from shapely.geometry import box, shape
    except Exception:  # noqa: BLE001 — shapely 缺席 → 不拆分（诚实降级）
        return [geom]

    west = box(0.0, -90.0, 180.0, 90.0)
    east = box(180.0, -90.0, 360.0, 90.0)
    try:
        shp = shape({"type": gtype, "coordinates": coords})
    except Exception:  # noqa: BLE001
        return [geom]

    if gtype in ("Point", "MultiPoint"):
        out: List[Dict[str, Any]] = []
        pts = list(shp.geoms) if shp.geom_type == "MultiPoint" else [shp]
        west_pts = [p for p in pts if p.x <= 180.0]
        east_pts = [p for p in pts if p.x > 180.0]
        if west_pts:
            out.append(_shift_coords_to_pm180({
                "type": "MultiPoint" if len(west_pts) > 1 else "Point",
                "coordinates": [list(p.coords[0]) for p in west_pts]
                if len(west_pts) > 1 else list(west_pts[0].coords[0]),
            }))
        if east_pts:
            out.append(_shift_coords_to_pm180({
                "type": "MultiPoint" if len(east_pts) > 1 else "Point",
                "coordinates": [list(p.coords[0]) for p in east_pts]
                if len(east_pts) > 1 else list(east_pts[0].coords[0]),
            }))
        return out or [geom]

    try:
        west_part = shp.intersection(west)
        east_part = shp.intersection(east)
    except Exception:  # noqa: BLE001
        return [geom]

    pieces: List[Dict[str, Any]] = []
    # west 碎片坐标 ∈ [0,180] —— 本就是合法 pm180，保持原值（180 不得被
    # 归一成 -180，否则贴 AM 边变成跨图直线）；仅 east 碎片 (180,360]
    # 平移回 [-180,0)。intersection 与两侧 box 求交各自返回单侧（可能
    # Multi）几何 —— 跨侧合并永不成立，无需 union。
    for part, shift in ((west_part, False), (east_part, True)):
        if part.is_empty:
            continue
        # review R1 #10：恰在 180 经线上的几何会在两侧 box 各产生一次退化
        # 命中（零面积碎片重复）—— 丢弃退化面积碎片。
        if part.geom_type.startswith(("Polygon", "MultiPolygon")):
            try:
                if float(part.area) < 1e-12:
                    continue
            except Exception:  # noqa: BLE001 — 面积不可得按原样保留
                pass
        geo = part.__geo_interface__
        if shift:
            geo = {
                "type": geo["type"],
                "coordinates": _shift_coords_to_pm180(geo["coordinates"]),
            }
        pieces.append(geo)
    if not pieces:
        return [{"type": gtype, "coordinates": coords}]
    return pieces


def split_geometry_at_antimeridian(
    geometry: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """把（可能跨 AM 的）GeoJSON 几何拆成不跨 AM 的 pm180 碎片集合。

    输入几何可以是 pm180（含环绕表示）或 e360 约定 —— 统一先平移进
    0-360 连续帧再按 180 经线二分。任何失败返回原几何（诚实降级）。
    """
    try:
        shifted: Dict[str, Any] = {
            "type": geometry.get("type"),
            "coordinates": _shift_coords_to_e360(geometry.get("coordinates")),
        }
        return _split_e360_geometry(shifted)
    except Exception:  # noqa: BLE001
        return [geometry]


def normalize_geojson_geometry(
    geometry: Dict[str, Any],
    target_convention: LongitudeConvention = "pm180",
) -> Dict[str, Any]:
    """把几何归一到目标经度约定（pm180 目标下 AM 穿越几何被拆分）。

    e360 目标只做标量换算（0-360 帧天然连续，无需拆分）。结构异常时
    返回原几何（绝不抛出 —— 记录/归一面不阻断分析）。
    """
    gtype = geometry.get("type")
    if gtype not in ("Point", "MultiPoint", "LineString", "MultiLineString",
                     "Polygon", "MultiPolygon", "GeometryCollection"):
        return geometry
    if target_convention == "e360":
        try:
            return {
                "type": gtype,
                "coordinates": _shift_coords_to_e360(
                    geometry.get("coordinates")),
            }
        except Exception:  # noqa: BLE001
            return geometry
    # pm180 目标
    try:
        coords = geometry.get("coordinates")
        lons = _sample_lons(coords)
        conv = detect_longitude_convention(lons)
        if conv == "e360":
            # e360 输入 → 平移回 pm180；>180 部分取模后可能与 <=180 部分
            # 拼出环绕几何 —— AM 穿越时必须拆分。
            parts = split_geometry_at_antimeridian(geometry)
            if len(parts) == 1:
                return parts[0]
            return {
                "type": "GeometryCollection",
                "geometries": parts,
            }
        return geometry  # pm180/ambiguous：原样（pm180 已合规）
    except Exception:  # noqa: BLE001
        return geometry


def _sample_lons(coords: Any, cap: int = 512) -> List[float]:
    """深度优先采样坐标数组中的经度（有界；Polygon 优先外环）。"""
    out: List[float] = []

    def walk(node: Any, depth: int) -> None:
        if len(out) >= cap or node is None:
            return
        if isinstance(node, (list, tuple)):
            if node and isinstance(node[0], (int, float)):
                out.append(float(node[0]))
                return
            for child in node:
                walk(child, depth + 1)

    walk(coords, 0)
    return out[:cap]


def geometry_crosses_antimeridian(lons: Sequence[float]) -> bool:
    """几何级 AM 穿越判定：相邻顶点经度跳变 > 180（有界采样序列）。

    比 max-min span 更准：贴 AM 线的合法碎片（170 … -180）span 达 350
    但不穿越；真穿越（170 → -170 直接连边）跳变 340。
    """
    vals = [float(v) for v in lons]
    for a, b in zip(vals, vals[1:]):
        if abs(b - a) > 180.0:
            return True
    return False


def describe_longitude_semantics(
    bbox: Optional[Sequence[float]],
    geometry: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """DatasetProfile / planner 的经度约定事实（有界、确定性）。

    返回 ``{"convention": ..., "crosses_antimeridian": bool,
    "normalization_required": bool}``；证据缺失时 convention 为
    ``ambiguous`` 且显式给 ``evidence: "absent"``（绝不虚构）。
    """
    lons: List[float] = []
    crosses = False
    evidence = "absent"
    if bbox is not None and len(bbox) >= 4:
        minx, miny, maxx, maxy = (float(v) for v in bbox[:4])
        evidence = "bbox"
        crosses = bbox_crosses_antimeridian(minx, miny, maxx, maxy)
        lons = [minx, maxx]
    if geometry is not None:
        geo_lons = _sample_lons(geometry.get("coordinates"))
        if geo_lons:
            evidence = "geometry" if evidence == "absent" else "bbox+geometry"
            lons = geo_lons or lons
            if len(geo_lons) >= 2:
                crosses = crosses or geometry_crosses_antimeridian(geo_lons)
    conv = detect_longitude_convention(lons) if lons else "ambiguous"
    return {
        "convention": conv,
        "crosses_antimeridian": crosses,
        "normalization_required": crosses or conv == "e360",
        "evidence": evidence,
    }


__all__ = [
    "normalize_lon_pm180",
    "normalize_lon_e360",
    "detect_longitude_convention",
    "bbox_crosses_antimeridian",
    "geometry_crosses_antimeridian",
    "split_geometry_at_antimeridian",
    "normalize_geojson_geometry",
    "describe_longitude_semantics",
]
