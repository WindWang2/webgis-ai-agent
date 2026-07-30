import json
import math
from pathlib import Path
from typing import Any, Dict, List, Union


def _calculate_suggested_zoom(minx: float, miny: float, maxx: float, maxy: float) -> int:
  dx = abs(maxx - minx)
  dy = abs(maxy - miny)
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


def _extract_bbox_and_geometries(features: List[Dict[str, Any]]) -> tuple[List[float], List[str]]:
  minx, miny = float("inf"), float("inf")
  maxx, maxy = float("-inf"), float("-inf")
  geom_types = set()

  def process_coords(coords: Any):
    nonlocal minx, miny, maxx, maxy
    if not coords:
      return
    if isinstance(coords[0], (int, float)):
      x, y = float(coords[0]), float(coords[1])
      if x < minx:
        minx = x
      if x > maxx:
        maxx = x
      if y < miny:
        miny = y
      if y > maxy:
        maxy = y
    else:
      for sub in coords:
        process_coords(sub)

  for f in features:
    geom = f.get("geometry")
    if not geom:
      continue
    gtype = geom.get("type")
    if gtype:
      geom_types.add(gtype)
    coords = geom.get("coordinates")
    if coords:
      process_coords(coords)

  if minx == float("inf"):
    return [0.0, 0.0, 0.0, 0.0], sorted(list(geom_types))

  return [round(minx, 6), round(miny, 6), round(maxx, 6), round(maxy, 6)], sorted(list(geom_types))


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
  bbox, geom_types = _extract_bbox_and_geometries(features)

  center_lng = round((bbox[0] + bbox[2]) / 2, 6)
  center_lat = round((bbox[1] + bbox[3]) / 2, 6)
  zoom = _calculate_suggested_zoom(bbox[0], bbox[1], bbox[2], bbox[3])

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

  return {
      "bbox": bbox,
      "crs": "EPSG:4326",
      "featureCount": feature_count,
      "geometryTypes": geom_types,
      "fields": fields_profile,
      "suggestedView": {"center": [center_lng, center_lat], "zoom": zoom},
  }
