"""Unit tests for Python MapSpec-to-SVG vector compiler target."""
from app.services.mapspec_to_svg import compile_mapspec_to_svg




def test_compile_mapspec_to_svg_basic():
    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {"name": "Beijing"},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Polygon", "coordinates": [[[116.3, 39.8], [116.5, 39.8], [116.5, 40.0], [116.3, 40.0], [116.3, 39.8]]]},
                            "properties": {"name": "Area 1"},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "pts",
                "type": "circle",
                "source": "s1",
                "paint": {"circle-color": "#de2d26", "circle-radius": 5},
            },
            {
                "id": "polys",
                "type": "fill",
                "source": "s1",
                "paint": {"fill-color": "#60a5fa", "fill-outline-color": "#1d4ed8"},
            }
        ],
    }

    svg_72 = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert "<svg" in svg_72
    assert "<circle" in svg_72
    assert "<path" in svg_72
    assert 'fill-rule="evenodd"' in svg_72
    # The compiler emits the canonical minimal form (_fmt_num strips trailing
    # zeros): 5.0 -> "5", 1.0 -> "1".
    assert 'r="5"' in svg_72
    assert 'stroke-width="1"' in svg_72

    svg_300 = compile_mapspec_to_svg(mapspec, target_dpi=300)
    # 5 * (300 / 72) = 20.83
    assert 'r="20.83"' in svg_300
    # 1.0 * (300 / 72) = 4.17
    assert 'stroke-width="4.17"' in svg_300
    assert 'viewBox="0 0 5000 3333.33"' in svg_300


def test_resolve_paint_value_style_methods():
    """MAPSPEC-01: Verify _resolve_paint_value supports constant, field, match, step, interpolate."""
    from app.services.mapspec_to_svg import _resolve_paint_value

    props = {"val": 15, "category": "B", "val_float": 50.0}

    # Constant
    assert _resolve_paint_value({"method": "constant", "value": "#ff0000"}) == "#ff0000"

    # Field
    assert _resolve_paint_value({"method": "field", "field": "category"}, props=props) == "B"

    # Match
    match_spec = {"method": "match", "field": "category", "cases": [["A", "#f00"], ["B", "#0f0"]], "default": "#00f"}
    assert _resolve_paint_value(match_spec, props=props) == "#0f0"

    # Step
    step_spec = {"method": "step", "field": "val", "stops": [[10, "#f00"], [20, "#00f"]], "default": "#fff"}
    assert _resolve_paint_value(step_spec, props={"val": 5}) == "#fff"
    assert _resolve_paint_value(step_spec, props={"val": 15}) == "#f00"
    assert _resolve_paint_value(step_spec, props={"val": 25}) == "#00f"

    # Interpolate number
    interp_num = {"method": "interpolate", "field": "val_float", "stops": [[0, 10], [100, 50]]}
    assert _resolve_paint_value(interp_num, props=props) == 30.0

    # Interpolate color
    interp_col = {"method": "interpolate", "field": "val_float", "stops": [[0, "#000000"], [100, "#ffffff"]]}
    assert _resolve_paint_value(interp_col, props=props) == "#808080"


def test_polygon_holes_rendered_with_evenodd():
    """MAPSPEC-02: Verify multi-ring polygon renders with fill-rule="evenodd" and multiple M ... Z path segments."""
    mapspec_hole = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [[116.0, 39.0], [117.0, 39.0], [117.0, 40.0], [116.0, 40.0], [116.0, 39.0]],  # Outer ring
                            [[116.3, 39.3], [116.7, 39.3], [116.7, 39.7], [116.3, 39.7], [116.3, 39.3]],  # Hole
                        ],
                    },
                },
            }
        },
        "layers": [{"id": "p", "type": "fill", "source": "s1", "paint": {"fill-color": "#123456"}}],
    }
    svg = compile_mapspec_to_svg(mapspec_hole, target_dpi=72)
    assert '<path d="M ' in svg
    assert ' Z M ' in svg
    assert 'fill-rule="evenodd"' in svg



