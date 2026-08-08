"""
Baseline Resolver module.
Extracts and computes spatial baseline metric values from SessionStore datasets or spatial features.
Ensures evidence gaps are explicitly flagged (missing_baseline=True) and auto-heals spatial metrics where possible without dummy fallbacks.
"""
import math
import logging
from typing import Any, Dict, List, Optional, Union
from shapely.geometry import shape

from app.services.spatial_decision.models import TargetAreaSpec, MetricDeltaV2, MetricRange

logger = logging.getLogger(__name__)

METRIC_META_MAP = {
    "housing_price": ("Housing Price", "RMB/m2"),
    "price": ("Average Price", "RMB/m2"),
    "green_coverage": ("Greenery Coverage Rate", "%"),
    "green_ratio": ("Green Ratio", "%"),
    "pop_density": ("Population Density", "people/km2"),
    "population_density": ("Population Density", "people/km2"),
    "area_km2": ("Target Surface Area", "km2"),
    "target_area_km2": ("Target Area", "km2"),
    "poi_density": ("POI Density", "points/km2"),
    "noise_level": ("Noise Level", "dB"),
    "subway_access_min": ("Subway Access Time", "min"),
}


def _humanize_metric(key: str) -> tuple[str, str]:
    if key in METRIC_META_MAP:
        return METRIC_META_MAP[key]
    return (key.replace("_", " ").title(), "")


def _calculate_geometry_area_km2(geometry_dict: Optional[Dict[str, Any]]) -> float:
    """Calculates spatial area in square kilometers for a GeoJSON geometry."""
    if not geometry_dict:
        return 0.0
    try:
        geom = shape(geometry_dict)
        if geom.is_empty or geom.area == 0:
            return 0.0

        centroid_lat = geom.centroid.y
        lat_rad = math.radians(centroid_lat)
        
        # Approx 1 deg lat ~ 111.32 km, 1 deg lng ~ 111.32 * cos(lat) km
        km_per_deg_lat = 111.32
        km_per_deg_lng = 111.32 * math.cos(lat_rad)
        
        area_km2 = geom.area * km_per_deg_lat * km_per_deg_lng
        return round(float(area_km2), 4)
    except Exception as e:
        logger.warning(f"Error calculating geometry area: {e}")
        return 0.0


