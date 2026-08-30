"""Raster & Remote Sensing Runtime V3 —— 数值/对抗/性能契约测试（ADR-0089）。

覆盖：
- G1 计算器 golden（A+B / A-B / A/B）与不aligned B 的对齐执行；
- G2 nodata 保持；
- 对抗：全 nodata、整数溢出、除零、空交集、损坏栅格、磁盘失败、取消清理、
  大栅格元数据只读头；
- whole-read 守卫（§54）：新窗口化算法绝不调用无 window/out_shape 的
  ``src.read()`` 整幅形态；
- 内容指纹（§31/§32）：写者摘要零重开、同内容同指纹、变内容变指纹；
- 复用 miss 条件（§34）；
- descriptor V2（§37）与 raster 契约验证（§46）。
"""
import os
import uuid
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_bounds, from_origin

from app.lib.cancellation import CancellationToken, OperationCancelled, use_token
from app.lib.geo_analysis.raster_grid import RasterAlignmentError
from app.lib.geo_analysis.raster_math import raster_calculator
from app.lib.geo_analysis.raster_change import detect_raster_change
from app.lib.geo_analysis.raster_windowed import windowed_band_index
from app.schemas.raster_spec import raster_content_fingerprint

TD = "data/tmp_v3_tests"


# ── 工具 ─────────────────────────────────────────────────────────────

def _td():
    os.makedirs(TD, exist_ok=True)
    return TD


def _write(name, data, *, crs="EPSG:4326", transform=None, nodata=None, count=None):
    path = os.path.join(TD, name)
    if count and count > 1:
        h, w = data.shape[1:]
        with rasterio.open(
            path, "w", driver="GTiff", height=h, width=w, count=count,
            dtype=data.dtype, crs=crs,
            transform=transform or from_origin(0, h, 1, 1), nodata=nodata,
        ) as dst:
            dst.write(data)
        return path
    h, w = data.shape
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=1,
        dtype=data.dtype, crs=crs,
        transform=transform or from_origin(0, h, 1, 1), nodata=nodata,
        tiled=True, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(data, 1)
    return path


class _ReadSpy:
    """rasterio.open 包装：记录每次 dataset.read 的窗口形态与解码像元数。

    whole-read 守卫用：任何没有 ``window=`` 也没有 ``out_shape=`` 的 read
    （整幅形态）都会被记为违规；``max_read_pixels`` 记录单次 read 实际
    解码的像元数（窗口/降采样形状，而不是数据集全尺寸）。
    """

    def __init__(self):
        self.calls = []          # (path, windowed, out_shape, decoded_pixels)
        self._real_open = rasterio.open

    def __call__(self, path, mode="r", **kwargs):
        ds = self._real_open(path, mode, **kwargs)
        if mode != "r":
            return ds
        spy = self
        orig_read = ds.read
        key = str(path)

        def read(*a, **kw):
            windowed = kw.get("window") is not None
            shape = kw.get("out_shape")
            if windowed:
                win = kw["window"]
                decoded = int(win.height) * int(win.width)
            elif shape is not None:
                dims = shape[1:] if len(shape) == 3 else shape
                decoded = int(dims[0]) * int(dims[1])
            else:
                decoded = ds.height * ds.width
            spy.calls.append((key, windowed, shape is not None, decoded))
            return orig_read(*a, **kw)

        ds.read = read  # type: ignore[method-assign]
        return ds

    def whole_reads_of(self, *paths):
        """整幅 read（无 window/out_shape）——只统计给定路径（源）。"""
        keys = {str(p) for p in paths}
        return [c for c in self.calls if c[0] in keys and not c[1] and not c[2]]

    @property
    def whole_reads(self):
        return [c for c in self.calls if not c[1] and not c[2]]

    @property
    def max_read_pixels(self):
        return max((c[3] for c in self.calls), default=0)


@pytest.fixture()
def read_spy(monkeypatch):
    spy = _ReadSpy()
    monkeypatch.setattr("rasterio.open", spy)
    # raster_math/raster_windowed/raster_change 直接 `import rasterio` 后用
    # rasterio.open(...)，同模块对象属性替换即可生效。
    yield spy


# ── G1 golden + 窗口化 ──────────────────────────────────────────────

