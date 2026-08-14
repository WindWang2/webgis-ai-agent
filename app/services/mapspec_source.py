"""MapSpec source-shape knowledge (ADR-0008, ADR-0050).

A MapSpec source carries a type and data references.
Supported source types:
- `type:"geojson"` — carries `inlineData`, `url`, or `dataPath`.
- `type:"raster"` — carries `imageRef`, `bounds`, `imageSize`.
- `type:"data_fabric"` / `type:"wms"` / `type:"wmts"` / `type:"pmtiles"` — Data Fabric lazy or materialized protocol sources (ADR-0050).
"""
from typing import Any, Dict, List, Optional

_INLINE = "inlineData"
_URL = "url"
_DATA_PATH = "dataPath"

_IMAGE_REF = "imageRef"
_BOUNDS = "bounds"
_IMAGE_SIZE = "imageSize"

DATAFABRIC_SOURCE_TYPES = {"data_fabric", "wms", "wmts", "pmtiles"}


def store_data(entry: Dict[str, Any], data: Any) -> None:
    """Classify `data` and write it into the source entry in place."""
    if data is None:
        return
    if isinstance(data, dict):
        if _IMAGE_REF in data:
            entry["type"] = "raster"
            entry[_IMAGE_REF] = data[_IMAGE_REF]
            if _BOUNDS in data:
                entry[_BOUNDS] = data[_BOUNDS]
            if _IMAGE_SIZE in data:
                entry[_IMAGE_SIZE] = data[_IMAGE_SIZE]
        elif "catalog_item_id" in data or data.get("type") in DATAFABRIC_SOURCE_TYPES:
            # DataFabric protocol source (ADR-0050). MUST be checked before
            # the plain-ref branch: a lazy/materialized fabric payload can
            # carry BOTH catalog_item_id and a ref:df-* ref_id, and the old
            # ordering demoted it to a geojson ref source, silently dropping
            # the lazy fabric semantics.
            entry["type"] = data.get("type", "data_fabric")
            for k, v in data.items():
                entry[k] = v
        elif isinstance(data.get("ref_id"), str) and data["ref_id"].startswith("ref:"):
            # Session-owned metadata carrier. It deliberately contains no
            # feature body: cartographic review can use the descriptor-derived
            # profile while the runtime resolves the opaque ref through the
            # existing data plane.
            entry["type"] = "geojson"
            entry["ref"] = data["ref_id"]
            entry["ref_id"] = data["ref_id"]
            if isinstance(data.get("profile"), dict):
                entry["profile"] = data["profile"]
            if isinstance(data.get("profile_fingerprint"), str):
                entry["profile_fingerprint"] = data["profile_fingerprint"]
            if isinstance(data.get("data_fingerprint"), str):
                entry["data_fingerprint"] = data["data_fingerprint"]
        else:
            # An ordinary object is a GeoJSON/inline carrier. Explicit source
            # replacement must not retain a prior raster/vector discriminator.
            entry["type"] = "geojson"
            entry[_INLINE] = data
    elif isinstance(data, str):
        # A source replacement is authoritative. Do not retain a stale raster
        # discriminator when a later mutation supplies a vector URL/ref.
        entry["type"] = "geojson"
        if data.startswith("ref:"):
            entry["ref_id"] = data
            entry[_URL] = data
        else:
            entry[_URL] = data


def profile_data(entry: Dict[str, Any]) -> Optional[Any]:
    """The data the profiler should inspect."""
    if is_raster_entry(entry) or is_data_fabric_entry(entry):
        return entry.get(_INLINE)
    return entry.get(_INLINE) or entry.get(_URL) or entry.get(_DATA_PATH)


def ref(entry: Dict[str, Any]) -> Optional[str]:
    """The string reference (url, ref_id, dataPath, imageRef) for checkpoint/materialization."""
    if is_raster_entry(entry):
        return entry.get(_IMAGE_REF)
    if is_data_fabric_entry(entry):
        return entry.get("ref_id") or entry.get(_URL) or entry.get(_DATA_PATH)
    return entry.get("ref_id") or entry.get("ref") or entry.get(_URL) or entry.get(_DATA_PATH)


def is_raster_entry(entry: Dict[str, Any]) -> bool:
    """True if the entry is a `type:"raster"` source."""
    return entry.get("type") == "raster"


def is_data_fabric_entry(entry: Dict[str, Any]) -> bool:
    """True if the entry is a DataFabric lazy or materialized protocol source entry (ADR-0050).

    Deliberately does NOT treat a bare ``ref_id`` as a fabric marker: the
    geojson ref branch of :func:`store_data` also writes ``ref_id``, so that
    heuristic misclassified every session geojson-ref source as fabric (and
    pipeline.py's profiler then skipped them). Fabric entries carry an
    explicit ``type``, a ``catalog_item_id``, the ``lazy`` flag, or a fabric
    ``ref:`` URL."""
    st = entry.get("type")
    if st in DATAFABRIC_SOURCE_TYPES:
        return True
    if "catalog_item_id" in entry or entry.get("lazy") is True:
        return True
    url_val = entry.get(_URL)
    if isinstance(url_val, str) and url_val.startswith("ref:"):
        return True
    return False


def raster_image_ref(entry: Dict[str, Any]) -> Optional[str]:
    """The raster source's PNG cursor (imageRef), else None."""
    return entry.get(_IMAGE_REF) if is_raster_entry(entry) else None


def raster_bounds(entry: Dict[str, Any]) -> Optional[List[float]]:
    """The raster source's WGS84 bounds [w, s, e, n], else None."""
    return entry.get(_BOUNDS) if is_raster_entry(entry) else None
