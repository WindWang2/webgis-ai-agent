"""MapSpec-to-SVG Compiler Target (Python Backend).

Compiles a declarative MapSpec into resolution-independent SVG vector markup
with DPI resolution scaling for WeasyPrint PDF report generation.
"""

from typing import Any, Dict, List, Tuple


def compile_mapspec_to_svg(
    mapspec: Dict[str, Any],
    target_dpi: int = 300,
    width: int = 1200,
    height: int = 800,
    padding: int = 40,
) -> str:
    dpi_scale = target_dpi / 72.0

    min_x, max_x = float("inf"), float("-inf")
    min_y, max_y = float("inf"), float("-inf")

    sources = mapspec.get("sources", {})
    for src in sources.values():
        geojson = src.get("data")
        if not geojson:
            continue
        features = geojson.get("features", [geojson]) if geojson.get("type") == "FeatureCollection" else [geojson]

        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue

            def extract_coords(c: Any) -> None:
                nonlocal min_x, max_x, min_y, max_y
                if isinstance(c, (list, tuple)) and len(c) >= 2 and isinstance(c[0], (int, float)):
                    min_x = min(min_x, c[0])
                    max_x = max(max_x, c[0])
                    min_y = min(min_y, c[1])
                    max_y = max(max_y, c[1])
                elif isinstance(c, list):
                    for sub in c:
                        extract_coords(sub)

            extract_coords(geom.get("coordinates", []))

    if min_x == float("inf"):
        min_x, max_x = -180.0, 180.0
        min_y, max_y = -80.0, 80.0

    range_x = max(max_x - min_x, 1.0)
    range_y = max(max_y - min_y, 1.0)

    def project(coord: Tuple[float, float]) -> Tuple[float, float]:
        lon, lat = coord[0], coord[1]
        px = padding + ((lon - min_x) / range_x) * (width - padding * 2)
        py = height - padding - ((lat - min_y) / range_y) * (height - padding * 2)
        return round(px, 2), round(py, 2)

    elements_svg = ""
    layers = mapspec.get("layers", [])

    for layer in layers:
        src_id = layer.get("source")
        src = sources.get(src_id, {})
        data = src.get("data")
        if not data:
            continue

        paint = layer.get("paint", {})
        layer_type = layer.get("type", "circle")
        features = data.get("features", [data]) if data.get("type") == "FeatureCollection" else [data]

        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue

            if layer_type == "circle" and geom.get("type") == "Point":
                x, y = project(geom.get("coordinates"))
                base_r = float(paint.get("circle-radius", 5))
                r = round(base_r * dpi_scale, 2)
                color = paint.get("circle-color", "#3b82f6")
                opacity = paint.get("circle-opacity", 1.0)
                elements_svg += f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}" fill-opacity="{opacity}" />\n'

            elif layer_type == "line" and geom.get("type") in ("LineString", "MultiLineString"):
                lines = [geom.get("coordinates")] if geom.get("type") == "LineString" else geom.get("coordinates")
                base_w = float(paint.get("line-width", 2))
                line_w = round(base_w * dpi_scale, 2)
                color = paint.get("line-color", "#2563eb")
                opacity = paint.get("line-opacity", 1.0)

                for line_coords in lines:
                    path_str = " L ".join(f"{project(c)[0]},{project(c)[1]}" for c in line_coords)
                    elements_svg += f'<path d="M {path_str}" stroke="{color}" stroke-width="{line_w}" stroke-opacity="{opacity}" fill="none" />\n'

            elif layer_type == "fill" and geom.get("type") in ("Polygon", "MultiPolygon"):
                polygons = [geom.get("coordinates")] if geom.get("type") == "Polygon" else geom.get("coordinates")
                color = paint.get("fill-color", "#60a5fa")
                opacity = paint.get("fill-opacity", 0.6)
                outline = paint.get("fill-outline-color", "#1d4ed8")

                for poly_rings in polygons:
                    if not poly_rings:
                        continue
                    points_str = " ".join(f"{project(c)[0]},{project(c)[1]}" for c in poly_rings[0])
                    elements_svg += f'<polygon points="{points_str}" fill="{color}" fill-opacity="{opacity}" stroke="{outline}" stroke-width="1" />\n'

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="#ffffff" />
  <g class="mapspec-vector-layers">
    {elements_svg}
  </g>
</svg>"""
