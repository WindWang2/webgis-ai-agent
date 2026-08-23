"""Pure-math tests for lib/geo_analysis/density.py (kde_surface + kde_contours).

Synthetic point fixtures - no network, no tool layer. Asserts the
GeoAnalysisResult return contract and algorithm correctness.

Extracted from app/tools/spatial_stats.py (architecture-review F2): these
algorithms previously lived in the tool adapter layer with zero behavioral
coverage. Now testable as pure functions.
"""

from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_analysis.density import kde_surface, kde_contours


def _synthetic_points(n: int = 20) -> dict:
    """N synthetic points clustered around two centers (Beijing + Shanghai)."""
    import random
    random.seed(42)
    features = []
    # cluster 1: around Beijing (116.4, 39.9)
    for i in range(n // 2):
        lng = 116.4 + random.uniform(-0.02, 0.02)
        lat = 39.9 + random.uniform(-0.02, 0.02)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"id": i, "weight": random.uniform(1, 10)},
        })
    # cluster 2: around Shanghai (121.5, 31.2)
    for i in range(n // 2, n):
        lng = 121.5 + random.uniform(-0.02, 0.02)
        lat = 31.2 + random.uniform(-0.02, 0.02)
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {"id": i, "weight": random.uniform(1, 10)},
        })
    return {"type": "FeatureCollection", "features": features}


# ── kde_surface ─────────────────────────────────────────────────────────────


def test_kde_surface_returns_geoanalysisresult():
    res = kde_surface(_synthetic_points(20))
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_kde_surface_has_grid_size_and_bandwidth():
    res = kde_surface(_synthetic_points(20))
    assert res.success
    fc = res.data
    assert "grid_size" in fc
    assert len(fc["grid_size"]) == 2
    assert "bandwidth_m" in fc
    assert fc["bandwidth_m"] > 0


def test_kde_surface_features_have_density():
    res = kde_surface(_synthetic_points(20), cell_size=2000)
    assert res.success
    fc = res.data
    assert fc["type"] == "FeatureCollection"
    # thresholded - only non-trivial cells survive
    assert fc["count"] >= 1
    for feat in fc["features"]:
        assert "density" in feat["properties"]
        assert feat["properties"]["density"] >= 0  # survived threshold; may round to 0.0


def test_kde_surface_too_few_points():
    res = kde_surface(_synthetic_points(2))
    assert not res.success
    assert res.error_type == "ValueError"


def test_kde_surface_weighted_field():
    res = kde_surface(_synthetic_points(20), value_field="weight", cell_size=2000)
    assert res.success
    assert res.data["count"] >= 1


def test_kde_surface_weighted_bad_field():
    res = kde_surface(_synthetic_points(20), value_field="nonexistent")
    assert not res.success
    assert res.error_type == "TypeError"


def test_kde_surface_explicit_bandwidth():
    res = kde_surface(_synthetic_points(20), bandwidth=5000, cell_size=2000)
    assert res.success
    assert res.data["bandwidth_m"] == 5000


def test_kde_surface_custom_bounds():
    res = kde_surface(
        _synthetic_points(20), cell_size=2000,
        bounds=[116.0, 39.0, 122.0, 40.0],
    )
    assert res.success


# ── kde_contours ────────────────────────────────────────────────────────────


def test_kde_contours_returns_geoanalysisresult():
    res = kde_contours(_synthetic_points(20))
    assert isinstance(res, GeoAnalysisResult)
    assert res.success


def test_kde_contours_has_levels_count():
    res = kde_contours(_synthetic_points(20), levels=6)
    assert res.success
    fc = res.data
    assert "levels_count" in fc
    # matplotlib may adjust the requested level count internally
    assert fc["levels_count"] >= 6


def test_kde_contours_features_have_density_value():
    res = kde_contours(_synthetic_points(20))
    assert res.success
    fc = res.data
    if fc["count"] > 0:
        for feat in fc["features"]:
            assert "density_value" in feat["properties"]
            assert "level" in feat["properties"]


