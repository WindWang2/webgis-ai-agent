"""百度 (Baidu) provider 实现 — POI / 地理编码 / 路径规划 / 行政区 / 距离矩阵 / 周边 / 多边形搜索。

M2 拆分：原 chinese_maps.py 的所有 _*_baidu 函数。
"""
from typing import Any, List, Optional
import json
import logging

from app.core.config import settings
from app.utils.coord_transform import wgs84_to_bd09, bd09_to_wgs84

from app.tools.chinese_maps.http import _baidu_get

logger = logging.getLogger(__name__)


async def _distance_matrix_baidu(
    origins: list[list],
    destinations: list[list],
    mode: str,
) -> dict:
    """Baidu v2 direction Matrix API — 一次请求完成全量 OD 计算。"""

    def _bd_fmt(lng: float, lat: float) -> str:
        # WGS84 → BD09，然后按"纬度,经度"格式提交给百度
        bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
        return f"{bd_lat},{bd_lng}"

    origin_str = "|".join(_bd_fmt(lo, la) for lo, la in origins)
    dest_str = "|".join(_bd_fmt(ld, la) for ld, la in destinations)
    mode_map = {"driving": "car", "walking": "foot", "riding": "bike"}
    params = {
        "origin": origin_str,
        "destination": dest_str,
        "mode": mode_map.get(mode, "car"),
    }
    data = await _baidu_get("/direction/v2/matrix", params)
    if "error" in data:
        return data

    result = data.get("result", {})
    rows = result.get("rows", [])
    matrix = []
    for ri, row in enumerate(rows):
        row_dist = []
        for ci, elem in enumerate(row.get("elements", [])):
            row_dist.append({
                "origin_index": ri,
                "dest_index": ci,
                "distance_km": elem.get("distance", {}).get("value", 0) / 1000.0,
                "duration_sec": elem.get("duration", {}).get("value", 0),
            })
        matrix.append(row_dist)
    return {
        "matrix": matrix,
        "origins_count": len(origins),
        "dests_count": len(destinations),
        "mode": mode,
        "provider": "baidu",
    }



async def _district_baidu(keywords: str, level: str, return_geometry: str = "point") -> dict:
    params = {"q": keywords}
    data = await _baidu_get("/api/v2/administrative", params)
    if "error" in data:
        return data
    districts = data.get("results", [])
    features = []
    for d in districts:
        loc = d.get("location", {})
        bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("level", ""),
                "code": str(d.get("code", "")),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features), "provider": "baidu"}


# ── Tianditu (天地图) provider functions ───────────────────────
# CGCS2000 ≈ WGS84, no coordinate transformation needed



async def _geocode_baidu(address: str, city: str) -> dict:
    params = {"address": address}
    if city:
        params["city"] = city
    data = await _baidu_get("/geocoding/v3/", params)
    if "error" in data:
        return data
    result = data.get("result", {})
    loc = result.get("location", {})
    bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
    lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
    # Baidu geocoding v3 不返回 canonical 地址，回显用户输入并把精度等级单独暴露
    return {
        "results": [{
            "location": [lng, lat],
            "formatted_address": address,
            "precision_level": result.get("level", ""),
            "confidence": result.get("confidence"),
            "comprehension": result.get("comprehension"),
            "province": "",
            "city": city,
            "district": "",
            "adcode": str(result.get("cityCode", "")),
        }],
        "count": 1,
        "provider": "baidu",
    }



async def _input_tips_baidu(
    keyword: str, city: str, location: Optional[list]
) -> dict:
    params = {"query": keyword, "region": city or "全国"}
    if location and len(location) == 2:
        bd_lng, bd_lat = wgs84_to_bd09(location[0], location[1])
        params["location"] = f"{bd_lat},{bd_lng}"
    data = await _baidu_get("/place/v2/suggestion", params)
    if "error" in data:
        return data
    suggestions = data.get("result", [])
    out = []
    for s in suggestions:
        loc = s.get("location") or {}
        coords = None
        if loc.get("lng") and loc.get("lat"):
            lng, lat = bd09_to_wgs84(loc["lng"], loc["lat"])
            coords = [lng, lat]
        out.append({
            "name": s.get("name", ""),
            "district": s.get("district", ""),
            "address": s.get("address", ""),
            "location": coords,
            "adcode": str(s.get("city_id", "")),
        })
    return {"tips": out, "count": len(out), "provider": "baidu"}



