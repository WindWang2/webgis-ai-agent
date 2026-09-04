"""Unit tests for 3D Extrusion Cartographic Model & Runtime (ADR-0095)."""
from app.lib.cartography.extrusion_model import (
    ExtrusionHeightSpec,
    analyze_height_field_distribution,
    build_extrusion_height_expression,
)
from app.services.analysis_cartography_converter import convert_analysis_to_mapspec_layer
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics


def _make_polygon_fc(properties_list):
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [
                            [104.0 + i * 0.05, 30.6],
                            [104.0 + (i + 1) * 0.05, 30.6],
                            [104.0 + (i + 1) * 0.05, 30.65],
                            [104.0 + i * 0.05, 30.65],
                            [104.0 + i * 0.05, 30.6],
                        ]
                    ],
                },
                "properties": props,
            }
            for i, props in enumerate(properties_list)
        ],
    }


def test_height_field_distribution_analysis():
    # Normal distribution
    vals = [100.0, 200.0, 300.0, 400.0, 500.0]
    stats = analyze_height_field_distribution(vals)
    assert stats["valid"] is True
    assert stats["count"] == 5
    assert stats["min"] == 100.0
    assert stats["max"] == 500.0
    assert stats["is_all_zero"] is False
    assert stats["has_extreme_outlier"] is False

    # All-zero detection
    stats_zero = analyze_height_field_distribution([0.0, 0.0, 0.0])
    assert stats_zero["valid"] is True
    assert stats_zero["is_all_zero"] is True

    # Extreme outlier detection (e.g. population of mega-district vs tiny ones)
    stats_outlier = analyze_height_field_distribution([10, 12, 11, 15, 14, 1000000])
    assert stats_outlier["valid"] is True
    assert stats_outlier["has_extreme_outlier"] is True

    # Empty/NaN values
    stats_empty = analyze_height_field_distribution([None, "invalid", float("nan")])
    assert stats_empty["valid"] is False


def test_build_extrusion_height_expression():
    spec = ExtrusionHeightSpec(
        height_field="pop",
        min_visual_height_m=50.0,
        max_visual_height_m=3000.0,
        transform="linear",
    )
    stats = {
        "valid": True,
        "is_all_zero": False,
        "min": 100.0,
        "max": 1000.0,
        "p05": 150.0,
        "p50": 500.0,
        "p95": 950.0,
    }
    expr = build_extrusion_height_expression(spec, stats)
    assert isinstance(expr, list)
    assert expr[0] == "interpolate"
    assert expr[1] == ["linear"]
    assert expr[2] == ["coalesce", ["get", "pop"], 100.0]

    # Constant fallback when all zero or invalid
    spec_zero = ExtrusionHeightSpec(height_field="pop", min_visual_height_m=20.0)
    expr_zero = build_extrusion_height_expression(spec_zero, {"valid": False})
    assert expr_zero == 20.0


def test_converter_3d_extrusion_dual_channels():
    # Height = GDP, Color = per_capita_gdp
    features_props = [
        {"gdp": 1000, "per_capita_gdp": 50000, "district": "A"},
        {"gdp": 2000, "per_capita_gdp": 80000, "district": "B"},
        {"gdp": 3000, "per_capita_gdp": 45000, "district": "C"},
        {"gdp": 4000, "per_capita_gdp": 95000, "district": "D"},
        {"gdp": 5000, "per_capita_gdp": 60000, "district": "E"},
    ]
    fc = _make_polygon_fc(features_props)

    analysis_payload = {
        "type_hint": "extrusion_3d",
        "geojson": fc,
        "metadata": {
            "extrusion": {
                "height_field": "gdp",
                "color_field": "per_capita_gdp",
                "height_unit": "亿元",
                "min_visual_height_m": 100.0,
                "max_visual_height_m": 4000.0,
            }
        },
        "legend_spec": {
            "type": "graduated",
            "field": "per_capita_gdp",
            "breaks": [40000, 60000, 80000, 100000],
            "palette_colors": ["#fee5d9", "#fcae91", "#fb6a4a", "#cb181d"],
            "labels": ["Low", "Mid", "High", "Very High"],
        },
    }

    layer, inline_fc, warnings = convert_analysis_to_mapspec_layer(analysis_payload)
    assert layer["type"] == "fill-extrusion"
    paint = layer["paint"]
    assert "fill-extrusion-height" in paint
    assert "fill-extrusion-color" in paint
    assert "fill-extrusion-base" in paint

    # Verify recommended view
    assert "recommended_view" in layer
    assert layer["recommended_view"]["pitch"] == 45.0
    assert layer["recommended_view"]["bearing"] == -15.0

    # Verify extrusion metadata
    assert "extrusion" in layer
    assert layer["extrusion"]["height_field"] == "gdp"
    assert layer["extrusion"]["height_unit"] == "亿元"


def test_converter_3d_extrusion_guard_on_non_polygon():
    # If point features are given to extrusion_3d, guard should fallback to circle/heatmap
    point_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.0, 30.6]},
                "properties": {"height": 500},
            }
        ],
    }
    analysis_payload = {
        "type_hint": "extrusion_3d",
        "geojson": point_fc,
    }
    layer, _, warnings = convert_analysis_to_mapspec_layer(analysis_payload)
    assert layer["type"] != "fill-extrusion"
    assert any("extrusion_3d_guard" in w for w in warnings)


