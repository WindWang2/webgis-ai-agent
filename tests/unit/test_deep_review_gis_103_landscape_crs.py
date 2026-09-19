"""GIS-103 regression: landscape metrics CRS classification + cos(lat) policy.

Deep-review finding: ecology_tools used a ``"4326" in crs`` substring test
(missing EPSG:4490 and other geographic CRSs) and skipped the cos(lat)
correction on the x cell size, inflating mid-latitude areas.
"""
import math
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.tools.ecology_tools import register_ecology_tools
from app.tools.registry import ToolRegistry


TD = "data/tmp_v3_tests"
METRES_PER_DEGREE = 111320.0


def _write_categorical(name, crs, cell_deg, north, west="100.0"):
    path = os.path.join(TD, name)
    n = 10
    arr = (np.indices((n, n)).sum(axis=0) % 2 + 1).astype("float64")
    with rasterio.open(
        path, "w", driver="GTiff", height=n, width=n, count=1,
        dtype="float64", crs=crs,
        transform=from_origin(float(west), north, cell_deg, cell_deg),
    ) as dst:
        dst.write(arr, 1)
    return path


async def _run_tool(fname):
    reg = ToolRegistry()
    register_ecology_tools(reg)
    return await reg.dispatch(
        "landscape_metrics_analysis",
        {"raster_path": f"tmp_v3_tests/{fname}", "nodata": 0},
        session_id="",
    )


def test_epsg4490_30m_raster_plausible_hectares():
    """EPSG:4490 must be treated as geographic (old substring missed it)."""
    os.makedirs(TD, exist_ok=True)
    res_deg = 30.0 / METRES_PER_DEGREE
    fname = f"lm4490_{uuid.uuid4().hex[:6]}.tif"
    p = _write_categorical(fname, "EPSG:4490", res_deg, north=10 * res_deg)
    try:
        import asyncio

        out = asyncio.run(_run_tool(fname))
        assert out.get("success") is True
        meta = out["landscape_metadata"]
        # 30 m x 30 m cell at the equator -> 0.09 ha/cell, 9 ha total
        assert meta["cell_area_ha"] == pytest.approx(0.09, rel=2e-3)
        assert meta["total_area_ha"] == pytest.approx(9.0, rel=2e-3)
    finally:
        os.remove(p)


def test_4326_at_40n_applies_cos_lat_correction():
    """Longitude cell width must shrink by cos(40 deg), not stay at 111320 m."""
    os.makedirs(TD, exist_ok=True)
    res_deg = 30.0 / METRES_PER_DEGREE
    fname = f"lm40n_{uuid.uuid4().hex[:6]}.tif"
    p = _write_categorical(fname, "EPSG:4326", res_deg, north=40.0 + 5 * res_deg)
    try:
        import asyncio

        out = asyncio.run(_run_tool(fname))
        assert out.get("success") is True
        meta = out["landscape_metadata"]
        expected = 0.09 * math.cos(math.radians(40.0))
        assert meta["cell_area_ha"] == pytest.approx(expected, rel=2e-3)
        # old buggy path would have reported the full 0.09 ha (1.31x too high)
        assert meta["cell_area_ha"] < 0.09 * 0.9
    finally:
        os.remove(p)


def test_projected_crs_keeps_metric_cell_size():
    os.makedirs(TD, exist_ok=True)
    fname = f"lmutm_{uuid.uuid4().hex[:6]}.tif"
    p = _write_categorical(fname, "EPSG:32650", 30.0, north=3500000.0)
    try:
        import asyncio

        out = asyncio.run(_run_tool(fname))
        assert out.get("success") is True
        meta = out["landscape_metadata"]
        assert meta["cell_area_ha"] == pytest.approx(0.09, rel=1e-6)
    finally:
        os.remove(p)
