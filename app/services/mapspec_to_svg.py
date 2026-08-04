"""MapSpec-to-SVG Compiler Target (Python Backend).

Compiles a declarative MapSpec into resolution-independent SVG vector markup
with DPI resolution scaling for WeasyPrint PDF report generation.
"""

import html as _html
import math as _math
from typing import Any, Dict, Tuple


def _escape_svg_attr(value: Any) -> str:
    """Escape a value for safe interpolation into an SVG attribute.

    Mirrors the JS twin's escapeSvgAttr: MapSpec paint values flow into
    attributes like fill="...", so a crafted value containing `"` could break
    out of the attribute (attribute-injection / XSS). quote=True escapes both
    double and single quotes.
    """
    return _html.escape(str(value), quote=True)


def _fmt_num(v: float) -> str:
    """Format a number for byte-identical parity with the TS twin.

    Both twins reduce every numeric SVG attribute to a canonical "minimal" form:
    round to 2 decimals, then strip trailing zeros and a dangling decimal point.
    So ``50.0`` -> ``"50"``, ``1.0`` -> ``"1"``, ``0.6`` -> ``"0.6"``,
    ``20.83`` -> ``"20.83"``. Mirrors ``fmtNum`` in the TS twin exactly
    (``f"{v:.2f}".rstrip('0').rstrip('.')``). The parity tests pin the expected
    strings, so any divergence fails the suite.
    """
    s = f"{v:.2f}"
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s


