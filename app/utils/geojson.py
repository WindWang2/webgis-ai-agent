"""GeoJSON utilities and common space/field operations."""
from typing import Any, Optional, List, Dict, Set, Tuple

def infer_field_type(v: Any) -> str:
    """Infer the type of a property value for schema representation.
    
    Returns one of: 'null', 'bool', 'number', 'array', 'object', 'string'.
    """
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "bool"
    if isinstance(v, (int, float)):
        return "number"
    if isinstance(v, (list, tuple)):
        return "array"
    if isinstance(v, dict):
        return "object"
    return "string"


def geojson_bbox(data: Any) -> Optional[List[float]]:
    """Extract a bounding box [west, south, east, north] from any GeoJSON-like structure."""
    if not isinstance(data, dict):
        return None
    if isinstance(data.get("bbox"), list) and len(data["bbox"]) >= 4:
        return [float(x) for x in data["bbox"][:4]]

    bounds = [float("inf"), float("inf"), float("-inf"), float("-inf")]
    found = False

    def walk(node: Any):
        nonlocal found
        if isinstance(node, list) and node and isinstance(node[0], (int, float)) and len(node) >= 2:
            lng, lat = float(node[0]), float(node[1])
            bounds[0] = min(bounds[0], lng)
            bounds[1] = min(bounds[1], lat)
            bounds[2] = max(bounds[2], lng)
            bounds[3] = max(bounds[3], lat)
            found = True
            return
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            if "coordinates" in node:
                walk(node["coordinates"])
            if node.get("type") == "FeatureCollection":
                for f in node.get("features", []) or []:
                    walk(f)
            elif node.get("type") == "Feature":
                geom = node.get("geometry")
                if geom:
                    walk(geom)

    walk(data)
    return bounds if found else None


def summarize_feature_properties(
    features: List[Dict[str, Any]],
    sample_size: int = 5,
    max_keys: Optional[int] = None,
    ignored_keys: Optional[Set[str]] = None
) -> Tuple[Dict[str, str], List[Dict[str, Any]]]:
    """Analyze a list of features to infer property schema types and extract sample properties.
    
    Returns:
        tuple: (typed_properties, sample_properties_list)
    """
    field_types: Dict[str, Set[str]] = {}
    sample: List[Dict[str, Any]] = []
    
    if ignored_keys is None:
        ignored_keys = {"fill_color", "opacity", "stroke_width", "__style__"}

    for idx, f in enumerate(features):
        if not isinstance(f, dict):
            continue
        props = f.get("properties") or {}
        if isinstance(props, dict):
            for k, v in props.items():
                k_str = str(k)
                if k_str in ignored_keys:
                    continue
                field_types.setdefault(k_str, set()).add(infer_field_type(v))
                
        if idx < sample_size:
            sample.append(props)

    # Consolidate types: discard null, >1 implies mixed
    typed_properties: Dict[str, str] = {}
    for i, (k, types) in enumerate(field_types.items()):
        if max_keys is not None and i >= max_keys:
            break
        types.discard("null")
        if not types:
            typed_properties[k] = "null"
        elif len(types) == 1:
            typed_properties[k] = next(iter(types))
        else:
            typed_properties[k] = "mixed"

    return typed_properties, sample
