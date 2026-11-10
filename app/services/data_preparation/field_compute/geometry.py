"""CRS 安全的面积/长度计算（G19 / WP05）。

不复制临时经纬度距离公式：CRS 资格由 ``app.lib.gis.crs_safety.classify_crs``
裁决，几何计算由 ``pyproj.Geod``（大地测量）或 shapely 平面几何（局部度量
投影）完成。带洞面 / 多部件 / 跨经线由既有稳健算法处理，本模块只做门控、
单位归一与资格披露。

- ``geographic``（如 EPSG:4326）→ ``Geod.geometry_area_perimeter`` /
  ``Geod.geometry_length``（椭球面，单位 m² / m，度输入也正确）；
- ``projected_local_metric``（UTM 等，已过尺度失真闸）→ shapely ``.area`` /
  ``.length``（单位 m² / m）；
- ``projected``（含 3857，尺度失真）→ ``CRS_NOT_METRIC`` 阻断；
- ``unknown`` / ``crs_source=assumed`` → ``CRS_UNKNOWN`` 阻断（猜测不成为事实）；
- Point / 空 / 非法几何 → ``GEOMETRY_NOT_MEASURABLE`` / ``GEOMETRY_INVALID``。
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from app.lib.gis.crs_safety import classify_crs

AREA_UNITS = {"area_km2": ("area", 1e6, "square_kilometers"),
              "area_m2": ("area", 1.0, "square_meters"),
              "length_km": ("length", 1e3, "kilometers"),
              "length_m": ("length", 1.0, "meters")}


class GeometryBlocked(Exception):
    """行级几何不可计算（带稳定码，计入错误统计而不丢行）。"""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def geometry_gate(crs: str, crs_source: str = "") -> Tuple[str, str]:
    """→ (mode, block_reason)。mode ∈ geodesic / planar / ""。"""
    if str(crs_source or "").strip().lower() == "assumed":
        return "", "CRS_UNKNOWN"
    cls = classify_crs(crs)
    if cls == "geographic":
        return "geodesic", ""
    if cls == "projected_local_metric":
        return "planar", ""
    if cls == "projected":
        return "", "CRS_NOT_METRIC"
    return "", "CRS_UNKNOWN"


def gate_reason_detail(mode: str, reason: str, crs: str) -> str:
    if not reason:
        return ""
    if reason == "CRS_NOT_METRIC":
        return (f"CRS {crs or '(空)'} 是投影坐标但尺度失真不可作局部度量 "
                "（如 EPSG:3857）—— 面积/长度已阻断；请在可靠度量 CRS 下重算")
    return (f"CRS {crs or '(空)'} 未知或为 assumed 猜测 —— 不能把平方度当 "
            "平方千米，几何计算已阻断；普通属性公式不受影响")


def _shape(geometry: Dict[str, Any]):
    from shapely.geometry import shape
    return shape(geometry)


def _bbox_dateline(geometry: Dict[str, Any]) -> bool:
    """跨 ±180° 经线检测（仅披露；Geod 大地测量本身可正确处理）。"""
    lons: list[float] = []

    def walk(node: Any) -> None:
        if isinstance(node, (list, tuple)) and node and isinstance(node[0], (int, float)):
            if len(node) >= 2 and isinstance(node[0], (int, float)):
                lons.append(float(node[0]))
            return
        for child in node or []:
            walk(child)

    walk(geometry.get("coordinates"))
    if not lons:
        return False
    return (max(lons) - min(lons)) > 180.0


def measure_geometry(geometry: Optional[Dict[str, Any]], func_name: str,
                     mode: str, block_reason: str, crs: str = ""
                     ) -> Tuple[float, Dict[str, Any]]:
    """行几何 → (值, 有界证据)；失败抛 ``GeometryBlocked``。"""
    if block_reason:
        raise GeometryBlocked(block_reason, gate_reason_detail(mode, block_reason, crs))
    if not isinstance(geometry, dict) or not geometry.get("type"):
        raise GeometryBlocked("GEOMETRY_NOT_MEASURABLE", "要素无几何")
    coords = geometry.get("coordinates")
    if coords in (None, [], ()):
        raise GeometryBlocked("GEOMETRY_NOT_MEASURABLE", "几何坐标为空")
    spec = AREA_UNITS.get(func_name)
    if spec is None:
        raise GeometryBlocked("GEOMETRY_INVALID", f"未知几何函数 {func_name!r}")
    kind, divisor, unit = spec
    gtype = str(geometry.get("type") or "")
    if kind == "area" and gtype not in ("Polygon", "MultiPolygon"):
        raise GeometryBlocked("GEOMETRY_NOT_MEASURABLE",
                              f"{gtype} 不是面（面积需要 Polygon/MultiPolygon）")
    if kind == "length" and gtype not in ("LineString", "MultiLineString"):
        raise GeometryBlocked("GEOMETRY_NOT_MEASURABLE",
                              f"{gtype} 不是线（长度需要 LineString/MultiLineString）")
    try:
        geom = _shape(geometry)
    except Exception as exc:  # noqa: BLE001
        raise GeometryBlocked("GEOMETRY_INVALID", str(exc)[:160]) from exc
    if geom.is_empty:
        raise GeometryBlocked("GEOMETRY_NOT_MEASURABLE", "空几何")
    try:
        if mode == "geodesic":
            from pyproj import Geod
            geod = Geod(ellps="WGS84")
            if kind == "area":
                area_m2, _perim = geod.geometry_area_perimeter(geom)
                raw = abs(float(area_m2))
            else:
                raw = abs(float(geod.geometry_length(geom)))
            method = "pyproj.Geod(WGS84)"
        elif mode == "planar":
            raw = abs(float(geom.area if kind == "area" else geom.length))
            method = "shapely.planar"
        else:  # pragma: no cover — gate 已阻断
            raise GeometryBlocked("CRS_UNKNOWN", "no measurement mode")
    except GeometryBlocked:
        raise
    except Exception as exc:  # noqa: BLE001
        raise GeometryBlocked("GEOMETRY_INVALID", str(exc)[:160]) from exc
    if not math.isfinite(raw):
        raise GeometryBlocked("GEOMETRY_INVALID", "非有限测量结果")
    value = raw / divisor
    evidence = {
        "function": func_name,
        "unit": unit,
        "crs": crs,
        "method": method,
        "geometry_type": gtype,
        "parts": int(getattr(geom, "geom_type", "") == "MultiPolygon") + 1,
        "has_holes": bool(kind == "area" and _has_holes(geometry)),
        "dateline_crossing": _bbox_dateline(geometry),
    }
    return value, evidence


def _has_holes(geometry: Dict[str, Any]) -> bool:
    coords = geometry.get("coordinates")
    if not isinstance(coords, list):
        return False
    if geometry.get("type") == "Polygon":
        return len(coords) > 1
    if geometry.get("type") == "MultiPolygon":
        return any(isinstance(p, list) and len(p) > 1 for p in coords)
    return False


__all__ = ["AREA_UNITS", "GeometryBlocked", "geometry_gate",
           "gate_reason_detail", "measure_geometry"]
