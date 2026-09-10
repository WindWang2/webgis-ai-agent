"""Test GIS-06: 3D extrusion stops with dynamic precision on small ranges.

Verifies that sub-centimeter data ranges (e.g. NDVI 0.001 to 0.004) generate
strictly increasing MapLibre interpolation stops rather than identical rounded stops,
preventing MapLibre GL JS shader compilation crashes.
"""
from app.lib.cartography.extrusion_model import (
    ExtrusionHeightSpec,
    build_extrusion_height_expression,
)


def _extract_domain_stops(expr: list) -> list[float]:
    """Helper to extract domain stops from MapLibre interpolate expression:
    ['interpolate', ['linear'], ['coalesce', ...], stop0_v, stop0_h, stop1_v, stop1_h, ...]
    """
    assert expr[0] == "interpolate"
    # Pairs start after index 3
    pairs = expr[3:]
    domain_stops = []
    for i in range(0, len(pairs), 2):
        domain_stops.append(float(pairs[i]))
    return domain_stops


def test_extrusion_subcent_ndvi_range_stops_strictly_increasing():
    """Small range [0.001, 0.004] must produce strictly increasing stops."""
    spec = ExtrusionHeightSpec(
        height_field="ndvi_change",
        min_visual_height_m=10.0,
        max_visual_height_m=100.0,
        transform="linear",
    )
    stats = {
        "valid": True,
        "min": 0.001,
        "max": 0.004,
        "has_extreme_outlier": False,
    }

    expr = build_extrusion_height_expression(spec, stats)
    domain_stops = _extract_domain_stops(expr)
    assert len(domain_stops) >= 2

    # MapLibre strict requirement: every subsequent stop must be strictly greater than preceding
    for i in range(len(domain_stops) - 1):
        assert domain_stops[i + 1] > domain_stops[i], (
            f"Stops must be strictly increasing: stop[{i}]={domain_stops[i]}, "
            f"stop[{i+1}]={domain_stops[i+1]}"
        )


def test_extrusion_micro_range_stops_strictly_increasing():
    """Micro range [0.00001, 0.00004] must also have strictly increasing stops."""
    spec = ExtrusionHeightSpec(
        height_field="deformation",
        min_visual_height_m=5.0,
        max_visual_height_m=50.0,
        transform="linear",
    )
    stats = {
        "valid": True,
        "min": 0.00001,
        "max": 0.00004,
        "has_extreme_outlier": False,
    }

    expr = build_extrusion_height_expression(spec, stats)
    domain_stops = _extract_domain_stops(expr)
    assert len(domain_stops) >= 2
    for i in range(len(domain_stops) - 1):
        assert domain_stops[i + 1] > domain_stops[i]


def test_extrusion_normal_range_remains_clean():
    """Normal range [10.0, 100.0] retains clean 2-decimal precision."""
    spec = ExtrusionHeightSpec(
        height_field="building_height",
        min_visual_height_m=10.0,
        max_visual_height_m=100.0,
        transform="linear",
    )
    stats = {
        "valid": True,
        "min": 10.0,
        "max": 100.0,
        "has_extreme_outlier": False,
    }

    expr = build_extrusion_height_expression(spec, stats)
    domain_stops = _extract_domain_stops(expr)
    assert domain_stops == [10.0, 32.5, 55.0, 77.5, 100.0]
