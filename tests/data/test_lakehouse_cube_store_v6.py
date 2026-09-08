"""Lakehouse V6 Wave 6 — Zarr cube store（group + consolidated + CoW 修订）。

覆盖面（ADR-0118 Zarr V6）：
- 真实写/读：group cube（每 band 一个 (time,y,x) 数组）、grid/铺排/dtype
  校验（复用 foundation）、round-trip 值一致；
- consolidated metadata：写后 zarr.json 在场（缺席 = 诚实降级）；
- chunk 粒度窗口读：counting store 证明只触窗口相交 chunk（结构性证据）；
- 内容寻址修订：manifest 身份；fork = 硬链接 CoW，旧 store 逐字节不动、
  旧修订持续可验证；同内容重发布 CAS 命中；
- typed 拒绝：空 band / 时间片不齐 / 越界时间片 / 非 v3 布局 CoW 全拷贝。
"""
from __future__ import annotations


import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

zarr_mod = pytest.importorskip("zarr")

from app.lib.geo_analysis.raster_grid import RasterGridProfile
from app.lib.geo_raster.chunk import build_chunk_descriptor_from_grid
from app.services.lakehouse.cube_store import (
    CubeError,
    collect_cube_entries,
    fork_cube_revision,
    open_cube,
    publish_cube,
    read_cube_window,
    write_cube,
)
from app.services.lakehouse.data_object import resolve_data_object, verify_data_object

GRID = RasterGridProfile(
    width=64, height=48, crs="EPSG:4326",
    transform=(1.0, 0.0, 0.0, 0.0, -1.0, 48.0),
    dtype="float32", nodata=-9999.0, band_count=1,
)
TIMES = ["2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z", "2024-03-01T00:00:00Z"]


def _write_slice(path, value, h=48, w=64, t=""):
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1,
        dtype="float32", crs="EPSG:4326",
        transform=from_origin(0, h, 1, 1), nodata=-9999.0,
    ) as dst:
        dst.write(np.full((h, w), value, dtype="float32"), 1)
    return path


def _band_groups(tmp_path, band, values_per_t):
    """每时间片两个 chunk descriptor（窗口铺满 64x48），source 指向真实
    GeoTIFF（foundation 默认读路径）。"""
    groups = []
    for t, value in enumerate(values_per_t):
        src = _write_slice(tmp_path / f"{band}_t{t}.tif", value)
        descriptors = [
            build_chunk_descriptor_from_grid(
                GRID, (0, 0, 32, 48), dtype="float32",
                source_uri=str(src), source_fingerprint=f"{band}-{t}",
                identity_extra=f"grp:{band}:t={t}:left",
            ),
            build_chunk_descriptor_from_grid(
                GRID, (32, 0, 32, 48), dtype="float32",
                source_uri=str(src), source_fingerprint=f"{band}-{t}",
                identity_extra=f"grp:{band}:t={t}:right",
            ),
        ]
        groups.append(descriptors)
    return groups


@pytest.fixture()
def cube_store(tmp_path):
    out = tmp_path / "cube.zarr"
    band_descriptors = {
        "red": _band_groups(tmp_path, "red", [10.0, 20.0, 30.0]),
        "nir": _band_groups(tmp_path, "nir", [1.0, 2.0, 3.0]),
    }
    write_cube(band_descriptors, TIMES, out)
    return out, band_descriptors


# ── 写/读 round-trip ──────────────────────────────────────────────────────


def test_cube_roundtrip(cube_store):
    store, _bd = cube_store
    root = open_cube(store)
    assert root.attrs["dims"] == ["time", "y", "x"]
    assert root.attrs["bands"] == ["nir", "red"]
    assert root.attrs["times"] == TIMES
    assert (store / "zarr.json").is_file()  # consolidated metadata 在场
    res = read_cube_window(store, time=slice(1, 3), bands=["red"])
    assert res["bands"]["red"].shape == (2, 48, 64)
    assert float(res["bands"]["red"][0, 0, 0]) == 20.0
    assert res["crs"] == "EPSG:4326"
    # 时间标签跟随切片（窗口真相）。
    assert res["times"] == TIMES[1:3]


def test_cube_rejects_invalid_band_groups(tmp_path):
    # 时间片数量与 times 不齐 → typed 拒绝（绝不写半截 cube）。
    bad = {"red": _band_groups(tmp_path, "r", [1.0, 2.0])}
    with pytest.raises(CubeError, match="time slices"):
        write_cube(bad, TIMES, tmp_path / "bad.zarr")
    with pytest.raises(CubeError, match="no bands"):
        write_cube({}, TIMES, tmp_path / "empty.zarr")


# ── chunk 粒度窗口读（lazy 证明）─────────────────────────────────────────


