"""Regression tests for audit round 4 GIS algorithm fixes.

#991  — isochrone facility→edge projection must use ONE STRtree built before
        the facility loop (the previous code brute-force scanned every edge
        per facility while its comment claimed the tree existed).
#1002 — algorithm edge batch: change-detection grid alignment by resolution/
        phase (not shape equality), Moran KNN weight symmetrization,
        nearest_neighbor target identity, DEM sentinel masking BEFORE
        downsampling.
#1003 — MVT antimeridian flag precomputed at index build; the per-tile hot
        path reads the flag instead of rescanning candidate coordinates.
"""
import asyncio
import json
import math

import numpy as np
import pytest
from shapely import STRtree
from shapely.geometry import LineString, Point, shape

from app.lib.geo_analysis.network import calculate_isochrones, nearest_neighbor_features
from app.services.rs.stac_client import DEM_SENTINEL_NODATA, _nan_block_mean


def _line_feature(x1, y1, x2, y2, fid="e1"):
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[x1, y1], [x2, y2]]},
        "properties": {"id": fid},
    }


def _point_feature(x, y, fid="p1", **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": {"id": fid, **props},
    }


def _fc(features):
    return {"type": "FeatureCollection", "features": features}


# ═══════════════════════════════════════════════════════════════════════════
# #991: STRtree isochrone projection
# ═══════════════════════════════════════════════════════════════════════════


def test_isochrone_strtree_projection_matches_brute_force_random():
    """Random small data: the STRtree nearest-edge lookup used by
    calculate_isochrones (#991) must pick the same edge and yield the same
    projected offset as a brute-force GEOS distance scan."""
    rng = np.random.default_rng(991)
    base_lon, base_lat = 116.0, 39.0
    edges = []
    for _ in range(40):
        x1 = base_lon + rng.uniform(0, 0.05)
        y1 = base_lat + rng.uniform(0, 0.05)
        x2 = x1 + rng.uniform(-0.01, 0.01)
        y2 = y1 + rng.uniform(-0.01, 0.01)
        edges.append(LineString([(x1, y1), (x2, y2)]))

    tree = STRtree(edges)  # same one-tree/nearest-index construction as #991
    for _ in range(100):
        pt = Point(base_lon + rng.uniform(0, 0.05), base_lat + rng.uniform(0, 0.05))
        idx = int(tree.nearest(pt))
        brute_idx = min(range(len(edges)), key=lambda j: edges[j].distance(pt))
        # Same edge selected (nearest distance ties are measure-zero on the
        # random data; a wrong index shows up as a larger distance)…
        assert edges[idx].distance(pt) == pytest.approx(
            edges[brute_idx].distance(pt), abs=1e-12
        )
        # …and identical projected offset along the host edge.
        assert edges[idx].project(pt) == pytest.approx(
            edges[brute_idx].project(pt), abs=1e-9
        )


def test_isochrone_projects_facility_onto_nearest_road_component():
    """End-to-end wiring: a facility ~55 m from road A and ~5.5 km from a
    disjoint road B must get its isochrone on A — an index/list misalignment
    in the STRtree edge lookup would buffer the wrong component."""
    net = _fc([
        _line_feature(0.10, 0.0, 0.10, 0.03, "near"),  # ~3.3 km vertical road
        _line_feature(0.15, 0.0, 0.15, 0.03, "far"),
    ])
    fac = _fc([_point_feature(0.1005, 0.015, "f1")])  # ~55 m east of "near"
    res = calculate_isochrones(net, fac, travel_time_min=10, mode="walking")
    assert res.success, res.summary
    props = res.data["features"][0]["properties"]
    assert props["reachable"] is True
    poly = shape(res.data["features"][0]["geometry"])
    minx, _miny, maxx, _maxy = poly.bounds
    assert maxx < 0.13, "isochrone must follow the NEAR road, not the far one"
    assert abs(minx - 0.10) < 0.01 and abs(maxx - 0.10) < 0.01


# ═══════════════════════════════════════════════════════════════════════════
# #1002①: change detection must align grids by res/phase, not shape equality
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


def _index_result(array, bounds):
    from app.services.rs.band_math import compute_raster_stats

    return {
        "status": "ok",
        "index_type": "NDVI",
        "stats": compute_raster_stats(array),
        "bbox": bounds,
        "raster_source": {"array": array, "bounds": bounds, "suggested_palette": "Viridis"},
    }


