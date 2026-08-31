"""Large Synthetic Raster Test（Runtime V3 §55，ADR-0089）。

生成一个 ~7200×7200（≈52M 像元，float32 ≈ 207MB）的分块写入合成栅格
（绝不一次构造巨大 Python ndarray），验证：

- 计算器 / 指数 / 变化检测在**有界窗口**设计下完成（不是靠 wall clock，
  而是锁死：源读取全部带 window、最大读取窗口 ≤ 预算推导边长²、
  计算过程零整幅 read）；
- 产物带 overview（瓦片服务低zoom降采样读受益，§29）；
- 尺寸按本机资源自动收缩（psutil 可用时按可用内存缩放）。
"""
import os
import uuid

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_analysis.raster_change import detect_raster_change
from app.lib.geo_analysis.raster_grid import window_side_from_budget
from app.lib.geo_analysis.raster_math import raster_calculator
from app.lib.geo_analysis.raster_windowed import windowed_band_index

TD = "data/tmp_large_raster"
SIDE = 7200  # ≈52M cells；资源不足时按 _auto_side() 收缩


def _auto_side() -> int:
    """按可用内存自动调整边长（保守：输入+输出+窗口工作集 ≤ 可用的 1/4）。"""
    try:
        import psutil

        avail = psutil.virtual_memory().available
        # 输入 float32 (4B) + 输出 (4B) + 窗口工作集（~64B/cell × window²）
        # 粗算：每像元全程占用 ≈ 16B（不含窗口，窗口另计）。
        budget_cells = avail / 4 / 16
        side = int(min(SIDE, budget_cells ** 0.5))
        return max(2048, side)
    except Exception:  # noqa: BLE001
        return 3600


def _write_windowed(path: str, side: int, *, patch: bool = False) -> None:
    """分块写合成栅格：棋盘格基底 + 中央变化块（change 检测用）。"""
    block = 1024
    with rasterio.open(
        path, "w", driver="GTiff", height=side, width=side, count=2,
        dtype="float32", crs="EPSG:32650",
        transform=from_origin(0, side * 10.0, 10.0, 10.0), nodata=-9999.0,
        tiled=True, blockxsize=256, blockysize=256, compress="lzw",
    ) as dst:
        for row0 in range(0, side, block):
            h = min(block, side - row0)
            for col0 in range(0, side, block):
                w = min(block, side - col0)
                rng = np.random.default_rng((row0 * 31 + col0) % (2**32))
                red = rng.uniform(0, 500, (h, w)).astype("float32")
                nir = rng.uniform(0, 1000, (h, w)).astype("float32")
                if patch:
                    # 中央 1200×1200 块整体抬升（变化检测金块）
                    if (row0 + h > side // 2 - 600 and row0 < side // 2 + 600
                            and col0 + w > side // 2 - 600 and col0 < side // 2 + 600):
                        nir = nir + 800.0
                dst.write(red, 1, window=rasterio.windows.Window(col0, row0, w, h))
                dst.write(nir, 2, window=rasterio.windows.Window(col0, row0, w, h))


@pytest.fixture(scope="module")
def large_pair():
    side = _auto_side()
    os.makedirs(TD, exist_ok=True)
    pa = os.path.join(TD, f"large_a_{uuid.uuid4().hex[:6]}.tif")
    pb = os.path.join(TD, f"large_b_{uuid.uuid4().hex[:6]}.tif")
    _write_windowed(pa, side)
    _write_windowed(pb, side, patch=True)
    yield pa, pb, side
    for p in (pa, pb):
        os.path.exists(p) and os.remove(p)
    for f in os.listdir(TD):
        os.remove(os.path.join(TD, f))


def test_large_raster_calculator_bounded_windows(large_pair, monkeypatch):
    pa, pb, side = large_pair
    from tests.unit.test_raster_runtime_v3 import _ReadSpy

    spy = _ReadSpy()
    real_open = rasterio.open
    monkeypatch.setattr("rasterio.open", spy)
    try:
        r = raster_calculator(pa, pb, expression="(B - A) / (B + A)")
    finally:
        monkeypatch.setattr("rasterio.open", real_open)

    # ── 性能断言（§53）：不靠 wall clock ─────────────────────────
    # 1) 源绝无整幅 read
    assert spy.whole_reads_of(pa, pb) == []
    # 2) 任何单次 read 解码像元 ≤ 窗口预算边长²（预算推导，§13）
    max_side = window_side_from_budget()
    assert spy.max_read_pixels <= max_side * max_side
    # 3) 产物网格 = A
    assert r["descriptor"]["width"] == side and r["descriptor"]["height"] == side
    # 4) 有 nodata 感知的统计（随机数据全有效）
    assert r["pixel_count"] == side * side
    # 5) 产物带 overview（低zoom瓦片读不再整幅解码）
    with real_open(r["output_path"]) as out:
        assert out.overviews(1), "expected overviews on large output"
    os.path.exists(r["output_path"]) and os.remove(r["output_path"])


def test_large_raster_windowed_index_bounded(large_pair, monkeypatch):
    pa, _, side = large_pair
    from tests.unit.test_raster_runtime_v3 import _ReadSpy

    spy = _ReadSpy()
    real_open = rasterio.open
    monkeypatch.setattr("rasterio.open", spy)
    try:
        res = windowed_band_index(pa, "ndvi", band_map={"red": 1, "nir": 2})
    finally:
        monkeypatch.setattr("rasterio.open", real_open)

    assert spy.whole_reads_of(pa) == []
    assert res["stats"]["valid_pixel_count"] == side * side
    # NDVI ∈ [-1, 1]
    assert -1.0 <= res["stats"]["min"] and res["stats"]["max"] <= 1.0
    with real_open(res["output_path"]) as out:
        assert out.overviews(1)
    os.path.exists(res["output_path"]) and os.remove(res["output_path"])


def test_large_raster_change_detection_bounded(large_pair, monkeypatch):
    pa, pb, side = large_pair
    from tests.unit.test_raster_runtime_v3 import _ReadSpy

    spy = _ReadSpy()
    real_open = rasterio.open
    monkeypatch.setattr("rasterio.open", spy)
    try:
        # band=2（NIR）：合成栅格的中央抬升块在 NIR 波段
        r = detect_raster_change(pa, pb, method="difference", threshold=400.0, band=2)
    finally:
        monkeypatch.setattr("rasterio.open", real_open)

    assert spy.whole_reads_of(pa, pb) == []
    # 中央抬升块被检出为变化（宽松断言：变化占比 > 1%）
    assert r["stats"]["changed_pixels"] > 0.01 * side * side
    assert r["alignment"]["status"] == "aligned"
    with real_open(r["output_path"]) as out:
        assert out.overviews(1)
    os.path.exists(r["output_path"]) and os.remove(r["output_path"])