async def _reverse_geocode_baidu(lng: float, lat: float) -> dict:
    bd_lng, bd_lat = wgs84_to_bd09(lng, lat)
    params = {"location": f"{bd_lat},{bd_lng}", "extensions_poi": 1}
    data = await _baidu_get("/reverse_geocoding/v3/", params)
    if "error" in data:
        return data
    r = data.get("result", {})
    addr = r.get("addressComponent", {})
    pois = r.get("pois", [])[:5]
    return {
        "formatted_address": r.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("district", ""),
        "street": addr.get("street", ""),
        "street_number": addr.get("street_number", ""),
        "nearby_pois": [{"name": p.get("name"), "distance": p.get("distance")} for p in pois],
        "provider": "baidu",
    }



async def _route_baidu(origin: list, dest: list, mode: str, city: str) -> dict:
    mode_map = {"driving": "driving", "walking": "walking", "cycling": "riding", "transit": "transit"}
    endpoint = mode_map.get(mode, "driving")
    o_bd = wgs84_to_bd09(origin[0], origin[1])
    d_bd = wgs84_to_bd09(dest[0], dest[1])
    params = {"origin": f"{o_bd[0]},{o_bd[1]}", "destination": f"{d_bd[0]},{d_bd[1]}"}
    if mode == "transit" and city:
        params["city"] = city
    data = await _baidu_get(f"/directionlite/v1/{endpoint}", params)
    if "error" in data:
        return data
    route = data.get("result", {}).get("routes", [])
    if not route:
        return {"error": "未找到路线"}
    r = route[0]
    steps_out = []
    polyline = []
    for step in r.get("steps", []):
        steps_out.append({
            "instruction": step.get("instruction", ""),
            "distance": step.get("distance", "0"),
            "duration": step.get("duration", "0"),
        })
        for loc in step.get("path", "").split(";"):
            parts = loc.split(",")
            if len(parts) == 2:
                bd_lng, bd_lat = float(parts[0]), float(parts[1])
                lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
                polyline.append([lng, lat])
    return {
        "distance_m": int(r.get("distance", 0)),
        "duration_s": int(r.get("duration", 0)),
        "polyline": polyline,
        "steps": steps_out,
        "provider": "baidu",
    }



async def _search_poi_around_baidu(
    center: list, radius_m: int, keyword: str, types: str, limit: int
) -> dict:
    bd_lng, bd_lat = wgs84_to_bd09(center[0], center[1])
    params = {
        "query": keyword or types,
        "location": f"{bd_lat},{bd_lng}",
        "radius": str(radius_m),
        "page_size": str(min(limit, 20)),
        "scope": "2",
    }
    data = await _baidu_get("/place/v2/search", params)
    if "error" in data:
        return data
    pois = data.get("results", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", {})
        b_lng, b_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(b_lng, b_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "distance_m": int(p.get("detail_info", {}).get("distance", 0) or 0),
                "tel": p.get("telephone", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "center": center,
        "radius_m": radius_m,
        "provider": "baidu",
    }



async def _search_poi_baidu(keyword: str, city: str, limit: int) -> dict:
    params = {"query": keyword, "region": city or "全国", "page_size": str(min(limit, 20))}
    data = await _baidu_get("/place/v2/search", params)
    if "error" in data:
        return data
    pois = data.get("results", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", {})
        bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(bd_lng, bd_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "tel": p.get("telephone", ""),
                "city": p.get("city", ""),
                "district": p.get("area", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "baidu",
    }



async def _search_poi_polygon_baidu(
    polygon: list, keyword: str, types: str, limit: int
) -> dict:
    # Baidu place v2 不接收 polygon 直接参数；用 polygon 外接 bbox 近似
    lngs = [p[0] for p in polygon]
    lats = [p[1] for p in polygon]
    w, e = min(lngs), max(lngs)
    s, n = min(lats), max(lats)
    sw_bd = wgs84_to_bd09(w, s)
    ne_bd = wgs84_to_bd09(e, n)
    params = {
        "query": keyword or types,
        "bounds": f"{sw_bd[1]},{sw_bd[0]},{ne_bd[1]},{ne_bd[0]}",
        "page_size": str(min(limit, 20)),
    }
    data = await _baidu_get("/place/v2/search", params)
    if "error" in data:
        return data
    pois = data.get("results", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", {})
        b_lng, b_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = bd09_to_wgs84(b_lng, b_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "tel": p.get("telephone", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "polygon": polygon,
        "provider": "baidu",
        "note": "Baidu 用 polygon 外接矩形 (bbox) 近似查询",
    }