def test_compile_mapspec_to_svg_escapes_paint_values():
    """P0-3b: paint color/opacity values are interpolated into SVG attributes
    without escaping, allowing attribute-injection (XSS) via a crafted color.
    The compiler must HTML-escape these values.
    """
    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "pts",
                "type": "circle",
                "source": "s1",
                # Tries to break out of the fill=" attribute.
                "paint": {"circle-color": 'red" onclick="alert(1)'},
            }
        ],
    }
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    # The injected attribute boundary must not survive.
    assert 'red" onclick' not in svg
    assert "&quot;" in svg


def test_compile_mapspec_to_svg_empty_or_degenerate_extents():
    """Defensive check: empty sources, empty features, or single point extents must not crash or divide-by-zero."""
    # Empty mapspec
    svg_empty = compile_mapspec_to_svg({})
    assert "<svg" in svg_empty

    # Mapspec with empty sources
    svg_no_src = compile_mapspec_to_svg({"sources": {}, "layers": []})
    assert "<svg" in svg_no_src

    # Mapspec with single point (degenerate range_x = 0, range_y = 0)
    single_pt_mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [10.0, 20.0]},
                },
            }
        },
        "layers": [
            {
                "id": "p1",
                "type": "circle",
                "source": "s1",
                "paint": {"circle-radius": 5},
            }
        ],
    }
    svg_single = compile_mapspec_to_svg(single_pt_mapspec)
    assert "<svg" in svg_single
    assert "<circle" in svg_single


def test_compile_mapspec_to_svg_nan_and_inf_bounds():
    """Defensive check: NaN or Inf in coordinates or bounds fallback to default bounds without crashing."""
    mapspec_nan = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [float("nan"), float("inf")]},
                        },
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [100.0, float("-inf")]},
                        },
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "l1",
                "type": "circle",
                "source": "s1",
            }
        ],
    }
    svg_nan = compile_mapspec_to_svg(mapspec_nan)
    assert "<svg" in svg_nan


def test_compile_mapspec_to_svg_invalid_log_inputs():
    """Defensive check: target_dpi <= 0, NaN, Inf, or invalid raster extent log arguments do not raise MathDomainError."""
    mapspec_raster = {
        "sources": {
            "r1": {
                "type": "raster",
                "tiles": ["https://tile.example.com/{z}/{x}/{y}.png"],
            }
        },
        "layers": [
            {
                "id": "r-layer",
                "type": "raster",
                "source": "r1",
            }
        ],
    }

    # Zero, negative, NaN, Inf target_dpi
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=0)
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=-96)
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=float("nan"))
    assert "<svg" in compile_mapspec_to_svg(mapspec_raster, target_dpi=float("inf"))


def test_compile_mapspec_to_svg_malformed_inputs_handled_gracefully():
    """Defensive check: non-dict mapspec, malformed layers, non-numeric paint properties do not raise unhandled exceptions."""
    assert "<svg" in compile_mapspec_to_svg(None)
    assert "<svg" in compile_mapspec_to_svg("invalid_mapspec")
    assert "<svg" in compile_mapspec_to_svg({"layers": ["invalid_layer_item"], "sources": None})

    malformed_paint_mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [10, 20]},
                },
            }
        },
        "layers": [
            {
                "id": "l1",
                "type": "circle",
                "source": "s1",
                "paint": {
                    "circle-radius": "invalid_number",
                    "circle-opacity": None,
                },
            }
        ],
    }
    svg = compile_mapspec_to_svg(malformed_paint_mapspec)
    assert "<svg" in svg


