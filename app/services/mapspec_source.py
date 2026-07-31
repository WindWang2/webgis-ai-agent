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
from typing import Any, Dict, Optional

# The three keys that can carry source data, in read-back priority order.
# `url` is intentionally listed before `dataPath` to match the checkpoint
# store's historical `url or dataPath` ordering (zero behavior change).
_INLINE = "inlineData"
_URL = "url"
_DATA_PATH = "dataPath"


def store_data(entry: Dict[str, Any], data: Any) -> None:
  """Classify `data` and write it into the source entry in place.

  dict → `entry.inlineData`; str → `entry.url`; None → no-op. Unconditional
  (no idempotency) — the overwrite-vs-skip decision is the caller's policy.
  """
  if data is None:
    return
  if isinstance(data, dict):
    entry[_INLINE] = data
  elif isinstance(data, str):
    entry[_URL] = data


def profile_data(entry: Dict[str, Any]) -> Optional[Any]:
  """The data the profiler should inspect: `inlineData` → `url` → `dataPath`.

  inlineData wins because it's the already-materialized payload; url/dataPath
  are references the profiler dereferences internally.
  """
  return entry.get(_INLINE) or entry.get(_URL) or entry.get(_DATA_PATH)


def ref(entry: Dict[str, Any]) -> Optional[str]:
  """The string a checkpoint should materialize: `url` → `dataPath`, else None.

  Note the known overload (ADR-0008): `url` carries real URLs *and* `ref:xxx`
  cursors today; the caller (checkpoint) decides which to treat as a cursor
  via `startswith("ref:")`. This helper does not split them.
  """
  return entry.get(_URL) or entry.get(_DATA_PATH)
