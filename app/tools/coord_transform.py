"""中国坐标系互转工具 (WGS84 ↔ GCJ-02 ↔ BD-09)。

中国 GIS 的核心痛点：
- WGS84  — 国际标准、GPS、OSM、Sentinel 等遥感数据、Nominatim 反查
- GCJ-02 — 高德地图 / 腾讯地图 / 谷歌中国版的偏移坐标系（"火星坐标"）
- BD-09  — 百度地图的二次偏移坐标系

三套坐标系在同一城市内可错位 200-500m，混用直接造成图层错位、缓冲区分析失真。
本工具支持把整张 GeoJSON 图层（点/线/面）一次性平移到目标坐标系。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.tools.registry import ToolRegistry, tool
from app.utils.coord_transform import (
    wgs84_to_gcj02,
    gcj02_to_wgs84,
    gcj02_to_bd09,
    bd09_to_gcj02,
)
from app.lib.geo_processor.core import safe_parse

logger = logging.getLogger(__name__)

_SUPPORTED = {"wgs84", "gcj02", "bd09"}


def _transform_point(lng: float, lat: float, src: str, dst: str) -> tuple[float, float]:
    """单点转换：先 normalize 到 gcj02 中转再发散到目标。"""
    if src == dst:
        return lng, lat
    # 先归一化到 gcj02
    if src == "wgs84":
        lng, lat = wgs84_to_gcj02(lng, lat)
    elif src == "bd09":
        lng, lat = bd09_to_gcj02(lng, lat)
    # 再从 gcj02 散到目标
    if dst == "wgs84":
        return gcj02_to_wgs84(lng, lat)
    if dst == "bd09":
        return gcj02_to_bd09(lng, lat)
    return lng, lat  # dst == gcj02


def _walk_coords(coords: Any, src: str, dst: str) -> Any:
    """递归走 GeoJSON coordinates 数组（兼容 Point/LineString/Polygon/Multi*）。"""
    if not isinstance(coords, list) or not coords:
        return coords
    # 判断是不是末端的 [lng, lat] 对
    if isinstance(coords[0], (int, float)) and len(coords) >= 2:
        lng, lat = _transform_point(float(coords[0]), float(coords[1]), src, dst)
        rest = coords[2:]  # 保留 z / m
        return [lng, lat, *rest]
    return [_walk_coords(c, src, dst) for c in coords]


def _transform_geometry(geom: dict, src: str, dst: str) -> dict:
    if not isinstance(geom, dict):
        return geom
    new = dict(geom)
    if "coordinates" in new:
        new["coordinates"] = _walk_coords(new["coordinates"], src, dst)
    if "geometries" in new:  # GeometryCollection
        new["geometries"] = [_transform_geometry(g, src, dst) for g in new["geometries"]]
    return new


def register_coord_transform_tools(registry: ToolRegistry):
    """注册中国坐标系互转工具。"""

    @tool(registry, name="transform_coordinates",
          tier=2, domains=["chinese"],
          description=(
              "中国坐标系互转 (WGS84 ↔ GCJ-02 ↔ BD-09)：批量把一张 GeoJSON 图层从一种坐标系平移到另一种。"
              "\n何时用：(1) 上传的数据来自高德 (GCJ-02) 但要叠加 OSM (WGS84) — 错位 ~300m；"
              "(2) 百度地图 (BD-09) POI 要与 Sentinel-2 (WGS84) 影像叠加；"
              "(3) 客户给的 Excel 坐标说不清是哪个系，看在地图上偏移方向反推后转回正常系。"
              "\n何时不用：(1) 数据本来就在同一坐标系 — 不要瞎转；"
              "(2) 用 Amap 自带工具 (search_poi、reverse_geocode_cn) — 它们返回 GCJ-02，前端基础已处理；"
              "(3) 中国境外的数据 — 函数会原样返回（GCJ-02/BD-09 偏移仅在国内生效）。"
              "\n关键约束：from_crs/to_crs ∈ {wgs84, gcj02, bd09}，**大小写不敏感**；"
              "支持 Point/LineString/Polygon/Multi* 及其 FeatureCollection 容器；"
              "保留 properties 与 z/m 维度。"
          ),
          param_descriptions={
              "geojson": "输入图层 GeoJSON 或引用(ref:xxx)",
              "from_crs": "源坐标系：'wgs84' | 'gcj02' | 'bd09'",
              "to_crs": "目标坐标系：'wgs84' | 'gcj02' | 'bd09'",
          })
    def transform_coordinates(geojson: Any, from_crs: str, to_crs: str) -> dict:
        src = (from_crs or "").lower().replace("-", "")
        dst = (to_crs or "").lower().replace("-", "")
        if src not in _SUPPORTED or dst not in _SUPPORTED:
            return {
                "success": False,
                "error": f"不支持的坐标系 from={from_crs} to={to_crs}。"
                         f"必须是 {sorted(_SUPPORTED)} 之一。",
            }

        data = safe_parse(geojson)
        if not data:
            return {"success": False, "error": "无法解析输入 GeoJSON"}

        if src == dst:
            return {
                "success": True,
                "data": data,
                "summary": f"源 = 目标坐标系 ({src})，原样返回。",
            }

        geo_type = data.get("type")
        if geo_type == "FeatureCollection":
            new_features = []
            for feat in data.get("features", []) or []:
                new_feat = dict(feat)
                new_feat["geometry"] = _transform_geometry(feat.get("geometry") or {}, src, dst)
                new_features.append(new_feat)
            out = {"type": "FeatureCollection", "features": new_features}
        elif geo_type == "Feature":
            out = dict(data)
            out["geometry"] = _transform_geometry(data.get("geometry") or {}, src, dst)
        else:
            # 裸 Geometry
            out = _transform_geometry(data, src, dst)

        return {
            "success": True,
            "data": out,
            "summary": f"已将图层从 {src} 转换为 {dst}",
            "metadata": {"from_crs": src, "to_crs": dst},
        }


def register_epsg_transform_tools(registry: ToolRegistry):
    """Register general-purpose EPSG-to-EPSG reprojection tool."""

    @tool(registry, name="reproject_coordinates",
          tier=2,
          description=(
              "通用坐标参考系 (CRS) 转换：将 GeoJSON 图层从一种 EPSG 坐标系重投影到另一种。"
              "\n何时用：(1) 上传的 Shapefile/GPKG 使用了地方坐标系（如 CGCS2000 / EPSG:4490），"
              "需要转为 WGS84 (EPSG:4326) 以叠加底图；"
              "(2) 分析结果需要转到 UTM 投影以计算精确面积/距离；"
              "(3) 客户要求输出特定坐标系的成果。"
              "\n何时不用：(1) 中国坐标偏移 (WGS84↔GCJ-02↔BD-09) — 用 transform_coordinates；"
              "(2) 数据已经在目标 CRS — 不要重复投影。"
              "\n参数格式：EPSG 代码，如 'EPSG:4326'、'EPSG:32650'。"
          ),
          param_descriptions={
              "geojson": "输入图层 GeoJSON 或引用(ref:xxx)",
              "from_epsg": "源坐标系 EPSG 代码，如 'EPSG:4326'",
              "to_epsg": "目标坐标系 EPSG 代码，如 'EPSG:32650'",
          })
    def reproject_coordinates(geojson: Any, from_epsg: str, to_epsg: str) -> dict:
        if from_epsg == to_epsg:
            data = safe_parse(geojson)
            return {
                "success": True,
                "data": data or geojson,
                "summary": f"源 = 目标 CRS ({from_epsg})，原样返回。",
            }

        try:
            import pyproj
            from shapely.geometry import shape as to_shape
            transformer = pyproj.Transformer.from_crs(
                pyproj.CRS(from_epsg), pyproj.CRS(to_epsg), always_xy=True
            )

            data = safe_parse(geojson)
            if not data:
                return {"success": False, "error": "无法解析输入 GeoJSON"}

            def reproject_coords(coords):
                if not isinstance(coords, list) or not coords:
                    return coords
                if isinstance(coords[0], (int, float)) and len(coords) >= 2:
                    x, y = transformer.transform(float(coords[0]), float(coords[1]))
                    return [x, y, *coords[2:]]
                return [reproject_coords(c) for c in coords]

            def reproject_geom(geom):
                if not isinstance(geom, dict):
                    return geom
                new = dict(geom)
                if "coordinates" in new:
                    new["coordinates"] = reproject_coords(new["coordinates"])
                if "geometries" in new:
                    new["geometries"] = [reproject_geom(g) for g in new["geometries"]]
                return new

            geo_type = data.get("type")
            if geo_type == "FeatureCollection":
                new_features = []
                for feat in data.get("features", []) or []:
                    nf = dict(feat)
                    nf["geometry"] = reproject_geom(feat.get("geometry") or {})
                    new_features.append(nf)
                out = {"type": "FeatureCollection", "features": new_features}
            elif geo_type == "Feature":
                out = dict(data)
                out["geometry"] = reproject_geom(data.get("geometry") or {})
            else:
                out = reproject_geom(data)

            return {
                "success": True,
                "data": out,
                "summary": f"已将图层从 {from_epsg} 重投影到 {to_epsg}",
                "metadata": {"from_epsg": from_epsg, "to_epsg": to_epsg},
            }
        except Exception as e:
            err = str(e).lower()
            if "crs" in err or "epsg" in err or "proj" in err:
                return {"success": False, "error": f"不支持的 CRS: {from_epsg} → {to_epsg} ({e})"}
            return {"success": False, "error": f"重投影失败: {e}"}
