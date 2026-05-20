"""测量 + 地图标注工具 (Annotation / Measurement)

提供 LLM 在地图上量距、量面、撒钉、清理标注的能力。
全部走"前端命令"模式：后端只计算 + 发指令，几何数据用 SSE 推到画布。
"""
from __future__ import annotations

import logging
import math
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from app.tools.registry import ToolRegistry, tool

logger = logging.getLogger(__name__)

EARTH_RADIUS_KM = 6371.0


# ─── Pydantic 模型 ───────────────────────────────────────────────────


class MeasureDistanceArgs(BaseModel):
    coordinates: List[List[float]] = Field(
        ...,
        description="折线顶点列表，每个点 [lng, lat]，至少 2 个点。例如 [[116.4,39.9],[116.5,40.0]]",
    )
    label: Optional[str] = Field(None, description="可选的折线标签，会显示在终点附近")


class MeasureAreaArgs(BaseModel):
    coordinates: List[List[float]] = Field(
        ...,
        description="多边形外环顶点列表，每个点 [lng, lat]，至少 3 个点。首尾点不需要重复。",
    )
    label: Optional[str] = Field(None, description="可选的多边形标签")


class AddMarkerArgs(BaseModel):
    longitude: float = Field(..., description="经度 (-180~180)")
    latitude: float = Field(..., description="纬度 (-90~90)")
    label: Optional[str] = Field(None, description="标注文字，会显示在 pin 旁边")
    color: str = Field("#ef4444", description="pin 颜色 hex，默认红 #ef4444")


# ─── 几何计算 ────────────────────────────────────────────────────────


def _haversine_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    lng1, lat1 = math.radians(a[0]), math.radians(a[1])
    lng2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(h))


def polyline_length_km(coords: List[List[float]]) -> float:
    total = 0.0
    for i in range(len(coords) - 1):
        total += _haversine_km((coords[i][0], coords[i][1]),
                               (coords[i + 1][0], coords[i + 1][1]))
    return total


def spherical_polygon_area_km2(coords: List[List[float]]) -> float:
    """球面多边形面积 (km²)，用 L'Huilier 公式 / spherical excess。

    coords 是 [[lng,lat],...]，外环；首尾不需要相同。逆/顺时针都给出正值。
    精度足够任何"看一眼大致多大"用途；要审计级精度请用 PostGIS / Turf。
    """
    n = len(coords)
    if n < 3:
        return 0.0
    # 闭合
    pts = coords + [coords[0]] if coords[0] != coords[-1] else list(coords)
    total = 0.0
    for i in range(len(pts) - 1):
        lng1, lat1 = math.radians(pts[i][0]), math.radians(pts[i][1])
        lng2, lat2 = math.radians(pts[i + 1][0]), math.radians(pts[i + 1][1])
        total += (lng2 - lng1) * (2 + math.sin(lat1) + math.sin(lat2))
    area = abs(total * EARTH_RADIUS_KM * EARTH_RADIUS_KM / 2.0)
    return area


def _validate_coords(coords: Any, min_points: int) -> Optional[str]:
    if not isinstance(coords, list) or len(coords) < min_points:
        return f"至少需要 {min_points} 个点"
    for p in coords:
        if not (isinstance(p, (list, tuple)) and len(p) >= 2):
            return f"坐标格式错误：每个点必须是 [lng, lat]，实际收到 {p!r}"
        try:
            lng, lat = float(p[0]), float(p[1])
        except (ValueError, TypeError):
            return f"坐标必须是数字: {p!r}"
        if not (-180 <= lng <= 180) or not (-90 <= lat <= 90):
            return f"坐标越界 ({lng}, {lat})"
    return None


# ─── 工具注册 ────────────────────────────────────────────────────────