def _patch_engine(monkeypatch, t1, t2):
    from app.services.rs.spectral_engine import SpectralRasterEngine

    async def fake_cvi(self, bbox, date_from, date_to, index_type="ndvi"):
        return t1 if date_from.startswith("2025") else t2

    monkeypatch.setattr(SpectralRasterEngine, "compute_vegetation_index", fake_cvi)


def _run_change(monkeypatch, t1, t2, threshold):
    from app.services import spatial_tasks

    _patch_engine(monkeypatch, t1, t2)
    return spatial_tasks.run_change_detection(
        bbox=[0.0, 0.0, 1.0, 1.0],
        t1_from="2025-06-01", t1_to="2025-06-30",
        t2_from="2026-06-01", t2_to="2026-06-30",
        index_type="ndvi",
        change_threshold=threshold,
    )


def test_change_detection_same_shape_subpixel_phase_reprojects(monkeypatch):
    """#1002①: two 6-col grids whose COMMON windows are both 6×4 — equal
    shapes — but T2's grid is phase-shifted by 0.3 px. The old shape-equality
    check compared every pixel pair shifted by 0.05 in value units (uniform
    false change); grid-parameter alignment must route through the reproject
    so only ≤1 edge column retains the unavoidable sub-pixel residue."""
    res = 1.0 / 6.0
    west1, west2 = -0.5, 0.05 - res  # phase offset 0.05 deg = 0.3 px
    a1 = np.tile(west1 + (np.arange(6) + 0.5) * res, (6, 1))  # v(x) = x
    a2 = np.tile(west2 + (np.arange(6) + 0.5) * res, (6, 1))
    t1 = _index_result(a1, [west1, 0.0, west1 + 6 * res, 1.0])
    t2 = _index_result(a2, [west2, 0.0, west2 + 6 * res, 1.0])

    result = _run_change(monkeypatch, t1, t2, threshold=0.03)
    assert result["success"] is True
    change = result["data"]["change"]
    assert change["method"] == "pixel_common_footprint"
    cls = change["classification"]
    # Unfixed: all 24 window pixels differ by exactly +0.05 (slight change,
    # delta_mean=0.05). Fixed: interior is exact; at most the single edge
    # column keeps a 0.05 residue → delta_mean ≤ 6*0.05/24 = 0.0125.
    assert cls["class_counts"]["no_change"] >= 18
    assert 0.0 <= change["delta_mean"] < 0.02


def test_change_detection_same_shape_different_resolution_reprojects(monkeypatch):
    """#1002①: equal window shapes with different resolutions (0.26 vs 0.25
    deg/px) must also reproject. Values sample the linear field v(x)=10x at
    pixel centers, so a correct reprojection is exact except the last edge
    column; the unfixed per-pixel offset grows to −0.35."""
    a1 = np.tile(10.0 * (np.arange(5) + 0.5) * 0.26, (4, 1))
    a2 = np.tile(10.0 * (np.arange(5) + 0.5) * 0.25, (4, 1))
    t1 = _index_result(a1, [0.0, 0.0, 1.3, 1.0])
    t2 = _index_result(a2, [0.0, 0.0, 1.25, 1.0])

    result = _run_change(monkeypatch, t1, t2, threshold=0.05)
    assert result["success"] is True
    change = result["data"]["change"]
    assert change["method"] == "pixel_common_footprint"
    cls = change["classification"]
    # Unfixed: a2−a1 = −(0.1j+0.05) → no_change=0, delta_mean=−0.2.
    # Fixed: interior columns exact → ≥12/16 unchanged, delta_mean > −0.15.
    assert cls["class_counts"]["no_change"] >= 12
    assert change["delta_mean"] > -0.15


def test_change_detection_aligned_grids_stay_pixel_wise(monkeypatch):
    """Negative control: identical grids (same res, same phase) must NOT
    reproject — the per-pixel diff is exact and the counts are per-pixel."""
    base = np.full((6, 6), 0.3)
    a1 = base.copy()
    a2 = base.copy()
    a2[2:4, 2:4] = 0.9  # +0.6 patch
    t1 = _index_result(a1, [0.0, 0.0, 1.0, 1.0])
    t2 = _index_result(a2, [0.0, 0.0, 1.0, 1.0])

    result = _run_change(monkeypatch, t1, t2, threshold=0.1)
    cls = result["data"]["change"]["classification"]
    assert cls["class_counts"]["significant_improvement"] == 4
    assert cls["class_counts"]["no_change"] == 32
    assert result["data"]["change"]["delta_mean"] == pytest.approx(0.6 * 4 / 36)