def test_kde_contours_legend_spec_when_features_exist():
    """legend_spec attaches as a top-level key on the FC for the cartography converters."""
    res = kde_contours(_synthetic_points(20))
    assert res.success
    fc = res.data
    if fc["count"] > 0:
        assert "legend_spec" in fc
        ls = fc["legend_spec"]
        assert ls["type"] == "continuous"
        assert "min" in ls and "max" in ls
        assert "palette" in ls


def test_kde_contours_too_few_points():
    res = kde_contours(_synthetic_points(4))
    assert not res.success
    assert res.error_type == "ValueError"


def test_kde_surface_drops_cells_below_threshold():
    """Threshold drop is exact: every surviving cell has density >= 10% of max.

    Pins the vectorized grid loop (np.nonzero on density >= threshold) to the
    original per-cell branch semantics (skip d_val < threshold).
    """
    res = kde_surface(_synthetic_points(20), cell_size=2000)
    assert res.success
    fc = res.data
    max_d = fc["stats"]["max_density"]
    threshold = max_d * 0.1
    assert len(fc["features"]) == fc["count"]
    for feat in fc["features"]:
        assert feat["properties"]["density"] >= threshold - 1e-6


def test_kde_contours_ring_distribution_keeps_holes():
    """#707: a ring-shaped distribution (high-density band around a low-density
    center) must NOT paint the enclosed center at the band's level — contourf
    hole boundaries are rebuilt with even-odd containment.

    #762 update: the even-odd rebuild is now parent-scoped, so per-level
    regions are exact density bands [level_i, level_i+1) and they TILE the
    KDE support — the union of all features legitimately reaches ~100% of the
    hull (the low-density center belongs to the lowest band). The old
    `< 0.90` union-coverage criterion pinned the #762 bug (band parts inside
    and adjacent to the hole silently dropped); the real #707 invariant is
    that the center is painted ONLY at the lowest band's level, never at any
    higher band's.
    """
    import numpy as np
    from shapely.geometry import shape, Point
    from shapely.ops import unary_union

    rng = np.random.default_rng(707)
    # ~360 points on a ring of radius ~1.5 km around a park (empty center)
    angles = np.linspace(0, 2 * np.pi, 360, endpoint=False)
    r = 1500.0 + rng.normal(0, 120.0, size=angles.size)
    lons = 116.0 + (r * np.cos(angles)) / (111_320.0 * np.cos(np.radians(39.0)))
    lats = 39.0 + (r * np.sin(angles)) / 110_540.0
    points = {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [float(lo), float(la)]}}
            for lo, la in zip(lons, lats)
        ],
    }

    res = kde_contours(points)
    assert res.success
    fc = res.data
    assert fc["count"] > 0

    center = Point(float(np.mean(lons)), float(np.mean(lats)))
    band_values = sorted({f["properties"]["density_value"] for f in fc["features"]})
    assert len(band_values) >= 2, "fixture degraded: need at least two bands"
    lowest = band_values[0]
    for v in band_values[1:]:
        higher_band = unary_union(
            [shape(f["geometry"]) for f in fc["features"]
             if f["properties"]["density_value"] == v]
        )
        assert not higher_band.contains(center), (
            f"#707 regression: enclosed low-density center painted at band level {v:.2e}"
        )
    lowest_band = unary_union(
        [shape(f["geometry"]) for f in fc["features"]
         if f["properties"]["density_value"] == lowest]
    )
    # the center's near-zero density belongs to the lowest band only
    assert lowest_band.contains(center)


