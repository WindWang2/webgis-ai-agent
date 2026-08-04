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


def _safe_float(val: Any, default: float = 0.0) -> float:
    """Safely convert value to float, returning default if invalid, None, NaN, or Inf."""
    try:
        if val is None:
            return default
        f = float(val)
        return f if _math.isfinite(f) else default
    except (ValueError, TypeError):
        return default


def _fmt_num(v: Any) -> str:
    """Format a number for byte-identical parity with the TS twin.

    Both twins reduce every numeric SVG attribute to a canonical "minimal" form:
    round to 2 decimals, then strip trailing zeros and a dangling decimal point.
    So ``50.0`` -> ``"50"``, ``1.0`` -> ``"1"``, ``0.6`` -> ``"0.6"``,
    ``20.83`` -> ``"20.83"``. Mirrors ``fmtNum`` in the TS twin exactly
    (``f"{v:.2f}".rstrip('0').rstrip('.')``). The parity tests pin the expected
    strings, so any divergence fails the suite.
    """
    try:
        v_float = float(v)
        if not _math.isfinite(v_float):
            return "0"
        s = f"{v_float:.2f}"
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        return s
    except Exception:
        return "0"


def _merc_y(lat: float) -> float:
    """Non-linear Web Mercator Y projection for screen latitude mapping.

    merc_y(lat) = log(tan(pi/4 + (lat * pi) / 360))
    """
    try:
        lat_val = float(lat)
        if not _math.isfinite(lat_val):
            lat_val = 0.0
    except (ValueError, TypeError):
        lat_val = 0.0
    clamped_lat = max(-85.05112878, min(85.05112878, lat_val))
    return _math.log(_math.tan(_math.pi / 4.0 + (clamped_lat * _math.pi) / 360.0))


def _compute_oversample_boost(target_dpi: float) -> int:
    """Oversample zoom boost for high-DPI raster basemap tiles.

    Mirrors the TS twin's ``computeOversampleBoost`` (in
    ``frontend/lib/map-kit/oversample.ts``): ``log2(dpi/96)`` clamped to
    ``[0, 2]`` (0 at 96 DPI, +1 at 192 DPI, +2 at 300+ DPI). Per spec #268 / #260.
    Handles invalid log inputs (target_dpi <= 0, NaN, Inf) defensively.
    """
    try:
        dpi_val = float(target_dpi)
        if not _math.isfinite(dpi_val) or dpi_val <= 0:
            return 0
        return min(2, max(0, round(_math.log2(dpi_val / 96.0))))
    except Exception:
        return 0


def _lon_to_tile_x(lon: float, zoom: int) -> int:
    try:
        if not _math.isfinite(lon):
            lon = 0.0
        z = max(0, min(30, int(zoom)))
        n = 2 ** z
        return max(0, min(n - 1, int(_math.floor((lon + 180.0) / 360.0 * n))))
    except Exception:
        return 0


def _lat_to_tile_y(lat: float, zoom: int) -> int:
    try:
        if not _math.isfinite(lat):
            lat = 0.0
        z = max(0, min(30, int(zoom)))
        n = 2 ** z
        lat_rad = _math.radians(max(-85.05112878, min(85.05112878, float(lat))))
        tan_val = _math.tan(lat_rad)
        cos_val = _math.cos(lat_rad)
        sec_val = (1.0 / cos_val) if abs(cos_val) > 1e-15 else 1.0
        val = tan_val + sec_val
        if val <= 0 or not _math.isfinite(val):
            return 0
        return max(0, min(n - 1, int(_math.floor((1.0 - _math.log(val) / _math.pi) / 2.0 * n))))
    except Exception:
        return 0


def _tile_x_to_lon(x: int, zoom: int) -> float:
    try:
        z = max(0, min(30, int(zoom)))
        n = 2 ** z
        return (x / float(n)) * 360.0 - 180.0
    except Exception:
        return -180.0


def _tile_y_to_lat(y: int, zoom: int) -> float:
    try:
        z = max(0, min(30, int(zoom)))
        n = 2 ** z
        y_val = _math.pi * (1.0 - 2.0 * y / float(n))
        return _math.degrees(_math.atan(_math.sinh(y_val)))
    except Exception:
        return 0.0


