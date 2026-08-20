"""Regression for #682 — zonal_stats must honor projected CRS via tool route.

#599 fixed clip/overlay/dissolve/join/h3 but zonal_stats dropped the
FeatureCollection `crs` at two points:
  1. app/tools/advanced_spatial.py tool boundary (features list only)
  2. app/services/spatial_analyzer.py rebuild without `crs`

A declared EPSG:3857 zone FC on a 3857 raster must not be misread as
EPSG:4326 — pyproj returns (inf,inf) silently and GIS-23 must fire, or
stats come back as None/zero and look plausible.

The test walks the TOOL ROUTE (ToolRegistry.dispatch) so the passthrough
is exercised, not just raster_ops directly.
"""
import os
import uuid

import numpy as np
import pyproj
import pytest
import rasterio
from rasterio.transform import from_origin

from app.tools.advanced_spatial import register_advanced_spatial_tools
from app.tools.registry import ToolRegistry
from app.utils.coord_transform import transform_geojson

# WGS84 zone ~ 0.05° box near Beijing (used as source for transform)
WGS84_ZONE_FC = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {"zone_id": 1},
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [116.0, 39.90],
                        [116.05, 39.90],
                        [116.05, 40.00],
                        [116.0, 40.00],
                        [116.0, 39.90],
                    ]
                ],
            },
        }
    ],
}


def _data_path(name: str) -> str:
    # validate_data_path allows <cwd>/data (and <cwd>/tmp). Under worktree
    # the pytest cwd (worktree root) differs from the bare relative path, so
    # resolve against the runtime cwd's data/tmp. Prefer data, fall back to tmp.
    data_dir = os.path.realpath("./data")
    tmp_dir = os.path.realpath("./tmp")
    os.makedirs(data_dir, exist_ok=True)
    os.makedirs(tmp_dir, exist_ok=True)
    # Return an absolute path under the cwd's data dir so validate passes.
    return os.path.join(data_dir, name)


def _make_raster(path: str, crs: str, value: float = 10.0) -> None:
    """Create a small constant-value raster covering the WGS84 zone bbox.

    For EPSG:4326 the grid is in degrees; for EPSG:3857 it is in metres
    reprojected from the same WGS84 bbox so the zone+ raster overlap when
    CRS is respected.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if crs == "EPSG:4326":
        west, south, east, north = 115.95, 39.85, 116.10, 40.05
        res = 0.02
        width = int(round((east - west) / res))
        height = int(round((north - south) / res))
        transform = from_origin(west, north, res, res)
    else:
        tr = pyproj.Transformer.from_crs("EPSG:4326", "EPSG:3857", always_xy=True)
        x0, y0 = tr.transform(115.95, 39.85)
        x1, y1 = tr.transform(116.10, 40.05)
        width, height = 10, 10
        res_x = (x1 - x0) / width
        res_y = (y1 - y0) / height
        transform = from_origin(x0, y1, res_x, res_y)
    data = np.full((height, width), value, dtype=np.float32)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        height=height,
        width=width,
        count=1,
        dtype=np.float32,
        crs=crs,
        transform=transform,
    ) as dst:
        dst.write(data, 1)


@pytest.fixture()
def registry():
    r = ToolRegistry()
    register_advanced_spatial_tools(r)
    return r


@pytest.mark.asyncio
async def test_zonal_stats_tool_3857_zone_on_3857_raster(registry):
    """Projected (EPSG:3857) zone FC on a 3857 raster via tool route — must be ~10."""
    raster_path = _data_path(f"test_682_3857_{uuid.uuid4().hex[:8]}.tif")
    _make_raster(raster_path, "EPSG:3857", value=10.0)
    # Build a 3857-declared FC from the WGS84 zone
    fc_3857 = transform_geojson(WGS84_ZONE_FC, from_crs="EPSG:4326", to_crs="EPSG:3857")
    # transform_geojson already writes crs when input has none? Input has no crs
    # so we must declare it explicitly (and transform_geojson preserves it on output
    # when from is 4326->3857 and input had no crs it will NOT add crs — so inject).
    fc_3857 = dict(fc_3857)
    fc_3857["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}
    try:
        result = await registry.dispatch("zonal_stats", {"geojson": fc_3857, "raster_path": raster_path})
        assert result.get("success") is True, result
        data = result.get("data")
        assert isinstance(data, dict) and data.get("type") == "FeatureCollection"
        feats = data.get("features", [])
        assert len(feats) == 1
        props = feats[0].get("properties", {})
        # On the bug (crs stripped) this zone is misread as 4326 and reprojected
        # 4326->3857 on 12M-metre coords -> (inf,inf) -> _has_inf_coords fires or
        # rasterstats returns None stats. Either way mean is not 10.
        assert props.get("mean") is not None, f"mean is None — CRS was likely dropped: {props}"
        assert abs(float(props["mean"]) - 10.0) < 1e-6, f"mean should be 10, got {props.get('mean')}"
        # default stats are mean/sum/max/min (no count)
        assert props.get("sum") is not None
    finally:
        try:
            os.unlink(raster_path)
        except OSError:
            pass


@pytest.mark.asyncio
async def test_zonal_stats_tool_3857_zone_on_4326_raster(registry):
    """Projected (EPSG:3857) zone FC on a WGS84 raster via tool route — must be ~42."""
    raster_path = _data_path(f"test_682_4326_{uuid.uuid4().hex[:8]}.tif")
    _make_raster(raster_path, "EPSG:4326", value=42.0)
    fc_3857 = transform_geojson(WGS84_ZONE_FC, from_crs="EPSG:4326", to_crs="EPSG:3857")
    fc_3857 = dict(fc_3857)
    fc_3857["crs"] = {"type": "name", "properties": {"name": "EPSG:3857"}}
    try:
        result = await registry.dispatch("zonal_stats", {"geojson": fc_3857, "raster_path": raster_path})
        assert result.get("success") is True, result
        data = result.get("data")
        feats = data.get("features", [])
        assert len(feats) == 1
        props = feats[0].get("properties", {})
        # Bug: 3857 coords treated as 4326 -> identity reprojection (4326->4326)
        # but coords are ~12M so they miss the raster entirely -> mean None.
        assert props.get("mean") is not None, f"mean is None — CRS was dropped: {props}"
        assert abs(float(props["mean"]) - 42.0) < 1e-6
        assert props.get("sum") is not None
    finally:
        try:
            os.unlink(raster_path)
        except OSError:
            pass
