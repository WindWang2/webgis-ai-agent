"""高德 (Amap) provider 实现 — POI 搜索 / 地理编码 / 路径规划 / 行政区划 / 等时圈 / 实时路况。

M2 拆分：原 chinese_maps.py 的所有 _*_amap 函数 + 等时圈实现（依赖 amap 路径距离）。
"""
import json
import logging
from typing import Any, List, Optional

from app.core.config import settings
from app.utils.coord_transform import wgs84_to_gcj02, gcj02_to_wgs84

from app.tools.chinese_maps.http import _amap_get, _speed_mps

logger = logging.getLogger(__name__)


async def _distance_matrix_amap(
    origins: list[list],
    destinations: list[list],
    mode: str,
) -> dict:
    """Amap 距离矩阵。

    - driving / walking 走 `/v3/distance`（一次请求完成全量 OD 计算）
        - type=1 驾车，type=3 步行
    - riding 该批量接口不支持，回退到 N×M 并发调用 /direction/bicycling

    所有坐标在请求前 WGS84 → GCJ-02。
    """
    # ── driving / walking：批量接口
    if mode in ("driving", "walking"):
        origin_str = ";".join(
            f"{wgs84_to_gcj02(lng, lat)[0]},{wgs84_to_gcj02(lng, lat)[1]}"
            for lng, lat in origins
        )
        dest_str = ";".join(
            f"{wgs84_to_gcj02(lng, lat)[0]},{wgs84_to_gcj02(lng, lat)[1]}"
            for lng, lat in destinations
        )
        params = {
            "origins": origin_str,
            "destination": dest_str,
            "type": "1" if mode == "driving" else "3",
        }
        data = await _amap_get("/distance", params)
        if "error" in data:
            return data

        results = data.get("results", [])
        matrix: list[list[dict | None]] = [
            [None] * len(destinations) for _ in range(len(origins))
        ]
        for item in results:
            # Amap origin_id/dest_id 从 1 开始计数
            oi = int(item.get("origin_id", 0)) - 1
            di = int(item.get("dest_id", 0)) - 1
            if 0 <= oi < len(origins) and 0 <= di < len(destinations):
                matrix[oi][di] = {
                    "origin_index": oi,
                    "dest_index": di,
                    "distance_km": float(item.get("distance", 0)) / 1000.0,
                    "duration_sec": int(item.get("duration", 0)),
                }
        return {
            "matrix": matrix,
            "origins_count": len(origins),
            "dests_count": len(destinations),
            "mode": mode,
            "provider": "amap",
        }

    # ── riding：批量接口不支持 → N×M 并发兜底
    semaphore = asyncio.Semaphore(6)

    async def _one(oi: int, di: int) -> dict | None:
        async with semaphore:
            dist_m = await _get_route_distance_amap(origins[oi], destinations[di], "riding")
            if dist_m <= 0:
                return None
            # 骑行速度估算 4.2 m/s 给个粗略 duration
            return {
                "origin_index": oi,
                "dest_index": di,
                "distance_km": dist_m / 1000.0,
                "duration_sec": int(dist_m / 4.2),
            }

    pairs = [(oi, di) for oi in range(len(origins)) for di in range(len(destinations))]
    flat = await asyncio.gather(*[_one(oi, di) for oi, di in pairs])
    matrix = [[None] * len(destinations) for _ in range(len(origins))]
    for (oi, di), cell in zip(pairs, flat):
        matrix[oi][di] = cell
    return {
        "matrix": matrix,
        "origins_count": len(origins),
        "dests_count": len(destinations),
        "mode": mode,
        "provider": "amap",
        "note": "Amap 批量距离接口不支持骑行，已通过 N×M 并发路径规划兜底",
    }