def test_grids_pixel_aligned_unit():
    """Direct unit check of the alignment predicate: shape equality alone
    never satisfies it when res or phase differ; exact grids do."""
    from app.services.spatial_tasks import _grids_pixel_aligned

    a1 = np.zeros((6, 10))
    a2 = np.zeros((6, 5))
    b1 = [0.0, 0.0, 1.0, 1.0]
    b2 = [0.0, 0.0, 0.5, 1.0]
    w_full = (0, 0, 6, 10)
    # Same grid, same window → aligned.
    assert _grids_pixel_aligned(a1, b1, w_full, a1.copy(), b1, w_full) is True
    # Same res & phase over different extents (windows both start at 0.05→0.5):
    # left1 = 0.0+0.1*5 = 0.5, left2 = 0.5+0.1*0 = 0.5 → integer phase 0.
    assert _grids_pixel_aligned(a1, b1, (0, 0, 6, 5), a2, b2, (0, 0, 6, 5)) is True
    # Same shapes, different res (0.1 vs 0.2 over same window width) → False.
    a3 = np.zeros((3, 4))
    assert _grids_pixel_aligned(a3, [0, 0, 1, 1], (0, 0, 3, 4), a3.copy(), [0, 0, 2, 1], (0, 0, 3, 4)) is False
    # Same res, non-integer column phase (0.05 deg = 0.5 px at res 0.1) → False.
    assert _grids_pixel_aligned(
        a3, [0.0, 0, 1, 1], (0, 0, 3, 4), a3.copy(), [0.05, 0, 1.05, 1], (0, 0, 3, 4)
    ) is False


# ═══════════════════════════════════════════════════════════════════════════
# #1002②: Moran's I KNN weights must be symmetrized (union + row-standardize)
# ═══════════════════════════════════════════════════════════════════════════


def _knn_weights(coords, k):
    from scipy import sparse
    from scipy.spatial import cKDTree

    n = len(coords)
    tree = cKDTree(coords)
    _, idx = tree.query(coords, k=k + 1)
    mask = idx != np.arange(n)[:, None]
    cols = idx[mask]
    rows = np.repeat(np.arange(n), k)
    return sparse.coo_matrix((np.ones(len(rows)), (rows, cols)), shape=(n, n))


def _symmetrize(w):
    from scipy import sparse

    csr = w.tocsr()
    w = csr.maximum(csr.transpose())
    row_sums = np.asarray(w.sum(axis=1)).ravel()
    inv_row = np.zeros_like(row_sums)
    np.divide(1.0, row_sums, out=inv_row, where=row_sums > 0)
    return (sparse.diags(inv_row) @ w).tocoo()


def _moran_from_weights(values, w):
    n = len(values)
    z = values - values.mean()
    s0 = float(w.sum())
    numerator = float(np.sum(w.data * z[w.row] * z[w.col]))
    denominator = np.sum(z ** 2)
    return (n / s0) * (numerator / denominator) if denominator > 0 else 0.0


def test_moran_i_uses_symmetric_row_standardized_knn_weights():
    """#1002②: on 10 collinear points (k=8) the raw KNN weights are
    asymmetric — point 0 links to 8 but 8 does not link back to 0. The
    reported Moran's I must match the symmetrized (union + row-standardized)
    reference and differ from the asymmetric one."""
    from app.lib.geo_analysis.statistics import moran_i_narrated
    from app.lib.geo_processor.core import to_utm_gdf

    xs = [i * 0.001 for i in range(10)]
    values = np.array([1.0, 1.0, 2.0, 2.0, 3.0, 3.0, 2.0, 2.0, 1.0, 1.0])
    fc = _fc([
        _point_feature(x, 39.0, f"p{i}", val=float(v))
        for i, (x, v) in enumerate(zip(xs, values))
    ])

    gdf, _ = to_utm_gdf(fc)
    coords = np.column_stack((gdf.centroid.x.values, gdf.centroid.y.values))
    vals = gdf["val"].to_numpy(dtype=float)
    k = min(8, len(vals) - 1)
    w_asym = _knn_weights(coords, k)
    # Fixture is discriminating: the raw KNN graph really is asymmetric.
    assert (w_asym != w_asym.transpose()).nnz > 0

    res = moran_i_narrated(fc, "val")
    assert res.success, res.summary
    expected_sym = _moran_from_weights(vals, _symmetrize(w_asym))
    expected_asym = _moran_from_weights(vals, w_asym)
    assert expected_sym != pytest.approx(expected_asym, abs=1e-6)
    assert res.data["moran_i"] == pytest.approx(expected_sym, abs=1e-12)
    assert res.data["moran_i"] != pytest.approx(expected_asym, abs=1e-6)


