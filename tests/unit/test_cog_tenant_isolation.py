"""
Unit and regression tests for GIS-08: Multi-tenant COG path isolation by session_id.

Verifies:
1. convert_raster_to_cog isolates output paths by session_id subdirectory.
2. Multiple sessions converting rasters with the same filename (e.g. dem.tif)
   write to distinct directories and do not overwrite or corrupt each other.
3. to_cog and ensure_cog incorporate session_id into destination paths.
4. Backward compatibility: when session_id is empty, destination defaults to out_dir directly.
"""
import asyncio
import os
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_raster.cog import ensure_cog, to_cog, validate_cog
from app.tools.raster_tools_cog import register_raster_cog_tools
from app.tools.registry import ToolRegistry


def _create_test_raster(path: Path, fill_value: int, size: int = 128) -> Path:
    """Create a non-tiled test GeoTIFF with specific fill value."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = np.full((size, size), fill_value, dtype=np.uint8)
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=1,
        dtype="uint8", crs="EPSG:4326", transform=from_origin(0, size, 1, 1),
    ) as dst:
        dst.write(data, 1)
    return path


def test_convert_raster_to_cog_session_isolation(tmp_path, monkeypatch):
    """Verify convert_raster_to_cog isolates output by session_id, avoiding file overwrite."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)

    # Tenant A and Tenant B both upload a raster named 'dem.tif'
    tenant_a_dir = data_dir / "tenant_a_upload"
    tenant_b_dir = data_dir / "tenant_b_upload"

    src_a = _create_test_raster(tenant_a_dir / "dem.tif", fill_value=42)
    src_b = _create_test_raster(tenant_b_dir / "dem.tif", fill_value=99)

    registry = ToolRegistry()
    register_raster_cog_tools(registry)
    convert_fn = registry._tools["convert_raster_to_cog"]

    # Convert for Tenant A
    rel_a = os.path.relpath(src_a, data_dir)
    res_a = asyncio.run(convert_fn(rel_a, out_dir="data/cog", session_id="session_alpha"))

    assert res_a["success"] is True
    assert res_a["converted"] is True
    out_a = Path(res_a["output_path"])
    assert "session_alpha" in out_a.parts
    assert out_a.is_file()

    # Convert for Tenant B
    rel_b = os.path.relpath(src_b, data_dir)
    res_b = asyncio.run(convert_fn(rel_b, out_dir="data/cog", session_id="session_beta"))

    assert res_b["success"] is True
    assert res_b["converted"] is True
    out_b = Path(res_b["output_path"])
    assert "session_beta" in out_b.parts
    assert out_b.is_file()

    # Paths must be completely distinct
    assert out_a != out_b

    # Verify contents were NOT overwritten
    with rasterio.open(out_a) as ds_a:
        arr_a = ds_a.read(1)
        assert (arr_a == 42).all(), "Tenant A's COG was overwritten or corrupted!"

    with rasterio.open(out_b) as ds_b:
        arr_b = ds_b.read(1)
        assert (arr_b == 99).all(), "Tenant B's COG was corrupted!"


def test_direct_cog_functions_session_isolation(tmp_path):
    """Verify to_cog and ensure_cog support session_id isolation."""
    src1 = _create_test_raster(tmp_path / "src1" / "elevation.tif", fill_value=10)
    src2 = _create_test_raster(tmp_path / "src2" / "elevation.tif", fill_value=20)

    out_dir = tmp_path / "cog_out"

    # to_cog with session_id
    cog1 = to_cog(src1, out_dir, session_id="user_sess_1")
    cog2 = to_cog(src2, out_dir, session_id="user_sess_2")

    assert cog1 != cog2
    assert "user_sess_1" in cog1.parts
    assert "user_sess_2" in cog2.parts
    assert cog1.is_file() and cog2.is_file()

    # ensure_cog with session_id
    src3 = _create_test_raster(tmp_path / "src3" / "landcover.tif", fill_value=30)
    cog3 = ensure_cog(src3, out_dir, session_id="user_sess_3")
    assert "user_sess_3" in cog3.parts
    assert validate_cog(str(cog3))["ok"]


def test_cog_conversion_empty_session_id_compatibility(tmp_path, monkeypatch):
    """Verify empty session_id writes directly to out_dir for backward compatibility."""
    monkeypatch.chdir(tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)

    src = _create_test_raster(data_dir / "plain.tif", fill_value=77)

    registry = ToolRegistry()
    register_raster_cog_tools(registry)
    convert_fn = registry._tools["convert_raster_to_cog"]

    rel_src = os.path.relpath(src, data_dir)
    res = asyncio.run(convert_fn(rel_src, out_dir="data/cog", session_id=""))

    assert res["success"] is True
    out_path = Path(res["output_path"])
    assert out_path.parent.name == "cog"
    assert out_path.name == "plain.tif"
    assert out_path.is_file()
