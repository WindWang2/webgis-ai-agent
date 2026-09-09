"""Lakehouse V7 Wave 2-4 — n-D labeled cube schema / store / xarray adapter.

覆盖面（ADR-0119 §1-§2）：
- 维度白名单 / y-x 尾轴契约 / 坐标单调与唯一 / CRS 真校验与降级披露；
- labeled store 写读往返（v3 dimension_names 维度绑定 —— R0-1 验收）；
- labeled selection 窗口读（多变量各取所轴；索引切片）；
- V6 兼容：v1 cube 合成投影可读；V6 入口对 v2 store typed 拒绝（R0-2）；
- 投影确定性（同输入同投影 —— manifest 身份前提）；负/边界全覆盖。
"""
from __future__ import annotations

import numpy as np
import pytest

zarr_mod = pytest.importorskip("zarr")

from app.services.lakehouse.cube_schema import (
    CubeSchemaError,
    check_crs,
    grid_coords_equal,
    transform_from_coords,
    validate_labeled_schema,
)
from app.services.lakehouse.cube_store import (
    CubeError,
    fork_cube_revision,
    publish_cube,
    read_cube_window,
    read_labeled_window,
    write_labeled_cube,
)

YS = [3.5, 2.5, 1.5, 0.5]
XS = [0.5, 1.5, 2.5, 3.5]
TIMES = ["2024-01-01T00:00:00Z", "2024-02-01T00:00:00Z"]
BANDS = ["B02", "B03"]
POLARS = ["VV", "VH"]


def _write_multi(tmp_path, **kw):
    return write_labeled_cube(
        {
            "reflectance": (
                ("time", "band", "y", "x"),
                np.arange(2 * 2 * 4 * 4, dtype="float32").reshape(2, 2, 4, 4),
            ),
            "sigma0": (
                ("time", "polarization", "y", "x"),
                np.zeros((2, 2, 4, 4), dtype="float32"),
            ),
        },
        {
            "time": TIMES, "band": BANDS, "polarization": POLARS,
            "y": YS, "x": XS,
        },
        tmp_path / "cube.zarr",
        crs="EPSG:4326",
        nodata=-9999.0,
        chunks={"time": 1, "band": 1, "polarization": 1, "y": 2, "x": 2},
        **kw,
    )


# ── schema 校验：正/负/边界 ────────────────────────────────────────────


def test_schema_accepts_multi_variable_projection(tmp_path):
    proj = _write_multi(tmp_path)
    assert proj["dims"] == ["time", "band", "polarization", "y", "x"]
    assert proj["shape"] == [2, 2, 2, 4, 4]
    assert proj["crs_checked"] == "rasterio"
    assert proj["variables"] == {
        "reflectance": {"dims": ["time", "band", "y", "x"], "dtype": "float32"},
        "sigma0": {"dims": ["time", "polarization", "y", "x"], "dtype": "float32"},
    }
    # 投影确定性（同输入同投影 → manifest 身份稳定）。
    proj2 = _write_multi(tmp_path)
    assert proj == proj2


def test_schema_rejects_unknown_dim_and_missing_spatial():
    coords = {"y": YS, "x": XS}
    with pytest.raises(CubeSchemaError, match="unknown dims"):
        validate_labeled_schema(
            dims=["depth", "y", "x"], shape=(2, 2, 2),
            coordinates={"depth": [0, 1], **coords}, crs="EPSG:4326",
            dtype="float32",
        )
    with pytest.raises(CubeSchemaError, match="spatial dim"):
        validate_labeled_schema(
            dims=["time"], shape=(2,), coordinates={"time": TIMES},
            crs="EPSG:4326", dtype="float32",
        )


def test_schema_requires_yx_last_two_axes():
    with pytest.raises(CubeSchemaError, match="last two axes"):
        validate_labeled_schema(
            dims=["y", "x", "time"], shape=(2, 2, 2),
            coordinates={"y": YS[:2], "x": XS[:2], "time": TIMES},
            crs="EPSG:4326", dtype="float32",
        )


def test_schema_coordinate_monotonicity_and_uniqueness():
    base = dict(crs="EPSG:4326", dtype="float32")
    with pytest.raises(CubeSchemaError, match="strictly decreasing"):
        validate_labeled_schema(
            dims=["y", "x"], shape=(2, 2),
            coordinates={"y": [0.5, 1.5], "x": XS[:2]}, **base,
        )
    with pytest.raises(CubeSchemaError, match="strictly increasing"):
        validate_labeled_schema(
            dims=["y", "x"], shape=(2, 2),
            coordinates={"y": YS[:2], "x": [2.5, 0.5]}, **base,
        )
    with pytest.raises(CubeSchemaError, match="duplicate"):
        validate_labeled_schema(
            dims=["time", "y", "x"], shape=(2, 1, 1),
            coordinates={"time": ["t0", "t0"], "y": YS[:1], "x": XS[:1]},
            **base,
        )
    with pytest.raises(CubeSchemaError, match="length"):
        validate_labeled_schema(
            dims=["time", "y", "x"], shape=(3, 1, 1),
            coordinates={"time": ["t0", "t1"], "y": YS[:1], "x": XS[:1]},
            **base,
        )