async def _district_amap(keywords: str, level: str, return_geometry: str = "point") -> dict:
    params = {"keywords": keywords, "subdistrict": "1", "extensions": "all" if return_geometry == "polygon" else "base"}
    # subdistrict 参数含义：0:不返回下级, 1:返回下级, 2:返回下级及其下级, 3:返回下级及其下级及其下级
    # 我们默认设为 1 以便用户能看到下级行政区列表
    data = await _amap_get("/config/district", params)
    if "error" in data:
        return data
    districts = data.get("districts", [])
    features = []
    for d in districts:
        center = d.get("center", "").split(",")
        lng, lat = (gcj02_to_wgs84(float(center[0]), float(center[1])) if len(center) == 2 else (0, 0))

        if return_geometry == "polygon":
            # 高德行政区划边界字段名为 'polyline'
            polyline_str = d.get("polyline", "")
            if polyline_str:
                from shapely.geometry import Polygon, MultiPolygon
                from shapely import simplify
                
                polygons = []
                # 高德 polyline 可能包含多个部分，以 | 分隔
                for part in polyline_str.split("|"):
                    coords = [
                        [float(v) for v in pt.split(",")]
                        for pt in part.split(";") if pt
                    ]
                    if len(coords) >= 3:
                        wgs84_coords = [gcj02_to_wgs84(lon, lat) for lon, lat in coords]
                        # 闭合环
                        if wgs84_coords[0] != wgs84_coords[-1]:
                            wgs84_coords.append(wgs84_coords[0])
                        polygons.append(Polygon(wgs84_coords))
                
                if not polygons:
                    geometry = {"type": "Point", "coordinates": [lng, lat]}
                else:
                    if len(polygons) == 1:
                        geom_obj = polygons[0]
                    else:
                        geom_obj = MultiPolygon(polygons)
                    
                    # 简化几何以提高传输效率
                    simplified = simplify(geom_obj, tolerance=0.0005, preserve_topology=True)
                    geometry = simplified.__geo_interface__
            else:
                geometry = {"type": "Point", "coordinates": [lng, lat]}
        else:
            geometry = {"type": "Point", "coordinates": [lng, lat]}

        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "name": d.get("name", ""),
                "level": d.get("level", ""),
                "adcode": d.get("adcode", ""),
                "citycode": d.get("citycode", ""),
            },
        })
    return {"type": "FeatureCollection", "features": features, "count": len(features),
            "provider": "amap", "geometry_type": features[0]["geometry"]["type"] if features else "Point"}



async def _geocode_amap(address: str, city: str) -> dict:
    params = {"address": address}
    if city:
        params["city"] = city
    data = await _amap_get("/geocode/geo", params)
    if "error" in data:
        return data
    geocodes = data.get("geocodes", [])
    if not geocodes:
        return {"results": [], "count": 0}
    results = []
    for g in geocodes:
        loc = g.get("location", "").split(",")
        if len(loc) != 2:
            continue
        gcj_lng, gcj_lat = float(loc[0]), float(loc[1])
        lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
        results.append({
            "location": [lng, lat],
            "formatted_address": g.get("formatted_address", ""),
            "province": g.get("province", ""),
            "city": g.get("city", ""),
            "district": g.get("district", ""),
            "adcode": g.get("adcode", ""),
        })
    return {"results": results, "count": len(results), "provider": "amap"}



async def _get_route_distance_amap(
    origin: list,
    destination: list,
    mode: str,
) -> float:
    """调用 Amap 路径规划 API，返回两点间单程距离（米）。用于等时圈半径探测。"""
    try:
        og_lng, og_lat = origin
        dg_lng, dg_lat = destination
        og_gcj = wgs84_to_gcj02(og_lng, og_lat)
        dg_gcj = wgs84_to_gcj02(dg_lng, dg_lat)
        o_str = f"{og_gcj[0]},{og_gcj[1]}"
        d_str = f"{dg_gcj[0]},{dg_gcj[1]}"

        if mode == "walking":
            params = {"origin": o_str, "destination": d_str}
            data = await _amap_get("/direction/walking", params)
        elif mode == "riding":
            params = {"origin": o_str, "destination": d_str}
            data = await _amap_get("/direction/bicycling", params)
        else:  # driving
            params = {"origin": o_str, "destination": d_str, "strategy": "10"}
            data = await _amap_get("/direction/driving", params)

        if "error" in data:
            return 0.0
        route = data.get("route", {})
        paths = route.get("paths", [])
        if not paths:
            return 0.0
        return float(paths[0].get("distance", 0))
    except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError):
        return 0.0


# ── Math / angle utilities ──────────────────────────────────────



