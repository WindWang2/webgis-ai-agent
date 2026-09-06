"""ADR-0101 D9（Raster Runtime V6）：多波段窗口执行、逐波段统计、颜色解释、
远端窗口读策略、对齐判定收敛。"""
from __future__ import annotations

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.cancellation import CancellationToken, OperationCancelled
from app.lib.geo_analysis.raster_grid import pixel_grids_aligned
from app.lib.geo_analysis.raster_windowed import (
    WindowedRasterWriter,
    build_output_profile,
)
from app.lib.geo_raster import RasterReader
from app.lib.geo_raster.reader import RasterReaderError
from app.lib.geo_raster.remote import (
    RemoteReadBudgetExceeded,
    RemoteReadPolicy,
    RemoteReadSession,
    is_transient_remote_error,
    remote_read_window,
    remote_uri,
)
from app.lib.geo_raster.windowed import AlgorithmProfile, execute_windowed

# ---------------------------------------------------------------- fixtures


def _write_multiband(path, *, bands: int = 3, size: int = 64, seed: int = 5) -> str:
    rng = np.random.default_rng(seed)
    data = rng.integers(1, 100, (bands, size, size)).astype("float32")
    with rasterio.open(
        path, "w", driver="GTiff", width=size, height=size, count=bands,
        dtype="float32", crs="EPSG:3857", transform=from_origin(0, size, 1, 1),
        nodata=-9999.0, tiled=True, blockxsize=32, blockysize=32,
    ) as dst:
        dst.write(data)
    return str(path)


@pytest.fixture()
def mb_raster(tmp_path):
    return _write_multiband(tmp_path / "mb.tif")


# ------------------------------------------------- 多波段窗口执行（§19）


class TestMultibandWindowedExecution:
    def test_bands_input_single_output(self, mb_raster):
        reader = RasterReader.open(mb_raster)
        seen_shapes = []

        def mean_fn(data, core, read):
            seen_shapes.append(data.shape)
            return data.mean(axis=0)

        result = execute_windowed(
            reader, AlgorithmProfile(), mean_fn, bands=(1, 2, 3), window_size=(32, 32))
        assert result.array.shape == (64, 64)
        assert seen_shapes and seen_shapes[0][0] == 3  # 堆叠维在最前
        reader.close()

    def test_band_validation(self, mb_raster):
        reader = RasterReader.open(mb_raster)
        with pytest.raises(RasterReaderError, match="out of range"):
            execute_windowed(reader, AlgorithmProfile(),
                             lambda d, c, r: d[0], bands=(1, 9))
        with pytest.raises(RasterReaderError, match="duplicate"):
            execute_windowed(reader, AlgorithmProfile(),
                             lambda d, c, r: d[0], bands=(1, 1))
        reader.close()

    def test_window_budget_guard(self, mb_raster, monkeypatch):
        import app.lib.geo_raster.windowed as win_mod

        monkeypatch.setattr(win_mod, "_MULTIBAND_WINDOW_BUDGET_BYTES", 16)
        reader = RasterReader.open(mb_raster)
        with pytest.raises(RasterReaderError, match="budget"):
            execute_windowed(reader, AlgorithmProfile(),
                             lambda d, c, r: d[0], bands=(1, 2, 3))
        reader.close()

    def test_single_band_path_unchanged(self, mb_raster):
        reader = RasterReader.open(mb_raster)
        result = execute_windowed(
            reader, AlgorithmProfile(), lambda d, c, r: d * 2.0, band=2)
        assert result.array.shape == (64, 64)
        reader.close()


# --------------------------------------------- 逐波段统计 + 颜色解释（§19）


