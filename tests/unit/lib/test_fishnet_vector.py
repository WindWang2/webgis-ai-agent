"""
Characterization tests for generate_fishnet (app/lib/geo_analysis/aggregation.py).

These tests pin the exact scalar behavior of generate_fishnet before and after
the NumPy vectorization. Every equivalence test compares the function output
against an independent, plainly-written scalar reference implementation (the
pre-vectorized algorithm, re-derived here from the documented semantics) to
guarantee identical cell count, identical cell order, and float-identical
coordinates (atol 1e-12).

The reference helpers deliberately use per-cell scalar loops (one cos/sin
call per vertex) rather than any vectorized construction, so they exercise a
different code path than the vectorized source.
"""
import numpy as np
import pytest
from shapely.geometry import box, Polygon

from app.lib.geo_analysis.aggregation import generate_fishnet
from app.lib.geo_processor.core import GeoAnalysisResult


# ---------------------------------------------------------------------------
# Independent scalar reference implementations
# ---------------------------------------------------------------------------

def _reference_square(bounds, cell_size):
    """Scalar reference for the square grid.

    One shapely box per cell, iterated x-outer / y-inner (matches the
    pre-vectorization cell order).
    """
    xmin, ymin, xmax, ymax = bounds
    polygons = []
    for x in list(np.arange(xmin, xmax, cell_size)):
        for y in list(np.arange(ymin, ymax, cell_size)):
            polygons.append(box(x, y, x + cell_size, y + cell_size))
    return polygons


def _reference_hexagon(bounds, cell_size):
    """Scalar reference for the hexagon grid.

    Per-cell 7-vertex rings computed with one cos/sin pair per vertex,
    iterated row-outer (y) / column-inner (x) with the j%2 row offset.
    """
    xmin, ymin, xmax, ymax = bounds
    R = cell_size / np.sqrt(3)
    dx = cell_size
    dy = 1.5 * R
    polygons = []
    for j, y in enumerate(np.arange(ymin - dy, ymax + dy, dy)):
        for x in np.arange(xmin - dx, xmax + dx, dx):
            x_offset = (j % 2) * (dx / 2)
            cx = x + x_offset
            vertices = []
            for deg in (0, 60, 120, 180, 240, 300, 0):
                a = np.radians(deg)
                vertices.append((cx + R * np.cos(a), y + R * np.sin(a)))
            polygons.append(Polygon(vertices))
    return polygons


def _reference_polygons(bounds, cell_size, type_):
    if type_ == "square":
        return _reference_square(bounds, cell_size)
    return _reference_hexagon(bounds, cell_size)


def _extract_cells(data):
    """Exterior-ring coordinate lists of every cell feature, in order."""
    return [
        feature["geometry"]["coordinates"][0]
        for feature in data["features"]
    ]


def _ring_arrays(cells):
    return np.asarray(cells, dtype=float)


def _assert_matches_reference(bounds, cell_size, type_):
    result = generate_fishnet(bounds, cell_size, type_)
    assert result.success is True, result.summary

    actual = _ring_arrays(_extract_cells(result.data))
    expected = _ring_arrays(
        [
            list(p.exterior.coords)
            for p in _reference_polygons(bounds, cell_size, type_)
        ]
    )

    # Identical cell count and per-cell vertex count (shape equality implies both).
    assert actual.shape == expected.shape, (
        f"cell count/vertex shape mismatch: got {actual.shape}, "
        f"expected {expected.shape}"
    )
    # Identical cell order + coordinates (elementwise, in order, strict tolerance).
    assert np.allclose(actual, expected, rtol=0, atol=1e-12), (
        f"cell coordinates differ from scalar reference "
        f"(max abs diff {np.max(np.abs(actual - expected)):.3e})"
    )


# ---------------------------------------------------------------------------
# Equivalence: square grid vs scalar reference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bounds,cell_size", [
    ((116.38, 39.89, 116.44, 39.95), 0.01),  # existing test's bounds (fp edge)
    ((0, 0, 1, 1), 0.25),                    # exact grid
    ((0, 0, 1, 1), 0.3),                     # cell_size does not divide extent
    ((-10, -10, 10, 10), 2.5),               # symmetric negative bounds
])
def test_square_matches_scalar_reference(bounds, cell_size):
    _assert_matches_reference(bounds, cell_size, "square")


