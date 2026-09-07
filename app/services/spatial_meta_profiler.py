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


def _derived_field_facts(
  field_schema: Dict[str, Any],
  *,
  feature_count: int,
) -> Dict[str, Any]:
  """field_schema → 派生事实键（numericFields/binaryFields/per-field null_ratio）。

  与 DatasetProfile.to_resolver_profile 同一权威规则（单一语义源）：
  - schema 完整（complete=True）→ numeric/binary 清单是权威的，空也照发
    （「证据证明缺席」≠「证据缺席」，scientific_preconditions 据此区分
    deferred 与 INSUFFICIENT_DATA）；
  - schema 截断/缺席 → 只有非空清单才发（正向证据），缺席键 = unknown。
  """
  numeric: List[str] = []
  categorical: List[str] = []
  binary: List[str] = []
  null_ratios: Dict[str, float] = {}
  for name, meta in field_schema.items():
    ftype = str((meta or {}).get("type") or "unknown") if isinstance(meta, dict) else "unknown"
    if ftype == "number":
      numeric.append(name)
    elif ftype == "boolean":
      categorical.append(name)
      binary.append(name)  # boolean 列恒为 0/1 二值域
    elif ftype in ("string",):
      categorical.append(name)
    if feature_count and isinstance(meta, dict):
      null_count = meta.get("null_count")
      if isinstance(null_count, int) and not isinstance(null_count, bool) and null_count >= 0:
        null_ratios[name] = round(min(null_count / feature_count, 1.0), 6)
  return {
    "numericFields": numeric,
    "categoricalFields": categorical,
    "binaryFields": binary,
    "null_ratios": null_ratios,
  }


def profile_from_descriptor(descriptor: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """#688：O(1) descriptor → Spatial Meta Profile 派生（零全量遍历）。

    store() 时算好的 ref descriptor（#666：bbox/feature_count/geometry_types
    + store 时一趟遍历产出的有界 field_schema）支撑授权路径的全部消费面
    ——view 注入（suggestedView）、图层类型推断（geometryTypes）、指纹、
    语义检查的字段证据（fields：存在性/类型/min·max/sampleValues）。
    schema 命中键上限被截断（field_schema_complete=False）或旧 descriptor
    无 field_schema 时，``fields_status`` 置 ``unknown``（本模块 return
    契约注释明确预留的语义：semantic review 不得把不可得的 schema 元数据
    当 missing-field 失败）。descriptor 缺失或不完整时返回 None，调用方
    降级全量 profile_geojson_source。

    V4（ADR-0104 #4）：descriptor 携带 CRS 时如实透传（``crs``/``crs_status``
    —— 此前硬编码 crs=None 使 resolver 的 crs_class 科学门在该路径上死亡）；
    同时派生 numericFields/binaryFields/null_ratio/hasTimeField 事实键
    （同一权威规则见 _derived_field_facts）。suggestedView 保持恒空：view
    推导需要完整 bbox+CRS 语义，保守语义不变（ref 层 auto-view 本就不可达）。
    """
    if not isinstance(descriptor, dict):
        return None
    fc = descriptor.get("feature_count")
    if not isinstance(fc, int) or isinstance(fc, bool) or fc < 0:
        return None
    bbox = descriptor.get("bbox")
    raw_schema = descriptor.get("field_schema")
    field_schema = dict(raw_schema) if isinstance(raw_schema, dict) and raw_schema else None
    # complete 缺省 True 与 RefDescriptor.from_dict 的旧字典兼容语义一致；
    # schema 为 None 时 status 必为 unknown（fields 空）。
    fields_status = (
        "explicit"
        if field_schema is not None and descriptor.get("field_schema_complete", True)
        else "unknown"
    )
    derived = _derived_field_facts(
        # null_count 是 store 时全量遍历的每字段真实计数（字段**键数**截断
        # 不影响已知字段的计数有效性），null_ratio 始终可派生。
        field_schema or {}, feature_count=fc,
    ) if field_schema else {"numericFields": [], "categoricalFields": [], "binaryFields": [], "null_ratios": {}}

    crs = descriptor.get("crs")
    crs = str(crs).strip() if isinstance(crs, str) and str(crs).strip() else None
    fields_out: Dict[str, Any] = dict(field_schema or {})
    for name, ratio in derived["null_ratios"].items():
        entry = fields_out.get(name)
        if isinstance(entry, dict):
            entry["null_ratio"] = ratio
    numeric_fields = list(derived["numericFields"])
    binary_fields = list(derived["binaryFields"])
    categorical_fields = list(derived["categoricalFields"])
    if fields_status != "explicit":
        # 截断 schema：空清单不构成权威缺席 —— 只保留正向证据。
        numeric_fields = numeric_fields or None
        binary_fields = binary_fields or None
        categorical_fields = categorical_fields or None

    # 时间证据：字段命名启发（惰性导入，避免模块加载环）；无证据不虚构
    # hasTimeField=False（descriptor 命名启发对「缺席」证据太弱）。
    has_time: Optional[bool] = None
    if field_schema:
        try:
            from app.lib.data.profile import looks_temporal

            has_time = any(
                looks_temporal(name, (meta or {}).get("sampleValues") or [])
                for name, meta in field_schema.items() if isinstance(meta, dict)
            ) or None
        except Exception:  # noqa: BLE001 — 时间证据是增值，绝不阻断派生
            has_time = None

    profile: Dict[str, Any] = {
        "bbox": list(bbox) if isinstance(bbox, (list, tuple)) else None,
        # V4：descriptor 声明了 CRS → explicit；未声明 → unknown（原硬编码
        # crs=None 的死亡门在此修复 —— ADR-0104 决策 #4）。
        "crs": crs,
        "crs_status": "explicit" if crs else "unknown",
        "featureCount": fc,
        "geometryTypes": sorted(
            t for t in (descriptor.get("geometry_types") or []) if isinstance(t, str)
        ),
        "fields": fields_out,
        "fields_status": fields_status,
        "suggestedView": {},
        "temporalProfile": None,
    }
    if numeric_fields:
        profile["numericFields"] = numeric_fields
    if categorical_fields:
        profile["categoricalFields"] = categorical_fields
    if binary_fields:
        profile["binaryFields"] = binary_fields
    if has_time:
        profile["hasTimeField"] = True
        profile["temporalObservationCount"] = fc
    if crs:
        try:
            from app.lib.gis.crs_safety import classify_crs

            profile["crsClass"] = classify_crs(crs)
        except Exception:  # noqa: BLE001 — 分类失败按 absent（消费方 own 兜底）
            pass
    return profile


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