def test_g1_calculator_golden():
    td = _td()
    pa = _write(f"a_{uuid.uuid4().hex[:6]}.tif", np.array([[1, 2], [3, 4]], dtype="float32"))
    pb = _write(f"b_{uuid.uuid4().hex[:6]}.tif", np.array([[5, 6], [7, 8]], dtype="float32"))
    try:
        r = raster_calculator(pa, pb, expression="A + B")
        with rasterio.open(r["output_path"]) as out:
            np.testing.assert_array_equal(out.read(1), [[6, 8], [10, 12]])
        r = raster_calculator(pa, pb, expression="A - B")
        with rasterio.open(r["output_path"]) as out:
            np.testing.assert_array_equal(out.read(1), [[-4, -4], [-4, -4]])
        r = raster_calculator(pa, pb, expression="A / B")
        with rasterio.open(r["output_path"]) as out:
            np.testing.assert_allclose(out.read(1), [[0.2, 2 / 6], [3 / 7, 0.5]], atol=1e-7)
    finally:
        for f in os.listdir(td):
            if f.endswith(".tif") and ("_calc" in f or f.startswith(("a_", "b_"))):
                os.remove(os.path.join(td, f))


def test_g2_nodata_mask_preserved():
    td = _td()
    a = np.array([[1.0, -9999.0], [3.0, 4.0]], dtype="float32")
    pa = _write(f"nd_{uuid.uuid4().hex[:6]}.tif", a, nodata=-9999.0)
    try:
        r = raster_calculator(pa, expression="A * B", constant=2.0)
        with rasterio.open(r["output_path"]) as out:
            arr = out.read(1)
            assert out.nodata == -9999.0
            assert arr[0, 1] == -9999.0  # nodata 保持
            np.testing.assert_allclose(arr[0, 0], 2.0)
        assert r["pixel_count"] == 3
    finally:
        os.remove(pa)
        os.remove(pa.replace(".tif", "_calc.tif"))


def test_unaligned_b_resamples_onto_a_grid(read_spy):
    """G4：B 20m → A 10m；输出网格 = A；窗口读（无整幅 read）。"""
    td = _td()
    pa = _write(
        f"g4a_{uuid.uuid4().hex[:6]}.tif",
        np.zeros((8, 8), dtype="float32"),
        crs="EPSG:32650", transform=from_bounds(0, 0, 80, 80, 8, 8),
    )
    pb = _write(
        f"g4b_{uuid.uuid4().hex[:6]}.tif",
        np.full((4, 4), 7.0, dtype="float32"),
        crs="EPSG:32650", transform=from_bounds(0, 0, 80, 80, 4, 4),
    )
    try:
        r = raster_calculator(pa, pb, expression="A + B")
        assert r["alignment"]["status"] == "needs_resample"
        assert r["alignment"]["resampled"] is True
        with rasterio.open(r["output_path"]) as out, rasterio.open(pa) as a:
            assert (out.width, out.height) == (a.width, a.height)
            assert out.transform.almost_equals(a.transform)
        # whole-read 守卫（§54）：源（A/B）任何 read 都带 window 或 out_shape
        assert read_spy.whole_reads_of(pa, pb) == []
    finally:
        os.remove(pa); os.remove(pb); os.remove(pa.replace(".tif", "_calc.tif"))


def test_crs_mismatch_auto_aligns(read_spy):
    """G5：跨 CRS 不静默逐像元 —— 自动 warp 到 A 网格。"""
    td = _td()
    pa = _write(
        f"g5a_{uuid.uuid4().hex[:6]}.tif",
        np.zeros((20, 20), dtype="float32"),
        crs="EPSG:3857", transform=from_bounds(12936000, 4855000, 12936800, 4865000, 20, 20),
    )
    pb = _write(
        f"g5b_{uuid.uuid4().hex[:6]}.tif",
        np.full((20, 20), 3.0, dtype="float32"),
        crs="EPSG:4326",
        transform=from_bounds(116.206, 39.926, 116.278, 39.986, 20, 20),
    )
    try:
        r = raster_calculator(pa, pb, expression="A + B")
        assert r["alignment"]["status"] == "needs_reproject"
        assert r["alignment"]["reprojected"] is True
        with rasterio.open(r["output_path"]) as out:
            assert out.crs.to_string() == "EPSG:3857"  # A 的 CRS
        assert read_spy.whole_reads_of(pa, pb) == []
    finally:
        os.remove(pa); os.remove(pb); os.remove(pa.replace(".tif", "_calc.tif"))


# ── 对抗（§52）──────────────────────────────────────────────────────