def test_kde_contours_ring_with_enclosed_core_keeps_island_bands_762():
    """#762: a POI ring around an empty moat PLUS a denser enclosed core
    cluster must keep the core's band polygons — the #707 even-odd rebuild
    subtracted every hole from ALL outers, erasing even-depth islands that
    lie geometrically inside a hole (the whole enclosed core).

    Ground truth is band-parity: contourf's ``allsegs[i]`` carries the
    boundary curves of band ``[levels[i], levels[i+1]]`` (it mixes the level-i
    and level-i+1 curves — verified on a controlled Gaussian), so the emitted
    level-``density_value`` region is that band. A probe with KDE density d
    must be contained in exactly the band whose interval covers d. With the
    bug, every band region lost its island part inside the moat hole.
    """
    import numpy as np
    import geopandas as gpd
    from shapely.geometry import shape, Point
    from shapely.ops import unary_union

    from app.lib.geo_processor.core import to_utm_gdf
    from app.lib.geo_analysis._vector import extract_centroids
    from app.lib.geo_analysis.density import _fit_kde, _cap_kde_points, _evaluate_kde

    rng = np.random.default_rng(762)
    n_ring, n_core = 1400, 300
    # ring of POIs at R=1800 m (sigma 80) with a wide empty moat, plus a
    # denser core cluster at r=150 m (sigma 40) enclosed by the moat.
    a = rng.uniform(0, 2 * np.pi, n_ring)
    r = 1800.0 + rng.normal(0, 80.0, n_ring)
    ca = rng.uniform(0, 2 * np.pi, n_core)
    cr = np.abs(rng.normal(0, 40.0, n_core))
    px = np.concatenate([r * np.cos(a), cr * np.cos(ca)])
    py = np.concatenate([r * np.sin(a), cr * np.sin(ca)])
    points = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [116.0 + x / 111_320.0, y / 110_574.0]},
            }
            for x, y in zip(px, py)
        ],
    }

    bandwidth = 150
    res = kde_contours(points, levels=12, bandwidth=bandwidth)
    assert res.success
    features = res.data["features"]
    assert features, "no contour features emitted"

    # Band regions from the emitted features, in UTM (metres) for containment.
    gdf, utm_crs = to_utm_gdf(points)
    coords = extract_centroids(gdf)
    kde, _bw, _clamped = _fit_kde(_cap_kde_points(coords.T)[0], bandwidth)
    band_values = sorted({f["properties"]["density_value"] for f in features})
    band_regions = {}
    for f in features:
        v = f["properties"]["density_value"]
        band_regions.setdefault(v, []).append(shape(f["geometry"]))
    band_regions = {
        v: gpd.GeoSeries([unary_union(g)], crs="EPSG:4326").to_crs(utm_crs).iloc[0]
        for v, g in band_regions.items()
    }

    def density_at(x_m, y_m):
        return float(_evaluate_kde(kde, np.array([[x_m], [y_m]]))[0])

    def band_of(d):
        """Return (value, region) of the band covering density d, or None when
        d is not comfortably inside any band (polygonization fuzz at edges)."""
        for i, v in enumerate(band_values):
            nxt = band_values[i + 1] if i + 1 < len(band_values) else float("inf")
            width = nxt - v if np.isfinite(nxt) else max(band_values[-1] - band_values[-2], 1e-30)
            if d - v > 0.25 * width and (nxt - d) > 0.25 * width:
                return v, band_regions[v]
        return None

    core_center = coords[n_ring:].mean(axis=0)
    # Walk a radial transect from the enclosed core center out past the core
    # disc edge into the moat. Every probe whose KDE density sits comfortably
    # inside a band MUST be contained in that band's emitted region — with the
    # #762 bug the low bands (whose allsegs mix ring and core curves) lose
    # their core parts, so probes at r≈300-360 m (density in the two lowest
    # non-zero bands) fall out of every region.
    checked = 0
    for radius in range(0, 460, 5):
        x_m = core_center[0] + radius
        d = density_at(x_m, core_center[1])
        hit = band_of(d)
        if hit is None:
            continue
        v, region = hit
        assert region.contains(Point(x_m, core_center[1])), (
            f"#762 regression: transect r={radius} m (KDE density {d:.3e}) belongs "
            f"to band starting at {v:.2e} but the emitted level region excludes it"
        )
        checked += 1
    assert checked >= 8, f"fixture degraded: only {checked} comfortable probes on the transect"
    # 3. a moat point stays excluded from every band above the lowest one
    import math
    ring_center = coords[:n_ring].mean(axis=0)
    moat_x = ring_center[0] + 900.0 * math.cos(0.3)
    moat_y = ring_center[1] + 900.0 * math.sin(0.3)
    d_moat = density_at(moat_x, moat_y)
    assert d_moat < band_values[1], "fixture degraded: moat is not low-density"
    for v, region in band_regions.items():
        if v > d_moat:
            assert not region.contains(Point(moat_x, moat_y)), (
                f"#762: moat point (density {d_moat:.3e}) painted into band {v:.2e}"
            )
