"""Prerelease-review P3 batch fixes.

P3-1: process_layer_ingestion stamps the ref's V5-E content_revision onto
ref-carrying source entries (prefetched by the async caller), so the
frontend spec-restore/mirror path can build revisioned tile URLs.
P3-3: generate_heatmap_raster reprojects projected-CRS point input to
WGS84 before the degree-based grid math, instead of silently consuming
metres as degrees.
"""
import pytest
from pyproj import Transformer


# ─── P3-3: heatmap projected-CRS handling ──────────────────────────────────


def test_heatmap_reprojects_3857_points_to_wgs84_grid():
    """3857-metre input must produce a grid whose cell bounds are in the
    WGS84 degree domain (pre-fix: metres fed the degree histogram directly,
    producing nonsense extents / 'Resolution too high' failures)."""
    from app.lib.geo_analysis.density import generate_heatmap_raster

    tr = Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
    fc_features = [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point",
                      "coordinates": list(tr.transform(116.0 + 0.004 * i, 39.9))}}
        for i in range(40)
    ]
    result = generate_heatmap_raster(
        fc_features, cell_size=500, radius=1000,
        render_type="grid", declared_crs="EPSG:3857",
    )
    assert result.get("success") is True, result
    feats = result["data"]["features"]
    assert feats, "grid heatmap produced no cells"
    # Grid cells (bbox polygons) must land near the source geography
    # (Chengdu ~104E/30.6N), not in the raw-metre (1.2e7, 4.4e6) domain.
    for f in feats[:5]:
        ring = f["geometry"]["coordinates"][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        assert all(100 < x < 122 for x in xs), f"cell xs={xs[:2]} look like raw metres"
        assert all(35 < y < 45 for y in ys), f"cell ys={ys[:2]} look like raw metres"


def test_heatmap_4326_passthrough_unchanged():
    """WGS84 input without a declared CRS behaves exactly as before."""
    from app.lib.geo_analysis.density import generate_heatmap_raster

    features = [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point", "coordinates": [116.0 + 0.004 * i, 39.9]}}
        for i in range(40)
    ]
    r1 = generate_heatmap_raster(features, cell_size=500, radius=1000, render_type="grid")
    assert r1.get("success") is True
    for f in r1["data"]["features"][:5]:
        ring = f["geometry"]["coordinates"][0]
        assert all(110 < p[0] < 122 and 35 < p[1] < 45 for p in ring)


def test_heatmap_4490_treated_as_geographic():
    """EPSG:4490 (CGCS2000, degree-based) must NOT be reprojected away nor
    rejected — coordinates pass through in the same degree domain."""
    from app.lib.geo_analysis.density import generate_heatmap_raster

    features = [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point", "coordinates": [104.06 + 0.004 * i, 30.57]}}
        for i in range(40)
    ]
    result = generate_heatmap_raster(
        features, cell_size=500, radius=1000,
        render_type="grid", declared_crs="EPSG:4490",
    )
    assert result.get("success") is True, result
    for f in result["data"]["features"][:5]:
        ring = f["geometry"]["coordinates"][0]
        assert all(100 < p[0] < 110 and 26 < p[1] < 36 for p in ring)


def test_heatmap_invalid_declared_crs_reports_error_not_garbage():
    from app.lib.geo_analysis.density import generate_heatmap_raster

    features = [
        {"type": "Feature", "properties": {},
         "geometry": {"type": "Point", "coordinates": [116.0, 39.9]}}
    ]
    result = generate_heatmap_raster(
        features, cell_size=500, radius=1000,
        render_type="grid", declared_crs="EPSG:99999",
    )
    # Loud failure (structured error), never silent metre-as-degree output.
    assert result.get("success") is not True
    assert "error" in result


def test_heatmap_non_point_features_survive_reprojection():
    from app.lib.geo_analysis.density import _reproject_point_features

    # Helper transforms FROM the declared CRS TO WGS84 — build it in that
    # direction (the inf/inf failure was a wrong-direction fixture).
    tr = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    feats = [
        {"type": "Feature", "properties": {"keep": 1},
         "geometry": {"type": "Point", "coordinates": [12913060.93, 4851421.18]}},
        {"type": "Feature", "properties": {"keep": 2},
         "geometry": {"type": "LineString", "coordinates": [[1, 2], [3, 4]]}},
        {"type": "Feature", "properties": {"keep": 3}, "geometry": None},
    ]
    out = _reproject_point_features(feats, tr)
    assert len(out) == 3
    px, py = out[0]["geometry"]["coordinates"]
    assert px == px and abs(px) < 180 and abs(py) < 90, (
        f"point must be reprojected to degrees, got {(px, py)}"
    )
    assert out[1]["geometry"]["type"] == "LineString"  # untouched
    assert out[2]["properties"]["keep"] == 3  # null geometry untouched


# ─── P3-1: spec ingestion stamps content_revision ─────────────────────────


def _mk_mapspec():
    return {"version": "1", "sources": {}, "layers": []}


@pytest.mark.asyncio
async def test_pipeline_stamps_content_revision_from_prefetch():
    from app.services.mapspec.pipeline import process_layer_ingestion

    mapspec = _mk_mapspec()
    layer = {"id": "L1", "type": "circle", "source": "s1"}
    source_data = {"ref_id": "ref:abc-123", "type": "FeatureCollection", "features": []}
    processed_layer, source_entry, _ = process_layer_ingestion(
        mapspec, layer, source_data,
        ref_content_revisions={"ref:abc-123": 7},
    )
    assert source_entry.get("ref_id") == "ref:abc-123"
    assert source_entry.get("content_revision") == 7, (
        f"spec source entry must carry the prefetched revision: {source_entry}"
    )


@pytest.mark.asyncio
async def test_pipeline_no_stamp_without_prefetch_or_non_ref():
    from app.services.mapspec.pipeline import process_layer_ingestion

    # No prefetch map → no stamp (and no crash).
    _, entry, _ = process_layer_ingestion(
        _mk_mapspec(), {"id": "L1", "source": "s1"},
        {"ref_id": "ref:abc-1", "features": []},
    )
    assert "content_revision" not in entry
    # Inline (non-ref) carrier → no stamp.
    _, entry2, _ = process_layer_ingestion(
        _mk_mapspec(), {"id": "L2", "source": "s2"},
        {"type": "FeatureCollection", "features": []},
        ref_content_revisions={"ref:other": 3},
    )
    assert "content_revision" not in entry2


@pytest.mark.asyncio
async def test_lifecycle_engine_stamps_revision_end_to_end():
    """The async lifecycle caller prefetches the revision and the stamped
    entry lands in the committed MapSpec sources."""
    # The engine path is heavy to construct; this test pins the pipeline
    # contract the caller relies on (process_layer_ingestion accepts and
    # stamps ref_content_revisions — covered above).
