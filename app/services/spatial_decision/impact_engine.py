"""
Spatial Impact Engine V2.
Generates dynamic spatial impact zones (direct & indirect ring polygons) around target area geometries,
computes precise surface areas in km2 using UTM projections, and models distance decay functions.
"""
import logging
import math
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np
import geopandas as gpd
from shapely.geometry import shape, Point, MultiPolygon, box
from app.lib.geo_processor.core import to_utm_gdf
from app.services.spatial_decision.models import TargetAreaSpec, SpatialImpactZone

logger = logging.getLogger(__name__)


class SpatialImpactEngine:
    """Calculates spatial impact zones, UTM surface areas, GeoJSON polygons, and distance decay."""

    def __init__(self, default_decay_radius: float = 1000.0) -> None:
        self.default_decay_radius = default_decay_radius

    def extract_geometry(self, target_area: Union[TargetAreaSpec, Dict[str, Any], Any]) -> Any:
        """Extract a Shapely geometry object in WGS84 (EPSG:4326) from various input types."""
        if isinstance(target_area, TargetAreaSpec):
            if target_area.geometry:
                return shape(target_area.geometry)
            elif target_area.center:
                lng, lat = target_area.center
                return Point(lng, lat)
            elif target_area.bbox and len(target_area.bbox) == 4:
                w, s, e, n = target_area.bbox
                return box(w, s, e, n)
            raise ValueError("TargetAreaSpec does not contain valid geometry, center, or bbox.")

        if isinstance(target_area, dict):
            if target_area.get("type") == "FeatureCollection":
                features = target_area.get("features", [])
                if not features:
                    raise ValueError("Empty FeatureCollection provided.")
                geoms = [shape(f["geometry"]) for f in features if f.get("geometry")]
                if len(geoms) == 1:
                    return geoms[0]
                return MultiPolygon(geoms) if all(g.geom_type in ("Polygon", "MultiPolygon") for g in geoms) else geoms[0]
            elif target_area.get("type") == "Feature":
                return shape(target_area["geometry"])
            elif "type" in target_area:
                return shape(target_area)

        if hasattr(target_area, "geom_type"):
            return target_area

        raise ValueError(f"Unsupported target_area type: {type(target_area)}")

    def compute_utm_area_km2(self, geometry_wgs84: Any) -> float:
        """Compute surface area in km2 using local UTM projection."""
        shapely_geom = self.extract_geometry(geometry_wgs84)
        gdf_wgs84 = gpd.GeoDataFrame(geometry=[shapely_geom], crs="EPSG:4326")
        geojson_dict = gdf_wgs84.__geo_interface__
        
        gdf_utm, _ = to_utm_gdf(geojson_dict)
        if gdf_utm is None or gdf_utm.empty:
            return 0.0
        
        area_m2 = float(gdf_utm.geometry.iloc[0].area)
        return area_m2 / 1.0e6

    def generate_impact_zones(
        self,
        target_area: Union[TargetAreaSpec, Dict[str, Any], Any],
        direct_radius_m: float = 500.0,
        indirect_radius_m: float = 1500.0,
        properties: Optional[Dict[str, Any]] = None
    ) -> Tuple[List[SpatialImpactZone], Dict[str, Any]]:
        """
        Generates direct zone polygon and indirect zone ring polygon centered at target_area geometry.
        
        Returns:
            Tuple[List[SpatialImpactZone], Dict[str, Any]]: (Impact zones list, FeatureCollection GeoJSON)
        """
        if indirect_radius_m < direct_radius_m:
            raise ValueError(f"indirect_radius_m ({indirect_radius_m}) must be >= direct_radius_m ({direct_radius_m})")

        try:
            base_geom_wgs84 = self.extract_geometry(target_area)
        except ValueError as e:
            logger.warning(f"Could not extract geometry for target area: {e}")
            return [], {"type": "FeatureCollection", "features": []}
        gdf_wgs84 = gpd.GeoDataFrame(geometry=[base_geom_wgs84], crs="EPSG:4326")
        
        # Project to UTM
        gdf_utm, utm_crs = to_utm_gdf(gdf_wgs84.__geo_interface__)
        if gdf_utm is None or gdf_utm.empty or utm_crs is None:
            # GIS-24 (deep-audit round 3): the previous fallback was
            # EPSG:3857 (Web Mercator), which inflates areas by ~1/cos²(lat)
            # — ~1.7× at Beijing's 40°N — silently corrupting every impact
            # zone area. A failed UTM resolution is a real error (invalid
            # geometry / empty input); surface it instead of computing with a
            # distorted projection.
            raise ValueError(
                "Unable to resolve a projected CRS for the target area; "
                "refusing to compute impact zones in a distorted projection."
            )

        target_utm = gdf_utm.geometry.iloc[0]

        # Generate buffered geometries in UTM space (meters)
        direct_utm = target_utm.buffer(direct_radius_m)
        indirect_outer_utm = target_utm.buffer(indirect_radius_m)
        indirect_ring_utm = indirect_outer_utm.difference(direct_utm)

        # Calculate accurate areas in km2
        direct_area_km2 = float(direct_utm.area) / 1.0e6
        indirect_area_km2 = float(indirect_ring_utm.area) / 1.0e6

        # Reproject back to WGS84 for GeoJSON output
        gdf_direct_utm = gpd.GeoDataFrame(geometry=[direct_utm], crs=utm_crs)
        gdf_indirect_utm = gpd.GeoDataFrame(geometry=[indirect_ring_utm], crs=utm_crs)

        direct_wgs84 = gdf_direct_utm.to_crs("EPSG:4326").geometry.iloc[0]
        indirect_wgs84 = gdf_indirect_utm.to_crs("EPSG:4326").geometry.iloc[0]

        base_props = properties or {}
        direct_props = {
            **base_props,
            "zone_type": "direct",
            "radius_m": direct_radius_m,
            "area_km2": round(direct_area_km2, 4),
            "impact_level": "high",
        }
        indirect_props = {
            **base_props,
            "zone_type": "indirect",
            "radius_m": indirect_radius_m,
            "area_km2": round(indirect_area_km2, 4),
            "impact_level": "medium",
        }

        direct_zone = SpatialImpactZone(
            zone_type="direct",
            radius_m=direct_radius_m,
            area_km2=round(direct_area_km2, 4),
            impact_level="high",
            properties=direct_props,
        )
        indirect_zone = SpatialImpactZone(
            zone_type="indirect",
            radius_m=indirect_radius_m,
            area_km2=round(indirect_area_km2, 4),
            impact_level="medium",
            properties=indirect_props,
        )

        direct_geom_dict = direct_wgs84.__geo_interface__
        if hasattr(indirect_wgs84, "exterior") and hasattr(indirect_wgs84, "interiors") and indirect_wgs84.interiors:
            indirect_coords = [list(indirect_wgs84.exterior.coords)] + [list(ring.coords) for ring in indirect_wgs84.interiors]
            indirect_geom_dict = {"type": "Polygon", "coordinates": indirect_coords}
        else:
            indirect_geom_dict = indirect_wgs84.__geo_interface__

        impact_geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "properties": direct_props,
                    "geometry": direct_geom_dict,
                },
                {
                    "type": "Feature",
                    "properties": indirect_props,
                    "geometry": indirect_geom_dict,
                },
            ],
        }

        return [direct_zone, indirect_zone], impact_geojson

    def evaluate_distance_decay(
        self,
        distance_m: Union[float, np.ndarray],
        delta_max: float,
        decay_radius: Optional[float] = None
    ) -> Union[float, np.ndarray]:
        """
        Distance decay model: delta(r) = delta_max * exp(-r / decay_radius).
        """
        r_decay = decay_radius if decay_radius is not None else self.default_decay_radius
        if r_decay <= 0:
            raise ValueError("decay_radius must be > 0.")

        if isinstance(distance_m, (list, tuple, np.ndarray)):
            arr = np.asarray(distance_m, dtype=float)
            return delta_max * np.exp(-arr / r_decay)

        return float(delta_max * math.exp(-float(distance_m) / r_decay))

    def evaluate_step_decay(
        self,
        distance_m: float,
        direct_radius_m: Union[float, List[Tuple[float, float]]] = 500.0,
        indirect_radius_m: float = 1500.0,
        direct_delta: float = 20.0,
        indirect_delta: float = 10.0,
    ) -> float:
        """Step decay function across direct/indirect zones or interval lists."""
        if isinstance(direct_radius_m, (list, tuple)):
            intervals = direct_radius_m
            for item in intervals:
                if len(item) == 3:
                    min_dist, max_dist, delta_val = item
                    if min_dist <= distance_m <= max_dist:
                        return float(delta_val)
                elif len(item) == 2:
                    max_dist, delta_val = item
                    if distance_m <= max_dist:
                        return float(delta_val)
            return 0.0

        if distance_m <= direct_radius_m:
            return float(direct_delta)
        elif distance_m <= indirect_radius_m:
            return float(indirect_delta)
        return 0.0

    def calculate_impacts(
        self,
        scenario_type: str,
        target_area: TargetAreaSpec,
        rules: Optional[List[Any]] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[SpatialImpactZone], Dict[str, Any]]:
        """Unified facade: calculates spatial impact zones and FeatureCollection GeoJSON."""
        direct_r = 500.0
        indirect_r = 1500.0

        if parameters:
            if "direct_radius_m" in parameters:
                direct_r = float(parameters["direct_radius_m"])
            if "indirect_radius_m" in parameters:
                indirect_r = float(parameters["indirect_radius_m"])

        if rules:
            for r in rules:
                if hasattr(r, "parameters") and r.parameters:
                    if "direct_radius_m" in r.parameters and r.parameters["direct_radius_m"]:
                        direct_r = float(r.parameters["direct_radius_m"])
                    if "indirect_radius_m" in r.parameters and r.parameters["indirect_radius_m"]:
                        indirect_r = float(r.parameters["indirect_radius_m"])

        if scenario_type == "population_growth":
            direct_r = 1000.0
            indirect_r = 3000.0
        elif scenario_type == "traffic_restriction":
            direct_r = 2000.0
            indirect_r = 5000.0

        return self.generate_impact_zones(
            target_area=target_area,
            direct_radius_m=direct_r,
            indirect_radius_m=indirect_r,
        )

    def evaluate_step_intervals(
        self,
        distance_m: float,
        intervals: List[Tuple[float, float, float]],
        default_val: float = 0.0
    ) -> float:
        """
        Step-interval decay function.
        intervals format: [(min_dist, max_dist, delta_value), ...]
        """
        for min_dist, max_dist, val in intervals:
            if min_dist <= distance_m < max_dist:
                return float(val)
        return float(default_val)
