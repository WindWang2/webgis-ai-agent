"""Unit tests for 3D Extrusion Cartographic Model & Runtime (ADR-0095)."""
import pytest
from app.lib.cartography.extrusion_model import (
    ExtrusionHeightSpec,
    analyze_height_field_distribution,
    build_extrusion_height_expression,
    build_extrusion_base_expression,
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