def test_schema_crs_validation_and_degradation():
    assert check_crs("EPSG:4326")["crs_checked"] == "rasterio"
    # 非 EPSG 字符串 rasterio 可解析（如 WKT/proj4 串小样本）；
    # 缺 rasterio 的环境降级正则并披露 —— 本环境有 rasterio，断言真校验。
    with pytest.raises(CubeSchemaError, match="invalid CRS"):
        check_crs("NOT-A-CRS")
    proj = validate_labeled_schema(
        dims=["y", "x"], shape=(2, 2),
        coordinates={"y": YS[:2], "x": XS[:2]}, crs="EPSG:4326",
        dtype="float32",
    )
    assert proj["crs"] == "EPSG:4326"


def test_schema_grid_alignment_helpers():
    assert grid_coords_equal(YS, XS, list(YS), list(XS))
    assert not grid_coords_equal(YS, XS, list(reversed(YS)), list(XS))
    assert not grid_coords_equal(YS, XS, YS[:-1], XS[:-1])
    # transform 推导：等距成立 / 不等距诚实缺席。
    tf = transform_from_coords(YS, XS)
    assert tf is not None and tf[0] == 1.0 and tf[4] == -1.0
    assert transform_from_coords([3.5, 2.0, 1.5, 0.5], XS) is None


# ── labeled store：写读往返 / v3 维度绑定 / 窗口 ──────────────────────


def test_labeled_store_roundtrip_and_window(tmp_path):
    _write_multi(tmp_path)
    store = tmp_path / "cube.zarr"
    r = read_labeled_window(
        store,
        index_slices={
            "time": slice(0, 1), "band": slice(1, 2),
            "y": slice(0, 2), "x": slice(2, 4),
        },
    )
    assert r["variables"]["reflectance"].shape == (1, 1, 2, 2)
    # polarization 未切片 → 整轴（各变量各取所轴）。
    assert r["variables"]["sigma0"].shape == (1, 2, 2, 2)
    assert r["coords"]["y"][0] == 3.5
    assert r["attrs"]["crs"] == "EPSG:4326"


def test_labeled_projection_readback_matches_write_projection(tmp_path):
    proj = _write_multi(tmp_path)
    from app.services.lakehouse.xarray_adapter import (
        labeled_projection_from_store,
    )

    back = labeled_projection_from_store(tmp_path / "cube.zarr")
    assert back["dims"] == proj["dims"]
    assert back["variables"] == proj["variables"]
    assert back["coords_summary"] == proj["coords_summary"]


def test_v6_entry_points_gate_labeled_store(tmp_path):
    """R0-2 验收：V6 读/fork 入口对 v2 store typed 拒绝（绝不读错轴）。"""
    _write_multi(tmp_path)
    store = tmp_path / "cube.zarr"
    with pytest.raises(CubeError, match="labeled cube"):
        read_cube_window(store, time=slice(0, 1))
    with pytest.raises(CubeError, match="labeled cube"):
        fork_cube_revision(store, tmp_path / "fork.zarr", updates={})


# ── V6 兼容：v1 cube 经合成投影可读 ───────────────────────────────────


def _write_v6_cube(tmp_path):
    from app.lib.geo_analysis.raster_grid import RasterGridProfile
    from app.lib.geo_raster.chunk import build_chunk_descriptor_from_grid

    grid = RasterGridProfile(
        width=8, height=8, crs="EPSG:4326",
        transform=(1.0, 0.0, 0.0, 0.0, -1.0, 8.0),
        dtype="float32", nodata=-9999.0, band_count=1,
    )
    groups = []
    for t in TIMES:
        groups.append([
            build_chunk_descriptor_from_grid(
                grid, (0, 0, 8, 8), dtype="float32", identity_extra=f"t={t}",
            )
        ])
    from app.services.lakehouse.cube_store import write_cube

    return write_cube(
        {"b1": groups}, TIMES, tmp_path / "v6.zarr",
        read_chunk=lambda d, t: np.full((8, 8), float(t[-3:-1]), dtype="float32"),
    )


def test_v6_cube_synthesized_projection_and_xarray(tmp_path):
    pytest.importorskip("xarray")
    from app.services.lakehouse.xarray_adapter import (
        open_cube_to_xarray,
        labeled_projection_from_store,
    )

    store = _write_v6_cube(tmp_path)
    proj = labeled_projection_from_store(store)
    assert proj["dims"] == ["time", "band", "y", "x"]
    assert proj["variables"] == {
        "data": {"dims": ["time", "band", "y", "x"], "dtype": "float32"}
    }
    ds, meta = open_cube_to_xarray(store)
    assert dict(ds.sizes)["time"] == 2
    assert list(ds.data_vars) == ["b1"]
    assert meta["schema_version"] == 1
    # publish（V6 通道）照常工作 —— v1 语义零变化。
    result = publish_cube(store, session_id="sess-x")
    assert result["published"] is True


