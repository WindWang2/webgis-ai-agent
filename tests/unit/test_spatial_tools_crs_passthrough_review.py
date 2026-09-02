"""Phase-E review M1-M6: projected CRS must survive the remaining tool boundaries.

The #1110 fix covered attribute_filter/central_feature; the review sweep found
six more adapters still handing down bare ``features`` lists into CRS-aware
operators (buffer_smart / statistics / nearest / cluster / SDE / moran_i).
With a projected CRS (UTM metres), the old bare-list path rebuilt a CRS-less
FC, metres were read as degrees, |lat|>84 tripped the polar branch and the
tools failed (or silently degraded to count-only). Forwarding the full FC
keeps the declared CRS.
"""
import pytest
from pyproj import Transformer

from app.tools.registry import ToolRegistry
from app.tools.spatial import register_spatial_tools
from app.tools.spatial_stats import register_spatial_stats_tools

_TR = Transformer.from_crs("EPSG:4326", "EPSG:32650", always_xy=True)


@pytest.fixture
def registry():
    r = ToolRegistry()
    register_spatial_tools(r)
    register_spatial_stats_tools(r)
    return r


def _projected_points_fc(n: int = 6, values: list[float] | None = None):
    feats = []
    for i in range(n):
        x, y = _TR.transform(116.0 + 0.002 * i, 39.9)
        props = {"idx": i, "val": values[i] if values else float(i + 1)}
        feats.append({
            "type": "Feature",
            "properties": props,
            "geometry": {"type": "Point", "coordinates": [x, y]},
        })
    return {
        "type": "FeatureCollection",
        "crs": {"type": "name", "properties": {"name": "EPSG:32650"}},
        "features": feats,
    }


# ─── M2 (most insidious): spatial_stats must not silently degrade ──────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_spatial_stats_projected_crs_keeps_metric_fields(registry):
    fc = _projected_points_fc()
    result = await registry.dispatch("spatial_stats", {"geojson": fc})
    assert result.get("success") is True, result
    data = result["data"]
    assert data.get("count") == 6
    # The old bare-list path silently returned count-only (area/bbox fields
    # vanished via the swallowed to_utm_gdf exception).
    assert "bbox" in data, f"metric fields missing (silent CRS degradation): {data}"
    assert "centroid" in data
    # Centroid must land back near the WGS84 truth, not metres-as-degrees.
    cx, cy = data["centroid"]
    assert 115.9 < cx < 116.1, f"centroid x={cx} looks like raw metres"
    assert 39.8 < cy < 40.0, f"centroid y={cy} looks like raw metres"


# ─── M1/M3/M4/M5/M6: projected input must succeed, not GEOS-fail ───────────


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_buffer_analysis_projected_crs_succeeds(registry):
    fc = _projected_points_fc()
    result = await registry.dispatch(
        "buffer_analysis", {"geojson": fc, "distance": 200, "unit": "m"}
    )
    assert result.get("success") is True, result
    assert result["data"], "buffer output empty"


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_nearest_neighbor_projected_crs_succeeds(registry):
    fc = _projected_points_fc(8)
    result = await registry.dispatch("nearest_neighbor", {"geojson": fc})
    assert result.get("success") is True, result
    # ~222 m spacing in UTM → clearly clustered R<1, and the mean distance
    # must be in metres (~222), not degrees (~0.002).
    data = result["data"]
    mnd = data.get("mean_nearest_distance")
    assert mnd is not None, f"mean_nearest_distance missing: {data}"
    assert 100 < mnd < 400, f"mean_nearest_distance={mnd} not metre-domain"


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_spatial_cluster_projected_crs_succeeds(registry):
    fc = _projected_points_fc(6)
    result = await registry.dispatch(
        "spatial_cluster", {"geojson": fc, "method": "dbscan", "eps": 500, "min_samples": 2}
    )
    assert result.get("success") is True, result


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_sde_projected_crs_succeeds(registry):
    fc = _projected_points_fc(6)
    result = await registry.dispatch("standard_deviational_ellipse", {"geojson": fc})
    assert result.get("success") is True, result


@pytest.mark.heavy
@pytest.mark.asyncio
async def test_moran_i_projected_crs_succeeds(registry):
    fc = _projected_points_fc(6, values=[1.0, 2.0, 1.5, 2.5, 1.2, 2.2])
    result = await registry.dispatch("moran_i", {"geojson": fc, "value_field": "val"})
    assert result.get("success") is True, result
