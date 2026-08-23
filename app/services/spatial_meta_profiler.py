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
  # Issue #598: a wrap-around bbox (west > east, e.g. 170..-170) crosses the
  # antimeridian — the true longitudinal span is the arc through ±180
  # (east+360-west), not the naive |east-west| = 340°, which mapped every
  # AM-crossing extent to zoom 1 (whole world). Mirrors the GIS-P3-7 center
  # fix: only wrap bboxes normalize, and the [-180, 180] full-world bbox keeps
  # its 360° span.
  dx = (east + 360.0 - west) if west > east else (east - west)
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


#: 分位断点位置（spec P3）：五数概括 + 十分位端点，覆盖分位/自然断点分类
#: 实际依赖的分布形状，同时保持向量短小（漂移比较是 O(1) 长度）。
QUANTILE_POSITIONS: Tuple[float, ...] = (0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 1.0)


def _quantiles(values: List[float]) -> List[float]:
  """Linear-interpolated quantiles at :data:`QUANTILE_POSITIONS`.

  Distribution shape, not extremes: a classification derived from quantile
  or natural breaks depends on these, so they are the drift anchor for
  project-scoped shared classification schemes (ADR-0069 / spec P3).
  Empty input returns ``[]`` (callers treat absence as unevaluable).
  """
  if not values:
    return []
  ordered = sorted(values)
  last = len(ordered) - 1
  out: List[float] = []
  for position in QUANTILE_POSITIONS:
    exact = position * last
    low = int(math.floor(exact))
    high = min(low + 1, last)
    frac = exact - low
    out.append(round(ordered[low] + (ordered[high] - ordered[low]) * frac, 6))
  return out


def profile_from_descriptor(descriptor: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """#688：O(1) descriptor → Spatial Meta Profile 派生（零全量遍历）。

    store() 时算好的 ref descriptor（#666：bbox/feature_count/geometry_types）
    足够支撑授权路径的消费面——view 注入（suggestedView）、图层类型推断
    （geometryTypes）、指纹。字段直方图不可得，``fields_status`` 置
    ``unknown``（本模块 return 契约注释明确预留的语义：semantic review
    不得把不可得的 schema 元数据当 missing-field 失败）。descriptor 缺失
    或不完整时返回 None，调用方降级全量 profile_geojson_source。
    """
    if not isinstance(descriptor, dict):
        return None
    fc = descriptor.get("feature_count")
    if not isinstance(fc, int) or isinstance(fc, bool) or fc < 0:
        return None
    bbox = descriptor.get("bbox")
    # suggestedView 恒空：全量 profiler 只对显式地理 CRS 计算 view（投影/
    # 未声明坐标上给 view 不安全），而 descriptor 不携带 CRS 信息——派生
    # 路径对齐该保守语义（ref 层 auto-view 本就走不到，见 #680）。
    return {
        "bbox": list(bbox) if isinstance(bbox, (list, tuple)) else None,
        "crs": None,
        "crs_status": "unknown",
        "featureCount": fc,
        "geometryTypes": sorted(
            t for t in (descriptor.get("geometry_types") or []) if isinstance(t, str)
        ),
        "fields": {},
        "fields_status": "unknown",
        "suggestedView": {},
        "temporalProfile": None,
    }


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
    # GIS-P3-7: RFC 7946 wrap-around bboxes (west > east) must center across
    # the antimeridian. Correct derivation: the midpoint of the arc
    # [west, east+360) is (west+east)/2 + 180, then wrapped to [-180, 180].
    # (The naive mean lands on Null Island; a modulo-first variant also
    # collapses to 0° for symmetric bboxes like 170/-170.)
    if west > east:
        center_lng = round((((west + east) / 2 + 180 + 180) % 360) - 180, 6)
    else:
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
    # PERF-F4: the old null_count re-scanned ALL features PER FIELD (O(F·K)
    # full walks per upsert). field_values[k] already collected exactly the
    # non-None values, so the null count is arithmetic.
    null_count = max(0, feature_count - len(vals))
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
          # spec P3: the distribution shape a classification scheme was
          # derived from. Quantiles (not min/max) are what quantile/natural-
          # breaks classifications depend on, so they are the drift anchor.
          # ``null_ratio`` moves independently of the quantiles (a column can
          # keep its shape while going half-empty), so both are recorded.
          "quantiles": _quantiles(numeric_vals),
          "null_ratio": (
              round(null_count / feature_count, 6) if feature_count else 0.0
          ),
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