# ---------------------------------------------------------------------------
# Equivalence: hexagon grid vs scalar reference
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bounds,cell_size", [
    ((116.38, 39.89, 116.44, 39.95), 0.01),      # existing test's bounds (fp edge)
    ((0, 0, 2, 2), 0.7),                          # fp edge: 0.7 does not divide
    ((-5, -5, 5, 5), 1.0),                        # exact grid
    ((500000, 4000000, 500010, 4000010), 10),     # large coordinates (UTM-like)
])
def test_hexagon_matches_scalar_reference(bounds, cell_size):
    _assert_matches_reference(bounds, cell_size, "hexagon")


# ---------------------------------------------------------------------------
# Explicit cell-order checks (independent of the equivalence comparison)
# ---------------------------------------------------------------------------

def test_square_cell_order_x_major():
    result = generate_fishnet((0, 0, 1, 1), 0.25, "square")
    cells = _extract_cells(result.data)
    assert len(cells) == 16
    # Every box ring starts at the column's minx: cells within a column share x,
    # y strictly increases; advancing to the next column moves x forward.
    assert cells[0][0][0] == cells[1][0][0] == cells[2][0][0] == cells[3][0][0]
    assert cells[0][0][1] < cells[1][0][1] < cells[2][0][1] < cells[3][0][1]
    assert cells[0][0][0] < cells[4][0][0]


def test_hexagon_cell_order_row_major():
    result = generate_fishnet((0, 0, 1, 1), 0.5, "hexagon")
    cells = _extract_cells(result.data)
    # dx == cell_size; 4 columns span the expanded x range, 5 rows span y.
    ncols = len(np.arange(0 - 0.5, 1 + 0.5, 0.5))
    assert ncols == 4
    assert len(cells) == ncols * 5
    # Hexagon rings start at the angle-0 vertex (cx + R, y): cells on the same
    # row share y and advance x; the first cell of the next row has a larger y.
    assert cells[0][0][1] == cells[1][0][1] == cells[2][0][1] == cells[3][0][1]
    assert cells[0][0][0] < cells[1][0][0] < cells[2][0][0] < cells[3][0][0]
    assert cells[0][0][1] < cells[ncols][0][1]


# ---------------------------------------------------------------------------
# Structure / bounds / type sanity
# ---------------------------------------------------------------------------

def test_square_bounds_type_and_geometry_sanity():
    result = generate_fishnet((116.38, 39.89, 116.44, 39.95), 0.01, "square")
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert result.data["type"] == "FeatureCollection"
    assert "square cells" in result.summary
    features = result.data["features"]
    assert len(features) == 49  # 7 cols x 7 rows (fp width 0.06000000000000227)
    for feature in features:
        assert feature["geometry"]["type"] == "Polygon"
        ring = feature["geometry"]["coordinates"][0]
        assert len(ring) == 5          # closed box ring
        assert ring[0] == ring[-1]     # ring closes on its first vertex


def test_hexagon_geometry_sanity():
    result = generate_fishnet((116.38, 39.89, 116.44, 39.95), 0.01, "hexagon")
    assert isinstance(result, GeoAnalysisResult)
    assert result.success is True
    assert "hexagon cells" in result.summary
    for feature in result.data["features"]:
        assert feature["geometry"]["type"] == "Polygon"
        ring = feature["geometry"]["coordinates"][0]
        assert len(ring) == 7          # hexagon ring (6 vertices + close)
        assert ring[0] == ring[-1]     # ring closes on its first vertex


def test_unsupported_type_returns_failure():
    result = generate_fishnet((0, 0, 1, 1), 0.5, "triangle")
    assert result.success is False
    assert "Unsupported type" in result.summary


# ---------------------------------------------------------------------------
# OOM cap behavior
# ---------------------------------------------------------------------------

def test_oom_cap_huge_cell_size_still_succeeds():
    # A cell_size far larger than the bounds must still produce a valid grid.
    result = generate_fishnet((0, 0, 10, 10), 1e9, "square")
    assert result.success is True
    assert len(result.data["features"]) == 1
    assert "Warning" not in result.summary


def test_oom_cap_dense_grid_adjusts_cell_size():
    # A grid denser than the 50k-cell estimate triggers the OOM cap: cell_size
    # is adjusted and the call still succeeds (the cap is an estimate, so the
    # final count may slightly exceed 50000 — see 50176 below).
    result = generate_fishnet((0, 0, 10, 10), 0.001, "square")
    assert result.success is True
    assert "Warning" in result.summary
    assert "adjusted" in result.summary
    assert 0 < len(result.data["features"]) <= 50176
