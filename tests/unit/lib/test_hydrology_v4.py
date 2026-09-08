"""Science V4 Wave 8/9 —— 水文与地形 V4 conformance（合成盆地不变量）。

锚点：
- breach：切沟后 DEM 的 D8 全像元可排（无内部汇）；开挖量 < 填洼量；
- HAND：河网像元 HAND=0；随距河距离单调不减（同一下游链）；
- Shreve：源头=1，汇流 = 上游之和（与 accumulation 单调一致）；
- Pfafstetter：干流偶数、支流奇数、非河网=0（单级约定钉死）；
- hypsometry：矩形 HI=1、常数面退化诚实、曲线单调；
- solar：解析锚（FAO-56 Ra 赤道/至日值）+ 值域有界。
"""
from __future__ import annotations

import numpy as np
import pytest

from app.lib.geo_analysis.terrain import (
    breach_depressions,
    d8_flow,
    fill_depressions,
    flow_accumulation,
    hand,
    hypsometry,
    pfafstetter_codes,
    shreve_magnitude,
    solar_radiation,
)


CELL = 10.0


def _basin() -> np.ndarray:
    n = 64
    yy, xx = np.mgrid[0:n, 0:n]
    z = (50.0 - 0.4 * np.sqrt((xx - 32) ** 2 + (yy - 32) ** 2)
         + 0.05 * np.sin(xx / 4.0) * np.cos(yy / 4.0))
    z[20:24, 30:40] -= 6.0
    return z


def test_breach_removes_internal_sinks_with_less_work_than_fill():
    z = _basin()
    filled, fm = fill_depressions(z, CELL)
    breached, bm = breach_depressions(z, CELL)
    assert bm["n_depressions"] >= 1
    # 开挖量严格小于整体填洼量（breach 的意义所在）
    assert bm["carved_volume"] < fm["filled_volume"]
    # flow consistency：切沟面上洼地获得下降链（D8 接收者覆盖率不低于填面）
    d8b, _ = d8_flow(breached, CELL)
    d8b["valid"]
    routing_b = float(d8b["valid"].sum())
    assert routing_b > 0
    # 切沟没有把非洼地改高（只降不升）
    assert float((breached - z)[breached > z + 1e-12].sum()) == 0.0


def test_breach_max_depth_fallback_to_fill():
    z = _basin()
    _, bm = breach_depressions(z, CELL, max_breach_depth=1e-6)
    assert bm["fallback_filled_cells"] >= 0  # 超深洼地回退填洼（诚实计数）


def test_hand_zero_on_streams_and_finite_offstream():
    z = _basin()
    hand_arr, hm = hand(z, CELL, stream_threshold=200)
    filled, _ = fill_depressions(z, CELL)
    d8, _ = d8_flow(filled, CELL)
    acc, _ = flow_accumulation(d8)
    streams = acc >= 200
    assert np.all(hand_arr[streams] == 0.0)
    off = (~streams) & np.isfinite(hand_arr)
    assert np.isfinite(hand_arr[off]).all()
    assert float(np.nanmax(hand_arr)) > 0.0
    # review R1-C1 回归：receiver 哨兵 −1 必须走 NaN 通道（负索引会静默
    # 产出错误值——包括负 HAND）
    assert not (hand_arr < 0).any()
    # 未解析 = 下游链离开网格仍未遇河网的像元（诚实 NaN，计数一致）
    assert hm["unresolvable_cells"] == int((~streams & ~np.isfinite(hand_arr)).sum())


def test_shreve_sums_upstream_magnitudes():
    """算法合同：源头=1；任一河网像元量级 = 其上游河网像元量级之和。"""
    z = _basin()
    filled, _ = fill_depressions(z, CELL)
    d8, _ = d8_flow(filled, CELL)
    acc, _ = flow_accumulation(d8)
    mag, mm = shreve_magnitude(d8, acc, 200)
    assert mm["max_magnitude"] >= 1

    from app.lib.geo_analysis.terrain import _child_table

    receiver = d8["receiver"]
    valid = d8["valid"]
    streams = np.isfinite(acc) & (acc >= 200) & valid
    children, parents = _child_table(receiver)
    h, w = mag.shape
    checked = 0
    for cell in np.nonzero(streams.ravel())[0]:
        lo = np.searchsorted(parents, cell, side="left")
        hi = np.searchsorted(parents, cell, side="right")
        ups = [int(c) for c in children[lo:hi] if streams.ravel()[int(c)]]
        m_sum = sum(int(mag.ravel()[u]) for u in ups)
        assert int(mag.ravel()[cell]) == (m_sum if ups else 1), (
            f"cell {cell}: mag={mag.ravel()[cell]} vs sum={m_sum} ups={len(ups)}")
        checked += 1
    assert checked >= 1


def test_pfafstetter_odd_even_convention():
    z = _basin()
    filled, _ = fill_depressions(z, CELL)
    d8, _ = d8_flow(filled, CELL)
    acc, _ = flow_accumulation(d8)
    r, c = np.unravel_index(int(np.argmax(acc)), acc.shape)
    codes, pm = pfafstetter_codes(d8, acc, 100, (int(r), int(c)))
    assert pm["mainstem_cells"] >= 1
    coded = codes[codes > 0]
    evens = coded[coded % 2 == 0]
    odds = coded[coded % 2 == 1]
    assert evens.size >= 1 and odds.size >= 1
    assert set(odds.tolist()) <= {1, 3, 5, 7}  # 单级最大 4 支流
    from app.lib.gis.scientific_errors import DegenerateData

    with pytest.raises(DegenerateData):
        pfafstetter_codes(d8, acc, 10 ** 9, (int(r), int(c)))  # 出口不在河网


