"""天地图 (Tianditu) provider 实现 — POI / 地理编码 / 行政区划。

M2 拆分：原 chinese_maps.py 的所有 _*_tianditu 函数。返回坐标系一律 WGS84，
所有 _tianditu_get 已强制带浏览器 UA 头绕过 WAF 418。
"""
from typing import Any, List, Optional
import json
import logging

from app.core.config import settings

from app.tools.chinese_maps.http import _tianditu_get

logger = logging.getLogger(__name__)


async def _district_tianditu(keywords: str, level: str, return_geometry: str = "point") -> dict:
    post_str = json.dumps({
        "searchWord": keywords,
        "searchType": "1",
        "needSubInfo": "true",
        "needAll": "false",
        "needPolygon": "false",
        "needPre": "true",
    }, ensure_ascii=False)
    data = await _tianditu_get("/administrative", {"postStr": post_str})
    if "error" in data:
        return data
    districts = data.get("data", [])
    if isinstance(districts, dict):
        districts = [districts]
    features = []
    for d in districts:
        lng = d.get("lnt", 0)
        lat = d.get("lat", 0)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("adminType", d.get("level", "")),
                "code": str(d.get("cityCode", "")),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "tianditu"}


# ── Phase 2 helpers: distance matrix, isochrone ─────────────────



async def _district_tianditu_v2(keywords: str, child_level: int, return_polygon: bool) -> dict:
    """天地图行政区划查询 V2 (支持边界轮廓)"""
    post_str = json.dumps({
        "searchWord": keywords,
        "searchType": "1",
        "needSubInfo": "true" if child_level > 0 else "false",
        "needAll": "false",
        "needPolygon": "true" if return_polygon else "false",
        "needPre": "true",
    }, ensure_ascii=False)
    
    data = await _tianditu_get("/administrative", {"postStr": post_str})
    if "error" in data:
        return data
    
    # 状态码 100 表示成功
    if str(data.get("status")) != "100":
        return {"error": f"Tianditu: {data.get('msg', '查询失败')}"}
        
    districts = data.get("data", [])
    if isinstance(districts, dict):
        districts = [districts]
        
    features = []
    
    def _parse_points(points_str):
        if not points_str: return None
        try:
            polygons = []
            for poly_str in points_str.split("|"):
                coords = []
                for pair in poly_str.split(";"):
                    parts = pair.split(",")
                    if len(parts) >= 2:
                        coords.append([float(parts[0]), float(parts[1])])
                if len(coords) >= 3:
                    if coords[0] != coords[-1]:
                        coords.append(coords[0])
                    polygons.append([coords])
            if not polygons: return None
            if len(polygons) == 1:
                return {"type": "Polygon", "coordinates": polygons[0]}
            return {"type": "MultiPolygon", "coordinates": polygons}
        except Exception as e:
            return None

    for d in districts:
        # 主项
        main_geom = _parse_points(d.get("points", ""))
        if not main_geom:
            lng, lat = float(d.get("lnt", 0)), float(d.get("lat", 0))
            main_geom = {"type": "Point", "coordinates": [lng, lat]}
            
        features.append({
            "type": "Feature",
            "geometry": main_geom,
            "properties": {
                "name": d.get("name", ""),
                "cityCode": d.get("cityCode", ""),
                "level": d.get("adminType", ""),
                "is_parent": True
            },
        })
        
        # 下级项 (如果存在且 child_level > 0)
        if child_level > 0:
            child_data = d.get("child", [])
            for c in child_data:
                # 注意：天地图 child 节点通常不带 points，除非 searchType 设为特定值
                # 这里我们先尝试解析，如果没有则存为点
                c_geom = _parse_points(c.get("points", ""))
                if not c_geom:
                    c_lng, c_lat = float(c.get("lnt", 0)), float(c.get("lat", 0))
                    c_geom = {"type": "Point", "coordinates": [c_lng, c_lat]}
                
                features.append({
                    "type": "Feature",
                    "geometry": c_geom,
                    "properties": {
                        "name": c.get("name", ""),
                        "cityCode": c.get("cityCode", ""),
                        "level": c.get("adminType", ""),
                        "is_child": True,
                        "parent_name": d.get("name")
                    }
                })

    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "tianditu",
    }



async def _geocode_tianditu(address: str, city: str) -> dict:
    ds = json.dumps({"keyWord": address}, ensure_ascii=False)
    data = await _tianditu_get("/geocoder", {"ds": ds})
    if "error" in data:
        return data
    result = data.get("result", {})
    loc = result.get("location", {})
    lng, lat = loc.get("lon", 0), loc.get("lat", 0)
    return {
        "results": [{
            "location": [lng, lat],
            "formatted_address": address,
            "level": result.get("level", ""),
        }],
        "count": 1,
        "provider": "tianditu",
    }



async def _reverse_geocode_tianditu(lng: float, lat: float) -> dict:
    post_str = json.dumps({"lon": lng, "lat": lat, "ver": 1})
    data = await _tianditu_get("/geocoder", {"postStr": post_str, "type": "geocode"})
    if "error" in data:
        return data
    result = data.get("result", {})
    addr = result.get("addressComponent", {})
    return {
        "formatted_address": result.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("county", ""),
        "street": addr.get("street", ""),
        "street_number": addr.get("streetNumber", ""),
        "provider": "tianditu",
    }



async def _search_poi_around_tianditu(
    center: list, radius_m: int, keyword: str, types: str, limit: int
) -> dict:
    payload = {
        "keyWord": keyword or types,
        "queryRadius": str(radius_m),
        "pointLonlat": f"{center[0]},{center[1]}",
        "queryType": "3",  # 周边搜索
        "start": "0",
        "count": str(min(limit, 50)),
    }
    post_str = json.dumps(payload, ensure_ascii=False)
    data = await _tianditu_get("/search", {"postStr": post_str, "type": "query"})
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        lonlat = p.get("lonlat", "").split(" ")
        if len(lonlat) != 2:
            continue
        lng, lat = float(lonlat[0]), float(lonlat[1])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "tel": p.get("phone", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "center": center,
        "radius_m": radius_m,
        "provider": "tianditu",
    }



async def _search_poi_tianditu(keyword: str, city: str, limit: int) -> dict:
    # specifyAdminCode 必须是数字行政区代码（如 "110000"），中文名传进去等于无过滤
    payload: dict = {
        "keyWord": keyword,
        "level": "12",
        "mapBound": "-180,-90,180,90",
        "queryType": "1",
        "start": "0",
        "count": str(min(limit, 50)),
    }
    if city and city.isdigit():
        payload["specifyAdminCode"] = city
    post_str = json.dumps(payload, ensure_ascii=False)
    data = await _tianditu_get("/search", {"postStr": post_str, "type": "query"})
    if "error" in data:
        return data
    pois = data.get("pois", [])
    if not pois and isinstance(data.get("resultType"), int):
        return {"type": "FeatureCollection", "features": [], "count": 0, "provider": "tianditu"}
    features = []
    for p in pois[:limit]:
        lonlat = p.get("lonlat", "").split(" ")
        if len(lonlat) != 2:
            continue
        lng, lat = float(lonlat[0]), float(lonlat[1])
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "tel": p.get("phone", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "tianditu",
    }


