"""Review W1/W2: geo_raster budgets must guard the allocation that happens."""
import numpy as np
import pytest

from app.lib.geo_raster import reader as reader_mod
from app.lib.geo_raster.reader import RasterReader, RasterReaderError
from app.lib.geo_raster.windowed import AlgorithmProfile, execute_windowed


@pytest.fixture
def tif_with_overview(tmp_path):
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin

    p = tmp_path / "ov.tif"
    with rasterio.open(
        p, "w", driver="GTiff", width=512, height=512, count=2, dtype="float32",
        crs="EPSG:4326", transform=from_origin(0, 1, 0.001, 0.001),
        tiled=True, blockxsize=256, blockysize=256,
    ) as dst:
        dst.write(np.ones((2, 512, 512), dtype="float32"))
        dst.build_overviews([16], Resampling.average)
    return str(p)


def test_w2_read_overview_budgets_decimated_output(tif_with_overview, monkeypatch):
    # full-res all-band = 2 MiB; coarsest overview (32×32 float32) = 4 KiB
    monkeypatch.setattr(reader_mod, "DEFAULT_FULL_READ_BUDGET_BYTES", 1024 * 1024)
    r = RasterReader.open(tif_with_overview)
    ov = r.read_overview(level=-1)
    assert ov.shape == (32, 32)
    with pytest.raises(RasterReaderError):
        r.read_full()


def test_w2_read_band_budgets_one_band(tif_with_overview, monkeypatch):
    # one band = 1 MiB (fits 1.5 MiB); all bands = 2 MiB (would not)
    monkeypatch.setattr(reader_mod, "DEFAULT_FULL_READ_BUDGET_BYTES", 1536 * 1024)
    r = RasterReader.open(tif_with_overview)
    assert r.read_band(2).shape == (512, 512)
    assert r.read_mask().shape == (512, 512)


def test_w1_execute_windowed_refuses_over_budget_output(tif_with_overview, monkeypatch):
    monkeypatch.setattr(reader_mod, "DEFAULT_FULL_READ_BUDGET_BYTES", 512 * 1024)
    r = RasterReader.open(tif_with_overview)
    fn = lambda data, core, read: data  # noqa: E731
    with pytest.raises(RasterReaderError, match="output"):
        execute_windowed(r, AlgorithmProfile(), fn)
    res = execute_windowed(r, AlgorithmProfile(), fn, budget_ok=True)
    assert res.array.shape == (512, 512)
