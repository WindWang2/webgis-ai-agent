"""Projected 2D metric CRS validator and WGS84 <-> Target CRS transformer (R17, R18)."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError

from app.schemas.georeferencing_schema import (
    GeorefCoordProvenance,
    GeorefDomainError,
    GeorefErrorCode,
)

_METRE_UNIT_NAMES = {"metre", "meter", "metres", "meters", "m"}


@dataclass(frozen=True)
class ValidatedMetricCRS:
    """Validated 2D projected metric CRS specification."""

    input_crs: str
    canonical_crs: str
    name: str
    unit_name: str
    unit_factor: float
    area_bounds_wgs84: Optional[Tuple[float, float, float, float]]


def validate_metric_projected_crs(crs_input: str) -> ValidatedMetricCRS:
    """Validate that ``crs_input`` is a 2D projected CRS with linear metre units.

    Explicitly rejects geographic (degree) CRS, feet/survey-feet CRS, 3D/vertical,
    and compound CRS to avoid silently mixing degrees/feet with metric resolution
    or RMSE units (R17).
    """
    raw = (crs_input or "").strip()
    if not raw:
        raise GeorefDomainError(
            GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
            "Target CRS cannot be empty.",
            status_code=422,
        )
    try:
        crs = CRS.from_user_input(raw)
    except CRSError as exc:
        raise GeorefDomainError(
            GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
            f"Invalid target CRS '{raw}': {exc}",
            status_code=422,
            details={"crs_input": raw},
        ) from exc

    if crs.is_compound or crs.is_vertical or crs.is_engineering:
        raise GeorefDomainError(
            GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
            f"Target CRS '{raw}' ({crs.name}) is vertical/compound/engineering and unsupported for 2D metric georeferencing.",
            status_code=422,
            details={"crs_input": raw, "crs_type": crs.type_name},
        )
    if crs.is_geographic or not crs.is_projected:
        raise GeorefDomainError(
            GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
            f"Target CRS '{raw}' ({crs.name}) must be a 2D projected metric CRS; geographic degree CRS cannot be used as metric output.",
            status_code=422,
            details={"crs_input": raw, "crs_type": crs.type_name},
        )
    axes = list(crs.axis_info or ())
    if len(axes) != 2:
        raise GeorefDomainError(
            GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
            f"Target CRS '{raw}' has {len(axes)} axes; exactly 2 horizontal axes are required.",
            status_code=422,
            details={"crs_input": raw, "axis_count": len(axes)},
        )

    for ax in axes:
        factor = float(getattr(ax, "unit_conversion_factor", 0.0) or 0.0)
        uname = str(getattr(ax, "unit_name", "") or "").strip()
        if uname.lower() not in _METRE_UNIT_NAMES or not math.isclose(factor, 1.0, rel_tol=1e-6, abs_tol=1e-9):
            raise GeorefDomainError(
                GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
                f"Target CRS '{raw}' uses non-metric axis unit '{uname}' (factor={factor}); only metre-based 2D projected CRS are supported.",
                status_code=422,
                details={"crs_input": raw, "unit_name": uname, "unit_factor": factor},
            )

    auth = crs.to_authority()
    canonical = f"{auth[0]}:{auth[1]}" if auth else crs.to_wkt()
    area_bounds = None
    if crs.area_of_use is not None and crs.area_of_use.bounds:
        b = crs.area_of_use.bounds
        area_bounds = (float(b[0]), float(b[1]), float(b[2]), float(b[3]))

    return ValidatedMetricCRS(
        input_crs=raw,
        canonical_crs=canonical,
        name=crs.name or canonical,
        unit_name=axes[0].unit_name,
        unit_factor=float(axes[0].unit_conversion_factor),
        area_bounds_wgs84=area_bounds,
    )


def convert_wgs84_to_target(
    lon: float,
    lat: float,
    target_crs: str,
) -> GeorefCoordProvenance:
    """Convert WGS84 (lon, lat) map click into target projected metric coordinates."""
    if not (math.isfinite(lon) and math.isfinite(lat)):
        raise GeorefDomainError(
            GeorefErrorCode.INVALID_GCP_COORDS,
            "WGS84 coordinates must be finite.",
            status_code=422,
        )
    if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
        raise GeorefDomainError(
            GeorefErrorCode.INVALID_GCP_COORDS,
            f"WGS84 coordinate ({lon}, {lat}) is outside [-180,180]x[-90,90].",
            status_code=422,
        )
    spec = validate_metric_projected_crs(target_crs)
    crs = CRS.from_user_input(spec.canonical_crs)
    warnings: List[str] = []
    if spec.area_bounds_wgs84 is not None:
        w, s, e, n = spec.area_bounds_wgs84
        if not (w - 2.0 <= lon <= e + 2.0 and s - 2.0 <= lat <= n + 2.0):
            warnings.append(
                f"Point ({lon:.5f}, {lat:.5f}) falls outside recommended area of use for {spec.canonical_crs} ({w:.2f},{s:.2f},{e:.2f},{n:.2f})."
            )
    if spec.canonical_crs == "EPSG:3857" and abs(lat) > 85.05112878:
        raise GeorefDomainError(
            GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
            f"Latitude {lat:.4f} exceeds EPSG:3857 Web Mercator domain [-85.0511, 85.0511].",
            status_code=422,
        )
    transformer = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    x, y = transformer.transform(float(lon), float(lat))
    if not (math.isfinite(x) and math.isfinite(y)):
        raise GeorefDomainError(
            GeorefErrorCode.INVALID_GCP_COORDS,
            f"Coordinate ({lon}, {lat}) failed projection into {spec.canonical_crs}.",
            status_code=422,
        )
    return GeorefCoordProvenance(
        source_crs="EPSG:4326",
        raw_x=float(lon),
        raw_y=float(lat),
        target_crs=spec.canonical_crs,
        map_x=float(x),
        map_y=float(y),
        axis_order="always_xy (lon,lat -> x,y)",
        transform_note="Transformed from WGS84 map click with pyproj(always_xy=True)",
        warnings=warnings,
    )


def ring_to_wgs84_geojson(
    corners_target: List[Tuple[float, float]],
    target_crs: str,
) -> Tuple[Dict[str, Any], List[float]]:
    """Transform target-CRS footprint ring [(x,y), ...] into WGS84 GeoJSON Polygon & bbox."""
    crs = CRS.from_user_input(target_crs)
    transformer = Transformer.from_crs(crs, "EPSG:4326", always_xy=True)
    ring_wgs84: List[List[float]] = []
    for x, y in corners_target:
        lon, lat = transformer.transform(float(x), float(y))
        if not (math.isfinite(lon) and math.isfinite(lat)):
            raise GeorefDomainError(
                GeorefErrorCode.UNSUPPORTED_TARGET_CRS,
                f"Failed to reproject footprint corner ({x}, {y}) from {target_crs} to WGS84.",
                status_code=422,
            )
        ring_wgs84.append([float(lon), float(lat)])
    # Drop a duplicated closure point before unwrapping: the loop below rewrites
    # longitudes and would shift an already-appended closure by +/-360 when the
    # ring's longitudinal sweep exceeds 180 deg, returning a non-closed Polygon.
    if len(ring_wgs84) > 1 and ring_wgs84[0] == ring_wgs84[-1]:
        ring_wgs84.pop()
    # Unwrap longitudes so a ring crossing the antimeridian keeps a continuous
    # sweep: pyproj wraps every corner into [-180, 180], and naive min/max turns
    # a ~300 km UTM zone-60N box into a ~357 deg "spans the globe" bbox. Lons
    # outside [-180, 180] stay valid GeoJSON (well-defined modulo 360).
    prev_lon: Optional[float] = None
    for pt in ring_wgs84:
        lon = pt[0]
        if prev_lon is not None:
            while lon - prev_lon > 180.0:
                lon -= 360.0
            while prev_lon - lon > 180.0:
                lon += 360.0
        pt[0] = lon
        prev_lon = lon
    # Re-append the closure from the already-unwrapped first point so the
    # returned ring is always closed (first position == last position).
    if ring_wgs84:
        ring_wgs84.append(list(ring_wgs84[0]))
    lons = [p[0] for p in ring_wgs84]
    lats = [p[1] for p in ring_wgs84]
    bbox = [min(lons), min(lats), max(lons), max(lats)]
    return {"type": "Polygon", "coordinates": [ring_wgs84]}, bbox
