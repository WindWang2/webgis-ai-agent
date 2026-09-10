"""Lakehouse V7 Wave 7 — remote sensing cube (optical + SAR + masks).

覆盖面（ADR-0119 §3）：
- 多源组装：optical→reflectance(time,band,y,x)、sar→sigma0(
  time,polarization,y,x)、mask→(time,y,x)；写入 labeled v2 store；
- 网格恒等：不一致源 typed 拒绝（绝不重采样/重投影）；
- 完整时间轴：变量缺时间步 typed 拒绝；重复 (time,variable,label) 拒绝；
- 值往返：写入的像元可经 labeled 窗口读回；
- 逐变量 dtype：uint8 mask 与 float32 reflectance 同 cube。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.lakehouse.cube_store import read_labeled_window
from app.services.lakehouse.rs_cube import RSCubeError, build_rs_cube

pytest.importorskip("zarr")
pytest.importorskip("rasterio")

H, W = 8, 8


def _write_slice(path, value, *, dtype="float32", transform=None):
    kwargs = dict(
        driver="GTiff", width=W, height=H, count=1,
        dtype=dtype, crs="EPSG:4326",
        transform=transform or from_origin(0, H, 1, 1),
    )
    if dtype == "float32":
        kwargs["nodata"] = -9999.0
    with rasterio.open(path, "w", **kwargs) as dst:
        dst.write(np.full((H, W), value, dtype=dtype), 1)
    return str(path)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    src = tmp_path / "data" / "src"
    src.mkdir(parents=True, exist_ok=True)
    return src


def _sources(src, *, optical=True, sar=True, cloud=True, steps=("2024-01", "2024-02")):
    sources = []
    for i, t in enumerate(steps):
        if optical:
            for j, band in enumerate(("B02", "B03")):
                sources.append({
                    "time": t, "role": "optical", "band": band,
                    "source": _write_slice(src / f"o_{i}_{j}.tif", 1.0 + i + j),
                })
        if sar:
            for pol, v in (("VV", 0.5), ("VH", 0.25)):
                sources.append({
                    "time": t, "role": "sar", "polarization": pol,
                    "source": _write_slice(src / f"s_{i}_{pol}.tif", v + i),
                })
        if cloud:
            sources.append({
                "time": t, "role": "cloud_mask",
                "source": _write_slice(src / f"c_{i}.tif", i % 2, dtype="uint8"),
            })
    return sources


@pytest.mark.asyncio
async def test_build_rs_cube_optical_sar_masks(data_dir):
    result = await build_rs_cube(
        "sess-rs", sources=_sources(data_dir), title="demo",
    )
    assert result["success"] is True
    assert result["variables"] == ["cloud_mask", "reflectance", "sigma0"]
    projection = result["labeled_projection"]
    assert projection["dims"] == ["time", "band", "polarization", "y", "x"]
    assert projection["variables"]["cloud_mask"]["dtype"] == "uint8"
    assert projection["variables"]["reflectance"]["dtype"] == "float32"
    # 值往返（labeled 窗口读）：第二时步 / 第二 band（值 = 1+1+1=3）。
    win = read_labeled_window(
        result["path"], index_slices={"time": slice(1, 2), "band": slice(1, 2)},
    )
    assert float(win["variables"]["reflectance"][0, 0, 0, 0]) == 3.0
    # time 已切片 → cloud_mask 形状 (1, H, W)，首元素即第二时步（值=1）。
    assert win["variables"]["cloud_mask"].shape == (1, H, W)
    assert int(win["variables"]["cloud_mask"][0, 0, 0]) == 1
    # 发布证据：payload 携带角色清单（血缘可审计）。
    from app.services.lakehouse.data_object import resolve_data_object

    if result.get("data_object_id"):
        manifest = resolve_data_object(result["data_object_id"])
        assert manifest is not None
        assert manifest["payload"]["roles"] == ["cloud_mask", "optical", "sar"]


@pytest.mark.asyncio
async def test_rs_cube_rejects_grid_mismatch(data_dir):
    shifted = _write_slice(
        data_dir / "shifted.tif", 1.0, transform=from_origin(0.5, H, 1, 1),
    )
    sources = [
        {"time": "t0", "role": "optical", "band": "B02",
         "source": _write_slice(data_dir / "ok.tif", 1.0)},
        {"time": "t1", "role": "optical", "band": "B02", "source": shifted},
    ]
    with pytest.raises(RSCubeError, match="grid differs"):
        await build_rs_cube("sess-rs2", sources=sources, title="bad")


@pytest.mark.asyncio
async def test_rs_cube_rejects_incomplete_and_duplicates(data_dir):
    base = [{"time": "t0", "role": "optical", "band": "B02",
             "source": _write_slice(data_dir / "a.tif", 1.0)}]
    with pytest.raises(RSCubeError, match="missing time steps"):
        await build_rs_cube("sess-rs3", sources=base + [
            {"time": "t1", "role": "sar", "polarization": "VV",
             "source": _write_slice(data_dir / "b.tif", 2.0)},
        ], title="incomplete")
    dup = [
        {"time": "t0", "role": "optical", "band": "B02",
         "source": _write_slice(data_dir / "c.tif", 1.0)},
        {"time": "t0", "role": "optical", "band": "B02",
         "source": _write_slice(data_dir / "d.tif", 1.0)},
        {"time": "t1", "role": "optical", "band": "B02",
         "source": _write_slice(data_dir / "e.tif", 2.0)},
    ]
    with pytest.raises(RSCubeError, match="duplicate source"):
        await build_rs_cube("sess-rs4", sources=dup, title="dup")


@pytest.mark.asyncio
async def test_rs_cube_rejects_bad_entries(data_dir):
    with pytest.raises(RSCubeError, match="role"):
        await build_rs_cube("sess-rs5", sources=[
            {"time": "t0", "source": "x.tif", "role": "hyperspectral", "band": "A"},
        ], title="bad-role")
    with pytest.raises(RSCubeError, match="'time' is required"):
        await build_rs_cube(
            "sess-rs5",
            sources=[{"source": "x.tif", "role": "optical", "band": "A"}],
            title="bad-time",
        )
    with pytest.raises(RSCubeError, match="band"):
        await build_rs_cube(
            "sess-rs5",
            sources=[{"time": "t0", "source": "x.tif", "role": "optical"}],
            title="bad-band",
        )
