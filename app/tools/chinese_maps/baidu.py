"""百度 (Baidu) provider — POI / 地理编码 / 路径规划 / 行政区 / 距离矩阵 / 周边 / 多边形搜索。

The deep Baidu provider class. Same interface shape as :class:`AmapProvider`;
source CRS is BD-09. CRS transforms cross :func:`wgs84_to_bd09` on the way in
and :func:`bd09_to_wgs84` on the way out.
"""
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from app.core.config import settings
from app.utils.coord_transform import wgs84_to_bd09, bd09_to_wgs84, transform_geojson

from app.tools.chinese_maps.http import _baidu_get
from app.tools.chinese_maps._shaping import shape_poi_collection

logger = logging.getLogger(__name__)


class BaiduProvider:
    """Baidu (百度) implementation of the Chinese-maps provider interface.

    Source CRS is BD-09. POI outputs route through ``shape_poi_collection``;
    route polylines normalize via one :func:`transform_geojson` pass.
    """

    SRC_CRS = "bd09"

    def __init__(self, get: Optional[Callable[[str, dict], Awaitable[dict]]] = None):
        self._get = get or _baidu_get

    # ── CRS helpers ───────────────────────────────────────────────

    def _to_src(self, lng: float, lat: float) -> tuple[float, float]:
        """WGS84 → BD-09 for request params."""
        return wgs84_to_bd09(lng, lat)

    def _to_wgs(self, lng: float, lat: float) -> tuple[float, float]:
        """BD-09 → WGS84 for response coords."""
        return bd09_to_wgs84(lng, lat)

    @staticmethod
    def _extract_loc(p: dict) -> tuple[float, float] | None:
        """Pull (lng, lat) from a Baidu ``{lng, lat}`` location dict, or None."""
        loc = p.get("location", {})
        lng, lat = loc.get("lng"), loc.get("lat")
        if lng is None or lat is None:
            return None
        try:
            return float(lng), float(lat)
        except (TypeError, ValueError):
            return None

    # ── Protocol capabilities ─────────────────────────────────────

    async def search_poi(self, keyword: str, city: str, limit: int) -> dict:
        params = {"query": keyword, "region": city or "全国", "page_size": str(min(limit, 20))}
        data = await self._get("/place/v2/search", params)
        if "error" in data:
            return data
        pois = data.get("results", [])
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "tel": p.get("telephone", ""),
                "city": p.get("city", ""),
                "district": p.get("area", ""),
            },
            provider="baidu",
            src_crs=self.SRC_CRS,
            limit=limit,
        )

    async def search_poi_around(
        self, center: list, radius_m: int, keyword: str, types: str, limit: int
    ) -> dict:
        bd_lng, bd_lat = self._to_src(center[0], center[1])
        params = {
            "query": keyword or types,
            "location": f"{bd_lat},{bd_lng}",
            "radius": str(radius_m),
            "page_size": str(min(limit, 20)),
            "scope": "2",
        }
        data = await self._get("/place/v2/search", params)
        if "error" in data:
            return data
        pois = data.get("results", [])
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "distance_m": int(p.get("detail_info", {}).get("distance", 0) or 0),
                "tel": p.get("telephone", ""),
            },
            provider="baidu",
            src_crs=self.SRC_CRS,
            limit=limit,
            extra_envelope={"center": center, "radius_m": radius_m},
        )

    async def search_poi_polygon(
        self, polygon: list, keyword: str, types: str, limit: int
    ) -> dict:
        # Baidu place v2 不接收 polygon 直接参数；用 polygon 外接 bbox 近似
        lngs = [p[0] for p in polygon]
        lats = [p[1] for p in polygon]
        w, e = min(lngs), max(lngs)
        s, n = min(lats), max(lats)
        sw_bd = self._to_src(w, s)
        ne_bd = self._to_src(e, n)
        params = {
            "query": keyword or types,
            "bounds": f"{sw_bd[1]},{sw_bd[0]},{ne_bd[1]},{ne_bd[0]}",
            "page_size": str(min(limit, 20)),
        }
        data = await self._get("/place/v2/search", params)
        if "error" in data:
            return data
        pois = data.get("results", [])
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "type": p.get("detail_info", {}).get("type", ""),
                "tel": p.get("telephone", ""),
            },
            provider="baidu",
            src_crs=self.SRC_CRS,
            limit=limit,
            extra_envelope={
                "polygon": polygon,
                "note": "Baidu 用 polygon 外接矩形 (bbox) 近似查询",
            },
        )

    async def geocode(self, address: str, city: str) -> dict:
        params = {"address": address}
        if city:
            params["city"] = city
        data = await self._get("/geocoding/v3/", params)
        if "error" in data:
            return data
        result = data.get("result", {})
        loc = result.get("location", {})
        bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
        lng, lat = self._to_wgs(bd_lng, bd_lat)
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

    async def reverse_geocode(self, lng: float, lat: float) -> dict:
        bd_lng, bd_lat = self._to_src(lng, lat)
        params = {"location": f"{bd_lat},{bd_lng}", "extensions_poi": 1}
        data = await self._get("/reverse_geocoding/v3/", params)
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

    async def route(self, origin: list, dest: list, mode: str, city: str) -> dict:
        mode_map = {"driving": "driving", "walking": "walking", "cycling": "riding", "transit": "transit"}
        endpoint = mode_map.get(mode, "driving")
        o_bd = self._to_src(origin[0], origin[1])
        d_bd = self._to_src(dest[0], dest[1])
        params = {"origin": f"{o_bd[0]},{o_bd[1]}", "destination": f"{d_bd[0]},{d_bd[1]}"}
        if mode == "transit" and city:
            params["city"] = city
        data = await self._get(f"/directionlite/v1/{endpoint}", params)
        if "error" in data:
            return data
        route = data.get("result", {}).get("routes", [])
        if not route:
            return {"error": "未找到路线"}
        return self._shape_route(route[0])

    async def input_tips(
        self, keyword: str, city: str, location: Optional[list]
    ) -> dict:
        params = {"query": keyword, "region": city or "全国"}
        if location and len(location) == 2:
            bd_lng, bd_lat = self._to_src(location[0], location[1])
            params["location"] = f"{bd_lat},{bd_lng}"
        data = await self._get("/place/v2/suggestion", params)
        if "error" in data:
            return data
        suggestions = data.get("result", [])
        out = []
        for s in suggestions:
            loc = s.get("location") or {}
            coords = None
            if loc.get("lng") and loc.get("lat"):
                lng, lat = self._to_wgs(loc["lng"], loc["lat"])
                coords = [lng, lat]
            out.append({
                "name": s.get("name", ""),
                "district": s.get("district", ""),
                "address": s.get("address", ""),
                "location": coords,
                "adcode": str(s.get("city_id", "")),
            })
        return {"tips": out, "count": len(out), "provider": "baidu"}

    async def district(self, keywords: str, level: str, return_geometry: str = "point") -> dict:
        params = {"q": keywords}
        data = await self._get("/api/v2/administrative", params)
        if "error" in data:
            return data
        districts = data.get("results", [])
        # Baidu administrative returns point centers only (no polygon field).
        raw_features = []
        for d in districts:
            loc = d.get("location", {})
            # center kept in BD-09 (source CRS); one transform pass normalizes.
            bd_lng, bd_lat = loc.get("lng", 0), loc.get("lat", 0)
            raw_features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [bd_lng, bd_lat]},
                "properties": {
                    "name": d.get("name", ""),
                    "level": d.get("level", ""),
                    "code": str(d.get("code", "")),
                },
            })
        fc = {"type": "FeatureCollection", "features": raw_features}
        fc = transform_geojson(fc, self.SRC_CRS, "wgs84")
        return {"type": "FeatureCollection", "features": fc["features"], "count": len(fc["features"]), "provider": "baidu"}

    async def distance_matrix(
        self, origins: list[list], destinations: list[list], mode: str,
    ) -> dict:
        """Baidu v2 direction Matrix API — 一次请求完成全量 OD 计算。"""

        def _bd_fmt(lng: float, lat: float) -> str:
            # WGS84 → BD09，然后按"纬度,经度"格式提交给百度
            bd_lng, bd_lat = self._to_src(lng, lat)
            return f"{bd_lat},{bd_lng}"

        origin_str = "|".join(_bd_fmt(lo, la) for lo, la in origins)
        dest_str = "|".join(_bd_fmt(ld, la) for ld, la in destinations)
        mode_map = {"driving": "car", "walking": "foot", "riding": "bike"}
        params = {
            "origin": origin_str,
            "destination": dest_str,
            "mode": mode_map.get(mode, "car"),
        }
        data = await self._get("/direction/v2/matrix", params)
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

    # ── output shaping ────────────────────────────────────────────

    def _shape_route(self, r: dict) -> dict:
        """Shape a Baidu route into the route result dict.

        Baidu encodes the polyline per-step in a ``path`` field (vs Amap's
        ``polyline``), in BD-09. Built in source CRS, then normalized to WGS84
        via one :func:`transform_geojson` pass over a synthetic LineString.
        """
        steps_out = []
        bd_polyline: list[list[float]] = []
        for step in r.get("steps", []):
            steps_out.append({
                "instruction": step.get("instruction", ""),
                "distance": step.get("distance", "0"),
                "duration": step.get("duration", "0"),
            })
            for loc in step.get("path", "").split(";"):
                parts = loc.split(",")
                if len(parts) == 2:
                    bd_polyline.append([float(parts[0]), float(parts[1])])

        if bd_polyline:
            line_fc = {"type": "Feature", "geometry": {"type": "LineString", "coordinates": bd_polyline}}
            wgs = transform_geojson(line_fc, self.SRC_CRS, "wgs84")
            polyline = wgs["geometry"]["coordinates"]
        else:
            polyline = []

        return {
            "distance_m": int(r.get("distance", 0)),
            "duration_s": int(r.get("duration", 0)),
            "polyline": polyline,
            "steps": steps_out,
            "provider": "baidu",
        }
