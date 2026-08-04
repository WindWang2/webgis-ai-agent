"""Unit tests for Analysis -> Cartography Converter (Seam A)."""
from app.services.analysis_cartography_converter import (
    is_analysis_result,
    convert_analysis_to_mapspec_layer,
    _infer_geometry_category,
)


def test_is_analysis_result_detection():
    # Plain GeoJSON dictionary
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {"val": 10},
            }
        ],
    }
    assert is_analysis_result(geojson) is False

    # Plain GeoJSON with a 'data' property key
    geojson_with_data_prop = {
        "type": "FeatureCollection",
        "data": "some_extra_info",
        "features": [],
    }
    assert is_analysis_result(geojson_with_data_prop) is False

    # Analysis result with legend_spec
    analysis_with_legend = {
        "success": True,
        "algorithm": "hotspot",
        "data": geojson,
        "legend_spec": {
            "type": "graduated",
            "field": "val",
            "breaks": [0.0, 5.0, 10.0],
            "palette_colors": ["#00ff00", "#ff0000"],
        },
    }
    assert is_analysis_result(analysis_with_legend) is True

    # Geometry-only analysis result without legend_spec
    geometry_analysis = {
        "success": True,
        "algorithm": "spatial_buffer",
        "data": geojson,
        "params": {"distance": 100},
    }
    assert is_analysis_result(geometry_analysis) is True

    # String input is not an analysis result dict
    assert is_analysis_result("ref:123") is False
    assert is_analysis_result(None) is False

    # Detection priority: a GeoJSON FeatureCollection that happens to carry a
    # top-level analysis-marker key (e.g. `algorithm`/`source_ref`) must still
    # be treated as GeoJSON, NOT an analysis result. GeoJSON wins over markers.
    geojson_with_marker = {
        "type": "FeatureCollection",
        "algorithm": "stray_metadata",  # legal extra top-level key
        "source_ref": "ref:leftover",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {},
            }
        ],
    }
    assert is_analysis_result(geojson_with_marker) is False


def test_graduated_legend_to_step_style_method():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {"score": 15.0},
            }
        ],
    }
    analysis = {
        "success": True,
        "algorithm": "hotspot_analysis",
        "data": geojson,
        "legend_spec": {
            "type": "graduated",
            "field": "score",
            "breaks": [0.0, 10.0, 20.0, 30.0],
            "palette_colors": ["#ffffb2", "#fd8d3c", "#bd0026"],
        },
        "source_ref": "ref:source_eq",
        "params": {"radius": 500},
    }

    layer_input = {"id": "hotspot_layer", "source": "hotspot_source"}
    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(
        analysis, layer_input
    )

    assert inline_geojson == geojson
    assert warnings == []
    assert converted_layer["id"] == "hotspot_layer"
    assert converted_layer["source"] == "hotspot_source"
    assert converted_layer["type"] == "circle"

    # Verify paint color step mapping
    color_paint = converted_layer["paint"]["color"]
    assert color_paint["method"] == "step"
    assert color_paint["field"] == "score"
    assert color_paint["default"] == "#ffffb2"
    assert color_paint["stops"] == [[10.0, "#fd8d3c"], [20.0, "#bd0026"]]

    # Verify provenance
    provenance = converted_layer["provenance"]
    assert provenance["algorithm"] == "hotspot_analysis"
    assert provenance["source_ref"] == "ref:source_eq"
    assert provenance["params"] == {"radius": 500}
    assert "computed_at" in provenance


def test_continuous_legend_to_interpolate_style_method():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [120.0, 30.0]},
                "properties": {"kde_density": 0.42},
            }
        ],
    }
    analysis = {
        "success": True,
        "algorithm": "kde_analysis",
        "data": geojson,
        "legend_spec": {
            "type": "continuous",
            "field": "kde_density",
            "min": 0.0,
            "max": 100.0,
            "palette_colors": ["#eff3ff", "#6baed6", "#08519c"],
        },
    }

    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(analysis)

    assert warnings == []
    assert converted_layer["type"] == "circle"

    # Verify paint color interpolate mapping
    color_paint = converted_layer["paint"]["color"]
    assert color_paint["method"] == "interpolate"
    assert color_paint["field"] == "kde_density"
    assert color_paint["stops"] == [
        [0.0, "#eff3ff"],
        [50.0, "#6baed6"],
        [100.0, "#08519c"],
    ]