# ═══════════════════════════════════════════════════════════════════════════
# #1002③: nearest_neighbor target identity
# ═══════════════════════════════════════════════════════════════════════════


def test_nearest_neighbor_uses_target_name_column_value():
    """Targets carrying a name column: nearest_target_id must be the NAME of
    the nearest target (not its DataFrame row number)."""
    src = _fc([_point_feature(0.0, 0.0, "s1")])
    tgt = _fc([
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.001, 0.0]},
         "properties": {"name": "T_near"}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.01, 0.0]},
         "properties": {"name": "T_far"}},
    ])
    res = nearest_neighbor_features(src, tgt)
    assert res.success, res.summary
    props = res.data["features"][0]["properties"]
    assert props["nearest_target_id"] == "T_near"
    assert "nearest_target_index" not in props
    assert props["distance_m"] == pytest.approx(111.3, abs=2.0)


def test_nearest_neighbor_id_column_preferred_and_json_safe():
    """id beats name in the priority order, and numpy integer ids must come
    back as plain ints (the result FeatureCollection goes through json.dumps)."""
    src = _fc([_point_feature(0.0, 0.0, "s1")])
    tgt = _fc([
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.001, 0.0]},
         "properties": {"id": 7, "name": "near"}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.01, 0.0]},
         "properties": {"id": 9, "name": "far"}},
    ])
    res = nearest_neighbor_features(src, tgt)
    assert res.success
    props = res.data["features"][0]["properties"]
    assert props["nearest_target_id"] == 7
    assert isinstance(props["nearest_target_id"], int)
    json.dumps(res.data["features"])  # must not raise on numpy scalars


def test_nearest_neighbor_without_identity_column_reports_row_index():
    """No id/name/fid column: the key must be renamed nearest_target_index
    (honest semantics) and the summary must say so."""
    src = _fc([_point_feature(0.0, 0.0, "s1")])
    tgt = _fc([
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.001, 0.0]},
         "properties": {"kind": "clinic"}},
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [0.01, 0.0]},
         "properties": {"kind": "hospital"}},
    ])
    res = nearest_neighbor_features(src, tgt)
    assert res.success
    props = res.data["features"][0]["properties"]
    assert props["nearest_target_index"] == 0
    assert "nearest_target_id" not in props
    assert "row index" in res.summary


# ═══════════════════════════════════════════════════════════════════════════
# #1002④: DEM sentinel masked BEFORE downsampling (undeclared-nodata path)
# ═══════════════════════════════════════════════════════════════════════════


def test_nan_block_mean_excludes_sentinels_and_handles_odd_shapes():
    """Unit: block means exclude NaN; an all-NaN block stays NaN; non-divisible
    shapes split into contiguous blocks so every source row is used."""
    src = np.array([
        [0.0, 4.0, 8.0, 12.0],
        [1.0, 5.0, 9.0, 13.0],
        [2.0, 6.0, 10.0, 14.0],
        [3.0, 7.0, 11.0, np.nan],
    ])
    out = _nan_block_mean(src, 2, 2)
    np.testing.assert_allclose(
        out, [[2.5, 10.5], [4.5, (10.0 + 14.0 + 11.0) / 3.0]], rtol=1e-12
    )
    # All-NaN block stays NaN.
    blk_nan = src.copy()
    blk_nan[2:4, 2:4] = np.nan
    assert np.isnan(_nan_block_mean(blk_nan, 2, 2)[1, 1])
    # Odd 11x9 shape: no data loss, no fabricated NaN.
    odd = np.full((11, 9), 2.0)
    odd[0, 0] = np.nan
    odd[10, 8] = np.nan
    out2 = _nan_block_mean(odd, 5, 3)
    assert out2.shape == (5, 3)
    assert not np.isnan(out2).any()
    np.testing.assert_allclose(out2, 2.0)


class _FakeAsset:
    def __init__(self, href):
        self.href = href


class _FakeItem:
    def __init__(self, item_id, bbox, hrefs):
        self.id = item_id
        self.bbox = list(bbox)
        self.assets = {k: _FakeAsset(v) for k, v in hrefs.items()}


class _FakeSearch:
    def __init__(self, items):
        self._items = items

    def items(self):
        return self._items