def test_adversarial_all_nodata_legitimate():
    """D：全 nodata 输入合法处理（统计为空，不崩、不产假值）。"""
    td = _td()
    pa = _write(f"allnd_{uuid.uuid4().hex[:6]}.tif",
                np.full((4, 4), -9999.0, dtype="float32"), nodata=-9999.0)
    try:
        r = raster_calculator(pa, expression="A * B", constant=2.0)
        assert r["pixel_count"] == 0
        assert r["quality_evidence"]["nodata_pixel_count"] == 16
    finally:
        os.remove(pa); os.remove(pa.replace(".tif", "_calc.tif"))


def test_adversarial_uint8_overflow_no_wrap():
    """E：uint8 A+B 中间运算不能静默溢出（float64 提升）。"""
    td = _td()
    pa = _write(f"u8_{uuid.uuid4().hex[:6]}.tif", np.full((4, 4), 200, dtype="uint8"))
    pb = _write(f"u8b_{uuid.uuid4().hex[:6]}.tif", np.full((4, 4), 100, dtype="uint8"))
    try:
        r = raster_calculator(pa, pb, expression="A + B")
        # 300 正确（uint8 会回绕成 44）
        assert r["min"] == pytest.approx(300.0)
        assert r["max"] == pytest.approx(300.0)
    finally:
        os.remove(pa); os.remove(pb); os.remove(pa.replace(".tif", "_calc.tif"))


def test_adversarial_divide_zero_no_inf():
    """F：除零 → nodata，绝不落盘 inf。"""
    td = _td()
    pa = _write(f"dz_{uuid.uuid4().hex[:6]}.tif", np.ones((4, 4), dtype="float32"))
    pb = _write(f"dzb_{uuid.uuid4().hex[:6]}.tif", np.zeros((4, 4), dtype="float32"))
    try:
        r = raster_calculator(pa, pb, expression="A / B")
        with rasterio.open(r["output_path"]) as out:
            arr = out.read(1)
        assert np.isfinite(arr).all() or (arr == 0).all()
        assert r["pixel_count"] == 0  # 全部无效（inf → nodata → 0）
    finally:
        os.remove(pa); os.remove(pb); os.remove(pa.replace(".tif", "_calc.tif"))


def test_adversarial_empty_overlap_structured_reject():
    """C：空交集 → RasterAlignmentError，不产空垃圾栅格。"""
    td = _td()
    pa = _write(f"eoa_{uuid.uuid4().hex[:6]}.tif", np.ones((4, 4), dtype="float32"),
                transform=from_bounds(0, 0, 4, 4, 4, 4))
    pb = _write(f"eob_{uuid.uuid4().hex[:6]}.tif", np.ones((4, 4), dtype="float32"),
                transform=from_bounds(100, 100, 104, 104, 4, 4))
    try:
        with pytest.raises(RasterAlignmentError):
            raster_calculator(pa, pb, expression="A + B")
        assert not os.path.exists(pa.replace(".tif", "_calc.tif"))
        # 变化检测同样拒绝
        with pytest.raises(RasterAlignmentError):
            detect_raster_change(pa, pb)
    finally:
        os.remove(pa); os.remove(pb)


def test_adversarial_corrupt_raster_structured_failure():
    """G：损坏栅格 → rasterio 异常上抛（结构化），不产半文件。"""
    td = _td()
    bad = os.path.join(td, f"corrupt_{uuid.uuid4().hex[:6]}.tif")
    with open(bad, "wb") as f:
        f.write(b"not a real geotiff at all")
    try:
        with pytest.raises(Exception):
            raster_calculator(bad, expression="A * B", constant=1.0)
    finally:
        os.remove(bad)


def test_adversarial_disk_failure_no_output(monkeypatch):
    """H：写失败 → 异常上抛，不留看似有效的产物。"""
    td = _td()
    pa = _write(f"disk_{uuid.uuid4().hex[:6]}.tif", np.ones((4, 4), dtype="float32"))
    out = pa.replace(".tif", "_calc.tif")

    real_atomic = "app.lib.geo_analysis.raster_windowed.atomic_output"

    def _failing_atomic(final_path, **kw):
        import app.lib.artifacts as artifacts

        ctx = artifacts.atomic_output(final_path, **kw)
        tmp = ctx.__enter__()
        with open(tmp, "wb") as f:
            f.write(b"partial")
        raise OSError("simulated disk failure mid-write")

    monkeypatch.setattr(real_atomic, _failing_atomic)
    try:
        with pytest.raises(OSError):
            raster_calculator(pa, expression="A * B", constant=1.0)
        assert not os.path.exists(out)  # 半文件绝不 finalize
    finally:
        monkeypatch.setattr(real_atomic, None)
        import app.lib.geo_analysis.raster_windowed as rw
        monkeypatch.setattr(rw, "atomic_output", __import__(
            "app.lib.artifacts", fromlist=["atomic_output"]).atomic_output)
        os.path.exists(pa) and os.remove(pa)


