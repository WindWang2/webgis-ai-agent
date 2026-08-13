import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from app.utils.geojson import geojson_bbox


def _declared_crs(data: Dict[str, Any]) -> Tuple[Optional[str], str]:
  """Return only CRS evidence explicitly carried by the source descriptor."""
  raw = data.get("crs")
  if isinstance(raw, str) and raw.strip():
    return raw.strip(), "explicit"
  if isinstance(raw, dict):
    properties = raw.get("properties")
    if isinstance(properties, dict):
      name = properties.get("name")
      if isinstance(name, str) and name.strip():
        return name.strip(), "explicit"
      code = properties.get("code")
      if code is not None and str(code).strip():
        authority = str(raw.get("type") or "EPSG").upper()
        return f"{authority}:{str(code).strip()}", "explicit"
  return None, "unknown"


def _is_explicit_geographic_crs(crs: Optional[str]) -> bool:
  if not crs:
    return False
  normalized = crs.upper().replace(" ", "")
  return normalized in {
      "EPSG:4326",
      "CRS:84",
      "OGC:CRS84",
      "URN:OGC:DEF:CRS:EPSG::4326",
      "HTTP://WWW.OPENGIS.NET/DEF/CRS/EPSG/0/4326",
      "HTTPS://WWW.OPENGIS.NET/DEF/CRS/EPSG/0/4326",
      "URN:OGC:DEF:CRS:OGC:1.3:CRS84",
      "HTTP://WWW.OPENGIS.NET/DEF/CRS/OGC/1.3/CRS84",
      "HTTPS://WWW.OPENGIS.NET/DEF/CRS/OGC/1.3/CRS84",
  }


def _calculate_suggested_zoom(west: float, south: float, east: float, north: float) -> int:
  dx = abs(east - west)
  dy = abs(north - south)
  span = max(dx, dy)

  if span <= 0:
    return 12
  if span >= 180:
    return 1
  if span >= 90:
    return 2
  if span >= 40:
    return 3
  if span >= 20:
    return 4
  if span >= 10:
    return 5
  if span >= 5:
    return 6
  if span >= 2.5:
    return 7
  if span >= 1.0:
    return 8
  if span >= 0.5:
    return 9
  if span >= 0.2:
    return 10
  if span >= 0.1:
    return 11
  if span >= 0.05:
    return 12
  if span >= 0.02:
    return 13
  if span >= 0.01:
    return 14
  return 15


def profile_geojson_source(geojson_data: Union[Dict[str, Any], str, bytes, Path]) -> Dict[str, Any]:
  """
  Analyzes a GeoJSON data source and produces a Spatial Meta Profile.
  """
  if isinstance(geojson_data, (str, Path)):
    path_obj = Path(geojson_data)
    if path_obj.is_file():
      with open(path_obj, "r", encoding="utf-8") as f:
        data = json.load(f)
    else:
      data = json.loads(str(geojson_data))
  elif isinstance(geojson_data, bytes):
    data = json.loads(geojson_data.decode("utf-8"))
  else:
    data = geojson_data

  features: List[Dict[str, Any]] = []
  if isinstance(data, dict):
    if data.get("type") == "FeatureCollection":
      features = data.get("features", [])
    elif data.get("type") == "Feature":
      features = [data]
    elif "features" in data:
      features = data["features"]

  feature_count = len(features)
  crs, crs_status = _declared_crs(data) if isinstance(data, dict) else (None, "unknown")

  # bbox: route through the canonical geojson_bbox (handles Feature / Geometry /
  # Collection + bbox short-circuit). geom_types is profiler-specific (single
  # consumer), so it stays inline here rather than widening geojson_bbox's
  # interface (Candidate #4).
  bbox = geojson_bbox(data) if isinstance(data, dict) else None

  geom_types = sorted({
      (f.get("geometry") or {}).get("type")
      for f in features
      if isinstance(f, dict) and (f.get("geometry") or {}).get("type")
  })

  # Empty source → no bbox → no suggestedView. Previously this returned
  # [0,0,0,0], whose center [0,0] (Null Island) got auto-injected as the
  # map view; now the downstream view_has_center check skips it.
  if bbox is not None and crs_status == "explicit" and _is_explicit_geographic_crs(crs):
    west, south, east, north = bbox
    center_lng = round((west + east) / 2, 6)
    center_lat = round((south + north) / 2, 6)
    zoom = _calculate_suggested_zoom(west, south, east, north)
    suggested_view = {"center": [center_lng, center_lat], "zoom": zoom}
  else:
    suggested_view = {}

  # Profile fields
  field_values: Dict[str, List[Any]] = {}
  field_keys: set[str] = set()
  for f in features:
    props = f.get("properties") or {}
    field_keys.update(str(k) for k in props)
    for k, v in props.items():
      if k not in field_values:
        field_values[k] = []
      if v is not None:
        field_values[k].append(v)

  fields_profile: Dict[str, Dict[str, Any]] = {}
  for k in sorted(field_keys):
    vals = field_values.get(k, [])
    null_count = sum(
        1
        for feature in features
        if not isinstance(feature.get("properties"), dict)
        or feature.get("properties", {}).get(k) is None
    )
    if not vals:
      fields_profile[k] = {
          "type": "string",
          "sampleValues": [],
          "null_count": null_count,
      }
      continue

    # Determine type
    numeric_vals = [
        float(v) for v in vals
        if isinstance(v, (int, float))
        and not isinstance(v, bool)
        and math.isfinite(float(v))
    ]
    bool_vals = [v for v in vals if isinstance(v, bool)]

    if len(numeric_vals) == len(vals):
      field_type = "number"
      f_min = min(numeric_vals)
      f_max = max(numeric_vals)
      f_mean = sum(numeric_vals) / len(numeric_vals)
      sample = list(dict.fromkeys(vals))[:5]
      fields_profile[k] = {
          "type": field_type,
          "min": round(f_min, 4),
          "max": round(f_max, 4),
          "mean": round(f_mean, 4),
          "sampleValues": sample,
          "null_count": null_count,
      }
    elif len(bool_vals) == len(vals):
      fields_profile[k] = {
          "type": "boolean",
          "sampleValues": list(dict.fromkeys(vals))[:5],
          "null_count": null_count,
      }
    else:
      # String / Date
      sample = list(dict.fromkeys(vals))[:5]
      fields_profile[k] = {
          "type": "string",
          "sampleValues": sample,
          "null_count": null_count,
      }

  # Temporal profiling
  from app.services.temporal.profiler import profile_temporal_dataset
  temporal_profile = profile_temporal_dataset(features)

  return {
      "bbox": bbox,
      "crs": crs,
      "crs_status": crs_status,
      "featureCount": feature_count,
      "geometryTypes": geom_types,
      "fields": fields_profile,
      # The profiler scanned the complete supplied feature collection, so a
      # missing key is authoritative absence. Descriptor-only profiles use
      # ``unknown`` instead; semantic review must not turn unavailable schema
      # metadata into a false missing-field failure.
      "fields_status": "explicit",
      "suggestedView": suggested_view,
      "temporalProfile": temporal_profile.model_dump() if temporal_profile.overall_confidence > 0 else None,
  }