class _FakeCatalog:
    def __init__(self, items):
        self._items = items

    def search(self, **kwargs):
        return _FakeSearch(self._items)


def _write_dem(path, values):
    import rasterio
    from rasterio.transform import from_origin

    height, width = values.shape
    with rasterio.open(
        str(path), "w", driver="GTiff", height=height, width=width, count=1,
        dtype="float64", crs="EPSG:4326",
        transform=from_origin(10.0, 60.0, 0.001, 0.001),
        # NB: NO nodata declaration — the #1002 gap.
    ) as dst:
        dst.write(values, 1)


def _fetch(monkeypatch, item, bbox, **kw):
    from app.services.rs.stac_client import StacClientPrimitive, stac_primitive

    monkeypatch.setattr(StacClientPrimitive, "_get_catalog",
                        lambda self: _FakeCatalog([item]))
    return asyncio.run(stac_primitive.fetch_stac_items_and_bands(
        collection="cop-dem-glo-30", bbox=bbox,
        bands_needed={"dem": "data"}, ds_factor=2, **kw,
    ))


_BBOX = [10.0, 59.996, 10.004, 60.0]


def _dem_with_sentinel_corner():
    dem = np.array([
        [0.0, 4.0, 8.0, 12.0],
        [1.0, 5.0, 9.0, 13.0],
        [2.0, 6.0, 10.0, 14.0],
        [3.0, 7.0, 11.0, -9999.0],
    ])
    assert dem.min() <= DEM_SENTINEL_NODATA
    return dem


def test_dem_undeclared_nodata_masked_before_downsample(monkeypatch, tmp_path):
    """#1002④: undeclared-nodata DEM + mask_sentinel_nodata=True → the
    sentinel is NaN-masked BEFORE the average downsample, so a mixed 2×2 block
    yields the mean of its VALID pixels (not a bilinear-smeared middle value)."""
    dem = _dem_with_sentinel_corner()
    path = tmp_path / "dem_undeclared.tif"
    _write_dem(path, dem)
    item = _FakeItem("dem-item", _BBOX, {"data": str(path)})

    res = _fetch(monkeypatch, item, _BBOX, mask_sentinel_nodata=True)
    assert "error" not in res, res.get("error")
    out = res["bands"]["dem"]
    assert out.shape == (2, 2)
    np.testing.assert_allclose(
        out, [[2.5, 10.5], [4.5, (10.0 + 14.0 + 11.0) / 3.0]], rtol=1e-9
    )
    assert not np.any(np.isclose(out, 10.51020408, atol=1e-3))  # no smearing


def test_dem_default_flag_keeps_bilinear_for_non_dem_paths(monkeypatch, tmp_path):
    """The fix must ONLY affect callers that opt in: default flag keeps the
    legacy bilinear downsample (sentinel smeared) for non-DEM reads."""
    dem = _dem_with_sentinel_corner()
    path = tmp_path / "dem_default.tif"
    _write_dem(path, dem)
    item = _FakeItem("dem-item", _BBOX, {"data": str(path)})

    res = _fetch(monkeypatch, item, _BBOX)
    assert "error" not in res, res.get("error")
    out = res["bands"]["dem"]
    # Bilinear average of [10, 14; 11, -9999] ≈ -2491 — the smeared middle
    # value that used to escape the downstream <= -9999 mask.
    assert out[1, 1] < -1000.0


def test_compute_terrain_undeclared_nodata_no_slope_pollution(monkeypatch, tmp_path):
    """End-to-end: compute_terrain (which opts in via mask_sentinel_nodata=True)
    over an UNDECLARED-nodata DEM must produce 100% valid, sane slopes — no
    phantom near-vertical gradient from sentinel-averaged pseudo-elevation."""
    from app.services.rs.spectral_engine import SpectralRasterEngine

    dem = _dem_with_sentinel_corner()
    path = tmp_path / "dem_e2e.tif"
    _write_dem(path, dem)
    item = _FakeItem("dem-item", _BBOX, {"data": str(path)})
    from app.services.rs.stac_client import StacClientPrimitive

    monkeypatch.setattr(StacClientPrimitive, "_get_catalog",
                        lambda self: _FakeCatalog([item]))
    engine = SpectralRasterEngine()
    res = asyncio.run(engine.compute_terrain(_BBOX, products=["slope"]))
    assert res.is_error is False, res.error_msg
    assert res.stats["valid_pixel_pct"] == "100.0%"
    assert res.stats["max"] < 30.0
    assert res.stats["min"] >= 0.0