def test_mapspec_05_stroke_and_dasharray_properties():
    """MAPSPEC-05: circle-stroke-color, circle-stroke-width, line-dasharray, line-linecap, line-linejoin."""
    mapspec_stroke = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "LineString",
                                "coordinates": [
                                    [116.4, 39.9],
                                    [116.5, 40.0],
                                ],
                            },
                        },
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "circle-stroke",
                "type": "circle",
                "source": "s1",
                "paint": {
                    "circle-color": "#3b82f6",
                    "circle-stroke-color": "#000000",
                    "circle-stroke-width": 2,
                },
            },
            {
                "id": "line-styled",
                "type": "line",
                "source": "s1",
                "layout": {
                    "line-linecap": "round",
                    "line-linejoin": "bevel",
                },
                "paint": {
                    "line-color": "#2563eb",
                    "line-width": 2,
                    "line-dasharray": [2, 4],
                },
            },
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_stroke, target_dpi=300)
    assert 'stroke="#000000"' in svg
    assert 'stroke-width="8.33"' in svg
    assert 'stroke-linecap="round"' in svg
    assert 'stroke-linejoin="bevel"' in svg
    assert 'stroke-dasharray="8.33,16.67"' in svg


def test_mapspec_06_text_halo_and_anchors():
    """MAPSPEC-06: Text halo SVG rendering and MapLibre text-anchor to SVG text-anchor/dominant-baseline."""
    mapspec_halo = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                            "properties": {"label": "Test Label"},
                        }
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "label-halo",
                "type": "symbol",
                "source": "s1",
                "layout": {
                    "text-field": "{label}",
                    "text-anchor": "top-left",
                },
                "paint": {
                    "text-color": "#000000",
                    "text-halo-color": "#ffffff",
                    "text-halo-width": 2,
                },
            }
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_halo, target_dpi=300)
    assert 'fill="none" stroke="#ffffff" stroke-width="16.67"' in svg
    assert 'text-anchor="start"' in svg
    assert 'dominant-baseline="hanging"' in svg
    assert "Test Label" in svg


def test_mapspec_07_polygon_bbox_centroid():
    """MAPSPEC-07: Compute bounding box centroid for polygon text label placement."""
    mapspec_poly_label = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [
                            [
                                [10, 20],
                                [30, 20],
                                [30, 40],
                                [10, 40],
                                [10, 20],
                            ]
                        ],
                    },
                    "properties": {"name": "PolyCenter"},
                },
            }
        },
        "layers": [
            {
                "id": "poly-text",
                "type": "symbol",
                "source": "s1",
                "layout": {"text-field": "{name}"},
            }
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_poly_label, target_dpi=72)
    assert '<text x="600"' in svg
    assert "PolyCenter" in svg


def test_mapspec_08_fallback_rendering():
    """MAPSPEC-08: Add fallback rendering for heatmap and fill-extrusion layer types."""
    mapspec_fallbacks = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": {
                    "type": "FeatureCollection",
                    "features": [
                        {
                            "type": "Feature",
                            "geometry": {"type": "Point", "coordinates": [116.4, 39.9]},
                        },
                        {
                            "type": "Feature",
                            "geometry": {
                                "type": "Polygon",
                                "coordinates": [
                                    [
                                        [116.3, 39.8],
                                        [116.5, 39.8],
                                        [116.5, 40.0],
                                        [116.3, 40.0],
                                        [116.3, 39.8],
                                    ]
                                ],
                            },
                        },
                    ],
                },
            }
        },
        "layers": [
            {
                "id": "heat",
                "type": "heatmap",
                "source": "s1",
                "paint": {
                    "heatmap-color": "#ff0000",
                    "heatmap-radius": 10,
                },
            },
            {
                "id": "3d-bldg",
                "type": "fill-extrusion",
                "source": "s1",
                "paint": {
                    "fill-extrusion-color": "#334155",
                    "fill-extrusion-opacity": 0.9,
                },
            },
        ],
    }
    svg = compile_mapspec_to_svg(mapspec_fallbacks, target_dpi=72)
    assert "<circle" in svg
    assert 'fill="#ff0000"' in svg
    assert 'r="10"' in svg
    assert '<path d="M ' in svg
    assert 'fill="#334155"' in svg
    assert 'fill-opacity="0.9"' in svg


