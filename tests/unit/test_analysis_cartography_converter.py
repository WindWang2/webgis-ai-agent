"""Unit tests for Analysis -> Cartography Converter (Seam A)."""
import json

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



# ── 原生热力图授权（type_hint=heatmap → type=heatmap + 官方范式 paint）──


def _point_fc(n=3):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature",
             "geometry": {"type": "Point", "coordinates": [104.0 + i * 0.01, 30.6]},
             "properties": {"name": f"p{i}"}}
            for i in range(n)
        ],
    }


def test_heatmap_type_hint_authors_official_paint():
    """type_hint=heatmap 的点要素结果 → heatmap 图层 + 官方范式 paint。

    回归：无 type_hint 时点要素被推断为 circle（heatmap_data native 的
    FC 结果走 dispatch MapSpec 授权，热力图从未以 heatmap 层挂上）。
    """
    fc = _point_fc()
    analysis = {
        "success": True,
        "algorithm": "heatmap_data",
        "data": fc,
        "type_hint": "heatmap",
        "metadata": {"render_type": "native", "palette": "thermal",
                     "radius": 1500, "point_count": 3},
    }
    converted_layer, inline_geojson, warnings = convert_analysis_to_mapspec_layer(analysis)

    assert inline_geojson == fc
    assert converted_layer["type"] == "heatmap"

    # dispatch 实际形态（数据在 ref 后面，无内联 geojson）：type_hint 单独
    # 驱动图层类型 —— 否则点要素默认推断 circle，热力图从未挂上。
    dispatch_shape = {
        "geojson": fc, "algorithm": "heatmap_data",
        "type_hint": "heatmap",
        "metadata": {"palette": "classic", "radius": 2000},
    }
    layer2, _, _ = convert_analysis_to_mapspec_layer(dispatch_shape)
    assert layer2["type"] == "heatmap"
    assert layer2["paint"]["heatmap-color"][2] == ["heatmap-density"]
    paint = converted_layer["paint"]
    # 官方 create-a-heatmap-layer 的五个 paint 键，非 circle 的 color 语义
    for key in ("heatmap-weight", "heatmap-intensity", "heatmap-color",
                "heatmap-radius", "heatmap-opacity"):
        assert key in paint, f"missing {key}"
    # 色带：thermal 首色 + ≥6 停靠点；密度键驱动
    color_expr = paint["heatmap-color"]
    assert color_expr[0] == "interpolate" and color_expr[2] == ["heatmap-density"]
    flat = json.dumps(color_expr)
    assert "#0066ff" in flat and "rgba(0,40,255,0)" in flat
    # radius/intensity 都是 zoom 插值；米制 radius(1500) 回落默认 20px
    assert paint["heatmap-radius"][2] == ["zoom"]
    assert paint["heatmap-intensity"][2] == ["zoom"]
    assert paint["heatmap-radius"][6] == 20  # 米制 1500 回落默认 20px
    assert not any("heatmap" in str(w) for w in warnings)


def test_heatmap_paint_palette_fallback_and_unknown():
    """未知 palette 回落 classic；px 语义 radius 直通。"""
    from app.lib.cartography.palettes import heatmap_paint, heatmap_legend_colors

    paint = heatmap_paint("不存在", 24)
    assert "#428cd2" in json.dumps(paint["heatmap-color"])  # classic 兜底
    assert paint["heatmap-radius"][6] == 24
    assert paint["heatmap-radius"][8] == min(80, int(24 * 1.7))

    legend = heatmap_legend_colors("classic")
    assert legend[0] == "#428cd2" and legend[-1] == "#eb2828" and len(legend) == 6
