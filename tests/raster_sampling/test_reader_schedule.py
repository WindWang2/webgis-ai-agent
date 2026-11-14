"""WP07 块读取调度 — 打开/块/预算/取消/ABA 防护（真实 GeoTIFF）。"""
from __future__ import annotations

import numpy as np
import pytest

from app.services.raster_sampling import reader_schedule
from app.services.raster_sampling.contracts import (
    RasterSamplingError,
    SourceBinding,
)
from app.services.raster_sampling.stations import StationSet, Station


def _binding(ref_id: str, bands=(1,), crs="EPSG:4326"):
    return SourceBinding(ref_id=ref_id, data_revision="fp:test", bands=list(bands),
                         crs=crs)


async def _resolve(session_id: str, ref: str):
    """走真实生产解析（resolve_source → RasterSource）。"""
    from app.services.raster_authoring.source import resolve_source

    return await resolve_source(session_id, ref, None, trusted_dispatch=True)


def _station_set(points, crs="EPSG:4326"):
    stations = [Station(sample_id=f"sp_t_{i:06d}", station_index=i, x=x, y=y,
                        parent_locator=f"p{i}")
                for i, (x, y) in enumerate(points)]
    return StationSet(mode="source_points", crs=crs, stations=stations)


def test_same_block_5000_points_single_open_few_reads(rs_env, raster_factory):
    """S17：5,000 点同块 → opens=1、窗口读有界（非 5000 次、非整幅）。"""
    made = {}

    async def make():
        import numpy as np
        rows, cols = np.mgrid[0:2000, 0:2000]
        values = (rows * 10 + cols).astype("float32")[None, ...]
        from rasterio.transform import from_origin as _fo
        ref, path = await raster_factory(
            "s17", name="s17_tiled", width=2000, height=2000, values=values,
            tiled=True, blockxsize=256, blockysize=256,
            transform=_fo(10.0, 20.0, 0.001, 0.001))
        made["ref"] = ref
        made["resolved"] = await _resolve("s17", ref)

    import asyncio
    asyncio.run(make())
    ref = made["ref"]
    resolved = made["resolved"]
    # 站位聚在 col 300..400 / row 300..400（同一 256 块 block(1,1)）
    rng = np.random.default_rng(7)
    pts = [(10.0 + (300 + rng.integers(0, 100)) * 0.001,
            20.0 - (300 + rng.integers(0, 100)) * 0.001)
           for _ in range(5000)]
    ss = _station_set(pts)
    rows, metrics, cost = reader_schedule.execute_sampling(
        resolved_sources=[resolved],
        bindings=[_binding(ref)],
        station_set=ss,
        method="source_pixel")
    assert metrics.opens == 1
    assert metrics.window_reads <= 8, f"5000 same-block points: {metrics.window_reads} reads"
    assert len(rows) == 5000
    # 解析 oracle：值 = row*10 + col（与生成式一致 —— 期望由坐标独立算出）
    by_idx = {r.station_index: r for r in rows}
    r0 = by_idx[0]
    exp_col = int((pts[0][0] - 10.0) / 0.001)
    exp_row = int((20.0 - pts[0][1]) / 0.001)
    assert r0.col == exp_col and r0.row == exp_row
    assert r0.raw_value == exp_row * 10 + exp_col


def test_strip_raster_single_point_full_row_cost(rs_env, raster_factory):
    """S18：非分块巨条带源 —— 单点也按真实条带解码成本预估/阻断。"""
    import asyncio

    ref_holder = {}

    async def make():
        import numpy as np
        values = np.zeros((1, 4, 80_000), dtype="float64")  # 80k×8B = 640KB/行
        ref, path = await raster_factory(
            "s18", name="s18_strip", width=80_000, height=4, values=values,
            tiled=False)
        ref_holder["ref"] = ref
        ref_holder["resolved"] = await _resolve("s18", ref)

    asyncio.run(make())
    ss = _station_set([(10.005, 19.995)])
    # 4 行 × 80000 列 × float64 = 2.56MB —— 未超条带闸；先证明能执行且
    # 记账按真实块（条带 = 全宽行）成本而非 1×1
    rows, metrics, cost = reader_schedule.execute_sampling(
        resolved_sources=[ref_holder["resolved"]],
        bindings=[_binding(ref_holder["ref"])],
        station_set=ss, method="source_pixel")
    assert metrics.opens == 1
    # itemsize=4（dtype 默认 float32）→ 一行全宽 = 80000×4 字节
    assert metrics.decoded_bytes >= 80_000 * 4  # 至少一行全宽
    # 放大闸下限：monkeypatch 条带预算 → 同请求必须 typed 拒绝（不静默）
    import app.services.raster_sampling.reader_schedule as mod
    orig = mod.MAX_STRIP_DECODE_BYTES
    mod.MAX_STRIP_DECODE_BYTES = 8
    try:
        with pytest.raises(RasterSamplingError) as ei:
            reader_schedule.execute_sampling(
                resolved_sources=[ref_holder["resolved"]],
                bindings=[_binding(ref_holder["ref"])],
                station_set=ss, method="source_pixel")
        assert ei.value.code == "BUDGET_EXCEEDED"
        assert "strip" in ei.value.detail or "block" in ei.value.detail
    finally:
        mod.MAX_STRIP_DECODE_BYTES = orig


