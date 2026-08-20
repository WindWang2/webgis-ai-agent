"""天地图 (Tianditu) provider — POI / 地理编码 / 行政区划。

The deep Tianditu provider class. Coordinates are CGCS2000 (≈ WGS84), so
``SRC_CRS = None`` and no CRS transform is applied on either side — this is
the "identity" provider for coordinate purposes. Implements 6 of the 9 shared
capabilities (no route / input_tips / search_poi_polygon — the dispatch sites
exclude tianditu for those).
"""
import json
import logging
from typing import Awaitable, Callable, Optional


from app.tools.chinese_maps.http import _tianditu_get
from app.tools.chinese_maps._shaping import shape_poi_collection

logger = logging.getLogger(__name__)

# ── city → adcode 本地解析（优先本地 SHP，表格缺失时用内置常见城市映射兜底，保障回归） ──

_FALLBACK_CITY_ADCODE: dict[str, str] = {
    "北京": "110000", "北京市": "110000",
    "上海": "310000", "上海市": "310000",
    "天津": "120000", "天津市": "120000",
    "重庆": "500000", "重庆市": "500000",
    "成都": "510100", "成都市": "510100",
    "广州": "440100", "广州市": "440100",
    "深圳": "440300", "深圳市": "440300",
    "杭州": "330100", "杭州市": "330100",
    "南京": "320100", "南京市": "320100",
    "武汉": "420100", "武汉市": "420100",
    "西安": "610100", "西安市": "610100",
    "苏州": "320500", "苏州市": "320500",
    "长沙": "430100", "长沙市": "430100",
    "郑州": "410100", "郑州市": "410100",
    "青岛": "370200", "青岛市": "370200",
    "大连": "210200", "大连市": "210200",
    "宁波": "330200", "宁波市": "330200",
    "厦门": "350200", "厦门市": "350200",
    "济南": "370100", "济南市": "370100",
    "合肥": "340100", "合肥市": "340100",
    "福州": "350100", "福州市": "350100",
    "昆明": "530100", "昆明市": "530100",
    "沈阳": "210100", "沈阳市": "210100",
    "长春": "220100", "长春市": "220100",
    "哈尔滨": "230100", "哈尔滨市": "230100",
    "太原": "140100", "太原市": "140100",
    "石家庄": "130100", "石家庄市": "130100",
}


def _resolve_city_adcode(city: str) -> str | None:
    """城市名 → 6 位 adcode（优先本地 SHP，缺失时用内置映射）。

    复用 app.tools.local_admin 的 _load_level 缓存，避免重复读盘。
    匹配语义与 query_admin_boundary 一致：按包含匹配，兼容“成都”→“成都市”。
    """
    name = (city or "").strip()
    if not name:
        return None
    # 1. 优先本地 SHP
    try:
        from app.tools.local_admin import _load_level, _LEVEL_ADCODE_COL, _LEVEL_NAME_COL

        for level in ("city", "district", "province"):
            gdf = _load_level(level)
            if gdf is None or getattr(gdf, "empty", False):
                continue
            name_cols = _LEVEL_NAME_COL.get(level, ())
            adcode_cols = _LEVEL_ADCODE_COL.get(level, ())
            name_col = next((c for c in name_cols if c in gdf.columns), None)
            adcode_col = next((c for c in adcode_cols if c in gdf.columns), None)
            if not name_col or not adcode_col:
                continue
            try:
                mask = gdf[name_col].astype(str).str.contains(name, na=False, regex=False)
            except Exception:
                continue
            if not mask.any():
                bare = name[:-1] if name.endswith("市") else None
                if bare:
                    try:
                        mask = gdf[name_col].astype(str).str.contains(bare, na=False, regex=False)
                    except Exception:
                        mask = None
                    if mask is None or not mask.any():
                        continue
                else:
                    continue
            matched = gdf[mask]
            for _, row in matched.iterrows():
                raw = row.get(adcode_col) or row.get("adcode")
                if raw is None or str(raw).strip() == "":
                    continue
                code = str(raw).strip().replace(".0", "")
                if code.isdigit():
                    return code.zfill(6)
                return code
    except Exception:
        pass
    # 2. 内置常见城市映射兜底（保障无 SHP 的 CI / 单测环境）
    if name in _FALLBACK_CITY_ADCODE:
        return _FALLBACK_CITY_ADCODE[name]
    bare = name[:-1] if name.endswith("市") else None
    if bare and bare in _FALLBACK_CITY_ADCODE:
        return _FALLBACK_CITY_ADCODE[bare]
    return None



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
        # specifyAdminCode 必须是数字行政区代码（如 "110000"）；城市名为中文时
        # 解析为 adcode，无法解析则显式失败，绝不静默全球检索（#683）。
        _clamped = max(1, min(int(limit), 25)) if isinstance(limit, int) else max(1, min(int(limit or 20), 25))
        payload: dict = {
            "keyWord": keyword,
            "level": "12",
            "mapBound": "-180,-90,180,90",
            "queryType": "1",
            "start": "0",
            "count": str(_clamped),
        }
        if city:
            if city.isdigit():
                payload["specifyAdminCode"] = city
            else:
                adcode = _resolve_city_adcode(city)
                if adcode:
                    payload["specifyAdminCode"] = adcode
                else:
                    return {
                        "error": (
                            f"Tianditu 不支持城市 '{city}' 的城市过滤：无法解析为行政区代码（adcode）。"
                            f"请改用高德/百度，或传入该城市的 6 位 adcode（如成都市 510100）"
                        ),
                        "correction_hint": "传入数字 adcode（如 '510100'）或改用 provider='amap'/'baidu'",
                    }
        post_str = json.dumps(payload, ensure_ascii=False)
        # 2026-08 实测：官方搜索服务已从 /search 迁移到 /v2/search（旧路径
        # 返回品牌化 404 HTML 页），geocoder 仍在 /geocoder 不变。
        data = await self._get("/v2/search", {"postStr": post_str, "type": "query"})
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
        data = await self._get("/v2/search", {"postStr": post_str, "type": "query"})
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