class TestWriterBandStatsAndColorInterp:
    def test_finalize_reports_real_per_band_ranges(self, tmp_path):
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        out = str(tmp_path / "two_band.tif")
        profile = build_output_profile(width=32, height=32, count=2, dtype="float32",
                                       crs="EPSG:3857",
                                       transform=from_origin(0, 32, 1, 1),
                                       nodata=-9999.0)
        grid = RasterGridProfile(width=32, height=32, crs="EPSG:3857",
                                 transform=from_origin(0, 32, 1, 1)[:6],
                                 dtype="float32", nodata=-9999.0, band_count=2)
        with WindowedRasterWriter(out, profile=profile, grid=grid) as w:
            from rasterio.windows import Window

            b1 = np.full((32, 32), 10.0, dtype="float32")
            b2 = np.full((32, 32), 900.0, dtype="float32")
            w.write(Window(0, 0, 32, 32), b1, band=1)
            w.write(Window(0, 0, 32, 32), b2, band=2)
            fin = w.finalize()
        bands = fin["descriptor"].bands
        assert len(bands) == 2
        assert bands[0].vmax == 10.0
        assert bands[1].vmax == 900.0  # V3 会把 band-1 的 10.0 复制到这里
        assert set(fin["band_stats"]) == {1, 2}
        with rasterio.open(out) as src:
            assert src.read(1).max() == 10.0
            assert src.read(2).max() == 900.0

    def test_colorinterp_preserved(self, tmp_path):
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        out = str(tmp_path / "rgb.tif")
        profile = build_output_profile(width=16, height=16, count=3, dtype="uint8",
                                       crs="EPSG:3857",
                                       transform=from_origin(0, 16, 1, 1),
                                       colorinterp=["Red", "Green", "Blue"])
        grid = RasterGridProfile(width=16, height=16, crs="EPSG:3857",
                                 transform=from_origin(0, 16, 1, 1)[:6],
                                 dtype="uint8", nodata=None, band_count=3)
        with WindowedRasterWriter(out, profile=profile, grid=grid) as w:
            from rasterio.windows import Window

            for b in (1, 2, 3):
                w.write(Window(0, 0, 16, 16),
                        np.zeros((16, 16), dtype="uint8"), band=b)
            w.finalize()
        with rasterio.open(out) as src:
            names = [c.name for c in src.colorinterp]
            assert names == ["red", "green", "blue"]

    def test_colorinterp_count_mismatch_rejected(self):
        with pytest.raises(ValueError, match="band count"):
            build_output_profile(width=4, height=4, count=2,
                                 colorinterp=["Red"])


# ------------------------------------------------- 远端窗口读策略（§22）


class _FlakyRemoteDS:
    """模拟 /vsicurl 数据集：前 n 次读抛瞬态网络错误。"""

    def __init__(self, fail_times: int, arr: np.ndarray):
        self._fails = fail_times
        self._arr = arr

    def read(self, band, window=None):
        if self._fails > 0:
            self._fails -= 1
            raise RuntimeError(
                "CURL error 28: Connection timed out after milliseconds")
        return self._arr


