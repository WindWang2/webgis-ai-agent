"""Bounded SVG label expression evaluation and authored label expansion."""
from typing import Any

def _svg_label_text(value: Any, props: dict, depth: int = 0) -> str | None:
    if depth > 16:
        return None
    if isinstance(value, str):
        import re
        return re.sub(r"\{([^{}]+)\}", lambda match: str(props.get(match[1], "")), value)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(int(value)) if isinstance(value, float) and value.is_integer() else str(value)
    if not isinstance(value, list) or not value:
        return None
    op, *args = value
    if op == "get" and args and isinstance(args[0], str):
        found = props.get(args[0])
        if isinstance(found, str):
            return found
        return _svg_label_text(found, {}, depth + 1) if isinstance(found, (int, float, bool)) else None
    if op in ("to-string", "literal") and args:
        return _svg_label_text(args[0], props, depth + 1)
    if op == "coalesce":
        return next((text for arg in args if (text := _svg_label_text(arg, props, depth + 1)) is not None), None)
    if op in ("concat", "format"):
        parts = [_svg_label_text(arg, props, depth + 1) for arg in (args[::2] if op == "format" else args)]
        return None if any(part is None for part in parts) else "".join(parts)
    return None


def _svg_layers(layers: list) -> list:
    out = []
    for layer in layers:
        out.append(layer)
        if not isinstance(layer, dict):
            continue
        label = layer.get("label")
        if not isinstance(label, dict) or not label.get("field") or layer.get("type") in ("raster", "heatmap", "background", "hillshade", "symbol", "text"):
            continue
        out.append({**layer, "id": f"{layer.get('id', '')}-label", "type": "symbol",
                    "layout": {**(layer.get("layout") or {}), "text-field": ["get", label["field"]], "text-size": label.get("size", 12)},
                    "paint": {"text-color": label.get("color", "#000000"), "text-halo-color": label.get("haloColor", "#ffffff"), "text-halo-width": label.get("haloWidth", 1)}})
    return out




def valid_geographic_coordinates(value: Any) -> bool:
    """Validate a bounded geometry without recursive depth or vertex amplification."""
    import math
    stack = [(iter([value]), 0)]
    visited = 0
    while stack:
        visited += 1
        if visited > 200_000:
            return False
        iterator, depth = stack[-1]
        try:
            item = next(iterator)
        except StopIteration:
            stack.pop()
            continue
        if not isinstance(item, (list, tuple)):
            return False
        if item and isinstance(item[0], (int, float)):
            if not (len(item) >= 2 and all(isinstance(coord, (int, float)) and not isinstance(coord, bool) and abs(coord) <= 180 and math.isfinite(coord) for coord in item[:2]) and abs(item[0]) <= 180 and abs(item[1]) <= 90):
                return False
        else:
            if depth >= 32:
                return False
            stack.append((iter(item), depth + 1))
    return True
