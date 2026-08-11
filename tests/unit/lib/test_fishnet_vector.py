"""Correctness tests for generate_fishnet (V-F01 metric-grid fix).

The fishnet_grid tool documents ``bounds`` as WGS84 degrees and ``cell_size``
as metres. Previously the metre value was applied directly in degree space
(``np.arange(xmin, xmax, cell_size)`` in degrees), so a 500 m request over
Beijing returned a single ~500-degree-wide polygon. The grid is now built in
a projected metric CRS and reprojected to EPSG:4326, so cells are metre-sized
on the ground.

These tests assert the metric contract: cell areas ≈ cell_size² in metres,
sensible cell counts, OOM protection in metres, and geometry sanity.
"""
import geopandas as gpd
from shapely.geometry import shape

from app.lib.geo_analysis.aggregation import generate_fishnet
from app.lib.geo_processor.core import GeoAnalysisResult

# Beijing ~0.4°×0.4° (~37 km × 44 km).
BEIJING = (116.38, 39.90, 116.42, 39.94)


def _cell_areas_m2(data):
    """Reproject cell polygons to UTM and return their ground areas in m²."""
    polys = [shape(f["geometry"]) for f in data["features"]]
    if not polys:
        return []
    gdf = gpd.GeoDataFrame(geometry=polys, crs="EPSG:4326")
    utm = str(gdf.estimate_utm_crs())
    return (gdf.to_crs(utm).area).tolist()


# --------------------------------------------------------------------------- #
# Metric correctness (the headline fix, V-F01)
# --------------------------------------------------------------------------- #
def test_square_cells_are_meter_sized():
    res = generate_fishnet(BEIJING, 500.0, "square")
    assert res.success
    areas = _cell_areas_m2(res.data)
    assert len(areas) > 1
    # Every cell is ~500 m × 500 m = 250 000 m² on the ground (allow UTM
    # distortion + reprojection rounding). The old bug produced one cell
    # spanning ~500 degrees.
    assert all(200_000 < a < 300_000 for a in areas), (
        f"cells not ~500m square: min={min(areas):.0f} max={max(areas):.0f}"
    )


def test_hexagon_cells_are_meter_sized():
    res = generate_fishnet(BEIJING, 500.0, "hexagon")
    assert res.success
    areas = _cell_areas_m2(res.data)
    assert len(areas) > 1
    # Hex of "size" 500 m: regular hexagon area with the implementation's
    # R = cell_size/sqrt(3) -> area = 2*R^2 * ... ~ cell_size² magnitude.
    # Assert all cells are within a sensible metre range (not degrees).
    assert all(100_000 < a < 350_000 for a in areas), (
        f"hex cells not metre-sized: min={min(areas):.0f} max={max(areas):.0f}"
    )


def test_smaller_cell_size_yields_more_cells():
    coarse = generate_fishnet(BEIJING, 1000.0, "square")
    fine = generate_fishnet(BEIJING, 500.0, "square")
    assert coarse.success and fine.success
    assert len(fine.data["features"]) > len(coarse.data["features"])


def test_cell_size_le_zero_rejected():
    res = generate_fishnet(BEIJING, 0.0, "square")
    assert not res.success
    assert res.error_type == "ValueError"
    res2 = generate_fishnet(BEIJING, -5.0, "square")
    assert not res2.success


# --------------------------------------------------------------------------- #
# Structure / bounds / type sanity
# --------------------------------------------------------------------------- #
def test_square_geometry_sanity():
    res = generate_fishnet(BEIJING, 500.0, "square")
    assert isinstance(res, GeoAnalysisResult)
    assert res.data["type"] == "FeatureCollection"
    assert "square cells" in res.summary
    for feature in res.data["features"]:
        assert feature["geometry"]["type"] == "Polygon"
        ring = feature["geometry"]["coordinates"][0]
        assert len(ring) == 5
        assert ring[0] == ring[-1]


def test_hexagon_geometry_sanity():
    res = generate_fishnet(BEIJING, 500.0, "hexagon")
    assert "hexagon cells" in res.summary
    for feature in res.data["features"]:
        assert feature["geometry"]["type"] == "Polygon"
        ring = feature["geometry"]["coordinates"][0]
        assert len(ring) == 7


def test_unsupported_type_returns_failure():
    res = generate_fishnet(BEIJING, 500.0, "triangle")
    assert res.success is False
    assert "Unsupported type" in res.summary


# --------------------------------------------------------------------------- #
# OOM cap (now in metres)
# --------------------------------------------------------------------------- #
def test_oom_cap_huge_cell_size_succeeds():
    res = generate_fishnet(BEIJING, 1e9, "square")
    assert res.success
    assert len(res.data["features"]) == 1
    assert "Warning" not in res.summary


def test_oom_cap_dense_grid_adjusts_cell_size():
    # 0.5 m cells over ~37 km × 44 km -> ~6.5 billion cells -> OOM cap fires.
    res = generate_fishnet((116.0, 39.0, 116.5, 39.5), 0.5, "square")
    assert res.success
    assert "Warning" in res.summary
    assert "adjusted" in res.summary


# --------------------------------------------------------------------------- #
# The regression that exposed V-F01: the old code returned ONE cell spanning
# hundreds of degrees for a metre cell_size over a degree extent.
# --------------------------------------------------------------------------- #
def test_no_planet_spanning_cell():
    res = generate_fishnet((116.0, 39.0, 116.8, 40.2), 500.0, "square")
    assert res.success
    assert len(res.data["features"]) > 100  # not a single collapsed cell
    for f in res.data["features"]:
        ring = f["geometry"]["coordinates"][0]
        lon_span = max(x for x, y in ring) - min(x for x, y in ring)
        # 500 m at lat ~40 is ~0.0067°; nowhere near a 500° span.
        assert lon_span < 0.1, lon_span