def test_adversarial_cancellation_cleans_temp(monkeypatch):
    """I：取消 → OperationCancelled，临时 .part 文件清理、无最终产物。"""
    td = _td()
    pa = _write(f"cancel_{uuid.uuid4().hex[:6]}.tif",
                np.ones((2048, 2048), dtype="float32"))
    out = pa.replace(".tif", "_calc.tif")
    # 强制小窗口 → 多个窗口边界可触发取消
    monkeypatch.setenv("RASTER_PROCESSING_MEMORY_MB", "1")
    from app.core.config import settings
    settings.RASTER_PROCESSING_MEMORY_MB = 1
    token = CancellationToken(job_id="test-cancel")
    token.cancel("user cancelled")
    try:
        with use_token(token):
            with pytest.raises(OperationCancelled):
                raster_calculator(pa, expression="A * B", constant=1.0)
        assert not os.path.exists(out)
        parts = [f for f in os.listdir(td) if ".part-" in f]
        assert parts == [] or all(not f.startswith("cancel_") for f in parts)
    finally:
        settings.RASTER_PROCESSING_MEMORY_MB = 256
        os.remove(pa)


def test_adversarial_huge_metadata_header_only(read_spy):
    """A：大栅格元数据操作（descriptor/指纹）只读头 + 有界降采样。"""
    from app.schemas.raster_spec import inspect_raster_artifact

    td = _td()
    pa = _write(f"huge_{uuid.uuid4().hex[:6]}.tif",
                np.zeros((512, 512), dtype="float32"))
    try:
        desc = inspect_raster_artifact(pa)
        assert desc.width == 512
        # inspect 的降采样读有 out_shape（有界）；绝无整幅无参 read
        assert read_spy.whole_reads == []
        # 指纹同样有界
        raster_content_fingerprint(pa)
        assert read_spy.whole_reads == []
        # max read 解码面积有界（≤ 2048² 降采样约束；512² 源一次整读也不超界）
        assert read_spy.max_read_pixels <= 2048 * 2048
    finally:
        os.remove(pa)


# ── 变化检测（P5）───────────────────────────────────────────────────

def test_change_detection_g6_golden():
    td = _td()
    t1 = np.array([[1.0, 2.0], [3.0, 4.0]], dtype="float32")
    t2 = np.array([[1.0, 2.0], [3.0, 10.0]], dtype="float32")
    pa = _write(f"t1_{uuid.uuid4().hex[:6]}.tif", t1, nodata=-9999.0)
    pb = _write(f"t2_{uuid.uuid4().hex[:6]}.tif", t2, nodata=-9999.0)
    try:
        r = detect_raster_change(pa, pb, method="difference", threshold=2.0)
        with rasterio.open(r["output_path"]) as out:
            arr = out.read(1)
        assert arr.dtype == np.uint8
        assert arr[1, 1] == 1 and arr.sum() == 1
        assert r["stats"]["changed_pixels"] == 1
        assert r["stats"]["change_ratio"] == pytest.approx(0.25)
        assert r["semantic_type"] == "raster_change_surface"
    finally:
        os.remove(pa); os.remove(pb); os.remove(pa.replace(".tif", "_change.tif"))


def test_change_detection_nodata_propagates():
    td = _td()
    t1 = np.array([[1.0, -9999.0], [3.0, 4.0]], dtype="float32")
    t2 = np.array([[1.0, 2.0], [3.0, 4.0]], dtype="float32")
    pa = _write(f"ndc1_{uuid.uuid4().hex[:6]}.tif", t1, nodata=-9999.0)
    pb = _write(f"ndc2_{uuid.uuid4().hex[:6]}.tif", t2, nodata=-9999.0)
    try:
        r = detect_raster_change(pa, pb, method="difference")
        with rasterio.open(r["output_path"]) as out:
            arr = out.read(1)
        assert np.isnan(arr[0, 1])  # T1 nodata → 输出 nodata
        assert r["stats"]["valid_pixel_count"] == 3
        assert r["stats"]["nodata_pixel_count"] == 1
    finally:
        os.remove(pa); os.remove(pb); os.remove(pa.replace(".tif", "_change.tif"))


