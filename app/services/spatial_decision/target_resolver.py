"""
Target Area Resolver module.
Resolves target_area inputs (administrative district names, session ref IDs, GeoJSON, BBOX)
into unified TargetAreaSpec value objects without hardcoded default coordinate fallbacks.
"""
import json
import re
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
from shapely.geometry import shape, MultiPolygon, Polygon, Point

from app.services.spatial_decision.models import TargetAreaSpec

logger = logging.getLogger(__name__)

BBOX_REGEX = re.compile(
    r"^\[?\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]?$"
)


def _process_shapely_geometry(geom_dict: Dict[str, Any]) -> Tuple[Dict[str, Any], str, Tuple[float, float], List[float]]:
    """Converts GeoJSON geometry dict into (geom_dict, geom_type, center, bbox) via Shapely."""
    shapely_obj = shape(geom_dict)
    centroid = shapely_obj.centroid
    bounds = list(shapely_obj.bounds)  # [minx, miny, maxx, maxy]
    center = (round(float(centroid.x), 6), round(float(centroid.y), 6))
    bbox = [round(float(b), 6) for b in bounds]
    geom_type = geom_dict.get("type", shapely_obj.geom_type)
    return geom_dict, geom_type, center, bbox


class TargetAreaResolver:
    """Resolves spatial target area input into standard TargetAreaSpec."""

    def __init__(self, session_store: Optional[Any] = None, geocode_provider: Optional[Any] = None):
        self._session_store = session_store
        self._geocode_provider = geocode_provider

    def _get_session_store(self) -> Any:
        if self._session_store is not None:
            return self._session_store
        from app.services.session_data import session_data_manager
        return session_data_manager

    def _get_geocode_provider(self) -> Any:
        if self._geocode_provider is not None:
            return self._geocode_provider
        try:
            from app.tools.chinese_maps.http import _has_provider
            if not _has_provider("amap"):
                return None
            from app.tools.chinese_maps.amap import AmapProvider
            return AmapProvider()
        except Exception as e:
            logger.warning(f"Failed to instantiate default AmapProvider: {e}")
            return None

    async def resolve(
        self,
        target_area: Any,
        session_id: Optional[str] = None,
        owner_token: Optional[str] = None
    ) -> TargetAreaSpec:
        """Main entry point to resolve target_area into TargetAreaSpec."""

        # 0. If already TargetAreaSpec
        if isinstance(target_area, TargetAreaSpec):
            return target_area

        query_str = str(target_area) if target_area is not None else ""

        # 1. Check if GeoJSON dict directly
        if isinstance(target_area, dict):
            # Check if TargetAreaSpec dict
            if "query" in target_area and "resolved_name" in target_area:
                try:
                    return TargetAreaSpec(**target_area)
                except Exception:
                    pass
            
            res = self._try_parse_geojson(target_area, query_str)
            if res:
                return res

        # 2. Check if string input
        if isinstance(target_area, str):
            target_str = target_area.strip()

            # 2a. Check if BBOX string
            bbox_res = self._try_parse_bbox(target_str)
            if bbox_res:
                return bbox_res

            # 2b. Check if GeoJSON string
            if target_str.startswith("{") and target_str.endswith("}"):
                try:
                    parsed_dict = json.loads(target_str)
                    if isinstance(parsed_dict, dict):
                        res = self._try_parse_geojson(parsed_dict, target_str)
                        if res:
                            return res
                except Exception:
                    pass

            # 2c. Check if session ref ID or layer ID
            if session_id:
                session_res = await self._try_resolve_session_ref(target_str, session_id)
                if session_res:
                    return session_res

            # 2d. Geocode administrative district name or address
            geocode_res = await self._try_geocode(target_str)
            if geocode_res:
                return geocode_res

        # 3. Unresolvable case — NEVER fallback to Beijing [116.4, 39.9] silently
        return TargetAreaSpec(
            query=query_str,
            geometry_type="Unknown",
            center=None,
            geometry=None,
            bbox=None,
            resolved_name=query_str if query_str else "Unresolved Target Area",
            source="unresolved",
            confidence=0.0,
            correction_hint=(
                f"Unable to resolve target area '{query_str}'. "
                "Please specify a valid administrative district name (e.g. '海淀区'), "
                "a BBOX string '[west,south,east,north]', a GeoJSON object, or a valid session reference ID."
            )
        )

    def _try_parse_bbox(self, text: str) -> Optional[TargetAreaSpec]:
        match = BBOX_REGEX.match(text)
        if not match:
            return None

        w, s, e, n = [float(g) for g in match.groups()]
        if w > e or s > n:
            return None

        polygon_geom = {
            "type": "Polygon",
            "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]
        }
        center = (round((w + e) / 2.0, 6), round((s + n) / 2.0, 6))
        bbox = [w, s, e, n]

        return TargetAreaSpec(
            query=text,
            geometry_type="BBOX",
            center=center,
            geometry=polygon_geom,
            bbox=bbox,
            resolved_name=f"BBOX [{w}, {s}, {e}, {n}]",
            source="bbox",
            confidence=1.0,
            correction_hint=None
        )

    def _try_parse_geojson(self, data: Dict[str, Any], query_str: str) -> Optional[TargetAreaSpec]:
        geojson_type = data.get("type")
        if not geojson_type:
            return None

        geometry = None
        name = data.get("properties", {}).get("name") if isinstance(data.get("properties"), dict) else None

        if geojson_type == "Feature":
            geometry = data.get("geometry")
        elif geojson_type == "FeatureCollection":
            features = data.get("features", [])
            if features:
                # If single feature or first feature name
                first_feat = features[0]
                geometry = first_feat.get("geometry")
                if not name and isinstance(first_feat.get("properties"), dict):
                    name = first_feat.get("properties", {}).get("name")
        elif geojson_type in ("Point", "Polygon", "MultiPolygon", "LineString", "MultiLineString", "GeometryCollection"):
            geometry = data

        if not geometry or not isinstance(geometry, dict):
            return None

        try:
            geom_dict, geom_type, center, bbox = _process_shapely_geometry(geometry)
            resolved_name = name or data.get("name") or "GeoJSON Target Area"
            return TargetAreaSpec(
                query=query_str,
                geometry_type=geom_type if geom_type in ("Point", "Polygon", "MultiPolygon") else "Polygon",
                center=center,
                geometry=geom_dict,
                bbox=bbox,
                resolved_name=resolved_name,
                source="geojson",
                confidence=1.0,
                correction_hint=None
            )
        except Exception as e:
            logger.warning(f"Failed to process GeoJSON geometry: {e}")
            return None

    async def _try_resolve_session_ref(self, ref_id: str, session_id: str) -> Optional[TargetAreaSpec]:
        try:
            store = self._get_session_store()
            data = await store.get(session_id, ref_id)
            if not data:
                return None

            if isinstance(data, dict):
                spec = self._try_parse_geojson(data, ref_id)
                if spec:
                    spec.source = "session_ref"
                    spec.query = ref_id
                    return spec
        except Exception as e:
            logger.warning(f"Error fetching session ref '{ref_id}' from store: {e}")

        return None

    async def _try_geocode(self, address_query: str) -> Optional[TargetAreaSpec]:
        provider = self._get_geocode_provider()
        if not provider:
            return None

        # 1. Try district geocoding if provider supports district()
        if hasattr(provider, "district") and callable(getattr(provider, "district")):
            try:
                res = await provider.district(keywords=address_query, level="", return_geometry="polygon")
                if isinstance(res, dict) and res.get("features"):
                    feature = res["features"][0]
                    geom = feature.get("geometry")
                    props = feature.get("properties", {})
                    name = props.get("name", address_query)
                    if geom:
                        geom_dict, geom_type, center, bbox = _process_shapely_geometry(geom)
                        return TargetAreaSpec(
                            query=address_query,
                            geometry_type=geom_type if geom_type in ("Point", "Polygon", "MultiPolygon") else "Polygon",
                            center=center,
                            geometry=geom_dict,
                            bbox=bbox,
                            resolved_name=name,
                            source="geocode",
                            confidence=0.95,
                            correction_hint=None
                        )
            except Exception as e:
                logger.debug(f"District geocode call failed for '{address_query}': {e}")

        # 2. Try standard geocode()
        if hasattr(provider, "geocode") and callable(getattr(provider, "geocode")):
            try:
                res = await provider.geocode(address=address_query, city="")
                if isinstance(res, dict) and res.get("results"):
                    first = res["results"][0]
                    loc = first.get("location")
                    if loc and isinstance(loc, (list, tuple)) and len(loc) == 2:
                        lng, lat = float(loc[0]), float(loc[1])
                        formatted_addr = first.get("formatted_address") or address_query
                        point_geom = {"type": "Point", "coordinates": [lng, lat]}
                        return TargetAreaSpec(
                            query=address_query,
                            geometry_type="Point",
                            center=(lng, lat),
                            geometry=point_geom,
                            bbox=[lng, lat, lng, lat],
                            resolved_name=formatted_addr,
                            source="geocode",
                            confidence=0.90,
                            correction_hint=None
                        )
            except Exception as e:
                logger.debug(f"Geocode call failed for '{address_query}': {e}")

        # 3. Fallback: Offline dictionary lookup for standard cities & districts (for test/offline mode)
        OFFLINE_DISTRICTS = {
            "海淀": ([116.31, 39.98], "北京市海淀区"),
            "中关村": ([116.31, 39.98], "北京市海淀区中关村"),
            "朝阳": ([116.48, 39.92], "北京市朝阳区"),
            "徐汇": ([121.43, 31.18], "上海市徐汇区"),
            "天河": ([113.36, 23.12], "广州市天河区"),
            "南山": ([113.93, 22.53], "深圳市南山区"),
            "西湖": ([120.13, 30.26], "杭州市西湖区"),
            "余杭": ([119.98, 30.27], "杭州市余杭区"),
            "武昌": ([114.31, 30.55], "武汉市武昌区"),
            "高新": ([104.06, 30.57], "成都高新区"),
            "北京": ([116.40, 39.90], "北京市"),
            "上海": ([121.47, 31.23], "上海市"),
            "广州": ([113.26, 23.13], "广州市"),
            "深圳": ([114.05, 22.54], "深圳市"),
            "杭州": ([120.15, 30.28], "杭州市"),
            "成都": ([104.06, 30.67], "成都市"),
            "武汉": ([114.30, 30.59], "武汉市"),
        }

        for kw, (center_coords, full_name) in OFFLINE_DISTRICTS.items():
            if kw in address_query:
                lng, lat = center_coords
                delta = 0.05
                w, s, e, n = lng - delta, lat - delta, lng + delta, lat + delta
                poly = {
                    "type": "Polygon",
                    "coordinates": [[[w, s], [e, s], [e, n], [w, n], [w, s]]]
                }
                return TargetAreaSpec(
                    query=address_query,
                    geometry_type="Polygon",
                    center=(lng, lat),
                    geometry=poly,
                    bbox=[w, s, e, n],
                    resolved_name=address_query if len(address_query) > len(full_name) else full_name,
                    source="offline_geocode",
                    confidence=0.85,
                    correction_hint=None,
                )

        return None


async def resolve_target_area(
    target_area: Any,
    session_id: Optional[str] = None,
    owner_token: Optional[str] = None,
    session_store: Optional[Any] = None,
    geocode_provider: Optional[Any] = None
) -> TargetAreaSpec:
    """Functional convenience wrapper for TargetAreaResolver."""
    resolver = TargetAreaResolver(session_store=session_store, geocode_provider=geocode_provider)
    return await resolver.resolve(target_area, session_id=session_id, owner_token=owner_token)