async def _input_tips_amap(
    keyword: str, city: str, location: Optional[list]
) -> dict:
    params = {"keywords": keyword}
    if city:
        params["city"] = city
        params["citylimit"] = "true"
    if location and len(location) == 2:
        lng, lat = wgs84_to_gcj02(location[0], location[1])
        params["location"] = f"{lng},{lat}"
    data = await _amap_get("/assistant/inputtips", params)
    if "error" in data:
        return data
    tips = data.get("tips", [])
    out = []
    for t in tips:
        loc_str = t.get("location", "")
        coords = None
        if isinstance(loc_str, str) and "," in loc_str:
            parts = loc_str.split(",")
            if len(parts) == 2:
                try:
                    lng, lat = gcj02_to_wgs84(float(parts[0]), float(parts[1]))
                    coords = [lng, lat]
                except ValueError:
                    coords = None
        out.append({
            "name": t.get("name", ""),
            "district": t.get("district", ""),
            "address": t.get("address", ""),
            "location": coords,
            "adcode": t.get("adcode", ""),
        })
    return {"tips": out, "count": len(out), "provider": "amap"}



async def _isochrone_analysis(
    provider: str,
    center: list,
    minutes: int,
    mode: str,
) -> dict:
    """沿 N 个方向调用路径规划 API，收集各方向在 `minutes` 时间内的最远到达点，用 Convex Hull 近似等时圈。"""
    import math

    num_radials = 12  # 每30°一条射线
    km_scale = {"driving": 1.0, "walking": 0.06, "riding": 0.35}[mode]
    angle_step = 2 * math.pi / num_radials

    # 生成候选径向目的地（在中心点周围大致方向）
    async def _radial_point(angle: float) -> tuple[float, float]:
        # 用单位向量 × 固定半径来确定方向，再做线性缩放
        probe_lng = center[0] + 0.01 * math.cos(angle) * km_scale
        probe_lat = center[1] + 0.01 * math.sin(angle) * km_scale

        try:
            dist_m = await _get_route_distance_amap(center, [probe_lng, probe_lat], mode)
            ratio = (minutes * 60 * _speed_mps(mode)) / max(dist_m, 1)
            capped_ratio = min(ratio, 1.0)  # 不超过探测器本身的位置
            return (
                center[0] + (probe_lng - center[0]) * capped_ratio,
                center[1] + (probe_lat - center[1]) * capped_ratio,
            )
        except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            # 回退：用均匀半径圆上的点
            fallback_radius_m = minutes * 60 * _speed_mps(mode)
            return (
                center[0] + fallback_radius_m * math.cos(angle) / 111000,
                center[1] + fallback_radius_m * math.sin(angle) / 111000,
            )

    semaphore = asyncio.Semaphore(6)
    angles = [angle_step * i for i in range(num_radials)]

    async def _guarded_radial(angle: float) -> tuple[float, float]:
        async with semaphore:
            return await _radial_point(angle)

    pts = await asyncio.gather(*[_guarded_radial(a) for a in angles])

    all_points = list(pts)

    if len(all_points) >= 3:
        from shapely.geometry import MultiPoint
        hull = MultiPoint(all_points).convex_hull
        geometry = hull.__geo_interface__
    else:
        geometry = {"type": "Point", "coordinates": center}

    return {
        "type": "Feature",
        "geometry": geometry,
        "properties": {
            "center": center,
            "minutes": minutes,
            "mode": mode,
            "provider": provider,
            "radius_m": minutes * 60 * _speed_mps(mode),
        },
    }



async def _reverse_geocode_amap(lng: float, lat: float) -> dict:
    gcj_lng, gcj_lat = wgs84_to_gcj02(lng, lat)
    params = {"location": f"{gcj_lng},{gcj_lat}", "extensions": "all"}
    data = await _amap_get("/geocode/regeo", params)
    if "error" in data:
        return data
    r = data.get("regeocode", {})
    addr = r.get("addressComponent", {})
    pois = r.get("pois", [])[:5]
    return {
        "formatted_address": r.get("formatted_address", ""),
        "province": addr.get("province", ""),
        "city": addr.get("city", ""),
        "district": addr.get("district", ""),
        "street": addr.get("streetNumber", {}).get("street", ""),
        "street_number": addr.get("streetNumber", {}).get("number", ""),
        "nearby_pois": [{"name": p.get("name"), "distance": p.get("distance")} for p in pois],
        "provider": "amap",
    }



