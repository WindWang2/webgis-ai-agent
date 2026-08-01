"""天地图 (Tianditu) provider — POI / 地理编码 / 行政区划。

The deep Tianditu provider class. Coordinates are CGCS2000 (≈ WGS84), so
``SRC_CRS = None`` and no CRS transform is applied on either side — this is
the "identity" provider for coordinate purposes. Implements 6 of the 9 shared
capabilities (no route / input_tips / search_poi_polygon — the dispatch sites
exclude tianditu for those).
"""
import json
import logging
from typing import Any, Awaitable, Callable, Optional

from app.core.config import settings

from app.tools.chinese_maps.http import _tianditu_get
from app.tools.chinese_maps._shaping import shape_poi_collection

logger = logging.getLogger(__name__)


class TiandituProvider:
    """Tianditu (天地图) implementation of the Chinese-maps provider interface.

    Source CRS is CGCS2000 (≈ WGS84): ``SRC_CRS = None`` signals to
    ``shape_poi_collection`` that no transform is needed. Implementing 6 of the
    9 shared capabilities; the missing three (route/input_tips/search_poi_polygon)
    are excluded at the dispatch layer.
    """

    SRC_CRS = None  # CGCS2000 ≈ WGS84

    def __init__(self, get: Optional[Callable[[str, dict], Awaitable[dict]]] = None):
        self._get = get or _tianditu_get

    # ── CRS helpers ───────────────────────────────────────────────
    # Tianditu is already WGS84: identity transforms. Present for interface
    # uniformity with AmapProvider/BaiduProvider (and so callers can treat
    # every provider the same way).

    def _to_src(self, lng: float, lat: float) -> tuple[float, float]:
        return lng, lat

    def _to_wgs(self, lng: float, lat: float) -> tuple[float, float]:
        return lng, lat

    @staticmethod
    def _extract_loc(p: dict) -> tuple[float, float] | None:
        """Pull (lng, lat) from a Tianditu ``"lng lat"`` lonlat string, or None."""
        lonlat = p.get("lonlat", "").split(" ")
        if len(lonlat) != 2:
            return None
        try:
            return float(lonlat[0]), float(lonlat[1])
        except ValueError:
            return None

    # ── Protocol capabilities (6 of 9) ────────────────────────────

    async def search_poi(self, keyword: str, city: str, limit: int) -> dict:
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
        data = await self._get("/search", {"postStr": post_str, "type": "query"})
        if "error" in data:
            return data
        pois = data.get("pois", [])
        if not pois and isinstance(data.get("resultType"), int):
            return {"type": "FeatureCollection", "features": [], "count": 0, "provider": "tianditu"}
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "tel": p.get("phone", ""),
            },
            provider="tianditu",
            src_crs=self.SRC_CRS,
            limit=limit,
        )

    async def search_poi_around(
        self, center: list, radius_m: int, keyword: str, types: str, limit: int
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
        data = await self._get("/search", {"postStr": post_str, "type": "query"})
        if "error" in data:
            return data
        pois = data.get("pois", [])
        return shape_poi_collection(
            pois,
            extract_coord=self._extract_loc,
            properties_fn=lambda p: {
                "name": p.get("name", ""),
                "address": p.get("address", ""),
                "tel": p.get("phone", ""),
            },
            provider="tianditu",
            src_crs=self.SRC_CRS,
            limit=limit,
            extra_envelope={"center": center, "radius_m": radius_m},
        )

    async def geocode(self, address: str, city: str) -> dict:
        ds = json.dumps({"keyWord": address}, ensure_ascii=False)
        data = await self._get("/geocoder", {"ds": ds})
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

    async def reverse_geocode(self, lng: float, lat: float) -> dict:
        post_str = json.dumps({"lon": lng, "lat": lat, "ver": 1})
        data = await self._get("/geocoder", {"postStr": post_str, "type": "geocode"})
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

    async def district(self, keywords: str, level: str, return_geometry: str = "point") -> dict:
        post_str = json.dumps({
            "searchWord": keywords,
            "searchType": "1",
            "needSubInfo": "true",
            "needAll": "false",
            "needPolygon": "false",
            "needPre": "true",
        }, ensure_ascii=False)
        data = await self._get("/administrative", {"postStr": post_str})
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

    # ── Tianditu-specific: admin division V2 (polygon support) ─────
    # NOT a Protocol method — called directly by the get_admin_division and
    # get_child_districts tools when they route to tianditu for boundary work.

    async def district_v2(self, keywords: str, child_level: int, return_polygon: bool) -> dict:
        """天地图行政区划查询 V2 (支持边界轮廓)"""
        post_str = json.dumps({
            "searchWord": keywords,
            "searchType": "1",
            "needSubInfo": "true" if child_level > 0 else "false",
            "needAll": "false",
            "needPolygon": "true" if return_polygon else "false",
            "needPre": "true",
        }, ensure_ascii=False)

        data = await self._get("/administrative", {"postStr": post_str})
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
            if not points_str:
                return None
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
                if not polygons:
                    return None
                if len(polygons) == 1:
                    return {"type": "Polygon", "coordinates": polygons[0]}
                return {"type": "MultiPolygon", "coordinates": polygons}
            except Exception:
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
                    "is_parent": True,
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
                            "parent_name": d.get("name"),
                        },
                    })

        return {
            "type": "FeatureCollection",
            "features": features,
            "count": len(features),
            "provider": "tianditu",
        }
