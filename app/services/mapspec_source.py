"""MapSpec source-shape knowledge (ADR-0008).

A MapSpec source is `{type: "geojson"}` carrying exactly one of `inlineData`
(dict), `url` (str), or `dataPath` (str ref). This shape was re-derived across
the store and its checkpoint companion; this pure-function module concentrates
it. Operates on bare dicts — *not* a value type, deliberately. See ADR-0008
for why (the same reasoning that kept `view_has_center` a function, not a
`MapSpecView` model).

Policy-free by design: the dict/str classifier here does only the genuinely
shared work. Call-site policy (source_profile's strict url-shape guard;
layer_upsert's idempotency guard) stays where it lives.
"""
from typing import Any, Dict, List, Optional

# The three keys that can carry source data, in read-back priority order.
# `url` is intentionally listed before `dataPath` to match the checkpoint
# store's historical `url or dataPath` ordering (zero behavior change).
_INLINE = "inlineData"
_URL = "url"
_DATA_PATH = "dataPath"

# Raster source keys (ADR-0011). A raster source is
# {type:"raster", imageRef, bounds:[w,s,e,n], imageSize:[w,h]}.
_IMAGE_REF = "imageRef"
_BOUNDS = "bounds"
_IMAGE_SIZE = "imageSize"


def store_data(entry: Dict[str, Any], data: Any) -> None:
  """Classify `data` and write it into the source entry in place.

  - geojson dict (no `imageRef`) → `entry.inlineData`
  - raster dict (carries `imageRef`) → `entry.type="raster"` + imageRef/bounds/imageSize
  - str → `entry.url`
  - None → no-op

  Unconditional (no idempotency) — the overwrite-vs-skip decision is the caller's policy.
  """
  if data is None:
    return
  if isinstance(data, dict):
    if _IMAGE_REF in data:
      # Raster payload (already-rendered PNG ref). Mark the entry as raster and
      # carry the georeferencing; do NOT fall through to inlineData.
      entry["type"] = "raster"
      entry[_IMAGE_REF] = data[_IMAGE_REF]
      if _BOUNDS in data:
        entry[_BOUNDS] = data[_BOUNDS]
      if _IMAGE_SIZE in data:
        entry[_IMAGE_SIZE] = data[_IMAGE_SIZE]
    else:
      entry[_INLINE] = data
  elif isinstance(data, str):
    entry[_URL] = data


def profile_data(entry: Dict[str, Any]) -> Optional[Any]:
  """The data the profiler should inspect: `inlineData` → `url` → `dataPath`.

  inlineData wins because it's the already-materialized payload; url/dataPath
  are references the profiler dereferences internally. Raster entries carry no
  GeoJSON to profile → None (caller checks is_raster_entry first and skips).
  """
  if is_raster_entry(entry):
    return None
  return entry.get(_INLINE) or entry.get(_URL) or entry.get(_DATA_PATH)


def ref(entry: Dict[str, Any]) -> Optional[str]:
  """The string a checkpoint should materialize, else None.

  For geojson: `url` → `dataPath` (ADR-0008). For raster: `imageRef`. Note the
  known overload (ADR-0008): `url` carries real URLs *and* `ref:xxx` cursors
  today; the caller (checkpoint) decides via `startswith("ref:")`. This helper
  does not split them.
  """
  if is_raster_entry(entry):
    return entry.get(_IMAGE_REF)
  return entry.get(_URL) or entry.get(_DATA_PATH)


# ─── raster predicates / accessors (ADR-0011) ──────────────────────────────


def is_raster_entry(entry: Dict[str, Any]) -> bool:
  """True if the entry is a `type:"raster"` source (imageRef + bounds)."""
  return entry.get("type") == "raster"


def raster_image_ref(entry: Dict[str, Any]) -> Optional[str]:
  """The raster source's PNG cursor (imageRef), else None."""
  return entry.get(_IMAGE_REF) if is_raster_entry(entry) else None


def raster_bounds(entry: Dict[str, Any]) -> Optional[List[float]]:
  """The raster source's WGS84 bounds [w, s, e, n], else None."""
  return entry.get(_BOUNDS) if is_raster_entry(entry) else None