class BaselineResolver:
    """Resolves baseline quantitative metrics for a given target area and baseline reference."""

    def __init__(self, session_store: Optional[Any] = None):
        self._session_store = session_store

    def _get_session_store(self) -> Any:
        if self._session_store is not None:
            return self._session_store
        from app.services.session_data_protocol import get_session_store
        return get_session_store()

    async def resolve_baseline(
        self,
        baseline_data_ref: str,
        target_area: TargetAreaSpec,
        session_id: str = "",
        metrics_needed: Optional[List[str]] = None
    ) -> Dict[str, MetricDeltaV2]:
        """Fetches baseline data and computes baseline metric values."""
        metrics_needed = metrics_needed or []
        resolved_metrics: Dict[str, MetricDeltaV2] = {}

        stored_data = None
        if baseline_data_ref and session_id:
            store = self._get_session_store()
            try:
                stored_data = await store.get(session_id, baseline_data_ref)
            except Exception as e:
                logger.warning(f"Failed to fetch baseline_data_ref '{baseline_data_ref}': {e}")

        # Attempt to extract explicit metrics map from stored data
        extracted_raw_metrics: Dict[str, float] = {}
        if isinstance(stored_data, dict):
            if "metrics" in stored_data and isinstance(stored_data["metrics"], dict):
                for k, v in stored_data["metrics"].items():
                    if isinstance(v, (int, float)):
                        extracted_raw_metrics[k] = float(v)
            else:
                # Top level numeric dict
                for k, v in stored_data.items():
                    if isinstance(v, (int, float)):
                        extracted_raw_metrics[k] = float(v)

        # Attempt spatial feature attribute aggregation if stored_data is GeoJSON FeatureCollection
        geojson_features = []
        if isinstance(stored_data, dict) and stored_data.get("type") == "FeatureCollection":
            geojson_features = stored_data.get("features", [])
        elif isinstance(stored_data, list):
            geojson_features = stored_data

        if geojson_features:
            spatial_aggregated = self._aggregate_spatial_features(geojson_features, target_area)
            extracted_raw_metrics.update(spatial_aggregated)

        # Process each requested metric
        for metric_key in metrics_needed:
            m_name, m_unit = _humanize_metric(metric_key)

            if metric_key in extracted_raw_metrics:
                val = extracted_raw_metrics[metric_key]
                resolved_metrics[metric_key] = MetricDeltaV2(
                    metric_key=metric_key,
                    metric_name=m_name,
                    baseline=val,
                    simulated=val,
                    delta_abs=0.0,
                    delta_pct=0.0,
                    unit=m_unit,
                    missing_baseline=False,
                    evidence_gap_note=None
                )
            else:
                # Check for spatial auto-healing (e.g. area_km2)
                auto_healed_val = self._try_auto_heal_metric(metric_key, target_area)
                if auto_healed_val is not None:
                    resolved_metrics[metric_key] = MetricDeltaV2(
                        metric_key=metric_key,
                        metric_name=m_name,
                        baseline=auto_healed_val,
                        simulated=auto_healed_val,
                        delta_abs=0.0,
                        delta_pct=0.0,
                        unit=m_unit,
                        missing_baseline=False,
                        evidence_gap_note=None
                    )
                else:
                    # Missing metric — flag evidence gap, NEVER return dummy 100.0!
                    resolved_metrics[metric_key] = MetricDeltaV2(
                        metric_key=metric_key,
                        metric_name=m_name,
                        baseline=0.0,
                        simulated=0.0,
                        delta_abs=0.0,
                        delta_pct=0.0,
                        unit=m_unit,
                        missing_baseline=True,
                        evidence_gap_note=(
                            f"Baseline dataset reference '{baseline_data_ref}' missing or does not contain metric '{metric_key}'. "
                            "Recorded evidence gap."
                        )
                    )

        return resolved_metrics

    def _aggregate_spatial_features(
        self,
        features: List[Dict[str, Any]],
        target_area: TargetAreaSpec
    ) -> Dict[str, float]:
        """Spatially filters features within target_area and averages numeric properties."""
        target_shape = None
        if target_area.geometry:
            try:
                target_shape = shape(target_area.geometry)
            except Exception:
                pass

        matching_features = []
        for feat in features:
            if not isinstance(feat, dict) or "geometry" not in feat:
                continue
            if target_shape:
                try:
                    feat_geom = shape(feat["geometry"])
                    if not target_shape.intersects(feat_geom):
                        continue
                except Exception:
                    pass
            matching_features.append(feat)

        if not matching_features:
            return {}

        # Collect numeric attributes
        prop_sums: Dict[str, float] = {}
        prop_counts: Dict[str, int] = {}

        for feat in matching_features:
            props = feat.get("properties", {})
            if not isinstance(props, dict):
                continue
            for k, v in props.items():
                if isinstance(v, (int, float)):
                    prop_sums[k] = prop_sums.get(k, 0.0) + float(v)
                    prop_counts[k] = prop_counts.get(k, 0) + 1

        averages = {}
        for k, total in prop_sums.items():
            cnt = prop_counts[k]
            if cnt > 0:
                averages[k] = round(total / cnt, 4)

        return averages

    def _try_auto_heal_metric(self, metric_key: str, target_area: TargetAreaSpec) -> Optional[float]:
        """Auto-heals spatial metrics from target_area geometry where possible."""
        if metric_key in ("area_km2", "target_area_km2", "area"):
            area = _calculate_geometry_area_km2(target_area.geometry)
            if area > 0:
                return area
        return None


async def resolve_baseline_metrics(
    baseline_data_ref: str,
    target_area: TargetAreaSpec,
    session_id: str = "",
    metrics_needed: Optional[List[str]] = None,
    session_store: Optional[Any] = None
) -> Dict[str, MetricDeltaV2]:
    """Functional wrapper for BaselineResolver."""
    resolver = BaselineResolver(session_store=session_store)
    return await resolver.resolve_baseline(
        baseline_data_ref=baseline_data_ref,
        target_area=target_area,
        session_id=session_id,
        metrics_needed=metrics_needed
    )