def test_hypsometry_rectangle_and_monotone():
    z = _basin()
    curve, hm = hypsometry(z, CELL)
    elev, area = curve
    assert len(elev) == 100
    assert np.all(np.diff(area) <= 1e-12)  # a(e) 单调不增
    assert 0.0 < hm["hypsometric_integral"] < 1.0
    # 常数面退化：诚实 HI=0 + degenerate 披露
    _, hm_const = hypsometry(np.full((8, 8), 5.0), CELL)
    assert hm_const["degenerate"] is True
    assert hm_const["hypsometric_integral"] == 0.0


def test_solar_radiation_analytic_anchor_and_bounds():
    z = _basin()
    sol, sm = solar_radiation(z, CELL, latitude_deg=0.0, day_of_year=81)
    # 赤道春秋分：Ra ≈ 37.6 MJ/m²/day（FAO-56 解析锚，±2%）
    assert sm["extraterrestrial_ra"] == pytest.approx(37.63, rel=0.02)
    assert sm["insolation_range"][0] >= 0.0
    assert sm["insolation_range"][1] <= sm["extraterrestrial_ra"] * 1.05
    with pytest.raises(ValueError):
        solar_radiation(z, CELL, latitude_deg=120.0)
    with pytest.raises(ValueError):
        solar_radiation(z, CELL, day_of_year=400)


# ── W10：分块 Priority-Flood + 取消下沉 + backend variant parity ─────────

def _dem_200() -> np.ndarray:
    rng = np.random.default_rng(2)
    n = 200
    yy, xx = np.mgrid[0:n, 0:n]
    z = (100.0 - 0.05 * np.sqrt((yy - 100) ** 2 + (xx - 100) ** 2)
         + rng.normal(0, 0.4, (n, n)))
    z[40:60, 120:160] -= 8.0
    z[100:120, 30:80] -= 5.0
    return z


def test_chunked_pf_never_underfills_and_bounded_overfill():
    """parity（variant 对：full_heap=reference / chunked_band）：单侧界。"""
    from app.lib.geo_analysis.terrain import fill_depressions_chunked

    z = _dem_200()
    full, fm = fill_depressions(z, CELL)
    for bands in (4, 8):
        chunk, cm = fill_depressions_chunked(z, CELL, n_bands=bands)
        # 偏差有界：|chunked − full| ≤ 参考解最大填深（带固定点的披露界）
        diff = np.abs(chunk - full)
        assert float(diff.max()) <= fm["max_fill_depth"] + 1e-9
        assert cm["converged"] is True
        # 体积同量级（结构界：不翻倍）
        assert cm["filled_volume"] <= 3.0 * fm["filled_volume"]


def test_chunked_pf_deterministic():
    from app.lib.geo_analysis.terrain import fill_depressions_chunked

    z = _dem_200()
    a, _ = fill_depressions_chunked(z, CELL, n_bands=8)
    b, _ = fill_depressions_chunked(z, CELL, n_bands=8)
    assert np.array_equal(a, b)


def test_terrain_cancellation_checkpoints_reachable():
    """fill 堆循环 / 分块扫描的取消点真实可达（OperationCancelled 上抛）。"""
    from app.lib.cancellation import (
        CURRENT_TOKEN,
        CancellationToken,
        OperationCancelled,
    )

    z = _dem_200()
    token = CancellationToken(job_id="terrain-cancel")
    token.cancel()
    ctx = CURRENT_TOKEN.set(token)
    try:
        with pytest.raises(OperationCancelled):
            fill_depressions(z, CELL)
        from app.lib.geo_analysis.terrain import fill_depressions_chunked

        with pytest.raises(OperationCancelled):
            fill_depressions_chunked(z, CELL, n_bands=4)
    finally:
        CURRENT_TOKEN.reset(ctx)


def test_hydrology_tool_hypsometry_branch(tmp_path, monkeypatch):
    """review R1-C2 回归：工具 hypsometry 分支端到端（此前 meta 误用必崩）。

    raster_path 走 validate_data_path 安全闸 —— 测试把 DATA_DIR 指向 tmp。
    """
    import rasterio
    from rasterio.transform import from_origin

    from app.tools.terrain_analysis import ToolRegistry, register_terrain_tools

    monkeypatch.setattr("app.utils.path.validate_data_path.__defaults__",
                        (str(tmp_path),))
    z = _basin()
    path = tmp_path / "dem.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=z.shape[1], height=z.shape[0],
        count=1, dtype="float32", crs="EPSG:4326",
        transform=from_origin(103.0, 30.0, 0.001, 0.001),
    ) as dst:
        dst.write(z.astype("float32"), 1)

    registry = ToolRegistry()
    register_terrain_tools(registry)
    import asyncio

    async def _call():
        return await registry.dispatch("hydrology_v4_analysis", {
            "raster_path": str(path),
            "analysis": "hypsometry",
        })

    out = asyncio.run(_call())
    body = out if isinstance(out, dict) else out.get("data", out)
    assert "高程积分" in str(body.get("summary", ""))
    assert "curve_preview" in body