def _compute_oversample_boost(target_dpi: float) -> int:
    """Oversample zoom boost for high-DPI raster basemap tiles.

    Mirrors the TS twin's ``computeOversampleBoost`` (in
    ``frontend/lib/map-kit/oversample.ts``): ``log2(dpi/96)`` clamped to
    ``[0, 2]`` (0 at 96 DPI, +1 at 192 DPI, +2 at 300+ DPI). Per spec #268.
    NOTE: this computes the boost factor only; the actual oversampled tile
    fetch is the separate #260 tile-rasterization-policy ticket.
    """
    return min(2, max(0, round(_math.log2(target_dpi / 96.0))))


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

    # range falls back to 1.0 only when there are no coords (extent is empty
    # or a single point), matching the TS twin's `(max - min) || 1.0`. The
    # previous `max(max_x - min_x, 1.0)` form clamped small-range maps (e.g.
    # a 0.3deg span) to 1.0, collapsing them - a parity bug the TS twin did
    # not have.
    range_x = (max_x - min_x) or 1.0
    range_y = (max_y - min_y) or 1.0

    def project(coord: Tuple[float, float]) -> Tuple[str, str]:
        lon, lat = coord[0], coord[1]
        px = padding + ((lon - min_x) / range_x) * (width - padding * 2)
        py = height - padding - ((lat - min_y) / range_y) * (height - padding * 2)
        # Return formatted strings so emitted attribute bytes match the TS twin
        # exactly (_fmt_num strips trailing .0 -> `152` not `152.0`).
        return _fmt_num(px), _fmt_num(py)

    elements_svg = ""
    layers = mapspec.get("layers", [])

    for layer in layers:
        src_id = layer.get("source")
        src = sources.get(src_id, {})
        if not src:
            continue

        paint = layer.get("paint", {})
        layer_type = layer.get("type", "circle")

        if layer_type == "raster":
            opacity = _escape_svg_attr(_fmt_num(float(paint.get("raster-opacity", 1.0))))
            tiles = src.get("tiles") or ([src.get("url")] if src.get("url") else [])
            tile_url = _escape_svg_attr(tiles[0]) if tiles and tiles[0] else ""
            if tile_url:
                # Oversample boost mirrors the TS twin's computeOversampleBoost
                # (log2(dpi/96), capped [0,2]) so a single formula lives in one
                # place. NOTE: this emits a *declarative* boost marker on the
                # <image>; the actual oversampled tile fetch is the separate
                # #260 tile-rasterization-policy ticket and is not performed here.
                zoom_boost = _compute_oversample_boost(target_dpi)
                elements_svg += f'<image x="0" y="0" width="{width}" height="{height}" href="{tile_url}" opacity="{opacity}" data-oversample-boost="{zoom_boost}" preserveAspectRatio="none" />\n'
            continue

        data = src.get("data")
        if not data:
            continue
        features = data.get("features", [data]) if data.get("type") == "FeatureCollection" else [data]

        for feat in features:
            geom = feat.get("geometry")
            if not geom:
                continue

            if layer_type == "circle" and geom.get("type") == "Point":
                x, y = project(geom.get("coordinates"))
                base_r = float(paint.get("circle-radius", 5))
                r = _fmt_num(base_r * dpi_scale)
                color = _escape_svg_attr(paint.get("circle-color", "#3b82f6"))
                opacity = _escape_svg_attr(_fmt_num(float(paint.get("circle-opacity", 1.0))))
                elements_svg += f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}" fill-opacity="{opacity}" />\n'

            elif layer_type == "line" and geom.get("type") in ("LineString", "MultiLineString"):
                lines = [geom.get("coordinates")] if geom.get("type") == "LineString" else geom.get("coordinates")
                base_w = float(paint.get("line-width", 2))
                line_w = _fmt_num(base_w * dpi_scale)
                color = _escape_svg_attr(paint.get("line-color", "#2563eb"))
                opacity = _escape_svg_attr(_fmt_num(float(paint.get("line-opacity", 1.0))))

                for line_coords in lines:
                    path_str = " L ".join(f"{project(c)[0]},{project(c)[1]}" for c in line_coords)
                    elements_svg += f'<path d="M {path_str}" stroke="{color}" stroke-width="{line_w}" stroke-opacity="{opacity}" fill="none" />\n'

            elif layer_type == "fill" and geom.get("type") in ("Polygon", "MultiPolygon"):
                polygons = [geom.get("coordinates")] if geom.get("type") == "Polygon" else geom.get("coordinates")
                color = _escape_svg_attr(paint.get("fill-color", "#60a5fa"))
                opacity = _escape_svg_attr(_fmt_num(float(paint.get("fill-opacity", 0.6))))
                outline = _escape_svg_attr(paint.get("fill-outline-color", "#1d4ed8"))
                outline_w = _fmt_num(1.0 * dpi_scale)

                for poly_rings in polygons:
                    if not poly_rings:
                        continue
                    points_str = " ".join(f"{project(c)[0]},{project(c)[1]}" for c in poly_rings[0])
                    elements_svg += f'<polygon points="{points_str}" fill="{color}" fill-opacity="{opacity}" stroke="{outline}" stroke-width="{outline_w}" />\n'

            elif layer_type in ("symbol", "text"):
                layout = layer.get("layout", {})
                text_field_pattern = str(layout.get("text-field") or paint.get("text-field") or "{name}")
                field_name = text_field_pattern.strip("{}")
                props = feat.get("properties") or {}
                raw_text = props.get(field_name) or props.get("name") or props.get("label") or ("" if text_field_pattern.startswith("{") else text_field_pattern)
                if not raw_text:
                    continue

                gtype = geom.get("type")
                coords = geom.get("coordinates")
                coord = None
                if gtype == "Point":
                    coord = coords
                elif gtype == "LineString" and coords:
                    coord = coords[len(coords) // 2]
                elif gtype == "Polygon" and coords and coords[0]:
                    coord = coords[0][0]
                if not coord:
                    continue

                x, y = project(coord)
                base_size = float(layout.get("text-size") or paint.get("text-size") or 12)
                font_size = _fmt_num(base_size * dpi_scale)
                color = _escape_svg_attr(paint.get("text-color") or layout.get("text-color") or "#000000")
                opacity = _escape_svg_attr(_fmt_num(float(paint.get("text-opacity") or layout.get("text-opacity") or 1.0)))
                font_raw = layout.get("text-font") or paint.get("text-font") or "sans-serif"
                font_family = _escape_svg_attr(", ".join(font_raw) if isinstance(font_raw, list) else font_raw)

                anchor = _escape_svg_attr(layout.get("text-anchor") or paint.get("text-anchor") or "middle")
                if anchor == "center":
                    anchor = "middle"
                elif anchor == "left":
                    anchor = "start"
                elif anchor == "right":
                    anchor = "end"

                text_escaped = _escape_svg_attr(raw_text)
                elements_svg += f'<text x="{x}" y="{y}" font-size="{font_size}" font-family="{font_family}" fill="{color}" fill-opacity="{opacity}" text-anchor="{anchor}" dominant-baseline="central">{text_escaped}</text>\n'

    return f"""<svg width="{width}" height="{height}" viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="#ffffff" />
  <g class="mapspec-vector-layers">
    {elements_svg}
  </g>
</svg>"""
