"""Unit tests for Isoline & Contour Cartographic Model & Runtime (ADR-0095)."""
import pytest
import numpy as np
from app.lib.cartography.isoline_model import (
    IsolineContourSpec,
    resolve_contour_levels,
    generate_contour_features_from_grid,
)
from app.services.analysis_cartography_converter import convert_analysis_to_mapspec_layer
from app.lib.cartography.semantic_checks import evaluate_cartography_semantics
from app.lib.geo_analysis.density import kde_contours


def test_resolve_contour_levels_explicit_preservation():
    # Explicit user levels must be 100% preserved
    user_levels = [100.0, 200.0, 300.0]
    spec = IsolineContourSpec(levels=user_levels)
    resolved = resolve_contour_levels(spec, [50.0, 350.0])
    assert resolved == [100.0, 200.0, 300.0]

    # Automatic equal interval levels
    spec_auto = IsolineContourSpec(levels=4, level_strategy="equal_interval")
    resolved_auto = resolve_contour_levels(spec_auto, [0.0, 100.0])
    assert len(resolved_auto) == 4
    assert resolved_auto[0] >= 0.0
    assert resolved_auto[-1] <= 100.0
    assert all(resolved_auto[i] < resolved_auto[i + 1] for i in range(len(resolved_auto) - 1))


def test_generate_contour_features_grid_lines():
    # Create a 2D Gaussian hill grid in Chengdu
    x = np.linspace(104.0, 104.1, 30)
    y = np.linspace(30.6, 30.7, 30)
    X, Y = np.meshgrid(x, y)
    Z = 500.0 * np.exp(-((X - 104.05)**2 + (Y - 30.65)**2) / 0.002)

    spec = IsolineContourSpec(
        mode="lines",
        levels=[100.0, 200.0, 300.0, 400.0],
        unit="m",
        index_contour_interval=2,
    )
    fc = generate_contour_features_from_grid(X, Y, Z, spec)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) > 0
    first = fc["features"][0]
    assert first["geometry"]["type"] in ("LineString", "MultiLineString")
    assert "level" in first["properties"]
    assert "value" in first["properties"]
    assert first["properties"]["unit"] == "m"
    assert "label" in first["properties"]
    assert "is_index_contour" in first["properties"]


def test_generate_contour_features_grid_filled_bands():
    x = np.linspace(104.0, 104.1, 30)
    y = np.linspace(30.6, 30.7, 30)
    X, Y = np.meshgrid(x, y)
    Z = 500.0 * np.exp(-((X - 104.05)**2 + (Y - 30.65)**2) / 0.002)

    spec = IsolineContourSpec(
        mode="filled_bands",
        levels=[100.0, 200.0, 300.0, 400.0],
        unit="m",
    )
    fc = generate_contour_features_from_grid(X, Y, Z, spec)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) > 0
    first = fc["features"][0]
    assert first["geometry"]["type"] in ("Polygon", "MultiPolygon")
    assert first["properties"]["layer_kind"] == "filled_contour_band"


def test_kde_contours_with_explicit_levels_and_modes():
    # 20 points in Chengdu
    pts = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [104.0 + (i % 5) * 0.02, 30.6 + (i // 5) * 0.02]},
                "properties": {},
            }
            for i in range(20)
        ],
    }

    # Test lines mode
    res_lines = kde_contours(pts, levels=6, mode="lines", unit="pts/km²")
    assert res_lines.success is True
    fc_lines = res_lines.data
    assert fc_lines["type_hint"] == "isoline_contour"
    assert len(fc_lines["features"]) > 0
    assert fc_lines["features"][0]["geometry"]["type"] == "LineString"
    assert "is_index_contour" in fc_lines["features"][0]["properties"]

    # Test filled bands mode
    res_bands = kde_contours(pts, levels=6, mode="filled_bands", unit="pts/km²")
    assert res_bands.success is True
    fc_bands = res_bands.data
    assert fc_bands["type_hint"] == "isoline_contour"
    assert len(fc_bands["features"]) > 0
    assert fc_bands["features"][0]["geometry"]["type"] == "Polygon"


def test_converter_isoline_contour():
    # Line contour conversion
    line_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[104.0, 30.6], [104.1, 30.7]]},
                "properties": {"value": 100, "is_index_contour": True},
            }
        ],
    }
    payload_line = {
        "type_hint": "isoline_contour",
        "geojson": line_fc,
        "metadata": {
            "isoline": {"model": "isoline_contour", "levels": [100.0, 200.0, 300.0]},
        },
    }
    layer_line, _, _ = convert_analysis_to_mapspec_layer(payload_line)
    assert layer_line["type"] == "line"
    assert "width" in layer_line["paint"]
    assert "isoline" in layer_line

    # Polygon contour conversion
    poly_fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[104.0, 30.6], [104.1, 30.6], [104.1, 30.7], [104.0, 30.7], [104.0, 30.6]]],
                },
                "properties": {"value": 100},
            }
        ],
    }
    payload_poly = {
        "type_hint": "isoline_contour",
        "geojson": poly_fc,
        "metadata": {
            "isoline": {"model": "isoline_contour", "levels": [100.0, 200.0, 300.0]},
        },
    }
    layer_poly, _, _ = convert_analysis_to_mapspec_layer(payload_poly)
    assert layer_poly["type"] == "fill"
    assert layer_poly["paint"]["opacity"] == 0.7