def test_semantic_checks_3d_extrusion_rules():
    fc = _make_polygon_fc([{"pop": 100}, {"pop": 200}])
    mapspec_topdown = {
        "version": "1.0",
        "view": {"center": [104.0, 30.6], "zoom": 10, "pitch": 0.0},
        "sources": {"s1": {"type": "geojson", "data": fc}},
        "layers": [
            {
                "id": "l1",
                "type": "fill-extrusion",
                "source": "s1",
                "extrusion": {
                    "height_field": "pop",
                    "min_visual_height_m": 10.0,
                    "max_visual_height_m": 2000.0,
                    "stats": {"valid": True, "is_all_zero": False},
                },
                "paint": {"fill-extrusion-height": ["get", "pop"]},
            }
        ],
    }

    report = evaluate_cartography_semantics(mapspec_topdown)
    checks_by_rule = {c.rule: c for c in report.checks}
    # Advisory warning for top-down camera pitch
    assert "EXTRUSION_PITCH_ADVISORY" in checks_by_rule
    assert checks_by_rule["EXTRUSION_PITCH_ADVISORY"].status == "warning"

    # Now tilted camera -> pitch advisory should pass
    mapspec_tilted = dict(mapspec_topdown)
    mapspec_tilted["view"] = {"center": [104.0, 30.6], "zoom": 10, "pitch": 45.0}
    report_tilted = evaluate_cartography_semantics(mapspec_tilted)
    checks_by_rule_tilted = {c.rule: c for c in report_tilted.checks}
    assert checks_by_rule_tilted["EXTRUSION_PITCH_ADVISORY"].status == "pass"


def test_extrusion_outlier_scaling_distribution():
    """Verify extreme outliers do not squash 99% of normal features under log1p and linear scaling."""
    # 9 normal district values and 1 extreme outlier
    values = [10.0, 15.0, 20.0, 25.0, 30.0, 35.0, 40.0, 45.0, 50.0, 1000000.0]
    stats = analyze_height_field_distribution(values)
    assert stats["valid"] is True
    assert stats["has_extreme_outlier"] is True
    assert "p25" in stats and "p75" in stats
    assert stats["p25"] > 0
    assert stats["p75"] > stats["p25"]

    # Test log1p transform
    spec_log = ExtrusionHeightSpec(
        height_field="val",
        transform="log1p",
        min_visual_height_m=10.0,
        max_visual_height_m=5000.0,
    )
    expr_log = build_extrusion_height_expression(spec_log, stats)
    assert isinstance(expr_log, list)
    assert expr_log[0] == "interpolate"

    # Extract stops: [domain, visual_height]
    stops = []
    for i in range(3, len(expr_log), 2):
        stops.append((expr_log[i], expr_log[i + 1]))

    # Domain stops must be strictly increasing
    for i in range(len(stops) - 1):
        assert stops[i][0] < stops[i + 1][0], f"Stops not strictly increasing: {stops}"

    # Verify that value 50.0 is not squashed into near-zero visual height
    # Stop 1 domain value should be near low values (~40.6), mapping to ~1257m
    d0, h0 = stops[0]
    d1, h1 = stops[1]
    assert d0 <= 50.0
    assert h1 >= 1000.0
    assert stops[-1][0] == 1000000.0
    assert stops[-1][1] == 5000.0


def test_extrusion_svg_export_degraded_attributes():
    """Verify fill-extrusion layers emit degraded export attributes in Python SVG compiler."""
    from app.services.mapspec_to_svg import compile_mapspec_to_svg

    mapspec = {
        "sources": {
            "s1": {
                "type": "geojson",
                "data": _make_polygon_fc([{"height": 200}]),
            }
        },
        "layers": [
            {
                "id": "ext1",
                "type": "fill-extrusion",
                "source": "s1",
                "paint": {
                    "fill-extrusion-color": "#ea580c",
                    "fill-extrusion-height": 200,
                    "fill-extrusion-opacity": 0.85,
                },
            }
        ],
    }
    svg = compile_mapspec_to_svg(mapspec, target_dpi=72)
    assert '<path' in svg
    assert 'data-export-degraded="true"' in svg
    assert 'data-export-degraded-reason="3d_perspective_not_vectorized"' in svg
    assert 'fill="#ea580c"' in svg


def test_tool_create_3d_extrusion_dual_channel_legend():
    """Verify create_3d_extrusion_map emits height scale legend when height_field != color_field (ADR-0095)."""
    from app.tools.registry import ToolRegistry
    from app.tools.cartography import register_cartography_tools

    registry = ToolRegistry()
    register_cartography_tools(registry)
    tool_fn = registry._tools["create_3d_extrusion_map"]

    fc = _make_polygon_fc([
        {"gdp": 100, "pop": 1000},
        {"gdp": 200, "pop": 2000},
        {"gdp": 300, "pop": 3000},
        {"gdp": 400, "pop": 4000},
        {"gdp": 500, "pop": 5000},
    ])

    # Case 1: Dual channel (height != color)
    res_dual = tool_fn(
        geojson=fc,
        height_field="gdp",
        color_field="pop",
        height_unit="亿元",
        min_visual_height_m=50.0,
        max_visual_height_m=3000.0,
    )
    assert "error" not in res_dual
    assert "height_legend" in res_dual
    hl = res_dual["height_legend"]
    assert hl["type"] == "height_scale"
    assert hl["field"] == "gdp"
    assert hl["unit"] == "亿元"
    assert hl["min_value"] == 100.0
    assert hl["max_value"] == 500.0
    assert hl["min_height_m"] == 50.0
    assert hl["max_height_m"] == 3000.0
    assert len(hl["stops"]) == 5
    assert res_dual["metadata"]["extrusion"]["height_legend"] == hl

    # Case 2: Single channel (height == color) -> no height legend needed
    res_single = tool_fn(
        geojson=fc,
        height_field="gdp",
        color_field="gdp",
    )
    assert "error" not in res_single
    assert "height_legend" not in res_single