async def _route_amap(origin: list, dest: list, mode: str, city: str) -> dict:
    mode_map = {"driving": "driving", "walking": "walking", "cycling": "bicycling", "transit": "transit/integrated"}
    endpoint = mode_map.get(mode, "driving")
    o_gcj = wgs84_to_gcj02(origin[0], origin[1])
    d_gcj = wgs84_to_gcj02(dest[0], dest[1])
    params = {"origin": f"{o_gcj[0]},{o_gcj[1]}", "destination": f"{d_gcj[0]},{d_gcj[1]}"}
    if mode == "transit" and city:
        params["city"] = city
    data = await _amap_get(f"/direction/{endpoint}", params)
    if "error" in data:
        return data
    route = data.get("route", {})
    paths = route.get("paths", [])
    if not paths:
        return {"error": "未找到路线"}
    path = paths[0]
    steps_out = []
    polyline = []
    for step in path.get("steps", []):
        steps_out.append({
            "instruction": step.get("instruction", ""),
            "distance": step.get("distance", "0"),
            "duration": step.get("duration", "0"),
        })
        for loc in step.get("polyline", "").split(";"):
            parts = loc.split(",")
            if len(parts) == 2:
                gcj_lng, gcj_lat = float(parts[0]), float(parts[1])
                lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
                polyline.append([lng, lat])
    return {
        "distance_m": int(path.get("distance", 0)),
        "duration_s": int(path.get("duration", 0)),
        "polyline": polyline,
        "steps": steps_out,
        "provider": "amap",
    }



async def _search_poi_amap(keyword: str, city: str, limit: int) -> dict:
    params = {"keywords": keyword, "city": city, "citylimit": "true" if city else "false", "offset": str(limit)}
    data = await _amap_get("/place/text", params)
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            continue
        gcj_lng, gcj_lat = float(loc[0]), float(loc[1])
        lng, lat = gcj02_to_wgs84(gcj_lng, gcj_lat)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "tel": p.get("tel", ""),
                "city": p.get("cityname", ""),
                "district": p.get("adname", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "provider": "amap",
    }



async def _search_poi_around_amap(
    center: list, radius_m: int, keyword: str, types: str, limit: int
) -> dict:
    gcj_lng, gcj_lat = wgs84_to_gcj02(center[0], center[1])
    params = {
        "location": f"{gcj_lng},{gcj_lat}",
        "radius": str(radius_m),
        "offset": str(min(limit, 25)),
        "sortrule": "distance",
    }
    if keyword:
        params["keywords"] = keyword
    if types:
        params["types"] = types
    data = await _amap_get("/place/around", params)
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            continue
        lng, lat = gcj02_to_wgs84(float(loc[0]), float(loc[1]))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "distance_m": int(p.get("distance", 0) or 0),
                "tel": p.get("tel", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "center": center,
        "radius_m": radius_m,
        "provider": "amap",
    }



async def _search_poi_polygon_amap(
    polygon: list, keyword: str, types: str, limit: int
) -> dict:
    # Amap polygon 参数：lng,lat|lng,lat|...
    gcj_pts = [wgs84_to_gcj02(p[0], p[1]) for p in polygon]
    poly_str = "|".join(f"{lng},{lat}" for lng, lat in gcj_pts)
    params = {"polygon": poly_str, "offset": str(min(limit, 25))}
    if keyword:
        params["keywords"] = keyword
    if types:
        params["types"] = types
    data = await _amap_get("/place/polygon", params)
    if "error" in data:
        return data
    pois = data.get("pois", [])
    features = []
    for p in pois[:limit]:
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            continue
        lng, lat = gcj02_to_wgs84(float(loc[0]), float(loc[1]))
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "tel": p.get("tel", ""),
            },
        })
    return {
        "type": "FeatureCollection",
        "features": features,
        "count": len(features),
        "polygon": polygon,
        "provider": "amap",
    }