# ═══════════════════════════════════════════════════════════════════════════
# #1003: MVT antimeridian flag precomputed at index build
# ═══════════════════════════════════════════════════════════════════════════


def _mvt_feature(gtype, coords, props=None):
    return {
        "type": "Feature",
        "geometry": {"type": gtype, "coordinates": coords},
        "properties": props or {},
    }


def _tile_of(z, lon, lat):
    world = 256.0 * (1 << z)
    px = (lon + 180.0) / 360.0 * world
    lat_rad = math.radians(lat)
    py = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * world
    return (z, int(px // 256), int(py // 256))


def test_mvt_index_precomputes_crosses_am_flags():
    """The stored crosses_am list is parallel to features and equal to
    _crosses_antimeridian computed from the coordinates."""
    from app.services.mvt import _crosses_antimeridian, build_spatial_index_entry

    fc = _fc([
        _mvt_feature("Point", [116.40, 39.92], {"id": "pt"}),
        _mvt_feature("LineString", [[116.30, 39.85], [116.50, 39.95]], {"id": "ln"}),
        _mvt_feature("LineString", [[170.0, 10.0], [-170.0, 10.0]], {"id": "am_ln"}),
        _mvt_feature("Polygon", [
            [[175.0, -5.0], [-175.0, -5.0], [-175.0, 5.0], [175.0, 5.0], [175.0, -5.0]]
        ], {"id": "am_poly"}),
        _mvt_feature("Polygon", [
            [[116.30, 39.85], [116.50, 39.85], [116.50, 40.00], [116.30, 40.00],
             [116.30, 39.85]]
        ], {"id": "poly"}),
    ])
    entry = build_spatial_index_entry(("s", "r"), fc)
    assert len(entry.crosses_am) == len(entry.features) == 5
    for feat, flag in zip(entry.features, entry.crosses_am):
        g = feat["geometry"]
        assert flag == _crosses_antimeridian(g["type"], g["coordinates"])
    assert entry.crosses_am == [False, False, True, True, False]


def test_mvt_am_features_index_path_byte_identical():
    """Equivalence: with the precomputed flag, tiles containing AM-crossing
    features stay byte-identical to the coordinate path (encode_tile)."""
    from app.services.mvt import build_spatial_index_entry, encode_tile, encode_tile_from_index

    fc = _fc([
        _mvt_feature("LineString", [[170.0, 10.0], [-170.0, 10.0]], {"id": "am_ln"}),
        _mvt_feature("Polygon", [
            [[175.0, -5.0], [-175.0, -5.0], [-175.0, 5.0], [175.0, 5.0], [175.0, -5.0]]
        ], {"id": "am_poly"}),
        _mvt_feature("Point", [179.9, 0.0], {"id": "pt"}),
    ])
    entry = build_spatial_index_entry(("s", "am"), fc)
    for z in (3, 12, 15):
        # Anchor on the point feature's own tile so the tile provably
        # intersects a candidate at every zoom.
        tile = _tile_of(z, 179.9, 0.0)
        via_index = encode_tile_from_index(entry, *tile)
        via_coords = encode_tile(entry.query_tile(*tile), *tile)
        assert via_index == via_coords, f"z={z} diverged"
        assert via_index != b"", f"z={z} produced an empty tile"


def test_mvt_tile_encode_does_not_rescan_candidates_for_am(monkeypatch):
    """Hot path: encoding tiles over NON-crossing candidates must perform zero
    _crosses_antimeridian coordinate scans — the flag is read from the index."""
    import app.services.mvt as mvt_mod
    from app.services.mvt import build_spatial_index_entry, encode_tile_from_index

    calls = {"n": 0}
    orig = mvt_mod._crosses_antimeridian

    def counting(gtype, coords):
        calls["n"] += 1
        return orig(gtype, coords)

    monkeypatch.setattr(mvt_mod, "_crosses_antimeridian", counting)

    line = [[116.0 + 0.01 * j, 39.9 + 0.005 * j] for j in range(40)]
    fc = _fc([_mvt_feature("LineString", line, {"id": i}) for i in range(30)])
    entry = build_spatial_index_entry(("s", "scan"), fc)
    after_build = calls["n"]
    assert after_build == 30  # once per feature at build time

    for d in range(4):
        encode_tile_from_index(entry, *_tile_of(12, 116.1 + d * 0.02, 39.95))
    assert calls["n"] == after_build, "per-tile AM rescan is back"
