"""Geometry/Vector science pack VNext tests (ADR-0099 geometry domain).

Property + fixture tests for the geometry science semantics:

- buffer monotonicity (seeded, 10 random shapes, r ∈ {100, 500, 1000})
- geographic-input buffer area accuracy incl. 60°N (≤1% of πr²)
- convex hull exact square fixture + collinear/degenerate documented behavior
- Voronoi: 3-point triangle → 3 cells; duplicate points honest behavior
- multi_ring_buffer: bands disjoint, union covers the disk, widths per spec
- overlay: exact intersection area / union ≥ max / disjoint → honest empty
- degenerate empty inputs: no silent crash anywhere
- CRS disclosure: buffer_analysis tool evidence reports the real auto-UTM
  transformation (transformations_applied mentions the UTM EPSG).

Implementation under test: app/lib/geo_processor/{geometry,overlay}.py,
app/lib/geo_analysis/geometry_ops.py, app/tools/spatial.py.
"""
import asyncio
import math
import random

import geopandas as gpd
import pytest
from shapely.geometry import shape

from app.lib.geo_analysis.geometry_ops import convex_hull, multi_ring_buffer, voronoi_polygons
from app.lib.geo_processor.core import GeoAnalysisResult
from app.lib.geo_processor.geometry import buffer_smart
from app.lib.geo_processor.overlay import overlay_smart

pytestmark = pytest.mark.unit


# ── fixtures ──────────────────────────────────────────────────────────


def _pt(x, y, props=None):
    return {"type": "Feature", "geometry": {"type": "Point", "coordinates": [x, y]},
            "properties": props or {}}


def _fc(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _random_blob_fc(seed: int) -> dict:
    """A seeded random star-shaped polygon near Beijing (degrees)."""
    rng = random.Random(seed)
    cx = 116.3 + rng.uniform(0, 0.2)
    cy = 39.8 + rng.uniform(0, 0.2)
    pts = []
    for i in range(9):
        ang = 2 * math.pi * i / 9 + rng.uniform(-0.25, 0.25)
        r_deg = rng.uniform(0.002, 0.006)
        pts.append([cx + r_deg * math.cos(ang), cy + r_deg * math.sin(ang)])
    ring = pts + [pts[0]]
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {},
    }]}


def _metric_area_m2(geometry, working_crs: str) -> float:
    """Area of a WGS84 output geometry measured in the analysis working CRS."""
    gdf = gpd.GeoDataFrame(geometry=[geometry], crs="EPSG:4326")
    return float(gdf.to_crs(working_crs).area.iloc[0])


# ── 1. buffer monotonicity (property, seeded) ─────────────────────────


def test_buffer_monotonicity_seeded():
    """area(r=500) ≥ area(r=100) and area(r=1000) ≥ area(r=500) for 10 shapes."""
    for seed in range(10):
        fc = _random_blob_fc(seed)
        prev = -1.0
        for radius in (100, 500, 1000):
            res = buffer_smart(fc, distance=radius, unit="m")
            assert res.success, (seed, radius, res.summary)
            geom = shape(res.data["features"][0]["geometry"])
            area = _metric_area_m2(geom, res.evidence["working_crs"])
            assert area >= prev, (seed, radius, area, prev)
            prev = area


# ── 2. geographic-input buffer area accuracy ──────────────────────────


@pytest.mark.parametrize("lat", [0.0, 30.0, 45.0, 60.0])
def test_buffer_geographic_area_accuracy_60n(lat):
    """A 1000 m buffer of a geographic point yields ≈ πr² (≤1% incl. UTM
    scale error <0.1% and arc discretization), at low lat AND 60°N."""
    fc = _fc(_pt(20.0, lat))
    res = buffer_smart(fc, distance=1000, unit="m")
    assert res.success, res.summary
    geom = shape(res.data["features"][0]["geometry"])
    area = _metric_area_m2(geom, res.evidence["working_crs"])
    expected = math.pi * 1000.0 ** 2
    assert area == pytest.approx(expected, rel=0.01)


# ── 3. convex hull fixtures ───────────────────────────────────────────


def test_convex_hull_square_fixture_exact():
    """Square corners + interior point → hull IS the square (interior point
    adds no area) and covers every input point."""
    corners = [(116.30, 39.80), (116.40, 39.80), (116.40, 39.90), (116.30, 39.90)]
    feats = [_pt(x, y, {"id": i}) for i, (x, y) in enumerate(corners)]
    feats.append(_pt(116.35, 39.85, {"id": 4}))  # interior point
    res = convex_hull(_fc(*feats))
    assert res.success, res.summary
    hull = shape(res.data["features"][0]["geometry"])
    assert res.data["features"][0]["properties"]["feature_count"] == 5

    # Hull area (UTM) equals the UTM area of the degree square: the interior
    # point must not inflate it.
    working = "EPSG:32650"
    square = gpd.GeoDataFrame(
        geometry=[shape({"type": "Polygon", "coordinates": [
            list(corners) + [corners[0]]]})], crs="EPSG:4326").to_crs(working)
    hull_m2 = _metric_area_m2(hull, working)
    assert hull_m2 == pytest.approx(float(square.area.iloc[0]), rel=1e-4)

    for feat in feats:
        pt = shape(feat["geometry"])
        assert hull.distance(pt) < 1e-9  # covers (boundary drift < 1 mm)


