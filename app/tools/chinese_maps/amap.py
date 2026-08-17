"""高德 (Amap) provider — POI 搜索 / 地理编码 / 路径规划 / 行政区划 / 等时圈 / 实时路况。

The deep Amap provider class. Encapsulates endpoint paths, request-param
building, response-unwrap keys, and **both sides of coordinate transformation**
(WGS84 ↔ GCJ-02) behind one interface. The 9 shared capabilities match the
``ChineseMapsProvider`` Protocol; the 3 Amap-only features
(:meth:`isochrone`, :meth:`transit`, :meth:`traffic`) are non-Protocol methods
called directly (no fallback — Amap-only by design).

The ``get`` callable injected into ``__init__`` is the seam between provider
logic and the :doc:`Tracked Provider HTTP Request <CONTEXT>` transport
(``tracked_provider_get``); it defaults to the real ``_amap_get`` and doubles
as the fake-GET test seam.
"""
import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import aiohttp

from app.utils.coord_transform import wgs84_to_gcj02, gcj02_to_wgs84, transform_geojson

from app.tools.chinese_maps.http import _amap_get, _speed_mps
from app.tools.chinese_maps._shaping import shape_poi_collection

logger = logging.getLogger(__name__)


class AmapProvider:
    """Amap (高德) implementation of the Chinese-maps provider interface.

    Source CRS is GCJ-02: every coordinate crosses
    :func:`wgs84_to_gcj02` on the way in and :func:`gcj02_to_wgs84` on the way
    out. POI outputs route through ``shape_poi_collection`` (already CRS-aware);
    non-POI outputs (route polylines, district polygons) normalize via one
    :func:`transform_geojson` pass.
    """

    SRC_CRS = "gcj02"

    def __init__(self, get: Optional[Callable[[str, dict], Awaitable[dict]]] = None):
        # The tracked-GET transport seam. ``None`` → the real _amap_get, which
        # delegates to tracked_provider_get (circuit breaker + rate limit).
        self._get = get or _amap_get

    # ── CRS helpers ───────────────────────────────────────────────

    def _to_src(self, lng: float, lat: float) -> tuple[float, float]:
        """WGS84 → GCJ-02 for request params."""
        return wgs84_to_gcj02(lng, lat)

    def _to_wgs(self, lng: float, lat: float) -> tuple[float, float]:
        """GCJ-02 → WGS84 for response coords."""
        return gcj02_to_wgs84(lng, lat)

    @staticmethod
    def _extract_loc(p: dict) -> tuple[float, float] | None:
        """Pull (lng, lat) from an Amap ``"lng,lat"`` location string, or None."""
        loc = p.get("location", "").split(",")
        if len(loc) != 2:
            return None
        try:
            return float(loc[0]), float(loc[1])
        except ValueError:
            return None

    # ── 9 Protocol capabilities ───────────────────────────────────

    async def search_poi(self, keyword: str, city: str, limit: int) -> dict:
        params = {"keywords": keyword, "city": city, "citylimit": "true" if city else "false", "offset": str(limit)}
        data = await self._get("/place/text", params)
        if "error" in data:
            return data
        pois = data.get("pois", [])
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "tel": p.get("tel", ""),
                "city": p.get("cityname", ""),
                "district": p.get("adname", ""),
            },
            provider="amap",
            src_crs=self.SRC_CRS,
            limit=limit,
        )

    async def search_poi_around(
        self, center: list, radius_m: int, keyword: str, types: str, limit: int
    ) -> dict:
        gcj_lng, gcj_lat = self._to_src(center[0], center[1])
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
        data = await self._get("/place/around", params)
        if "error" in data:
            return data
        pois = data.get("pois", [])
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "distance_m": int(p.get("distance", 0) or 0),
                "tel": p.get("tel", ""),
            },
            provider="amap",
            src_crs=self.SRC_CRS,
            limit=limit,
            extra_envelope={"center": center, "radius_m": radius_m},
        )

    async def search_poi_polygon(
        self, polygon: list, keyword: str, types: str, limit: int
    ) -> dict:
        # Amap polygon 参数：lng,lat|lng,lat|...
        gcj_pts = [self._to_src(p[0], p[1]) for p in polygon]
        poly_str = "|".join(f"{lng},{lat}" for lng, lat in gcj_pts)
        params = {"polygon": poly_str, "offset": str(min(limit, 25))}
        if keyword:
            params["keywords"] = keyword
        if types:
            params["types"] = types
        data = await self._get("/place/polygon", params)
        if "error" in data:
            return data
        pois = data.get("pois", [])
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", "") or p.get("pname", ""),
                "type": p.get("type", ""),
                "tel": p.get("tel", ""),
            },
            provider="amap",
            src_crs=self.SRC_CRS,
            limit=limit,
            extra_envelope={"polygon": polygon},
        )

    async def geocode(self, address: str, city: str) -> dict:
        params = {"address": address}
        if city:
            params["city"] = city
        data = await self._get("/geocode/geo", params)
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
            lng, lat = self._to_wgs(gcj_lng, gcj_lat)
            results.append({
                "location": [lng, lat],
                "formatted_address": g.get("formatted_address", ""),
                "province": g.get("province", ""),
                "city": g.get("city", ""),
                "district": g.get("district", ""),
                "adcode": g.get("adcode", ""),
            })
        return {"results": results, "count": len(results), "provider": "amap"}

    async def reverse_geocode(self, lng: float, lat: float) -> dict:
        gcj_lng, gcj_lat = self._to_src(lng, lat)
        params = {"location": f"{gcj_lng},{gcj_lat}", "extensions": "all"}
        data = await self._get("/geocode/regeo", params)
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

    async def route(self, origin: list, dest: list, mode: str, city: str) -> dict:
        mode_map = {"driving": "driving", "walking": "walking", "cycling": "bicycling", "transit": "transit/integrated"}
        endpoint = mode_map.get(mode, "driving")
        o_gcj = self._to_src(origin[0], origin[1])
        d_gcj = self._to_src(dest[0], dest[1])
        params = {"origin": f"{o_gcj[0]},{o_gcj[1]}", "destination": f"{d_gcj[0]},{d_gcj[1]}"}
        if mode == "transit" and city:
            params["city"] = city
        data = await self._get(f"/direction/{endpoint}", params)
        if "error" in data:
            return data
        route = data.get("route", {})
        if mode == "transit":
            # Issue #542: /direction/transit/integrated returns plans under
            # ``route.transits``, not ``route.paths`` — reading paths made the
            # transit branch dead code that always returned 未找到路线.
            transits = route.get("transits", []) or []
            if not transits:
                return {"error": "未找到路线"}
            return self._shape_transit(transits[0])
        paths = route.get("paths", [])
        if not paths:
            return {"error": "未找到路线"}
        return self._shape_route(paths[0])

    async def input_tips(
        self, keyword: str, city: str, location: Optional[list]
    ) -> dict:
        params = {"keywords": keyword}
        if city:
            params["city"] = city
            params["citylimit"] = "true"
        if location and len(location) == 2:
            lng, lat = self._to_src(location[0], location[1])
            params["location"] = f"{lng},{lat}"
        data = await self._get("/assistant/inputtips", params)
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
                        lng, lat = self._to_wgs(float(parts[0]), float(parts[1]))
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

    async def district(self, keywords: str, level: str, return_geometry: str = "point") -> dict:
        params = {"keywords": keywords, "subdistrict": "1", "extensions": "all" if return_geometry == "polygon" else "base"}
        # subdistrict 参数含义：0:不返回下级, 1:返回下级, 2:返回下级及其下级, 3:返回下级及其下级及其下级
        # 我们默认设为 1 以便用户能看到下级行政区列表
        data = await self._get("/config/district", params)
        if "error" in data:
            return data
        return self._shape_district(data.get("districts", []), return_geometry)

    # /v3/distance 契约：origins 用 "|" 分隔且每请求最多 100 个点；destination
    # 只接受单个坐标点；返回 results 按请求内 origin_id（1 起）排列，dest_id
    # 恒为 "1"（单终点）。多终点矩阵必须拆成每终点一次请求（issue #440）。
    _DISTANCE_MAX_ORIGINS = 100

    async def distance_matrix(
        self, origins: list[list], destinations: list[list], mode: str,
    ) -> dict:
        """Amap 距离矩阵。

        - driving / walking 走 ``/v3/distance``：
            - type=1 驾车，type=3 步行
            - 契约限制 destination 为单点 → 每个终点一次请求，该请求的
              results 填入矩阵的一列；origins > 100 时按 100 分块
        - riding 该批量接口不支持，回退到 N×M 并发调用 /direction/bicycling

        所有坐标在请求前 WGS84 → GCJ-02。
        """
        # ── driving / walking：批量接口（每终点一次请求）
        if mode in ("driving", "walking"):
            type_param = "1" if mode == "driving" else "3"
            matrix: list[list[dict | None]] = [
                [None] * len(destinations) for _ in range(len(origins))
            ]
            semaphore = asyncio.Semaphore(6)

            async def _one_dest(di: int) -> str | None:
                """单终点（可能多个 origins 分块）请求；结果填矩阵第 di 列。

                返回 error 描述（该终点请求失败时），否则 None。
                """
                d_gcj = self._to_src(destinations[di][0], destinations[di][1])
                for chunk_start in range(0, len(origins), self._DISTANCE_MAX_ORIGINS):
                    chunk = origins[chunk_start:chunk_start + self._DISTANCE_MAX_ORIGINS]
                    origin_str = "|".join(
                        f"{gx},{gy}"
                        for gx, gy in (self._to_src(o[0], o[1]) for o in chunk)
                    )
                    params = {
                        "origins": origin_str,
                        "destination": f"{d_gcj[0]},{d_gcj[1]}",
                        "type": type_param,
                    }
                    async with semaphore:
                        data = await self._get("/distance", params)
                    if "error" in data:
                        return str(data["error"])
                    for item in data.get("results", []):
                        # origin_id 是请求内 1 起编号，分块时补回全局行号；
                        # dest_id 恒为 "1"（单终点），列号直接用本请求的 di。
                        try:
                            oi = int(item.get("origin_id", 0)) - 1 + chunk_start
                        except (TypeError, ValueError):
                            continue
                        if 0 <= oi < len(origins):
                            matrix[oi][di] = {
                                "origin_index": oi,
                                "dest_index": di,
                                "distance_km": float(item.get("distance", 0)) / 1000.0,
                                "duration_sec": int(item.get("duration", 0)),
                            }
                return None

            errors = await asyncio.gather(*[
                _one_dest(di) for di in range(len(destinations))
            ])
            failed = [di for di, err in enumerate(errors) if err]
            if failed:
                if all(cell is None for row in matrix for cell in row):
                    # 全部终点都失败 → 与单请求时代行为一致，整体报错
                    return {"error": errors[failed[0]]}
                # 部分失败：保留成功的列，失败列置 None 并显式列出（issue #440：
                # 不再静默丢弃，旧实现会返回全 None 矩阵）
                return {
                    "matrix": matrix,
                    "origins_count": len(origins),
                    "dests_count": len(destinations),
                    "mode": mode,
                    "provider": "amap",
                    "errors": [f"dest_index {di}: {errors[di]}" for di in failed],
                    "note": f"{len(failed)}/{len(destinations)} 个终点请求失败，对应矩阵列为 None",
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
                dist_m = await self._get_route_distance(origins[oi], destinations[di], "riding")
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

    # ── Amap-only capabilities (NOT in the Protocol; no fallback) ──

    # Fixed ~1.1 km (driving) / ~390 m (riding) / ~66 m (walking) probe lines
    # made every isochrone saturate at the probe distance regardless of
    # ``minutes``: ``ratio = budget / probe_dist`` was always > 1 and got
    # capped at 1.0, so the returned polygon was a function of the mode only
    # while ``radius_m`` (speed × time) contradicted the geometry (GIS-…).
    # Instead we now probe FAR past the nominal budget once to measure the
    # actual route speed, scale proportionally, then run one bounded
    # correction probe — so the polygon genuinely grows with ``minutes`` and
    # tracks real road speeds. Quota stays bounded: 2 probes × 12 radials max.
    _ISOCHRONE_PROBE_MARGIN = 1.25   # first probe beyond the nominal budget
    _ISOCHRONE_CORRECTION_MARGIN = 1.05

    async def isochrone(self, center: list, minutes: int, mode: str) -> dict:
        """沿 N 个方向调用路径规划 API，收集各方向在 `minutes` 时间内的最远到达点，用 Convex Hull 近似等时圈。

        每个方向先做一次超出名义预算 ~25% 的远探测来实测路线速度，再按
        ``速度 × 时间`` 推算可达距离，并做一次有界的校正探测；探测失败时
        直接用 ``speed × time`` 推出半径（兜底圆已按 cos(lat) 修正两轴）。
        """
        import math

        num_radials = 12  # 每30°一条射线
        budget_s = minutes * 60.0
        nominal_speed = _speed_mps(mode)
        target_m = budget_s * nominal_speed
        # 经度每度米数随纬度收缩：cos(lat) 修正，两极附近下限保护。
        cos_lat = max(math.cos(math.radians(center[1])), 0.02)
        angle_step = 2 * math.pi / num_radials

        def _offset(angle: float, dist_m: float) -> tuple[float, float]:
            """中心点向 ``angle`` 方向移动 ``dist_m`` 米（WGS84 近似，含 cos(lat)）。"""
            dlat = dist_m * math.sin(angle) / 111320.0
            dlng = dist_m * math.cos(angle) / (111320.0 * cos_lat)
            return center[0] + dlng, center[1] + dlat

        async def _radial_point(angle: float) -> tuple[float, float]:
            # 1) 一次“远探测”：超出名义预算，路线时长才会跨住预算，从而能实测速度。
            probe_m = target_m * self._ISOCHRONE_PROBE_MARGIN
            probe_pt = _offset(angle, probe_m)
            dist_m, dur_s = await self._probe_route(center, probe_pt, mode)
            if dist_m <= 0:
                # 探测失败（API 错误/无路线）：直接用 speed × time 推半径。
                return _offset(angle, target_m)

            speed_mps = (dist_m / dur_s) if dur_s and dur_s > 0 else nominal_speed
            reachable_m = speed_mps * budget_s

            # 2) 一次有界校正探测：在推算的可达距离处再量一次，收窄误差。
            if reachable_m > 0:
                corr_pt = _offset(angle, reachable_m * self._ISOCHRONE_CORRECTION_MARGIN)
                dist2, dur2 = await self._probe_route(center, corr_pt, mode)
                if dist2 > 0:
                    speed2 = (dist2 / dur2) if dur2 and dur2 > 0 else speed_mps
                    reachable_m = speed2 * budget_s

            return _offset(angle, max(reachable_m, 0.0))

        semaphore = asyncio.Semaphore(6)
        angles = [angle_step * i for i in range(num_radials)]

        async def _guarded_radial(angle: float) -> tuple[float, float]:
            try:
                async with semaphore:
                    return await _radial_point(angle)
            except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError):
                # 兜底：均匀半径圆上的点（speed × time，cos(lat) 修正）。
                return _offset(angle, target_m)

        pts = await asyncio.gather(*[_guarded_radial(a) for a in angles])

        all_points = list(pts)

        if len(all_points) >= 3:
            from shapely.geometry import MultiPoint
            hull = MultiPoint(all_points).convex_hull
            geometry = hull.__geo_interface__
        else:
            geometry = {"type": "Point", "coordinates": center}

        # radius_m 与几何一致：取各方向最终点的平均径向距离，而非名义值。
        def _radial_dist_m(pt: tuple[float, float]) -> float:
            dx = (pt[0] - center[0]) * 111320.0 * cos_lat
            dy = (pt[1] - center[1]) * 111320.0
            return math.hypot(dx, dy)

        radius_m = int(round(sum(_radial_dist_m(p) for p in pts) / len(pts))) if pts else 0

        return {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "center": center,
                "minutes": minutes,
                "mode": mode,
                "provider": "amap",
                "radius_m": radius_m,
            },
        }

    async def transit(
        self, origin: list, destination: list, city: str, city_d: str, strategy: int,
    ) -> dict:
        o_gcj = self._to_src(origin[0], origin[1])
        d_gcj = self._to_src(destination[0], destination[1])
        params: dict = {
            "origin": f"{o_gcj[0]},{o_gcj[1]}",
            "destination": f"{d_gcj[0]},{d_gcj[1]}",
            "city": city,
            "strategy": str(strategy),
        }
        if city_d:
            params["cityd"] = city_d
        data = await self._get("/direction/transit/integrated", params)
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
                                lng, lat = self._to_wgs(float(parts[0]), float(parts[1]))
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
                                lng, lat = self._to_wgs(float(parts[0]), float(parts[1]))
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

    async def traffic(
        self,
        mode: str,
        rectangle: Optional[list],
        center: Optional[list],
        radius_m: int,
        level: int,
    ) -> dict:
        if mode == "rectangle":
            w, s, e, n = rectangle  # type: ignore[misc]
            # WGS84 → GCJ02 双角
            sw = self._to_src(w, s)
            ne = self._to_src(e, n)
            params = {"rectangle": f"{sw[0]},{sw[1]};{ne[0]},{ne[1]}"}
            endpoint = "/traffic/status/rectangle"
        else:
            gcj_lng, gcj_lat = self._to_src(center[0], center[1])  # type: ignore[index]
            params = {"location": f"{gcj_lng},{gcj_lat}", "radius": str(radius_m)}
            endpoint = "/traffic/status/circle"
        if level:
            params["level"] = str(level)
        data = await self._get(endpoint, params)
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

    async def _probe_route(
        self, origin: list, destination: list, mode: str,
    ) -> tuple[float, float]:
        """调用 Amap 路径规划 API，返回 (距离米, 时长秒)，失败返回 (0.0, 0.0)。

        用于等时圈探测：时长用来实测路线平均速度，避免用名义速度外推。
        """
        try:
            og_lng, og_lat = origin
            dg_lng, dg_lat = destination
            og_gcj = self._to_src(og_lng, og_lat)
            dg_gcj = self._to_src(dg_lng, dg_lat)
            o_str = f"{og_gcj[0]},{og_gcj[1]}"
            d_str = f"{dg_gcj[0]},{dg_gcj[1]}"

            if mode == "walking":
                params = {"origin": o_str, "destination": d_str}
                data = await self._get("/direction/walking", params)
            elif mode == "riding":
                params = {"origin": o_str, "destination": d_str}
                data = await self._get("/direction/bicycling", params)
            else:  # driving
                params = {"origin": o_str, "destination": d_str, "strategy": "10"}
                data = await self._get("/direction/driving", params)

            if "error" in data:
                return 0.0, 0.0
            route = data.get("route", {})
            paths = route.get("paths", [])
            if not paths:
                return 0.0, 0.0
            path = paths[0]
            return float(path.get("distance", 0)), float(path.get("duration", 0))
        except (aiohttp.ClientError, json.JSONDecodeError, KeyError, ValueError, TypeError):
            return 0.0, 0.0

    async def _get_route_distance(
        self, origin: list, destination: list, mode: str,
    ) -> float:
        """调用 Amap 路径规划 API，返回两点间单程距离（米）。用于等时圈半径探测。"""
        dist_m, _ = await self._probe_route(origin, destination, mode)
        return dist_m

    # ── output shaping (non-POI; one transform_geojson pass) ──────

    def _shape_route(self, path: dict) -> dict:
        """Shape an Amap route path into the route result dict.

        Builds the polyline in GCJ-02 while collecting step metadata, then
        normalizes the whole coordinate list to WGS84 via a single
        :func:`transform_geojson` pass over a synthetic LineString.
        """
        steps_out = []
        gcj_polyline: list[list[float]] = []
        for step in path.get("steps", []):
            steps_out.append({
                "instruction": step.get("instruction", ""),
                "distance": step.get("distance", "0"),
                "duration": step.get("duration", "0"),
            })
            for loc in step.get("polyline", "").split(";"):
                parts = loc.split(",")
                if len(parts) == 2:
                    gcj_polyline.append([float(parts[0]), float(parts[1])])

        # One CRS pass: GCJ-02 → WGS84 over the whole polyline.
        if gcj_polyline:
            line_fc = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": gcj_polyline}}
            wgs = transform_geojson(line_fc, self.SRC_CRS, "wgs84")
            polyline = wgs["geometry"]["coordinates"]
        else:
            polyline = []

        return {
            "distance_m": int(path.get("distance", 0)),
            "duration_s": int(path.get("duration", 0)),
            "polyline": polyline,
            "steps": steps_out,
            "provider": "amap",
        }

    def _shape_transit(self, plan: dict) -> dict:
        """Shape the first Amap transit plan into the route result dict.

        Transit plans nest the polyline in ``walking.steps[].polyline`` and
        ``bus.buslines[].polyline`` (issue #542); both are assembled in GCJ-02
        and normalized to WGS84 via a single :func:`transform_geojson` pass,
        mirroring the driving/walking/cycling shape.
        """
        gcj_polyline: list[list[float]] = []
        steps_out = []
        for seg in plan.get("segments", []) or []:
            for step in seg.get("walking", {}).get("steps", []) or []:
                for loc in (step.get("polyline", "") or "").split(";"):
                    parts = loc.split(",")
                    if len(parts) == 2:
                        gcj_polyline.append([float(parts[0]), float(parts[1])])
            for bl in seg.get("bus", {}).get("buslines", []) or []:
                steps_out.append({
                    "instruction": bl.get("name", ""),
                    "distance": bl.get("distance", "0"),
                    "duration": bl.get("duration", "0"),
                })
                for loc in (bl.get("polyline", "") or "").split(";"):
                    parts = loc.split(",")
                    if len(parts) == 2:
                        gcj_polyline.append([float(parts[0]), float(parts[1])])

        # One CRS pass: GCJ-02 → WGS84 over the whole polyline.
        if gcj_polyline:
            line_fc = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": gcj_polyline}}
            wgs = transform_geojson(line_fc, self.SRC_CRS, "wgs84")
            polyline = wgs["geometry"]["coordinates"]
        else:
            polyline = []

        return {
            "distance_m": int(plan.get("distance", 0) or 0),
            "duration_s": int(plan.get("duration", 0) or 0),
            "polyline": polyline,
            "steps": steps_out,
            "provider": "amap",
        }

    def _shape_district(self, districts: list, return_geometry: str) -> dict:
        """Shape Amap administrative-district records into a FeatureCollection.

        Built entirely in GCJ-02 (the source CRS) — including the center points
        used for the point fallback — then normalized to WGS84 via a single
        :func:`transform_geojson` pass. Building in source CRS uniformly avoids
        the double-transform hazard where pre-converted points would be shifted
        a second time by the batch pass.
        """
        raw_features = []
        for d in districts:
            center = d.get("center", "").split(",")
            # NOTE: deliberately kept in GCJ-02 (source CRS) here — the single
            # transform_geojson pass below normalizes the whole FC, points
            # included. Pre-converting would cause a double shift.
            lng, lat = (float(center[0]), float(center[1])) if len(center) == 2 else (0.0, 0.0)

            if return_geometry == "polygon":
                # 高德行政区划边界字段名为 'polyline'
                polyline_str = d.get("polyline", "")
                geometry = None
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
                            # 闭合环
                            if coords[0] != coords[-1]:
                                coords.append(coords[0])
                            polygons.append(Polygon(coords))

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

            raw_features.append({
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "name": d.get("name", ""),
                    "level": d.get("level", ""),
                    "adcode": d.get("adcode", ""),
                    "citycode": d.get("citycode", ""),
                },
            })

        # One CRS pass: GCJ-02 → WGS84 over the whole FeatureCollection.
        fc = {"type": "FeatureCollection", "features": raw_features}
        fc = transform_geojson(fc, self.SRC_CRS, "wgs84")

        return {"type": "FeatureCollection", "features": fc["features"], "count": len(fc["features"]),
                "provider": "amap", "geometry_type": fc["features"][0]["geometry"]["type"] if fc["features"] else "Point"}