def test_window_read_touches_only_intersecting_chunks(cube_store):
    from zarr.storage import LocalStore

    store, _bd = cube_store

    class CountingStore(LocalStore):
        def __init__(self, inner):
            self._inner = inner
            self.chunk_reads = []
            super().__init__(root=inner.root, read_only=True)

        async def get(self, key, *a, **kw):
            s = str(key)
            if "/c/" in s:
                self.chunk_reads.append(s)
            return await self._inner.get(key, *a, **kw)

    cs = CountingStore(LocalStore(root=str(store), read_only=True))
    root = zarr_mod.open_group(store=cs, mode="r")
    arr = root["red"]
    # 时间片 1 的右下角 16x16 窗口 → 恰 1 个 chunk。
    win = arr[1, 32:48, 32:48]
    assert win.shape == (16, 16)
    assert len(cs.chunk_reads) == 1, cs.chunk_reads
    # chunk 布局 (1, 48, 32)：该窗口恰命中 (t=1, y=0, x=1) 块。
    assert cs.chunk_reads[0].endswith("c/1/0/1"), cs.chunk_reads[0]


# ── 内容寻址修订（CoW fork）──────────────────────────────────────────────


def test_publish_cube_manifest_and_dedup(cube_store):
    store, _bd = cube_store
    first = publish_cube(store, session_id="sess-cube")
    assert first["published"] is True
    assert first["durable"] == "published"
    assert first["entry_count"] > 3
    second = publish_cube(store, session_id="sess-cube")
    assert second["data_object_id"] == first["data_object_id"]
    assert second["deduped"] is True
    # 跨 owner = 不同逻辑对象。
    other = publish_cube(store, session_id="sess-cube-2")
    assert other["data_object_id"] != first["data_object_id"]
    # manifest 可解析 + 全 chunk digest 校验通过。
    manifest = resolve_data_object(first["data_object_id"])
    assert manifest["kind"] == "zarr_cube"
    assert verify_data_object(first["data_object_id"]) == "verified"


def test_fork_revision_is_copy_on_write(cube_store, tmp_path):
    store, _bd = cube_store
    before = {p: p.stat().st_mtime_ns for p in sorted(store.rglob("*")) if p.is_file()}
    before_entries = collect_cube_entries(store)
    old_manifest = publish_cube(store, session_id="s1")["data_object_id"]

    # 更新 red 波段 t=1：新值 99.0。
    upd_groups = _band_groups(tmp_path, "upd", [99.0])
    target = tmp_path / "rev2.zarr"
    fork_cube_revision(
        store, target,
        updates={"red": [(1, upd_groups[0], None)]},
    )

    # 旧 store 逐字节不动（mtime + 内容根一致）。
    after = {p: p.stat().st_mtime_ns for p in sorted(store.rglob("*")) if p.is_file()}
    assert before == after
    assert collect_cube_entries(store) == before_entries

    # 新修订：t=1 已更新，其余时间片/波段值不变（硬链接共享）。
    res = read_cube_window(target, bands=["red"])
    assert float(res["bands"]["red"][1, 0, 0]) == 99.0
    assert float(res["bands"]["red"][0, 0, 0]) == 10.0
    assert float(res["bands"]["red"][2, 0, 0]) == 30.0
    res_nir = read_cube_window(target, bands=["nir"])
    assert float(res_nir["bands"]["nir"][1, 0, 0]) == 2.0

    # 两个修订是不同的 DataObject（各自 manifest 可验证）。
    new_manifest = publish_cube(target, session_id="s1")["data_object_id"]
    assert new_manifest != old_manifest
    assert verify_data_object(old_manifest) == "verified"
    assert verify_data_object(new_manifest) == "verified"


def test_fork_rejects_out_of_range_time(cube_store, tmp_path):
    store, _bd = cube_store
    upd = _band_groups(tmp_path, "u", [0.0])
    with pytest.raises(CubeError, match="out of range"):
        fork_cube_revision(
            store, tmp_path / "rev3.zarr",
            updates={"red": [(99, upd[0], None)]},
        )


def test_fork_unknown_band_rejected(cube_store, tmp_path):
    store, _bd = cube_store
    upd = _band_groups(tmp_path, "u", [0.0])
    with pytest.raises(CubeError, match="unknown band"):
        fork_cube_revision(
            store, tmp_path / "rev4.zarr",
            updates={"swir": [(0, upd[0], None)]},
        )


def test_fork_target_exists_rejected(cube_store, tmp_path):
    store, _bd = cube_store
    upd = _band_groups(tmp_path, "u", [0.0])
    target = tmp_path / "rev5.zarr"
    fork_cube_revision(store, target, updates={"red": [(0, upd[0], None)]})
    with pytest.raises(CubeError, match="already exists"):
        fork_cube_revision(store, target, updates={"red": [(0, upd[0], None)]})


def test_publish_manifest_only_over_budget(cube_store):
    store, _bd = cube_store
    res = publish_cube(store, session_id="s9", blob_budget_bytes=0)
    assert res["published"] is True
    assert res["durable"] == "manifest_only"
    # manifest-only 也是可解析、可校验身份的对象。
    manifest = resolve_data_object(res["data_object_id"])
    assert manifest is not None