class TestRemoteReadPolicy:
    def test_transient_classification(self):
        assert is_transient_remote_error(RuntimeError("CURL error 28: timeout")) is True
        assert is_transient_remote_error(RuntimeError("HTTP response code: 503")) is True
        assert is_transient_remote_error(RuntimeError("HTTP response code: 404")) is False
        assert is_transient_remote_error(RuntimeError("checksum mismatch")) is False

    def test_remote_uri_detection(self):
        assert remote_uri("https://example.com/a.tif") is True
        assert remote_uri("/vsis3/bucket/a.tif") is True
        assert remote_uri("/data/local.tif") is False
        assert remote_uri(None) is False

    def test_retry_on_transient_then_success(self, mb_raster):
        arr = np.zeros((8, 8), dtype="float32")
        ds = _FlakyRemoteDS(fail_times=1, arr=arr)
        session = RemoteReadSession(policy=RemoteReadPolicy(
            max_attempts=3, backoff_s=0.01, jitter=False, use_provider_health=False))
        from rasterio.windows import Window

        got = remote_read_window(session, "https://example.com/a.tif", ds, 1,
                                 Window(0, 0, 8, 8))
        assert got.shape == (8, 8)
        assert session.requests == 2
        assert session.retries == 1

    def test_permanent_failure_never_retried(self, mb_raster):
        class PermanentDS:
            def read(self, band, window=None):
                raise RuntimeError("HTTP response code: 404 (not found)")

        session = RemoteReadSession(policy=RemoteReadPolicy(
            max_attempts=3, use_provider_health=False))
        from rasterio.windows import Window

        with pytest.raises(RuntimeError, match="404"):
            remote_read_window(session, "https://example.com/a.tif", PermanentDS(), 1,
                               Window(0, 0, 4, 4))
        assert session.retries == 0

    def test_request_budget_hard_stop(self):
        class OkDS:
            def read(self, band, window=None):
                return np.zeros((1, 1), dtype="float32")

        session = RemoteReadSession(policy=RemoteReadPolicy(
            max_attempts=1, max_requests=2, use_provider_health=False))
        from rasterio.windows import Window

        for _ in range(2):
            remote_read_window(session, "https://e.com/a.tif", OkDS(), 1, Window(0, 0, 1, 1))
        with pytest.raises(RemoteReadBudgetExceeded, match="requests"):
            remote_read_window(session, "https://e.com/a.tif", OkDS(), 1, Window(0, 0, 1, 1))

    def test_cancellation_between_retries(self):
        class AlwaysFailsDS:
            def read(self, band, window=None):
                raise RuntimeError("CURL error 28: timeout")

        session = RemoteReadSession(policy=RemoteReadPolicy(
            max_attempts=5, backoff_s=5.0, jitter=False, use_provider_health=False))
        token = CancellationToken(job_id="t")
        token.cancel("cancelled by test")
        from rasterio.windows import Window

        with pytest.raises(OperationCancelled):
            remote_read_window(session, "https://e.com/a.tif", AlwaysFailsDS(), 1,
                               Window(0, 0, 1, 1), cancel_token=token)

    def test_windowed_executor_uses_remote_session(self, mb_raster, monkeypatch):
        """远端 URI 的 reader 在窗口循环中携带预算会话（计数可见）。"""
        import app.lib.geo_raster.windowed as win_mod

        monkeypatch.setattr(win_mod, "remote_uri", lambda uri: True)
        used = {}
        real = win_mod.remote_read_window

        def spy(session, *a, **kw):
            used["calls"] = used.get("calls", 0) + 1
            return real(session, *a, **kw)

        monkeypatch.setattr(win_mod, "remote_read_window", spy)
        reader = RasterReader.open(mb_raster)
        execute_windowed(reader, AlgorithmProfile(),
                         lambda d, c, r: d, band=1, window_size=(32, 32))
        reader.close()
        assert used.get("calls", 0) >= 1  # 远端路径被走（预算会话生效）

    def test_local_source_never_pays_remote_overhead(self, mb_raster, monkeypatch):
        """本地源红线：不建远端会话、不经过远端读路径（零开销）。"""
        import app.lib.geo_raster.windowed as win_mod

        used = {}
        monkeypatch.setattr(win_mod, "remote_read_window",
                            lambda *a, **kw: used.setdefault("called", True))
        reader = RasterReader.open(mb_raster)
        execute_windowed(reader, AlgorithmProfile(),
                         lambda d, c, r: d, band=1, window_size=(32, 32))
        reader.close()
        assert "called" not in used


# --------------------------------------------- 对齐判定收敛（§21）


class TestAlignmentConvergence:
    def test_pixel_grids_aligned_positive(self):
        assert pixel_grids_aligned(10.0, 10.0, 0.0, 0.0,
                                   10.0, 10.0, 30.0, -20.0) is True

    def test_resolution_mismatch_rejected(self):
        assert pixel_grids_aligned(10.0, 10.0, 0.0, 0.0,
                                   10.5, 10.0, 0.0, 0.0) is False

    def test_subpixel_phase_rejected(self):
        assert pixel_grids_aligned(10.0, 10.0, 0.0, 0.0,
                                   10.0, 10.0, 5.0, 0.0) is False

    def test_degenerate_resolution_rejected(self):
        assert pixel_grids_aligned(0.0, 10.0, 0.0, 0.0,
                                   0.0, 10.0, 0.0, 0.0) is False

    def test_spatial_tasks_delegates_to_authority(self):
        import app.services.spatial_tasks as st
        from app.lib.geo_analysis import raster_grid

        assert "pixel_grids_aligned" in st._grids_pixel_aligned.__code__.co_names
        assert raster_grid.pixel_grids_aligned is not None