def test_convex_hull_degenerate_honest_failure():
    """Degenerate inputs never crash: exactly-coincident points → typed
    honest failure (hull collapses to a Point); degree-collinear points →
    documented behavior: the UTM projection breaks exact collinearity, so a
    thin sliver polygon is produced (≤1% of a comparable full triangle)."""
    # 3 coincident points: hull is a Point → typed ValueError, no crash.
    res = convex_hull(_fc(_pt(116.4, 39.9), _pt(116.4, 39.9), _pt(116.4, 39.9)))
    assert isinstance(res, GeoAnalysisResult)
    assert res.success is False
    assert res.error_type == "ValueError"
    assert "非共线" in res.summary

    # Collinear in degrees: documented sliver-polygon behavior (no crash).
    res2 = convex_hull(_fc(_pt(116.3, 39.9), _pt(116.4, 39.95), _pt(116.5, 40.0)))
    assert isinstance(res2, GeoAnalysisResult)
    assert res2.success is True
    hull_m2 = _metric_area_m2(shape(res2.data["features"][0]["geometry"]), "EPSG:32650")
    # A full triangle spanning the same extent is ~1.9e8 m² (probed); the
    # sliver must be far below 1% of it.
    assert 0 < hull_m2 < 0.01 * 1.9e8


# ── 4. voronoi ────────────────────────────────────────────────────────


def test_voronoi_triangle_three_cells():
    """3 non-collinear points → 3 finite cells covering every input point."""
    res = voronoi_polygons(_fc(_pt(116.3, 39.9), _pt(116.5, 39.9), _pt(116.4, 40.05)))
    assert res.success, res.summary
    assert res.data["count"] == 3
    cells = [shape(f["geometry"]) for f in res.data["features"]]
    assert len(cells) == 3
    pts = [shape(f["geometry"]) for f in
           _fc(_pt(116.3, 39.9), _pt(116.5, 39.9), _pt(116.4, 40.05))["features"]]
    for pt in pts:
        assert any(c.covers(pt) for c in cells)


def test_voronoi_duplicates_honest_behavior():
    """Duplicate points are handled honestly and deterministically:

    - a mixed set with duplicated points succeeds; each duplicate receives
      its own (identical, by mirror symmetry) valid cell — no silent dedup,
      no crash;
    - a fully coincident set degenerates → typed Qhull failure (honest
      error, never a fabricated tessellation)."""
    mixed = _fc(
        _pt(116.3, 39.9, {"id": 1}),
        _pt(116.3, 39.9, {"id": 2}),   # exact duplicate of id 1
        _pt(116.5, 39.95, {"id": 3}),
        _pt(116.4, 39.8, {"id": 4}),
    )
    res = voronoi_polygons(mixed)
    assert res.success, res.summary
    by_id = {f["properties"]["id"]: f for f in res.data["features"]}
    assert 1 in by_id and 2 in by_id  # no silent dedup
    a1 = by_id[1]["properties"]["area_km2"]
    a2 = by_id[2]["properties"]["area_km2"]
    assert a1 == pytest.approx(a2, rel=1e-6)  # duplicates share one region

    coincident = voronoi_polygons(_fc(_pt(116.4, 39.9), _pt(116.4, 39.9), _pt(116.4, 39.9)))
    assert isinstance(coincident, GeoAnalysisResult)
    assert coincident.success is False
    # scipy surfaces QhullError (caught in the library) — the summary is the
    # honest failure channel, error_type the real exception class name.
    assert "Voronoi 计算失败" in coincident.summary
    assert coincident.error_type in ("ValueError", "QhullError", "RuntimeError")


# ── 5. multi-ring buffer geometry ─────────────────────────────────────


