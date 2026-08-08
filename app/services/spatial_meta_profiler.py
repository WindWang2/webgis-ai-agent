import json
from pathlib import Path
from typing import Any, Dict, List, Union

from app.utils.geojson import geojson_bbox


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
  if bbox is not None:
    west, south, east, north = bbox
    center_lng = round((west + east) / 2, 6)
    center_lat = round((south + north) / 2, 6)
    zoom = _calculate_suggested_zoom(west, south, east, north)
    suggested_view = {"center": [center_lng, center_lat], "zoom": zoom}
  else:
    suggested_view = {}

  # Profile fields
  field_values: Dict[str, List[Any]] = {}
  for f in features:
    props = f.get("properties") or {}
    for k, v in props.items():
      if k not in field_values:
        field_values[k] = []
      if v is not None:
        field_values[k].append(v)

  fields_profile: Dict[str, Dict[str, Any]] = {}
  for k, vals in field_values.items():
    if not vals:
      fields_profile[k] = {"type": "string", "sampleValues": []}
      continue

    # Determine type
    numeric_vals = [float(v) for v in vals if isinstance(v, (int, float)) and not isinstance(v, bool)]
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
      }
    elif len(bool_vals) == len(vals):
      fields_profile[k] = {
          "type": "boolean",
          "sampleValues": list(dict.fromkeys(vals))[:5],
      }
    else:
      # String / Date
      sample = list(dict.fromkeys(vals))[:5]
      fields_profile[k] = {
          "type": "string",
          "sampleValues": sample,
      }

  # Temporal profiling
  from app.services.temporal.profiler import profile_temporal_dataset
  temporal_profile = profile_temporal_dataset(features)

  return {
      "bbox": bbox,
      "crs": "EPSG:4326",
      "featureCount": feature_count,
      "geometryTypes": geom_types,
      "fields": fields_profile,
      "suggestedView": suggested_view,
      "temporalProfile": temporal_profile.model_dump() if temporal_profile.overall_confidence > 0 else None,
  }