def test_value_cell_budget_pre_allocation(rs_env, raster_factory):
    """S19：站位×源×波段超预算 → 分配前 typed 阻断（不偷减）。"""
    import asyncio

    async def make():
        ref, path = await raster_factory("s19", name="s19_small")
        return ref, await _resolve("s19", ref)

    ref, resolved = asyncio.run(make())
    ss = _station_set([(10.01, 19.99)] * 1)
    import app.services.raster_sampling.reader_schedule as mod
    orig = mod.MAX_VALUE_CELLS
    mod.MAX_VALUE_CELLS = 0
    try:
        with pytest.raises(RasterSamplingError) as ei:
            reader_schedule.execute_sampling(
                resolved_sources=[resolved], bindings=[_binding(ref)],
                station_set=ss, method="source_pixel")
        assert ei.value.code == "BUDGET_EXCEEDED"
    finally:
        mod.MAX_VALUE_CELLS = orig


def test_mutation_between_batches_typed_failure(rs_env, raster_factory):
    """S34：批间源改写（含改回原样的 ABA 意图）→ typed 失败，弃已产内容。"""
    import asyncio
    import os

    made = {}

    async def make():
        # 两个块区域的站位分布（>1 批 → 批间复核有机会触发）
        from rasterio.transform import from_origin as _fo
        ref, path = await raster_factory(
            "s34", name="s34_tiled", width=2048, height=64, tiled=True,
            blockxsize=256, blockysize=64,
            transform=_fo(10.0, 20.0, 0.001, 0.001),
            values=np.zeros((1, 64, 2048), dtype="float32"))
        made["ref"], made["path"] = ref, path
        made["resolved"] = await _resolve("s34", ref)

    asyncio.run(make())
    # 站位分居两个远距块 → 两组
    pts = [(10.0 + 100 * 0.001, 19.99)] * 3 + [(10.0 + 1900 * 0.001, 19.99)] * 3
    ss = _station_set(pts)
    path = made["path"]

    calls = {"n": 0}

    def _hook(_done):
        # 第一批完成后改写源文件（mtime_ns/size 变化 → 代次漂移）
        calls["n"] += 1
        if calls["n"] == 1:
            st = os.stat(path)
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000))

    _hook.every = 1  # 触发周期（_ProgressCounter 构造时读取）

    with pytest.raises(RasterSamplingError) as ei:
        reader_schedule.execute_sampling(
            resolved_sources=[made["resolved"]],
            bindings=[_binding(made["ref"])],
            station_set=ss, method="source_pixel",
            progress_hook=_hook)
    assert ei.value.code == "SOURCE_MUTATED_DURING_READ"


def test_two_sources_own_grids_no_hidden_resample(rs_env, raster_factory):
    """S03：双源不同分辨率 —— 各自真实网格定位，不暗中重采样对齐。"""
    import asyncio

    made = {}

    async def make():
        ref1, p1 = await raster_factory(
            "s03a", name="s03_coarse", width=8, height=8,
            values=np.full((1, 8, 8), 111.0, dtype="float32"))
        ref2, p2 = await raster_factory(
            "s03b", name="s03_fine", width=80, height=80,
            values=np.full((1, 80, 80), 222.0, dtype="float32"),
            transform=__import__("rasterio.transform", fromlist=["from_origin"])
            .from_origin(10.0, 20.0, 0.001, 0.001))
        made["ref1"], made["ref2"] = ref1, ref2
        made["r1"] = await _resolve("s03a", ref1)
        made["r2"] = await _resolve("s03b", ref2)

    asyncio.run(make())
    ss = _station_set([(10.04, 19.95), (10.05, 19.94)])
    rows, metrics, _ = reader_schedule.execute_sampling(
        resolved_sources=[made["r1"], made["r2"]],
        bindings=[_binding(made["ref1"]), _binding(made["ref2"])],
        station_set=ss, method="source_pixel")
    assert metrics.opens == 2
    src1 = {r.sample_id: r for r in rows if r.source_ref == made["ref1"]}
    src2 = {r.sample_id: r for r in rows if r.source_ref == made["ref2"]}
    assert all(r.raw_value == 111.0 for r in src1.values())
    assert all(r.raw_value == 222.0 for r in src2.values())
    # 分辨率差异体现在网格定位（col/row 不同分辨率）
    sid = next(iter(src1))
    assert src1[sid].col != src2[sid].col  # 各自网格


def test_band17_metadata_supplement_beyond_descriptor(rs_env, raster_factory):
    """S12：band_meta 覆盖外的波段直读真实头（不从 band 1 推断）。"""
    import asyncio

    made = {}

    async def make():
        ref, path = await raster_factory(
            "s12", name="s12_70band", bands=70, width=4, height=4,
            band_tags={17: {"units": "K", "scale": "0.01", "offset": "273.15",
                            "band_role": "temperature"}})
        made["ref"] = ref
        made["path"] = path

    asyncio.run(make())
    resolved = asyncio.run(_resolve("s12", made["ref"]))
    assert resolved.band_count == 70
    assert len(resolved.band_meta) == 64  # 元数据帽
    assert resolved.bands_truncated is True

    # 直接在打开的数据集上采集 band 17/65 facts
    from app.lib.geo_raster.env import open_raster, rasterio_env
    binding = _binding(made["ref"], bands=[17, 65])
    with rasterio_env():
        with open_raster(made["path"]) as ds:
            grid = reader_schedule.collect_source_grid(binding, resolved, ds,
                                                       "source_pixel")
    f17 = grid.band_facts[17]
    assert f17.unit == "K"
    assert f17.scale_declared and f17.scale == 0.01
    assert f17.offset_declared and f17.offset == 273.15
    assert f17.supplement_source == "band_meta"  # 17 ≤ 64 → band_meta 已覆盖
    f65 = grid.band_facts[65]
    assert f65.supplement_source == "supplemented"  # 65 > 64 → 补读
    assert f65.dtype  # 真实 dtype 来自直读