def test_change_detection_normalized_zero_denominator_nodata():
    """零分母 → nodata（与 NDVI golden 同语义），不产 inf。"""
    td = _td()
    t1 = np.array([[5.0, 0.0]], dtype="float32")
    t2 = np.array([[15.0, 0.0]], dtype="float32")
    pa = _write(f"nzc1_{uuid.uuid4().hex[:6]}.tif", t1)
    pb = _write(f"nzc2_{uuid.uuid4().hex[:6]}.tif", t2)
    try:
        r = detect_raster_change(pa, pb, method="normalized_difference")
        with rasterio.open(r["output_path"]) as out:
            arr = out.read(1)
        assert np.isnan(arr[0, 1])  # 0+0 分母 → nodata（不产 inf/0）
        np.testing.assert_allclose(arr[0, 0], 0.5, atol=1e-6)  # (15−5)/20
    finally:
        os.remove(pa); os.remove(pb); os.remove(pa.replace(".tif", "_change.tif"))


def test_change_detection_invalid_method_and_threshold():
    td = _td()
    pa = _write(f"iv_{uuid.uuid4().hex[:6]}.tif", np.ones((2, 2), dtype="float32"))
    pb = _write(f"ivb_{uuid.uuid4().hex[:6]}.tif", np.ones((2, 2), dtype="float32"))
    try:
        with pytest.raises(ValueError):
            detect_raster_change(pa, pb, method="magic")
        with pytest.raises(ValueError):
            detect_raster_change(pa, pb, threshold=-1.0)
        with pytest.raises(ValueError):
            detect_raster_change(pa, pb, threshold=0.0)
    finally:
        os.remove(pa); os.remove(pb)


# ── 指纹 / descriptor / 复用（P8/P10）───────────────────────────────

def test_writer_descriptor_no_reopen(read_spy):
    """§37：写者直接产 descriptor —— 不重开输出文件（read 计数不含输出路径）。"""
    td = _td()
    pa = _write(f"desc_{uuid.uuid4().hex[:6]}.tif",
                np.arange(16, dtype="float32").reshape(4, 4))
    try:
        r = raster_calculator(pa, expression="A * B", constant=2.0)
        d = r["descriptor"]
        assert d["width"] == 4 and d["height"] == 4
        assert d["transform"] is not None and len(d["transform"]) == 6
        assert d["resolution_x"] == 1.0
        assert d["driver"] == "GTiff"
        # 计算器只读过源（窗口化），从未整幅
        assert read_spy.whole_reads_of(pa) == []
        assert r["quality_evidence"]["output_crs"] == "EPSG:4326"
    finally:
        os.remove(pa); os.remove(pa.replace(".tif", "_calc.tif"))


def test_content_fingerprint_stability():
    """§31/§32：同内容（不同路径/mtime）同指纹；变内容变指纹；不 hash mtime。"""
    td = _td()
    data = np.arange(16, dtype="float32").reshape(4, 4)
    p1 = _write(f"fp1_{uuid.uuid4().hex[:6]}.tif", data)
    p2 = _write(f"fp2_{uuid.uuid4().hex[:6]}.tif", data.copy())
    try:
        fp1, fp2 = raster_content_fingerprint(p1), raster_content_fingerprint(p2)
        assert fp1 == fp2  # 内容相同（路径无关）
        # 播 mtime（内容不变）→ 指纹不变（§32：mtime 不是内容）
        os.utime(p1, (1_000_000, 1_000_000))
        assert raster_content_fingerprint(p1) == fp1
        # 改一个像元 → 指纹变
        with rasterio.open(p1, "r+") as src:
            src.write(np.array([[999.0]], dtype="float32"), 1, window=rasterio.windows.Window(0, 0, 1, 1))
        assert raster_content_fingerprint(p1) != fp1
        # 缺失文件 → None（降级，不抛）
        assert raster_content_fingerprint(os.path.join(td, "nope.tif")) is None
    finally:
        os.remove(p1); os.remove(p2)


def test_writer_digest_deterministic_and_content_sensitive():
    """写者内容摘要：同输入同摘要；参数变（表达式变）摘要变。"""
    td = _td()
    pa = _write(f"dg_{uuid.uuid4().hex[:6]}.tif",
                np.arange(16, dtype="float32").reshape(4, 4))
    try:
        r1 = raster_calculator(pa, expression="A * 2")
        r2 = raster_calculator(pa, expression="A * 2")
        r3 = raster_calculator(pa, expression="A * 3")
        assert r1["content_fingerprint"] == r2["content_fingerprint"]
        assert r1["content_fingerprint"] != r3["content_fingerprint"]
    finally:
        os.remove(pa)
        for suffix in ("_calc.tif",):
            p = pa.replace(".tif", suffix)
            os.path.exists(p) and os.remove(p)