async def _traffic_amap(
    mode: str,
    rectangle: Optional[list],
    center: Optional[list],
    radius_m: int,
    level: int,
) -> dict:
    if mode == "rectangle":
        w, s, e, n = rectangle  # type: ignore[misc]
        # WGS84 → GCJ02 双角
        sw = wgs84_to_gcj02(w, s)
        ne = wgs84_to_gcj02(e, n)
        params = {"rectangle": f"{sw[0]},{sw[1]};{ne[0]},{ne[1]}"}
        endpoint = "/traffic/status/rectangle"
    else:
        gcj_lng, gcj_lat = wgs84_to_gcj02(center[0], center[1])  # type: ignore[index]
        params = {"location": f"{gcj_lng},{gcj_lat}", "radius": str(radius_m)}
        endpoint = "/traffic/status/circle"
    if level:
        params["level"] = str(level)
    data = await _amap_get(endpoint, params)
    if "error" in data:
        return data
    ts = data.get("trafficinfo", {})
    eval_block = ts.get("evaluation", {})
    roads = ts.get("roads", [])
    out_roads = []
    for r in roads:
        out_roads.append({
            "name": r.get("name", ""),
            "status": r.get("status", ""),
            "speed_kmh": float(r.get("speed", 0) or 0),
            "direction": r.get("direction", ""),
            "lcodes": r.get("lcodes", ""),
        })
    return {
        "description": ts.get("description", ""),
        "evaluation": {
            "status": eval_block.get("status", ""),
            "expedite": eval_block.get("expedite", ""),
            "congested": eval_block.get("congested", ""),
            "blocked": eval_block.get("blocked", ""),
            "unknown": eval_block.get("unknown", ""),
        },
        "roads": out_roads,
        "road_count": len(out_roads),
        "provider": "amap",
    }



async def _transit_amap(
    origin: list, destination: list, city: str, city_d: str, strategy: int
) -> dict:
    o_gcj = wgs84_to_gcj02(origin[0], origin[1])
    d_gcj = wgs84_to_gcj02(destination[0], destination[1])
    params: dict = {
        "origin": f"{o_gcj[0]},{o_gcj[1]}",
        "destination": f"{d_gcj[0]},{d_gcj[1]}",
        "city": city,
        "strategy": str(strategy),
    }
    if city_d:
        params["cityd"] = city_d
    data = await _amap_get("/direction/transit/integrated", params)
    if "error" in data:
        return data
    route = data.get("route", {})
    transits = route.get("transits", []) or []
    plans = []
    for t in transits[:5]:
        segments = []
        polyline = []
        for seg in t.get("segments", []):
            walking = seg.get("walking", {})
            bus = seg.get("bus", {})
            for step in walking.get("steps", []) or []:
                for loc in (step.get("polyline", "") or "").split(";"):
                    parts = loc.split(",")
                    if len(parts) == 2:
                        try:
                            lng, lat = gcj02_to_wgs84(float(parts[0]), float(parts[1]))
                            polyline.append([lng, lat])
                        except ValueError:
                            pass
            for bl in bus.get("buslines", []) or []:
                segments.append({
                    "type": "bus",
                    "name": bl.get("name", ""),
                    "departure_stop": bl.get("departure_stop", {}).get("name", ""),
                    "arrival_stop": bl.get("arrival_stop", {}).get("name", ""),
                    "via_num": int(bl.get("via_num", 0) or 0),
                })
                for loc in (bl.get("polyline", "") or "").split(";"):
                    parts = loc.split(",")
                    if len(parts) == 2:
                        try:
                            lng, lat = gcj02_to_wgs84(float(parts[0]), float(parts[1]))
                            polyline.append([lng, lat])
                        except ValueError:
                            pass
        plans.append({
            "duration_s": int(t.get("duration", 0) or 0),
            "walking_distance_m": int(t.get("walking_distance", 0) or 0),
            "cost_yuan": float(t.get("cost", 0) or 0),
            "transit_count": len([s for s in t.get("segments", []) if s.get("bus", {}).get("buslines")]),
            "segments": segments,
            "polyline": polyline,
        })
    return {
        "plans": plans,
        "count": len(plans),
        "provider": "amap",
    }


