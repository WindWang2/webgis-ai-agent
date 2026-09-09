"""Lakehouse V6 Wave 7 — session cube 生产路径 + ref:cube disk-cursor。

覆盖面（ADR-0118）：
- build_session_cube：时间片栅格 → zarr cube + `ref:cube/<id>` + DataObject
  （血缘 = 输入栅格指纹；复用键 = 输入+时间轴+chunking）；
- ref:cube disk-cursor：路径派生（charset 白名单）、probe_ref、GC rmtree；
- 网格不一致 → typed 拒绝（无静默重采样）；
- 窗口读（chunk 粒度）+ 跨会话不可见（owner 隔离）。
"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.services.artifact_registry import (
    cube_ref_exists,
    cube_store_path,
    get_artifact,
    is_cube_ref,
    probe_ref,
)
from app.services.lakehouse.cube_service import (
    CubeServiceError,
    build_session_cube,
    read_session_cube_window,
)

pytest.importorskip("zarr")
pytest.importorskip("rasterio")


def _write_slice(path, value, h=48, w=64):
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((h, w), value, dtype="float32"), 1)
    return str(path)


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "DATA_DIR", str(tmp_path / "data"))
    return tmp_path / "data"


@pytest.fixture()
def time_slices(data_dir, tmp_path):
    src = data_dir / "src"
    src.mkdir(parents=True, exist_ok=True)
    return [
        {"time": "2024-01", "source": _write_slice(src / "t1.tif", 10.0)},
        {"time": "2024-02", "source": _write_slice(src / "t2.tif", 20.0)},
        {"time": "2024-03", "source": _write_slice(src / "t3.tif", 30.0)},
    ]


@pytest.mark.asyncio
async def test_build_session_cube_end_to_end(data_dir, time_slices):
    res = await build_session_cube("sess-cube", time_sources=time_slices, title="T")
    assert res["success"] is True
    assert res["ref"].startswith("ref:cube/")
    assert res["steps"] == 3
    assert res["durable"] == "published"
    assert len(res["data_object_id"]) == 64

    # disk-cursor：路径单点 + 活性。
    assert cube_store_path("sess-cube", res["ref"]) is not None
    assert cube_store_path("sess-cube", res["ref"]).is_dir()
    assert cube_ref_exists("sess-cube", res["ref"])
    probed = await probe_ref("sess-cube", res["ref"])
    assert probed == {"kind": "lakehouse_cube", "exists": True}

    # 台账：cube ref 是一等公民。
    record = await get_artifact("sess-cube", res["ref"])
    assert record is not None
    assert record.artifact_type == "lakehouse_cube"
    assert record.metadata["times"] == ["2024-01", "2024-02", "2024-03"]

    # 内容身份 + 血缘（输入指纹进 manifest）。
    from app.services.lakehouse.data_object import resolve_data_object

    manifest = resolve_data_object(res["data_object_id"])
    assert manifest["kind"] == "zarr_cube"
    assert manifest["owner_scope"] == {"session_id": "sess-cube"}
    assert len(manifest["source_refs"]) == 3


@pytest.mark.asyncio
async def test_cube_window_read_and_owner_isolation(data_dir, time_slices):
    res = await build_session_cube("sess-w", time_sources=time_slices, title="T")
    ref = res["ref"]
    out = await read_session_cube_window("sess-w", ref, time=slice(2, 3))
    assert float(out["bands"]["b1"][0, 0, 0]) == 30.0
    assert out["times"][0] == "2024-03"
    # 跨会话 → 不可见（typed，不泄漏存在性）。
    with pytest.raises(CubeServiceError, match="not alive"):
        await read_session_cube_window("other-session", ref)
    with pytest.raises(CubeServiceError, match="not a cube ref"):
        await read_session_cube_window("sess-w", "ref:raster/abc")


@pytest.mark.asyncio
async def test_cube_rejects_misaligned_grid(data_dir, tmp_path):
    """网格不一致 → typed 拒绝（绝不静默重采样）。"""
    src = data_dir / "src"
    src.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        src / "odd.tif", "w", driver="GTiff", width=32, height=32, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, 32, 1, 1), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((32, 32), 5.0, dtype="float32"), 1)
    with pytest.raises(CubeServiceError, match="grid validation failed"):
        await build_session_cube(
            "sess-bad",
            time_sources=[
                {"time": "2024-01", "source": _write_slice(src / "a.tif", 1.0)},
                {"time": "2024-02", "source": str(src / "odd.tif")},
            ],
            title="T",
        )


@pytest.mark.asyncio
async def test_cube_gc_removes_orphan_store(data_dir, time_slices):
    from app.services.artifact_registry import collect_orphan_refs, mark_status

    res = await build_session_cube("sess-gc", time_sources=time_slices, title="T")
    ref, store = res["ref"], cube_store_path("sess-gc", res["ref"])
    assert store.is_dir()
    await mark_status("sess-gc", ref, "stale")
    deleted = await collect_orphan_refs("sess-gc")
    assert ref in deleted
    assert not store.exists()


def test_cube_ref_charset_guards(data_dir):
    assert is_cube_ref("ref:cube/abcd1234")
    assert cube_store_path("s", "ref:cube/../../evil") is None
    assert cube_store_path("../evil", "ref:cube/abc") is None