def test_analysis_reuse_miss_conditions():
    """§34 miss 条件：不同表达式 / 不同内容指纹 → 不同键或指纹复核失败。"""
    from app.lib.gis.analysis_reuse import (
        compute_analysis_key,
        snapshot_raster_fingerprints,
    )

    td = _td()
    pa = _write(f"ru_{uuid.uuid4().hex[:6]}.tif",
                np.arange(16, dtype="float32").reshape(4, 4))
    try:
        k1 = compute_analysis_key("raster_calculator", {"raster_a": pa, "expression": "A * 2"})
        k2 = compute_analysis_key("raster_calculator", {"raster_a": pa, "expression": "A * 3"})
        assert k1 != k2  # 不同表达式 → 不同 key
        fp1 = snapshot_raster_fingerprints({"raster_a": pa})
        assert pa in fp1
        # 内容重写 → 指纹变（复用复核将 miss）
        with rasterio.open(pa, "r+") as src:
            src.write(np.array([[42.0]], dtype="float32"), 1, window=rasterio.windows.Window(0, 0, 1, 1))
        fp2 = snapshot_raster_fingerprints({"raster_a": pa})
        assert fp2[pa] != fp1[pa]
        # 非栅格参数不进指纹表
        assert snapshot_raster_fingerprints({"foo": "bar.tif"}) == {}
    finally:
        os.remove(pa)


# ── 光谱指数窗口化（P4）─────────────────────────────────────────────

def test_windowed_index_matches_full_array_reference(read_spy):
    """窗口化指数 = 全数组参考实现（逐位）；whole-read 守卫。"""
    td = _td()
    rng = np.random.default_rng(42)
    red = rng.uniform(0, 1000, (600, 500)).astype("float32")
    nir = rng.uniform(0, 1000, (600, 500)).astype("float32")
    p = _write(f"idx_{uuid.uuid4().hex[:6]}.tif", np.stack([red, nir]), count=2)
    try:
        res = windowed_band_index(p, "ndvi", band_map={"red": 1, "nir": 2})
        assert read_spy.whole_reads_of(p) == []  # 源绝无整幅 read（输出核验除外）
        with rasterio.open(res["output_path"]) as out:
            got = out.read(1)
        # 参考：band_math 的公式 truth
        from app.services.rs.band_math import compute_index_array

        ref = compute_index_array("ndvi", red=red.astype(float), nir=nir.astype(float))
        ref32 = np.where(np.isnan(ref), -9999.0, ref).astype("float32")
        np.testing.assert_allclose(got, ref32, atol=1e-5)
        assert res["stats"]["valid_pixel_count"] > 0
    finally:
        os.remove(p); os.remove(p.replace(".tif", "_ndvi.tif"))


def test_windowed_index_evi_dn_normalization_global():
    """EVI 的 DN→反射率判定是全局一次（不逐窗口抖动）。"""
    td = _td()
    dn = np.full((300, 300), 3000.0, dtype="float32")  # DN 尺度 (>1.5)
    bands = np.stack([dn * 0.5, dn, dn * 1.2])  # blue, red, nir
    h, w = bands.shape[1:]
    p = os.path.join(td, f"evi_{uuid.uuid4().hex[:6]}.tif")
    with rasterio.open(
        p, "w", driver="GTiff", height=h, width=w, count=3, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0, h, 1, 1),
    ) as dst:
        dst.write(bands)
    try:
        res = windowed_band_index(p, "evi", band_map={"blue": 1, "red": 2, "nir": 3})
        with rasterio.open(res["output_path"]) as out:
            arr = out.read(1)
        from app.services.rs.band_math import compute_index_array

        ref = compute_index_array(
            "evi",
            blue=(dn * 0.5).astype(float), red=dn.astype(float), nir=(dn * 1.2).astype(float),
        )
        ref32 = np.where(np.isnan(ref), -9999.0, ref).astype("float32")
        np.testing.assert_allclose(arr, ref32, atol=1e-4)
    finally:
        os.remove(p); os.remove(p.replace(".tif", "_evi.tif"))


# ── 契约验证（P13）──────────────────────────────────────────────────