def test_v6_fork_guard_rejects_multi_time_chunks(tmp_path):
    """R0-16 验收：chunk[0]!=1 的 v1 store fork typed 拒绝（CoW 语义保真）。"""
    zarr_mod = pytest.importorskip("zarr")

    store = _write_v6_cube(tmp_path)
    # 人为改成 (2,4,4) chunking（覆盖多个 t）；重写后重整元数据
    # （否则 fork 侧读到 stale consolidated metadata —— 与生产写入器
    # write_cube 恒 consolidate 的行为一致）。
    root = zarr_mod.open_group(store=str(store), mode="a")
    arr = root["b1"]
    data = np.asarray(arr)
    del root["b1"]
    new = root.create_array("b1", shape=data.shape, chunks=(2, 4, 4),
                            dtype="float32")
    new[:] = data
    from app.services.lakehouse.cube_store import consolidate_cube_metadata

    consolidate_cube_metadata(store)
    with pytest.raises(CubeError, match="per-time chunking"):
        fork_cube_revision(
            store, tmp_path / "fork.zarr",
            updates={"b1": [(0, [], None)]},
        )


# ── manifest 投影（labeled 语义参与身份）──────────────────────────────


def test_labeled_cube_publish_carries_projection(tmp_path):
    proj = _write_multi(tmp_path)
    store = tmp_path / "cube.zarr"
    result = publish_cube(
        store, session_id="sess-l7",
        payload_extra={"labeled": proj},
    )
    assert result["published"] is True
    from app.services.lakehouse.data_object import resolve_data_object

    manifest = resolve_data_object(result["data_object_id"])
    assert manifest is not None
    assert manifest["payload"]["labeled"]["dims"] == proj["dims"]
    assert manifest["payload"]["labeled"]["variables"] == proj["variables"]

def test_xarray_open_zarr_acceptance(tmp_path):
    """R0-1/R1-6 验收：v2 store 直接经 xr.open_zarr 读取 —— 维度由 v3
    dimension_names 绑定，坐标自动识别，数据变量齐备。"""
    xr = pytest.importorskip("xarray")
    _write_multi(tmp_path)
    store = tmp_path / "cube.zarr"
    ds = xr.open_zarr(str(store), consolidated=False)
    assert dict(ds.sizes) == {
        "time": 2, "band": 2, "polarization": 2, "y": 4, "x": 4,
    }
    assert set(ds.data_vars) == {"reflectance", "sigma0"}
    for dim in ("time", "y", "x"):
        assert dim in ds.coords
    # 坐标值正确性（绝对地理坐标含平移 —— R1-3 回归）。
    assert float(ds.coords["y"].values[0]) == 3.5
    assert float(ds.coords["y"].values[-1]) == 0.5
    assert float(ds.coords["x"].values[0]) == 0.5

def test_coords_include_affine_translation_and_align_discrimination():
    """R1-3 回归：坐标含仿射平移（绝对地理坐标）；跨原点对齐可判别。"""
    from app.services.lakehouse.cube_schema import (
        coords_from_transform_list,
        require_aligned,
    )

    # from_origin(x0=10, y0=20, a=1, e=-1) → GDAL [1,0,10,0,-1,20]。
    grid = coords_from_transform_list(
        [1.0, 0.0, 10.0, 0.0, -1.0, 20.0], 2, 2
    )
    assert grid["y"] == [19.5, 18.5]  # f=20 参与平移（绝对坐标）
    assert grid["x"] == [10.5, 11.5]  # c=10 参与平移
    proj_a = validate_labeled_schema(
        dims=["y", "x"], shape=(2, 2),
        coordinates={"y": grid["y"], "x": grid["x"]},
        crs="EPSG:4326", dtype="float32",
    )
    # 同形状同分辨率、不同地理位置的源 → 对齐判定必须拒绝。
    grid_b = coords_from_transform_list(
        [1.0, 0.0, 50.0, 0.0, -1.0, 60.0], 2, 2
    )
    reference = {
        "crs": "EPSG:4326", "dtype": "float32",
        "coords_summary": proj_a["coords_summary"],
    }
    candidate = {
        "crs": "EPSG:4326", "dtype": "float32",
        "coords_summary": {
            "y": {"kind": "grid", "n": 2, "start": grid_b["y"][0],
                  "end": grid_b["y"][-1]},
            "x": {"kind": "grid", "n": 2, "start": grid_b["x"][0],
                  "end": grid_b["x"][-1]},
        },
    }
    with pytest.raises(CubeSchemaError, match="grid differs"):
        require_aligned(reference, candidate, what="shifted-source")