def register_annotation_tools(registry: ToolRegistry):
    @tool(
        registry,
        name="measure_distance",
        description=(
            "在地图上量两点或多点折线的距离 (Haversine 球面距离，米/公里精度)。"
            "\n何时用：用户说『量一下这两个点多远』『沿这条路线走多少公里』。"
            "\n何时不用：要量面积 — 用 measure_area；只是想知道点的地名 — 用 reverse_geocode。"
            "\n关键约束：coordinates 至少 2 个点，每个 [lng, lat]；返回 km 数值 + 前端绘制折线。"
        ),
        args_model=MeasureDistanceArgs,
    )
    def measure_distance(coordinates: List[List[float]], label: Optional[str] = None) -> dict:
        err = _validate_coords(coordinates, 2)
        if err:
            return {"error": err}
        total_km = polyline_length_km(coordinates)
        unit, value = ("km", total_km) if total_km >= 1 else ("m", total_km * 1000)
        summary = f"折线总长 {value:.2f} {unit} (共 {len(coordinates)} 个顶点)"
        return {
            "success": True,
            "summary": summary,
            "distance_km": total_km,
            "point_count": len(coordinates),
            "command": "draw_measurement",
            "params": {
                "shape": "polyline",
                "coordinates": coordinates,
                "label": label or f"{value:.2f} {unit}",
            },
        }

    @tool(
        registry,
        name="measure_area",
        description=(
            "在地图上量多边形面积 (球面公式，km² 精度)。"
            "\n何时用：用户说『这块地多大』『画个圈量个面积』『统计该区域覆盖了多少平方公里』。"
            "\n何时不用：量距 — 用 measure_distance；要严格审计精度 — 用 PostGIS 工具。"
            "\n关键约束：coordinates 至少 3 个点，每个 [lng, lat]；首尾点不需要重复；"
            "顺/逆时针均返回正值。"
        ),
        args_model=MeasureAreaArgs,
    )
    def measure_area(coordinates: List[List[float]], label: Optional[str] = None) -> dict:
        err = _validate_coords(coordinates, 3)
        if err:
            return {"error": err}
        area_km2 = spherical_polygon_area_km2(coordinates)
        unit, value = ("km²", area_km2) if area_km2 >= 0.1 else ("m²", area_km2 * 1_000_000)
        summary = f"多边形面积 {value:.2f} {unit} (共 {len(coordinates)} 个顶点)"
        return {
            "success": True,
            "summary": summary,
            "area_km2": area_km2,
            "point_count": len(coordinates),
            "command": "draw_measurement",
            "params": {
                "shape": "polygon",
                "coordinates": coordinates,
                "label": label or f"{value:.2f} {unit}",
            },
        }

    @tool(
        registry,
        name="add_marker",
        description=(
            "在地图上撒一个 pin 标注。常用于：『把这个点钉一下』『标记一下我说的位置』。"
            "\n何时用：要在地图上长期可见某个坐标 (分析结果点、检索命中点、用户提到的地标)。"
            "\n何时不用：只是飞过去看一眼 — 用 fly_to_location；想画一条线 — 用 measure_distance。"
            "\n关键约束：经纬度十进制度；color 默认红 #ef4444。多次调用累计添加，用 clear_markers 一次性清空。"
        ),
        args_model=AddMarkerArgs,
    )
    def add_marker(
        longitude: float,
        latitude: float,
        label: Optional[str] = None,
        color: str = "#ef4444",
    ) -> dict:
        if not (-180 <= longitude <= 180) or not (-90 <= latitude <= 90):
            return {"error": f"坐标越界 ({longitude}, {latitude})"}
        return {
            "success": True,
            "summary": f"已在 ({longitude:.4f}, {latitude:.4f}) 添加 pin{'：' + label if label else ''}",
            "command": "add_marker",
            "params": {
                "longitude": longitude,
                "latitude": latitude,
                "label": label,
                "color": color,
            },
        }

    @tool(
        registry,
        name="clear_annotations",
        description=(
            "清空地图上所有由 add_marker / measure_distance / measure_area 留下的标注。"
            "\n何时用：用户说『清掉刚才的标记』『把测量线擦了』『重新开始』。"
            "\n何时不用：要保留部分标注 — 当前实现是全清，不支持精细化删除。"
        ),
    )
    def clear_annotations() -> dict:
        return {
            "success": True,
            "summary": "已清空所有地图标注",
            "command": "clear_annotations",
            "params": {},
        }