def test_contract_validation_raster_evidence():
    from app.lib.gis.contract_validation import (
        C_CRS_UNDECLARED,
        C_RASTER_GRID_EVIDENCE_INCOMPLETE,
        C_RASTER_GRID_EVIDENCE_MISSING,
        validate_output_contract,
    )
    from app.lib.gis.dataset_profile import DatasetProfile

    # 声明 raster 输出 + 有完整 raster 证据 → 无栅格类 finding
    profile_full = DatasetProfile.from_raster_descriptor(
        {"width": 4, "height": 4, "band_count": 1, "crs": "EPSG:4326",
         "dtype": "float32", "nodata": -9999.0, "resolution_x": 1.0,
         "bounds": [0, 0, 4, 4]}
    )
    findings = validate_output_contract(["raster_surface"], profile_full)
    codes = {f.code for f in findings}
    assert C_RASTER_GRID_EVIDENCE_MISSING not in codes
    assert C_RASTER_GRID_EVIDENCE_INCOMPLETE not in codes

    # raster 声明 + 无 raster 证据 → MISSING warning
    profile_bare = DatasetProfile(source="ref_descriptor", geometry_types=["point"])
    findings = validate_output_contract(["raster_surface"], profile_bare)
    codes = {f.code for f in findings}
    assert C_RASTER_GRID_EVIDENCE_MISSING in codes
    assert any(f.severity == "warning" for f in findings)

    # raster 证据存在但宽高未知 → INCOMPLETE warning
    profile_partial = DatasetProfile.from_raster_descriptor({"band_count": 1})
    findings = validate_output_contract(["raster_surface"], profile_partial)
    codes = {f.code for f in findings}
    assert C_RASTER_GRID_EVIDENCE_INCOMPLETE in codes
    assert C_CRS_UNDECLARED in codes


def test_raster_profile_geometry_kind():
    from app.lib.gis.dataset_profile import DatasetProfile

    profile = DatasetProfile.from_raster_descriptor(
        {"width": 4, "height": 4, "band_count": 1, "crs": "EPSG:4326", "dtype": "float32"}
    )
    assert profile.geometry_kind == "raster"
    assert profile.raster is not None and profile.raster.width == 4
    assert profile.crs == "EPSG:4326"


# ── 注册表诚实（P13/§48-49）─────────────────────────────────────────

def test_registry_raster_matrix_honest():
    """Capability → Algorithm → Tool 矩阵（§48）：栅格族 native 声明的工具
    必须真实注册，且栅格变化/波段代数不再挂错 capability。"""
    from app.tools import init_tools
    from app.tools.registry import ToolRegistry
    from app.lib.gis.algorithm_registry import get_algorithm_registry
    from app.lib.gis.capability_registry import get_capability_registry

    reg = ToolRegistry()
    init_tools(reg)
    tool_names = set(reg.list_tools())

    algos = get_algorithm_registry()
    caps = get_capability_registry()
    matrix = [
        ("raster_change_detection", "remote.change.raster", "detect_raster_change"),
        ("band_math", "raster.algebra", "raster_calculator"),
        ("raster_reclassify", "raster.reclassify.rule", "raster_reclassify"),
        ("raster_resample", "raster.resample.grid", "raster_resample"),
        ("zonal_statistics", "remote.zonal_stats", "zonal_stats"),
        ("ndvi", "remote.ndvi", "compute_ndvi"),
    ]
    for cap_id, algo_id, tool_name in matrix:
        assert caps.has(cap_id), cap_id
        algo = algos.get(algo_id)
        assert algo is not None, algo_id
        assert algo.runtime_status == "native", algo_id
        assert tool_name in algo.tool_candidates, (algo_id, tool_name)
        assert tool_name in tool_names, (algo_id, tool_name)
        assert cap_id in algo.capabilities, (algo_id, cap_id)

    # raster_calculator 不再归为“数据获取”语义
    assert algos.tool_to_capability()["raster_calculator"] == "band_math"
    # 矢量时序变化与栅格变化是不同 capability
    temporal = algos.get("temporal.change")
    assert "change_detection" in temporal.capabilities
    assert "raster_change_detection" not in temporal.capabilities


# ── 指数契约（§18：每种指数的 bands/formula/range/nodata/dtype）────────

def _write_multiband(name, bands):
    path = os.path.join(TD, name)
    arr = np.stack(bands)
    h, w = arr.shape[1:]
    with rasterio.open(
        path, "w", driver="GTiff", height=h, width=w, count=len(bands),
        dtype="float32", crs="EPSG:4326", transform=from_origin(0, h, 1, 1),
    ) as dst:
        dst.write(arr)
    return path


