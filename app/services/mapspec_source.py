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
            entry["type"] = data.get("type", "data_fabric")
            for k, v in data.items():
                entry[k] = v
        else:
            entry[_INLINE] = data
    elif isinstance(data, str):
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
    return entry.get(_URL) or entry.get(_DATA_PATH)


def is_raster_entry(entry: Dict[str, Any]) -> bool:
    """True if the entry is a `type:"raster"` source."""
    return entry.get("type") == "raster"


def is_data_fabric_entry(entry: Dict[str, Any]) -> bool:
    """True if the entry is a DataFabric lazy or materialized protocol source entry (ADR-0050)."""
    st = entry.get("type")
    if st in DATAFABRIC_SOURCE_TYPES:
        return True
    if "catalog_item_id" in entry or "ref_id" in entry or entry.get("lazy") is True:
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