def test_multi_ring_bands_disjoint_cover_widths():
    """For a single point and distances [500, 1000, 1500] (merge_rings=True):
    ring bands are pairwise disjoint, their union covers the full 1500 m
    disk, and each band's outer radius matches its distance (widths per
    spec). Annulus areas match π(d_i² − d_{i-1}²)."""
    center_fc = _fc(_pt(116.4, 39.9))
    res = multi_ring_buffer(center_fc, distances=[500, 1000, 1500])
    assert res.success, res.summary
    bands = [shape(f["geometry"]) for f in res.data["features"]]
    assert len(bands) == 3

    working = "EPSG:32650"  # Beijing → UTM 50N (the auto-selected zone)
    bands_m = gpd.GeoSeries(bands, crs="EPSG:4326").to_crs(working)
    center_m = gpd.GeoSeries([shape(_pt(116.4, 39.9)["geometry"])],
                             crs="EPSG:4326").to_crs(working).iloc[0]

    # pairwise disjoint: intersection area ~0 (reprojection drift < 10 m²)
    for i in range(3):
        for j in range(i + 1, 3):
            inter = bands_m.iloc[i].intersection(bands_m.iloc[j]).area
            assert inter < 10.0, (i, j, inter)

    # annulus areas per spec (1% discretization tolerance)
    expected = [math.pi * 500.0 ** 2,
                math.pi * (1000.0 ** 2 - 500.0 ** 2),
                math.pi * (1500.0 ** 2 - 1000.0 ** 2)]
    for band, exp in zip(bands_m, expected):
        assert band.area == pytest.approx(exp, rel=0.01)

    # union covers the 1500 m disk (1% tolerance)
    union_area = gpd.GeoSeries([bands_m.union_all()], crs=working).area.iloc[0]
    assert union_area == pytest.approx(math.pi * 1500.0 ** 2, rel=0.01)

    # outer radius (= width from center) per spec
    for band, dist in zip(bands_m, (500, 1000, 1500)):
        radius = band.hausdorff_distance(center_m)
        assert radius == pytest.approx(dist, rel=0.01)


# ── 6. overlay ────────────────────────────────────────────────────────


def _square(minx, miny, maxx, maxy):
    return {"type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [
                [[minx, miny], [maxx, miny], [maxx, maxy], [minx, maxy], [minx, miny]]]},
            "properties": {}}


def test_overlay_intersection_area_exact():
    """Two overlapping squares: intersection area exactly 0.5 deg² (overlay
    is pure GEOS topology in the WGS84 frame) and union ≥ max(areaA, areaB)."""
    a = _square(0.0, 0.0, 1.0, 1.0)   # area 1.0
    b = _square(0.5, 0.0, 1.5, 1.0)   # area 1.0, overlap [0.5,1]×[0,1] = 0.5
    res = overlay_smart(_fc(a), _fc(b), how="intersection")
    assert res.success, res.summary
    feats = res.data["features"]
    assert len(feats) == 1
    inter = shape(feats[0]["geometry"])
    assert inter.area == pytest.approx(0.5, rel=1e-12)

    res_u = overlay_smart(_fc(a), _fc(b), how="union")
    assert res_u.success
    union_area = sum(shape(f["geometry"]).area for f in res_u.data["features"])
    assert union_area >= max(1.0, 1.0)
    assert union_area == pytest.approx(1.5, rel=1e-9)


def test_overlay_disjoint_honest_empty():
    """Disjoint squares: intersection succeeds with an HONEST empty result
    (zero features), not a fabricated geometry and not an error."""
    res = overlay_smart(_fc(_square(0, 0, 1, 1)), _fc(_square(5, 0, 6, 1)),
                        how="intersection")
    assert res.success, res.summary
    assert res.data["features"] == []
    assert res.data["type"] == "FeatureCollection"


# ── 7. degenerate inputs ──────────────────────────────────────────────


def test_degenerate_empty_inputs_honest():
    """Empty FeatureCollections produce honest results everywhere — typed
    failures where nothing can be computed, an empty success for overlay —
    and never a raw TypeError/AttributeError crash (pre-VNext the three
    geometry_ops constructors crashed on the (None, None) sentinel)."""
    empty = _fc()

    res_buf = buffer_smart(empty, distance=100, unit="m")
    assert isinstance(res_buf, GeoAnalysisResult) and res_buf.success is False
    assert "empty" in res_buf.summary.lower()

    for fn in (convex_hull, voronoi_polygons, multi_ring_buffer):
        res = fn(empty)  # must not raise
        assert isinstance(res, GeoAnalysisResult)
        assert res.success is False
        assert res.error_type == "ValueError"

    res_over = overlay_smart(empty, empty, how="intersection")
    assert res_over.success is True
    assert res_over.data["features"] == []


# ── 8. CRS disclosure (tool evidence) ─────────────────────────────────


def test_buffer_crs_disclosure_evidence():
    """buffer_analysis on geographic input attaches scientific_evidence whose
    transformations_applied honestly names the auto-UTM metric frame."""
    from app.tools.registry import ToolRegistry
    from app.tools.spatial import register_spatial_tools

    registry = ToolRegistry()
    register_spatial_tools(registry)
    resp = asyncio.run(registry.dispatch(
        "buffer_analysis",
        {"geojson": _fc(_pt(116.39, 39.9)), "distance": 1000.0, "unit": "m"},
        session_id="",
    ))
    assert resp.get("success") is True, resp
    ev = resp.get("scientific_evidence")
    assert isinstance(ev, dict)
    assert ev["algorithm"] == "geometry.buffer"
    assert ev["tool"] == "buffer_analysis"
    assert ev["inputs"]["crs"] == "EPSG:4326"
    assert ev["inputs"]["crs_class"] == "geographic"
    transformations = ev["transformations_applied"]
    assert any("auto UTM" in t and "EPSG:32" in t for t in transformations), transformations

    # honest limitation disclosure travels with the evidence
    assert any("UTM" in lim for lim in ev["limitations"])