def test_index_contracts_ndwi_nbr_golden():
    """NDWI=(G−N)/(G+N)；NBR=(N−S)/(N+S)；手工金值 + 零分母→nodata。"""
    td = _td()
    green = np.array([[300.0, 100.0], [0.0, 50.0]], dtype="float32")
    nir = np.array([[100.0, 100.0], [0.0, 50.0]], dtype="float32")
    swir = np.array([[100.0, 300.0], [10.0, 50.0]], dtype="float32")
    # bands: green=1, nir=2, swir=3
    p = _write_multiband(f"nb_{uuid.uuid4().hex[:6]}.tif", [green, nir, swir])
    try:
        res = windowed_band_index(p, "ndwi", band_map={"green": 1, "nir": 2})
        with rasterio.open(res["output_path"]) as out:
            arr = out.read(1)
        # 金值：(300−100)/(300+100)=0.5；(100−100)/200=0；0+0→nodata；0/100=0
        np.testing.assert_allclose(arr[0, 0], 0.5, atol=1e-6)
        np.testing.assert_allclose(arr[0, 1], 0.0, atol=1e-6)
        np.testing.assert_allclose(arr[1, 1], 0.0, atol=1e-6)
        assert arr[1, 0] == -9999.0  # 零分母 → nodata（不是 0）

        res2 = windowed_band_index(p, "nbr", band_map={"nir": 2, "swir1": 3})
        with rasterio.open(res2["output_path"]) as out:
            arr2 = out.read(1)
        # (100−100)/200=0；(100−300)/400=−0.5；(0−10)/10=−1；0/100=0
        np.testing.assert_allclose(arr2[0, 0], 0.0, atol=1e-6)
        np.testing.assert_allclose(arr2[0, 1], -0.5, atol=1e-6)
        np.testing.assert_allclose(arr2[1, 0], -1.0, atol=1e-6)
        assert arr2[1, 1] == 0.0
        # 输出 dtype 契约：float32 / nodata -9999
        with rasterio.open(res2["output_path"]) as out:
            assert out.dtypes[0] == "float32" and out.nodata == -9999.0
    finally:
        for suffix in ("_ndwi.tif", "_nbr.tif"):
            os.path.exists(p.replace(".tif", suffix)) and os.remove(p.replace(".tif", suffix))
        os.remove(p)


def test_index_contract_missing_band_roles_fails():
    """缺波段角色（3 波段 RGB 无 NIR）→ 结构化失败，不猜。"""
    td = _td()
    p = _write_multiband(f"rgb_{uuid.uuid4().hex[:6]}.tif", [
        np.full((2, 2), 10.0, dtype="float32")] * 3)
    try:
        with pytest.raises(ValueError, match="nir"):
            windowed_band_index(p, "ndvi", band_map={"red": 1})
    finally:
        os.remove(p)


def test_calculate_index_exposes_band_map_and_evidence():
    """calculate_index（工具体路径）：band_map/descriptor/evidence 增量字段。"""
    from app.services.nature_resource_analyzer import NatureResourceAnalyzer

    td = _td()
    red = np.array([[100.0, 50.0]], dtype="float32")
    nir = np.array([[200.0, 50.0]], dtype="float32")
    fname = f"ci_{uuid.uuid4().hex[:6]}.tif"
    p = _write_multiband(fname, [red, red, red, nir])
    out_dir = os.path.join(td, "idx_out")
    os.makedirs(out_dir, exist_ok=True)
    try:
        # calculate_index 走 validate_data_path：入参是 data 目录相对路径
        r = NatureResourceAnalyzer.calculate_index(
            f"tmp_v3_tests/{fname}", "ndvi", red_band=1, nir_band=4,
            output_dir="tmp_v3_tests/idx_out",
        )
        assert r["success"] is True
        assert r["band_map"] == {"red": 1, "nir": 4}
        assert r["index_type"] == "ndvi"
        ev = r["quality_evidence"]
        assert ev["input_width"] == 2 and ev["output_width"] == 2
        assert ev["resampled"] is False and ev["reprojected"] is False
        assert "content_fingerprint" in r and "descriptor" in r
        with rasterio.open(r["result_path"]) as out:
            arr = out.read(1)
        np.testing.assert_allclose(arr[0, 0], 1 / 3, atol=1e-6)
        # (50−50)/(50+50)=0 是合法零值（分母非零），不是 nodata
        assert arr[0, 1] == 0.0
    finally:
        os.path.exists(p) and os.remove(p)