def _substitute_tile_url(template: str, x: int, y: int, z: int) -> str:
    try:
        url = str(template).replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
        url = url.replace("{TILEMATRIX}", str(z)).replace("{TILECOL}", str(x)).replace("{TILEROW}", str(y))
        if "{s}" in url:
            subdomains = ["a", "b", "c", "0", "1", "2", "3"]
            s = subdomains[abs(x + y) % len(subdomains)]
            url = url.replace("{s}", s)
        return url
    except Exception:
        return str(template)


def _parse_color(c: Any) -> Tuple[int, int, int] | None:
    if not isinstance(c, str):
        return None
    s = c.strip().lower()
    if s.startswith("#"):
        hex_str = s[1:]
        if len(hex_str) == 3:
            return (int(hex_str[0] * 2, 16), int(hex_str[1] * 2, 16), int(hex_str[2] * 2, 16))
        elif len(hex_str) in (6, 8):
            return (int(hex_str[0:2], 16), int(hex_str[2:4], 16), int(hex_str[4:6], 16))
    elif s.startswith("rgb"):
        import re
        m = re.search(r"rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", s)
        if m:
            return (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    return None


def _rgb_to_hex(r: float, g: float, b: float) -> str:
    r_c = max(0, min(255, int(round(r))))
    g_c = max(0, min(255, int(round(g))))
    b_c = max(0, min(255, int(round(b))))
    return f"#{r_c:02x}{g_c:02x}{b_c:02x}"


def _interpolate_value(v0: Any, v1: Any, t: float) -> Any:
    try:
        n0, n1 = float(v0), float(v1)
        if _math.isfinite(n0) and _math.isfinite(n1):
            return n0 + t * (n1 - n0)
    except (ValueError, TypeError):
        pass

    c0 = _parse_color(v0)
    c1 = _parse_color(v1)
    if c0 and c1:
        r = c0[0] + t * (c1[0] - c0[0])
        g = c0[1] + t * (c1[1] - c0[1])
        b = c0[2] + t * (c1[2] - c0[2])
        return _rgb_to_hex(r, g, b)

    return v0 if t < 0.5 else v1


def _resolve_paint_value(
    val: Any, props: Any = None, fallback: Any = None
) -> Any:
    if val is None:
        return fallback
    if not isinstance(val, dict):
        return val

    method = val.get("method")
    if not isinstance(method, str):
        return val

    if method == "constant":
        return val.get("value", fallback)

    if method == "field":
        f = val.get("field")
        if props and isinstance(props, dict) and f and f in props and props[f] is not None:
            return props[f]
        return fallback

    if method == "match":
        f = val.get("field")
        prop_val = props.get(f) if (props and isinstance(props, dict) and f) else None
        cases = val.get("cases", [])
        if prop_val is not None and isinstance(cases, list):
            for pair in cases:
                if isinstance(pair, (list, tuple)) and len(pair) >= 2:
                    if str(prop_val) == str(pair[0]) or prop_val == pair[0]:
                        return pair[1]
        return val.get("default", fallback)

    if method == "step":
        f = val.get("field")
        prop_val = _safe_float(props.get(f), default=float("nan")) if (props and isinstance(props, dict) and f) else float("nan")
        stops = val.get("stops", [])
        if not isinstance(stops, list) or not stops:
            return val.get("default", fallback)
        res = val.get("default") if "default" in val else (stops[0][1] if len(stops[0]) >= 2 else fallback)
        if _math.isfinite(prop_val):
            for stop in stops:
                if isinstance(stop, (list, tuple)) and len(stop) >= 2:
                    thresh = _safe_float(stop[0], default=float("nan"))
                    if _math.isfinite(thresh) and prop_val >= thresh:
                        res = stop[1]
        return res

    if method == "interpolate":
        f = val.get("field")
        prop_val = _safe_float(props.get(f), default=float("nan")) if (props and isinstance(props, dict) and f) else float("nan")
        stops = val.get("stops", [])
        if not isinstance(stops, list) or not stops:
            return val.get("default", fallback)
        if not _math.isfinite(prop_val):
            return stops[0][1] if (len(stops[0]) >= 2) else fallback

        first_stop = stops[0]
        last_stop = stops[-1]
        x_min = _safe_float(first_stop[0], default=float("-inf"))
        x_max = _safe_float(last_stop[0], default=float("inf"))

        if prop_val <= x_min:
            return first_stop[1]
        if prop_val >= x_max:
            return last_stop[1]

        for i in range(len(stops) - 1):
            s0, s1 = stops[i], stops[i + 1]
            x0 = _safe_float(s0[0], default=float("nan"))
            x1 = _safe_float(s1[0], default=float("nan"))
            if _math.isfinite(x0) and _math.isfinite(x1) and x0 <= prop_val <= x1:
                if x1 == x0:
                    return s0[1]
                t = (prop_val - x0) / (x1 - x0)
                return _interpolate_value(s0[1], s1[1], t)
        return last_stop[1]

    return val.get("value", fallback)


def compile_mapspec_to_svg(
    mapspec: Dict[str, Any],
    target_dpi: int = 300,
    width: int = 1200,
    height: int = 800,
    padding: int = 40,
) -> str:
    try:
        if not isinstance(mapspec, dict):
            mapspec = {}

        try:
            target_dpi_val = float(target_dpi)
            if not _math.isfinite(target_dpi_val) or target_dpi_val <= 0:
                target_dpi_val = 300.0
        except (ValueError, TypeError):
            target_dpi_val = 300.0
        dpi_scale = target_dpi_val / 72.0

        try:
            width_val = int(width) if _math.isfinite(float(width)) and width > 0 else 1200
        except (ValueError, TypeError):
            width_val = 1200

        try:
            height_val = int(height) if _math.isfinite(float(height)) and height > 0 else 800
        except (ValueError, TypeError):
            height_val = 800

        try:
            padding_val = int(padding) if _math.isfinite(float(padding)) and padding >= 0 else 40
        except (ValueError, TypeError):
            padding_val = 40

        scaled_width = width_val * dpi_scale
        scaled_height = height_val * dpi_scale
        scaled_padding = padding_val * dpi_scale

        min_x, max_x = float("inf"), float("-inf")
        min_y, max_y = float("inf"), float("-inf")

        sources = mapspec.get("sources", {})
        if isinstance(sources, dict):
            for src in sources.values():
                if not isinstance(src, dict):
                    continue
                geojson = src.get("data")
                if not isinstance(geojson, dict):
                    continue
                features = (
                    geojson.get("features", [geojson])
                    if geojson.get("type") == "FeatureCollection"
                    else [geojson]
                )
                if not isinstance(features, list):
                    features = [geojson]

                for feat in features:
                    if not isinstance(feat, dict):
                        continue
                    geom = feat.get("geometry")
                    if not isinstance(geom, dict):
                        continue

                    def extract_coords(c: Any) -> None:
                        nonlocal min_x, max_x, min_y, max_y
                        if (
                            isinstance(c, (list, tuple))
                            and len(c) >= 2
                            and isinstance(c[0], (int, float))
                            and isinstance(c[1], (int, float))
                        ):
                            try:
                                x_val, y_val = float(c[0]), float(c[1])
                                if _math.isfinite(x_val) and _math.isfinite(y_val):
                                    min_x = min(min_x, x_val)
                                    max_x = max(max_x, x_val)
                                    min_y = min(min_y, y_val)
                                    max_y = max(max_y, y_val)
                            except (ValueError, TypeError):
                                pass
                        elif isinstance(c, list):
                            for sub in c:
                                extract_coords(sub)

                    extract_coords(geom.get("coordinates", []))

        if (
            not _math.isfinite(min_x)
            or not _math.isfinite(max_x)
            or not _math.isfinite(min_y)
            or not _math.isfinite(max_y)
            or max_x < min_x
            or max_y < min_y
        ):
            min_x, max_x = -180.0, 180.0
            min_y, max_y = -80.0, 80.0

        range_x = (max_x - min_x) if (_math.isfinite(max_x - min_x) and (max_x - min_x) > 0) else 1.0
        range_y = (max_y - min_y) if (_math.isfinite(max_y - min_y) and (max_y - min_y) > 0) else 1.0

        merc_min_y = _merc_y(min_y)
        merc_max_y = _merc_y(max_y)
        range_merc_y = (merc_max_y - merc_min_y) if (_math.isfinite(merc_max_y - merc_min_y) and (merc_max_y - merc_min_y) > 0) else 1.0

        def project(coord: Any) -> Tuple[str, str]:
            try:
                if not isinstance(coord, (list, tuple)) or len(coord) < 2:
                    lon, lat = 0.0, 0.0
                else:
                    lon = float(coord[0]) if _math.isfinite(float(coord[0])) else 0.0
                    lat = float(coord[1]) if _math.isfinite(float(coord[1])) else 0.0
            except (ValueError, TypeError):
                lon, lat = 0.0, 0.0

            w_span = scaled_width - scaled_padding * 2
            h_span = scaled_height - scaled_padding * 2

            px = scaled_padding + ((lon - min_x) / range_x) * w_span
            norm_y = (_merc_y(lat) - merc_min_y) / range_merc_y
            py = scaled_height - scaled_padding - norm_y * h_span

            if not _math.isfinite(px):
                px = 0.0
            if not _math.isfinite(py):
                py = 0.0

            return _fmt_num(px), _fmt_num(py)

        elements_svg = ""
        layers = mapspec.get("layers", [])
        if isinstance(layers, list):
            for layer in layers:
                if not isinstance(layer, dict):
                    continue
                try:
                    src_id = layer.get("source")
                    src = sources.get(src_id, {}) if isinstance(sources, dict) else {}
                    if not isinstance(src, dict) or not src:
                        continue

                    paint = layer.get("paint", {})
                    if not isinstance(paint, dict):
                        paint = {}
                    layer_type = layer.get("type", "circle")

                    if layer_type == "raster":
                        raw_opacity = _resolve_paint_value(paint.get("raster-opacity") or paint.get("opacity"), None, 1.0)
                        opacity = _escape_svg_attr(_fmt_num(_safe_float(raw_opacity, 1.0)))
                        tiles = src.get("tiles") or ([src.get("url")] if src.get("url") else [])
                        tile_url_template = tiles[0] if (isinstance(tiles, list) and tiles and tiles[0]) else ""
                        if tile_url_template:
                            zoom_boost = _compute_oversample_boost(target_dpi_val)
                            is_template = any(k in tile_url_template for k in ("{x}", "{y}", "{z}", "{TILECOL}", "{TILEROW}", "{TILEMATRIX}"))
                            if is_template:
                                try:
                                    denom = max(max(range_x, range_y), 0.0001)
                                    if not _math.isfinite(denom) or denom <= 0:
                                        denom = 360.0
                                    log_arg = 360.0 / denom
                                    if log_arg <= 0 or not _math.isfinite(log_arg):
                                        calculated_base = 0
                                    else:
                                        calculated_base = max(0, min(19, int(_math.floor(_math.log2(log_arg)))))
                                except Exception:
                                    calculated_base = 0

                                z = max(0, min(19, calculated_base + zoom_boost))
                                x_min = _lon_to_tile_x(min_x, z)
                                x_max = _lon_to_tile_x(max_x, z)
                                y_min = _lat_to_tile_y(max_y, z)
                                y_max = _lat_to_tile_y(min_y, z)

                                merc_min_y = _merc_y(min_y)
                                merc_max_y = _merc_y(max_y)
                                range_merc_y = (merc_max_y - merc_min_y) or 1.0

                                def proj_x(lon: float) -> float:
                                    return scaled_padding + ((lon - min_x) / range_x) * (scaled_width - scaled_padding * 2)

                                def proj_y(lat: float) -> float:
                                    norm_y = (_merc_y(lat) - merc_min_y) / range_merc_y
                                    return scaled_height - scaled_padding - norm_y * (scaled_height - scaled_padding * 2)

                                for tx in range(x_min, x_max + 1):
                                    for ty in range(y_min, y_max + 1):
                                        lon_w = _tile_x_to_lon(tx, z)
                                        lon_e = _tile_x_to_lon(tx + 1, z)
                                        lat_n = _tile_y_to_lat(ty, z)
                                        lat_s = _tile_y_to_lat(ty + 1, z)

                                        t_px = proj_x(lon_w)
                                        t_py = proj_y(lat_n)
                                        t_pw = proj_x(lon_e) - proj_x(lon_w)
                                        t_ph = proj_y(lat_s) - proj_y(lat_n)

                                        t_url = _escape_svg_attr(_substitute_tile_url(tile_url_template, tx, ty, z))
                                        elements_svg += f'<image x="{_fmt_num(t_px)}" y="{_fmt_num(t_py)}" width="{_fmt_num(t_pw)}" height="{_fmt_num(t_ph)}" href="{t_url}" opacity="{opacity}" data-oversample-boost="{zoom_boost}" preserveAspectRatio="none" />\n'
                            else:
                                tile_url = _escape_svg_attr(tile_url_template)
                                elements_svg += f'<image x="0" y="0" width="{_fmt_num(scaled_width)}" height="{_fmt_num(scaled_height)}" href="{tile_url}" opacity="{opacity}" data-oversample-boost="{zoom_boost}" preserveAspectRatio="none" />\n'
                        continue

                    data = src.get("data")
                    if not isinstance(data, dict):
                        continue
                    features = data.get("features", [data]) if data.get("type") == "FeatureCollection" else [data]
                    if not isinstance(features, list):
                        features = [data]

                    for feat in features:
                        if not isinstance(feat, dict):
                            continue
                        geom = feat.get("geometry")
                        if not isinstance(geom, dict):
                            continue
                        props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}

                        if layer_type == "circle" and geom.get("type") == "Point":
                            coords = geom.get("coordinates")
                            if not coords:
                                continue
                            x, y = project(coords)
                            base_r = _safe_float(_resolve_paint_value(paint.get("circle-radius") or paint.get("radius"), props, 5.0), 5.0)
                            r = _fmt_num(base_r * dpi_scale)
                            color = _escape_svg_attr(_resolve_paint_value(paint.get("circle-color") or paint.get("color"), props, "#3b82f6"))
                            opacity = _escape_svg_attr(_fmt_num(_safe_float(_resolve_paint_value(paint.get("circle-opacity") or paint.get("opacity"), props, 1.0), 1.0)))

                            base_stroke_w = _safe_float(_resolve_paint_value(paint.get("circle-stroke-width") or paint.get("stroke-width") or paint.get("strokeWidth"), props, 0.0), 0.0)
                            stroke_attr = ""
                            if base_stroke_w > 0:
                                stroke_w = _fmt_num(base_stroke_w * dpi_scale)
                                stroke_color = _escape_svg_attr(_resolve_paint_value(paint.get("circle-stroke-color") or paint.get("stroke-color") or paint.get("strokeColor"), props, "#000000"))
                                stroke_opacity = _escape_svg_attr(_fmt_num(_safe_float(_resolve_paint_value(paint.get("circle-stroke-opacity") or paint.get("stroke-opacity") or paint.get("strokeOpacity"), props, 1.0), 1.0)))
                                stroke_attr = f' stroke="{stroke_color}" stroke-width="{stroke_w}" stroke-opacity="{stroke_opacity}"'

                            elements_svg += f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}" fill-opacity="{opacity}"{stroke_attr} />\n'

                        elif layer_type == "line" and geom.get("type") in ("LineString", "MultiLineString"):
                            layout = layer.get("layout", {}) if isinstance(layer.get("layout"), dict) else {}
                            coords = geom.get("coordinates")
                            if not coords:
                                continue
                            lines = [coords] if geom.get("type") == "LineString" else coords
                            if not isinstance(lines, list):
                                continue
                            base_w = _safe_float(_resolve_paint_value(paint.get("line-width") or paint.get("width"), props, 2.0), 2.0)
                            line_w = _fmt_num(base_w * dpi_scale)
                            color = _escape_svg_attr(_resolve_paint_value(paint.get("line-color") or paint.get("color"), props, "#2563eb"))
                            opacity = _escape_svg_attr(_fmt_num(_safe_float(_resolve_paint_value(paint.get("line-opacity") or paint.get("opacity"), props, 1.0), 1.0)))

                            extra_attrs = ""
                            linecap = _resolve_paint_value(layout.get("line-linecap") or paint.get("line-linecap") or layout.get("lineCap") or paint.get("lineCap"), props, None)
                            if linecap and isinstance(linecap, str):
                                extra_attrs += f' stroke-linecap="{_escape_svg_attr(linecap)}"'

                            linejoin = _resolve_paint_value(layout.get("line-linejoin") or paint.get("line-linejoin") or layout.get("lineJoin") or paint.get("lineJoin"), props, None)
                            if linejoin and isinstance(linejoin, str):
                                extra_attrs += f' stroke-linejoin="{_escape_svg_attr(linejoin)}"'

                            raw_dash = _resolve_paint_value(paint.get("line-dasharray") or layout.get("line-dasharray") or paint.get("dasharray"), props, None)
                            if raw_dash is not None:
                                dash_str = ""
                                if isinstance(raw_dash, (list, tuple)):
                                    dash_str = ",".join(_fmt_num(_safe_float(v, 0.0) * dpi_scale) for v in raw_dash)
                                elif isinstance(raw_dash, str) and raw_dash.strip():
                                    import re
                                    parts = [p for p in re.split(r"[\s,]+", raw_dash.strip()) if p]
                                    try:
                                        dash_str = ",".join(_fmt_num(_safe_float(p, 0.0) * dpi_scale) for p in parts)
                                    except Exception:
                                        dash_str = _escape_svg_attr(raw_dash)
                                if dash_str:
                                    extra_attrs += f' stroke-dasharray="{dash_str}"'

                            for line_coords in lines:
                                if not isinstance(line_coords, list) or not line_coords:
                                    continue
                                path_str = " L ".join(f"{project(c)[0]},{project(c)[1]}" for c in line_coords)
                                elements_svg += f'<path d="M {path_str}" stroke="{color}" stroke-width="{line_w}" stroke-opacity="{opacity}" fill="none"{extra_attrs} />\n'

                        elif layer_type in ("fill", "fill-extrusion") and geom.get("type") in ("Polygon", "MultiPolygon"):
                            coords = geom.get("coordinates")
                            if not coords:
                                continue
                            polygons = [coords] if geom.get("type") == "Polygon" else coords
                            if not isinstance(polygons, list):
                                continue
                            default_color = "#94a3b8" if layer_type == "fill-extrusion" else "#60a5fa"
                            default_opacity = 0.8 if layer_type == "fill-extrusion" else 0.6
                            default_outline = "#475569" if layer_type == "fill-extrusion" else "#1d4ed8"

                            color = _escape_svg_attr(_resolve_paint_value(paint.get("fill-extrusion-color") or paint.get("fill-color") or paint.get("color"), props, default_color))
                            opacity = _escape_svg_attr(_fmt_num(_safe_float(_resolve_paint_value(paint.get("fill-extrusion-opacity") or paint.get("fill-opacity") or paint.get("opacity"), props, default_opacity), default_opacity)))
                            outline = _escape_svg_attr(_resolve_paint_value(paint.get("fill-extrusion-base-color") or paint.get("fill-outline-color") or paint.get("fill-outline") or paint.get("strokeColor"), props, default_outline))
                            outline_w = _fmt_num(1.0 * dpi_scale)

                            for poly_rings in polygons:
                                if not isinstance(poly_rings, list) or not poly_rings:
                                    continue
                                ring_paths = []
                                for ring in poly_rings:
                                    if not isinstance(ring, list) or not ring:
                                        continue
                                    pts = " L ".join(f"{project(c)[0]},{project(c)[1]}" for c in ring)
                                    ring_paths.append(f"M {pts} Z")
                                if not ring_paths:
                                    continue
                                d_str = " ".join(ring_paths)
                                elements_svg += f'<path d="{d_str}" fill="{color}" fill-opacity="{opacity}" fill-rule="evenodd" stroke="{outline}" stroke-width="{outline_w}" />\n'

                        elif layer_type == "heatmap":
                            pts = []
                            gtype = geom.get("type")
                            coords = geom.get("coordinates")
                            if gtype == "Point" and isinstance(coords, (list, tuple)):
                                pts.append(coords)
                            elif gtype in ("MultiPoint", "LineString") and isinstance(coords, list):
                                pts.extend(coords)
                            elif gtype == "Polygon" and isinstance(coords, list) and coords and isinstance(coords[0], list):
                                pts.extend(coords[0])

                            base_r = _safe_float(_resolve_paint_value(paint.get("heatmap-radius") or paint.get("radius") or paint.get("circle-radius"), props, 15.0), 15.0)
                            r = _fmt_num(base_r * dpi_scale)
                            color = _escape_svg_attr(_resolve_paint_value(paint.get("heatmap-color") or paint.get("color"), props, "#ef4444"))
                            opacity = _escape_svg_attr(_fmt_num(_safe_float(_resolve_paint_value(paint.get("heatmap-opacity") or paint.get("opacity"), props, 0.6), 0.6)))

                            for pt in pts:
                                if not isinstance(pt, (list, tuple)) or len(pt) < 2:
                                    continue
                                x, y = project(pt)
                                elements_svg += f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}" fill-opacity="{opacity}" />\n'

                        elif layer_type in ("symbol", "text"):
                            layout = layer.get("layout", {})
                            if not isinstance(layout, dict):
                                layout = {}
                            text_field_pattern = str(layout.get("text-field") or paint.get("text-field") or "{name}")
                            field_name = text_field_pattern.strip("{}")
                            raw_text = props.get(field_name) or props.get("name") or props.get("label") or ("" if text_field_pattern.startswith("{") else text_field_pattern)
                            if not raw_text:
                                continue

                            gtype = geom.get("type")
                            coords = geom.get("coordinates")
                            coord = None
                            if gtype == "Point":
                                coord = coords
                            elif gtype == "LineString" and isinstance(coords, list) and coords:
                                coord = coords[len(coords) // 2]
                            elif gtype in ("Polygon", "MultiPolygon") and isinstance(coords, list):
                                p_min_x, p_max_x = float("inf"), float("-inf")
                                p_min_y, p_max_y = float("inf"), float("-inf")

                                def extract_poly_pts(c: Any) -> None:
                                    nonlocal p_min_x, p_max_x, p_min_y, p_max_y
                                    if (
                                        isinstance(c, (list, tuple))
                                        and len(c) >= 2
                                        and isinstance(c[0], (int, float))
                                        and isinstance(c[1], (int, float))
                                    ):
                                        try:
                                            xv, yv = float(c[0]), float(c[1])
                                            if _math.isfinite(xv) and _math.isfinite(yv):
                                                p_min_x = min(p_min_x, xv)
                                                p_max_x = max(p_max_x, xv)
                                                p_min_y = min(p_min_y, yv)
                                                p_max_y = max(p_max_y, yv)
                                        except (ValueError, TypeError):
                                            pass
                                    elif isinstance(c, list):
                                        for sub in c:
                                            extract_poly_pts(sub)

                                extract_poly_pts(coords)
                                if (
                                    _math.isfinite(p_min_x)
                                    and _math.isfinite(p_max_x)
                                    and _math.isfinite(p_min_y)
                                    and _math.isfinite(p_max_y)
                                ):
                                    coord = [(p_min_x + p_max_x) / 2.0, (p_min_y + p_max_y) / 2.0]

                            if not coord:
                                continue

                            x, y = project(coord)
                            base_size = _safe_float(_resolve_paint_value(layout.get("text-size") or paint.get("text-size") or layout.get("labelSize") or paint.get("labelSize"), props, 12.0), 12.0)
                            font_size = _fmt_num(base_size * dpi_scale)
                            color = _escape_svg_attr(_resolve_paint_value(paint.get("text-color") or layout.get("text-color") or paint.get("labelColor") or layout.get("labelColor"), props, "#000000"))
                            opacity = _escape_svg_attr(_fmt_num(_safe_float(_resolve_paint_value(paint.get("text-opacity") or layout.get("text-opacity") or paint.get("labelOpacity") or layout.get("labelOpacity"), props, 1.0), 1.0)))
                            font_raw = layout.get("text-font") or paint.get("text-font") or "sans-serif"
                            font_family = _escape_svg_attr(", ".join(font_raw) if isinstance(font_raw, list) else font_raw)

                            raw_anchor = str(layout.get("text-anchor") or paint.get("text-anchor") or "center")
                            svg_text_anchor = "middle"
                            svg_dominant_baseline = "central"

                            if raw_anchor == "top":
                                svg_text_anchor = "middle"
                                svg_dominant_baseline = "hanging"
                            elif raw_anchor == "bottom":
                                svg_text_anchor = "middle"
                                svg_dominant_baseline = "ideographic"
                            elif raw_anchor in ("left", "start"):
                                svg_text_anchor = "start"
                                svg_dominant_baseline = "central"
                            elif raw_anchor in ("right", "end"):
                                svg_text_anchor = "end"
                                svg_dominant_baseline = "central"
                            elif raw_anchor == "top-left":
                                svg_text_anchor = "start"
                                svg_dominant_baseline = "hanging"
                            elif raw_anchor == "top-right":
                                svg_text_anchor = "end"
                                svg_dominant_baseline = "hanging"
                            elif raw_anchor == "bottom-left":
                                svg_text_anchor = "start"
                                svg_dominant_baseline = "ideographic"
                            elif raw_anchor == "bottom-right":
                                svg_text_anchor = "end"
                                svg_dominant_baseline = "ideographic"

                            text_escaped = _escape_svg_attr(raw_text)

                            base_halo_w = _safe_float(_resolve_paint_value(paint.get("text-halo-width") or layout.get("text-halo-width") or paint.get("haloWidth") or layout.get("haloWidth") or paint.get("textHaloWidth"), props, 0.0), 0.0)
                            if base_halo_w > 0:
                                halo_w = _fmt_num(base_halo_w * dpi_scale * 2.0)
                                halo_color = _escape_svg_attr(_resolve_paint_value(paint.get("text-halo-color") or layout.get("text-halo-color") or paint.get("haloColor") or layout.get("haloColor") or paint.get("textHaloColor"), props, "#ffffff"))
                                halo_opacity_val = _resolve_paint_value(paint.get("text-halo-opacity") or layout.get("text-halo-opacity") or paint.get("haloOpacity") or layout.get("haloOpacity"), props, 1.0)
                                halo_opacity = _escape_svg_attr(_fmt_num(_safe_float(halo_opacity_val, 1.0)))
                                elements_svg += f'<text x="{x}" y="{y}" font-size="{font_size}" font-family="{font_family}" fill="none" stroke="{halo_color}" stroke-width="{halo_w}" stroke-opacity="{halo_opacity}" stroke-linejoin="round" stroke-linecap="round" text-anchor="{svg_text_anchor}" dominant-baseline="{svg_dominant_baseline}">{text_escaped}</text>\n'

                            elements_svg += f'<text x="{x}" y="{y}" font-size="{font_size}" font-family="{font_family}" fill="{color}" fill-opacity="{opacity}" text-anchor="{svg_text_anchor}" dominant-baseline="{svg_dominant_baseline}">{text_escaped}</text>\n'

                except Exception:
                    continue

        viewbox_w = _fmt_num(scaled_width)
        viewbox_h = _fmt_num(scaled_height)

        return f"""<svg width="{width_val}" height="{height_val}" viewBox="0 0 {viewbox_w} {viewbox_h}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="#ffffff" />
  <g class="mapspec-vector-layers">
    {elements_svg}
  </g>
</svg>"""

    except Exception:
        w_fallback = width if isinstance(width, (int, float)) and width > 0 else 1200
        h_fallback = height if isinstance(height, (int, float)) and height > 0 else 800
        return f"""<svg width="{int(w_fallback)}" height="{int(h_fallback)}" viewBox="0 0 {int(w_fallback)} {int(h_fallback)}" xmlns="http://www.w3.org/2000/svg">
  <rect width="100%" height="100%" fill="#ffffff" />
  <g class="mapspec-vector-layers">
  </g>
</svg>"""