def test_categorical_legend_to_match_style_method():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
                "properties": {"cluster": "HH"},
            }
        ],
    }
    analysis = {
        "success": True,
        "algorithm": "lisa_analysis",
        "data": geojson,
        "legend_spec": {
            "type": "categorical",
            "field": "cluster",
            "categories": [
                {"key": "HH", "color": "#ff0000", "label": "High-High"},
                {"key": "LL", "color": "#0000ff", "label": "Low-Low"},
                {"key": "NS", "color": "#cccccc", "label": "Not Significant"},
            ],
        },
    }

    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(analysis)

    assert warnings == []
    assert converted_layer["type"] == "fill"

    # Verify paint color match mapping
    color_paint = converted_layer["paint"]["color"]
    assert color_paint["method"] == "match"
    assert color_paint["field"] == "cluster"
    assert color_paint["cases"] == [
        ["HH", "#ff0000"],
        ["LL", "#0000ff"],
        ["NS", "#cccccc"],
    ]
    assert color_paint["default"] == "#cccccc"


def test_categorical_default_is_last_category_color_ignoring_legend_default():
    """The categorical legend_spec contract has no `default` field; the match
    default is always the last category color. An extraneous `legend_spec.default`
    key must be ignored (regression guard for the undocumented-override fix)."""
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {"cluster": "HH"},
            }
        ],
    }
    analysis = {
        "success": True,
        "algorithm": "lisa",
        "data": geojson,
        "legend_spec": {
            "type": "categorical",
            "field": "cluster",
            "categories": [
                {"key": "HH", "color": "#ff0000", "label": "High-High"},
                {"key": "LL", "color": "#0000ff", "label": "Low-Low"},
            ],
            # Extraneous, not part of the contract — must be ignored.
            "default": "#ffffff",
        },
    }

    converted_layer, _, _ = convert_analysis_to_mapspec_layer(analysis)
    color_paint = converted_layer["paint"]["color"]
    # Default is the LAST category color, NOT the ignored legend_spec.default.
    assert color_paint["default"] == "#0000ff"


def test_geometry_inference_and_constant_fallback():
    polygon_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
                "properties": {},
            }
        ],
    }
    analysis = {
        "success": True,
        "algorithm": "spatial_buffer",
        "data": polygon_geojson,
        "params": {"distance": 50},
    }

    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(analysis)

    assert inline_geojson == polygon_geojson
    assert converted_layer["type"] == "fill"
    assert converted_layer["paint"]["color"] == "#3b82f6"
    assert converted_layer["paint"]["opacity"] == 0.6
    assert converted_layer["provenance"]["algorithm"] == "spatial_buffer"


def test_mixed_geometries_warning():
    mixed_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [0, 0]},
                "properties": {},
            },
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [1, 1]},
                "properties": {},
            },
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
                },
                "properties": {},
            },
        ],
    }
    analysis = {
        "success": True,
        "algorithm": "overlay_analysis",
        "data": mixed_geojson,
    }

    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(analysis)

    assert converted_layer["type"] == "circle"
    assert any("mixed_geometries" in w for w in warnings)
    assert any("mixed_geometries" in w for w in converted_layer["provenance"].get("warnings", []))


def test_unsupported_geometry_collection_does_not_crash():
    geom_coll_geojson = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "GeometryCollection", "geometries": []},
                "properties": {},
            }
        ],
    }
    cat, warnings = _infer_geometry_category(geom_coll_geojson)
    assert cat == "point"
    assert any("no_geometries" in w for w in warnings)


def test_unrecognized_legend_type_warning():
    geojson = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": "Point", "coordinates": [0, 0]}}
        ],
    }
    analysis = {
        "success": True,
        "algorithm": "custom_algorithm",
        "data": geojson,
        "legend_spec": {
            "type": "unknown_custom_type",
            "field": "score",
        },
    }

    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(analysis)

    assert converted_layer["type"] == "circle"
    assert converted_layer["paint"]["color"] == "#3b82f6"
    assert any("unrecognized_legend_type" in w for w in warnings)
    assert any("unrecognized_legend_type" in w for w in converted_layer["provenance"]["warnings"])


def test_converter_exception_resilience_on_malformed_input():
    # Pass a dict designed to trigger unexpected attribute error or exception during internal parsing
    malformed_analysis = {
        "success": True,
        "algorithm": "broken_algo",
        "data": "not_a_valid_geojson_or_dict",
        "legend_spec": 12345,  # Invalid type
    }

    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(malformed_analysis)

    assert converted_layer["id"] == "broken_algo_layer"
    assert converted_layer["type"] in ["circle", "fill", "line"]
    assert "provenance" in converted_layer
    assert len(converted_layer["provenance"]["warnings"]) >= 1

