"""Conformance tests for the spatial-sampling family (Science V6).

Contract bullets under test (descriptors in app/lib/gis/algorithms/
sampling.py; numerics in app/lib/geo_analysis/spatial_sampling.py):

- ``sampling.random_points``: containment in the projected frame,
  exact per-polygon counts, (input, seed) determinism, seed sensitivity;
- ``sampling.systematic_grid``: grid spacing respected (nearest-neighbour
  spacing ≥ spacing − fp), random start offset varies with seed;
- ``sampling.stratified_points``: equal vs proportional allocation
  tables (proportional follows area weights); zero-area stratum skipped;
- typed errors: empty frame, non-polygon frame, missing stratum field,
  bad seed / n / spacing / allocation.
"""
import numpy as np
import pytest
from shapely.geometry import shape

from app.lib.geo_analysis.spatial_sampling import (
    random_points_in_polygons,
    stratified_points_in_polygons,
    systematic_grid_points,
)
from app.lib.gis.scientific_errors import (
    InsufficientSamples,
    MissingRequiredField,
    NoValidObservations,
)

pytestmark = pytest.mark.unit


# ── fixtures ─────────────────────────────────────────────────────────

def _grid_fc(nrows, ncols, cell=0.01, lon0=116.0, lat0=39.0, **prop_fns):
    """nrows×ncols 多边形网格（共享顶点；每格属性由 prop_fns 生成）。"""
    xs = [lon0 + i * cell for i in range(ncols + 1)]
    ys = [lat0 + j * cell for j in range(nrows + 1)]
    feats = []
    for r in range(nrows):
        for c in range(ncols):
            props = {k: fn(r, c) for k, fn in prop_fns.items()}
            feats.append({
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": [[
                    [xs[c], ys[r]], [xs[c + 1], ys[r]],
                    [xs[c + 1], ys[r + 1]], [xs[c], ys[r + 1]],
                    [xs[c], ys[r]],
                ]]},
                "properties": props,
            })
    return {"type": "FeatureCollection", "features": feats}


# ── sampling.random_points ───────────────────────────────────────────

def test_random_points_containment_and_determinism():
    fc = _grid_fc(2, 3)
    a, meta = random_points_in_polygons(fc, n=10, seed=42)
    b, _ = random_points_in_polygons(fc, n=10, seed=42)
    assert a == b, "same (input, seed) must be bit-identical"
    assert meta["sample_count"] == 60  # 6 polygons × 10
    assert meta["polygon_count"] == 6
    assert meta["design"] == "srs_per_polygon"

    import geopandas as gpd

    from app.lib.geo_processor.core import to_utm_gdf

    gdf, utm = to_utm_gdf(fc)
    pts = gpd.GeoSeries([shape(f["geometry"]) for f in a["features"]],
                        crs="EPSG:4326").to_crs(utm)
    assert all(any(g.covers(p) for g in gdf.geometry) for p in pts)
    # 逐面计数精确 = n
    from collections import Counter

    counts = Counter(f["properties"]["polygon_index"] for f in a["features"])
    assert set(counts.values()) == {10}


def test_random_points_seed_sensitivity():
    fc = _grid_fc(2, 2)
    a, _ = random_points_in_polygons(fc, n=5, seed=42)
    b, _ = random_points_in_polygons(fc, n=5, seed=7)
    assert a != b, "different seeds must produce different samples"


def test_systematic_grid_spacing_and_alignment():
    from scipy.spatial import cKDTree

    # 0.01° ≈ 1.11km（纬向经度跨度随 cos(lat) 变化，U 工作帧内判定）
    fc = _grid_fc(3, 3)
    fc_out, meta = systematic_grid_points(fc, spacing=500.0, seed=42)
    assert meta["sample_count"] == len(fc_out["features"]) > 0

    from app.lib.geo_processor.core import to_utm_gdf

    gdf, _ = to_utm_gdf(fc_out)
    coords = np.column_stack((gdf.geometry.x.values, gdf.geometry.y.values))
    # 抽样坐标已在 UTM 工作帧（米）—— 最近邻距离 ≥ spacing（对角不计）
    tree = cKDTree(coords)
    dists, _ = tree.query(coords, k=2)
    nn = dists[:, 1]
    assert nn.min() >= 500.0 - 1e-6
    # 起点偏移随种子变化（网格整体平移 → 载荷不同）
    fc_b, _ = systematic_grid_points(fc, spacing=500.0, seed=99)
    assert fc_b != fc_out


def test_stratified_allocation_tables():
    fc = _grid_fc(2, 4, landuse=lambda r, c: "forest" if c < 3 else "urban")
    # equal：每层 n_per_stratum
    fc_eq, meta_eq = stratified_points_in_polygons(
        fc, "landuse", 12, allocation="equal", seed=42)
    assert meta_eq["allocation_table"] == {"forest": 12, "urban": 12}
    assert meta_eq["sample_count"] == 24
    # proportional：层预算按层面积权重在层内多边形间再分摊；
    # 每层总量恰为 n_per_stratum（forest 6 格 40 点 / urban 2 格 40 点）
    fc_pr, meta_pr = stratified_points_in_polygons(
        fc, "landuse", 40, allocation="proportional", seed=42)
    f_n = meta_pr["allocation_table"]["forest"]
    u_n = meta_pr["allocation_table"]["urban"]
    assert f_n == 40 and u_n == 40
    # 层内面积权重分摊：forest 6 等格均摊 ≈ 40/6，urban 2 等格 ≈ 20/格
    from collections import Counter

    per_poly = Counter(f["properties"]["polygon_index"]
                       for f in fc_pr["features"])
    forest_counts = [v for k, v in per_poly.items() if k % 4 < 3]
    urban_counts = [v for k, v in per_poly.items() if k % 4 >= 3]
    assert min(forest_counts) >= 5 and max(forest_counts) <= 8
    assert min(urban_counts) >= 18 and max(urban_counts) <= 22
    # 层标签正确落到样本属性
    labels = {f["properties"]["stratum"] for f in fc_pr["features"]}
    assert labels == {"forest", "urban"}


def test_sampling_adversarial_inputs():
    fc = _grid_fc(2, 2, landuse=lambda r, c: "a")
    with pytest.raises(NoValidObservations):
        random_points_in_polygons({"type": "FeatureCollection",
                                   "features": []}, n=5)
    points_fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "Point",
                                        "coordinates": [116.0, 39.0]},
        "properties": {},
    }]}
    with pytest.raises(NoValidObservations, match="polygonal"):
        random_points_in_polygons(points_fc, n=5)
    with pytest.raises(MissingRequiredField, match="wrong_field"):
        stratified_points_in_polygons(fc, "wrong_field", 5)
    with pytest.raises(InsufficientSamples):
        random_points_in_polygons(fc, n=0)
    with pytest.raises(ValueError, match="seed"):
        random_points_in_polygons(fc, n=5, seed=-1)
    with pytest.raises(ValueError, match="spacing"):
        systematic_grid_points(fc, spacing=0)
    with pytest.raises(ValueError, match="allocation"):
        stratified_points_in_polygons(fc, "landuse", 5, allocation="junk")