def test_semantic_checks_contour_levels_validation():
    # 1. Non-monotonic levels fail
    mapspec_bad = {
        "version": "1.0",
        "sources": {"s1": {"type": "geojson", "data": {"type": "FeatureCollection", "features": []}}},
        "layers": [
            {
                "id": "iso1",
                "type": "line",
                "source": "s1",
                "isoline": {"levels": [300.0, 100.0, 200.0]},
            }
        ],
    }
    report_bad = evaluate_cartography_semantics(mapspec_bad)
    checks_by_rule = {c.rule: c for c in report_bad.checks}
    assert "CONTOUR_LEVELS_VALID" in checks_by_rule
    assert checks_by_rule["CONTOUR_LEVELS_VALID"].status == "fail"

    # 2. Monotonic levels pass
    mapspec_good = {
        "version": "1.0",
        "sources": {"s1": {"type": "geojson", "data": {"type": "FeatureCollection", "features": []}}},
        "layers": [
            {
                "id": "iso2",
                "type": "line",
                "source": "s1",
                "isoline": {"levels": [100.0, 200.0, 300.0]},
            }
        ],
    }
    report_good = evaluate_cartography_semantics(mapspec_good)
    checks_by_rule_good = {c.rule: c for c in report_good.checks}
    assert "CONTOUR_LEVELS_VALID" in checks_by_rule_good
    assert checks_by_rule_good["CONTOUR_LEVELS_VALID"].status == "pass"


def test_isoline_nested_island_containment():
    """Verify nested island peak (depth 2) inside crater lake hole (depth 1) is not deleted (ADR-0095 & bug #762)."""
    import numpy as np
    from shapely.geometry import shape, Point
    from app.lib.cartography.isoline_model import generate_contour_features_from_grid, IsolineContourSpec

    # Create a grid with central peak, surrounding valley/lake, and outer annular ring
    # Peak at center (r=0, Z=150), valley at r=4 (Z~10), ring at r=8 (Z~120), edge (Z~0)
    grid_size = 50
    x = np.linspace(-12, 12, grid_size)
    y = np.linspace(-12, 12, grid_size)
    X, Y = np.meshgrid(x, y)
    R = np.sqrt(X**2 + Y**2)

    Z = 150.0 * np.exp(-(R**2) / 6.0) + 120.0 * np.exp(-((R - 7.5)**2) / 4.0)

    # Project coordinates centered at (104.0, 30.6)
    lon = 104.0 + X * 0.01
    lat = 30.6 + Y * 0.01

    spec = IsolineContourSpec(
        mode="filled_bands",
        levels=[30.0, 80.0, 180.0],
        unit="m",
    )

    fc = generate_contour_features_from_grid(lon, lat, Z, spec)
    assert fc["type"] == "FeatureCollection"
    assert len(fc["features"]) > 0

    # For level band [80, 180]:
    # It must contain BOTH the outer annular ring AND the central island peak
    high_band_features = [
        f for f in fc["features"]
        if f["properties"].get("min_level") == 80.0
    ]
    assert len(high_band_features) >= 1

    # Check center island peak: Point(104.0, 30.6) must be inside high band geometry
    center_pt = Point(104.0, 30.6)
    contains_center_island = any(shape(f["geometry"]).contains(center_pt) for f in high_band_features)
    assert contains_center_island, "Nested island peak at center was erroneously deleted by hole subtraction"

    # Also check that an annular ring point (e.g. at R=7.5 -> lon=104.075, lat=30.6) is also contained
    ring_pt = Point(104.0 + 7.5 * 0.01, 30.6)
    contains_ring = any(shape(f["geometry"]).contains(ring_pt) for f in high_band_features)
    assert contains_ring, "Outer annular ring of high band is missing"


def test_isoline_metadata_envelope_semantic_qa():
    """Verify isoline_model emits metadata.isoline.levels and enables CONTOUR_LEVELS_VALID semantic QA."""
    import numpy as np
    from app.lib.cartography.isoline_model import generate_contour_features_from_grid, IsolineContourSpec
    from app.services.analysis_cartography_converter import convert_analysis_to_mapspec_layer
    from app.lib.cartography.semantic_checks import evaluate_cartography_semantics

    grid_size = 20
    x = np.linspace(-5, 5, grid_size)
    y = np.linspace(-5, 5, grid_size)
    X, Y = np.meshgrid(x, y)
    Z = 100.0 * np.exp(-(X**2 + Y**2) / 8.0)
    lon = 104.0 + X * 0.01
    lat = 30.6 + Y * 0.01

    spec = IsolineContourSpec(
        mode="lines",
        levels=[20.0, 40.0, 60.0, 80.0],
        unit="m",
    )
    fc = generate_contour_features_from_grid(lon, lat, Z, spec)

    # 1. Verify metadata envelope
    assert "metadata" in fc
    assert "isoline" in fc["metadata"]
    assert fc["metadata"]["isoline"]["levels"] == [20.0, 40.0, 60.0, 80.0]

    # 2. Pass to converter
    analysis_payload = {
        "type_hint": "isoline_contour",
        "geojson": fc,
        "metadata": fc["metadata"],
    }
    layer, _, warnings = convert_analysis_to_mapspec_layer(analysis_payload)
    assert "isoline" in layer
    assert layer["isoline"]["levels"] == [20.0, 40.0, 60.0, 80.0]

    # 3. Evaluate semantic checks
    mapspec = {
        "version": "1.0",
        "sources": {layer["source"]: {"type": "geojson", "data": fc}},
        "layers": [layer],
    }
    report = evaluate_cartography_semantics(mapspec)
    checks = {c.rule: c for c in report.checks}
    assert "CONTOUR_LEVELS_VALID" in checks
    assert checks["CONTOUR_LEVELS_VALID"].status == "pass"

