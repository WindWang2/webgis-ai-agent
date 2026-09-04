"""Raster Runtime V5 — whole-read guard, multi-band windowed read, unified
bounded fingerprint, GDAL env as a runtime property.

The spy tests exist to FAIL on regressions: any path that starts reading a
whole band (``dataset.read()`` / ``read(1)`` without ``window=``) inside the
windowed entry points trips ``test_windowed_paths_never_whole_read``.
"""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.windows import Window

from app.lib.geo_raster import (
    AlgorithmProfile,
    RasterReader,
    RasterReaderError,
    execute_windowed,
)
from app.lib.geo_analysis.raster_math import raster_calculator
from app.lib.geo_raster.env import rasterio_env
from app.lib.geo_raster.fingerprint import (
    content_digest,
    content_fingerprint,
    raster_content_fingerprint_v5,
)
from app.services.temporal.raster import TemporalRasterEngine


# ── fixtures / helpers ──────────────────────────────────────────────────────

def _write_tiled(path, data, *, dtype="uint8", nodata=None, count=1):
    """Multi-block tiled GTiff (64×64 blocks) — block-boundary discipline is
    what makes whole-read regressions observable."""
    h, w = data.shape[-2], data.shape[-1]
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=count,
        dtype=dtype, crs="EPSG:4326", transform=from_origin(0, h, 1, 1),
        nodata=nodata, tiled=True, blockxsize=64, blockysize=64,
    ) as dst:
        if data.ndim == 2:
            dst.write(data, 1)
        else:
            dst.write(data)
    return path


@pytest.fixture()
def tiled_pair(tmp_path):
    """512×512 uint8 pair on one grid; T2 = T1 + 5 → exact diff field.

    Values start at 1 (never 0): raster_calculator without an explicit
    nodata defaults its output sentinel to 0, and a 0-valued pixel would
    legitimately drop out of valid_pixel_count."""
    rng = np.random.default_rng(7)
    base = rng.integers(1, 200, size=(512, 512), dtype=np.uint8)
    p1 = _write_tiled(tmp_path / "t1.tif", base)
    p2 = _write_tiled(tmp_path / "t2.tif", base + 5)
    return str(p1), str(p2), base


@pytest.fixture()
def multiband_raster(tmp_path):
    """3-band 128×128 uint8, one distinct value layer per band."""
    data = np.stack([
        np.full((128, 128), 10, dtype=np.uint8),
        np.full((128, 128), 20, dtype=np.uint8),
        np.full((128, 128), 30, dtype=np.uint8),
    ])
    p = _write_tiled(tmp_path / "mb.tif", data, count=3)
    return str(p), data


class _ReadSpy:
    """Wrap ``rasterio.open`` and record the shape of every ``ds.read``.

    A call is a WHOLE read iff it carries neither ``window=`` nor
    ``out_shape=`` — the §54 forbidden shape on hot paths. Detection
    semantics are pinned by ``test_read_spy_detects_whole_reads``.
    """

    def __init__(self):
        self.windowed_flags: list[bool] = []
        self._real_open = rasterio.open

    def __call__(self, path, mode="r", **kwargs):
        ds = self._real_open(path, mode, **kwargs)
        if mode != "r":
            return ds
        spy = self
        orig_read = ds.read

        def read(*a, **kw):
            windowed = kw.get("window") is not None or kw.get("out_shape") is not None
            spy.windowed_flags.append(windowed)
            return orig_read(*a, **kw)

        ds.read = read  # type: ignore[method-assign]
        return ds

    @property
    def whole_reads(self) -> list[int]:
        return [i for i, w in enumerate(self.windowed_flags) if not w]


# ── (a) whole-read guard over the windowed entry points ─────────────────────


def test_read_spy_detects_whole_reads(tmp_path, monkeypatch):
    """Detector self-test: the spy MUST flag a genuine whole-band read, or
    the guard below proves nothing."""
    p = _write_tiled(tmp_path / "s.tif", np.zeros((64, 64), dtype=np.uint8))
    spy = _ReadSpy()
    monkeypatch.setattr(rasterio, "open", spy)
    with rasterio.open(p) as ds:
        ds.read(1)                              # forbidden whole-band shape
        ds.read(1, window=Window(0, 0, 8, 8))   # sanctioned windowed shape
    assert spy.windowed_flags == [False, True]
    assert spy.whole_reads == [0]


def test_windowed_paths_never_whole_read(tiled_pair, monkeypatch):
    """select_time_slice / raster_difference / execute_windowed /
    raster_calculator must only read through windows. A whole-band read
    anywhere in these paths fails this test — that is its purpose."""
    p1, p2, _ = tiled_pair
    spy = _ReadSpy()
    monkeypatch.setattr(rasterio, "open", spy)

    engine = TemporalRasterEngine()
    selected = engine.select_time_slice([
        {"path": p1, "timestamp": "2024-01-01T00:00:00Z"},
        {"path": p2, "timestamp": "2024-02-01T00:00:00Z"},
    ])
    assert len(selected) == 2

    diff = engine.raster_difference(p1, p2)
    assert diff["pixel_count"] == 512 * 512
    assert diff["mean_difference"] == pytest.approx(5.0)

    with RasterReader.open(p1) as r:
        res = execute_windowed(
            r, AlgorithmProfile(), lambda a, core, read_win: a,
            window_size=(128, 128),
        )
    assert res.array.shape == (512, 512)

    out = raster_calculator(p1, expression="A * 2")
    assert out["pixel_count"] == 512 * 512

    assert spy.windowed_flags, "spy observed no reads — fixture/regression"
    assert spy.whole_reads == [], (
        f"whole-band (no window=) reads at call indexes {spy.whole_reads}"
    )


# ── (b) multi-band read_window ──────────────────────────────────────────────


def test_read_window_multiband_stacks(multiband_raster):
    p, data = multiband_raster
    with RasterReader.open(p) as r:
        # default / band= path unchanged: single-band 2D output
        single = r.read_window((16, 16, 32, 32))
        assert single.shape == (32, 32)
        np.testing.assert_array_equal(single, data[0][16:48, 16:48])
        np.testing.assert_array_equal(r.read_window((0, 0, 8, 8), band=2), data[1][:8, :8])

        stacked = r.read_window((16, 16, 32, 32), bands=(1, 2, 3))
        assert stacked.shape == (3, 32, 32)
        np.testing.assert_array_equal(stacked, data[:, 16:48, 16:48])

        # order follows the request, not the file
        reordered = r.read_window((0, 0, 8, 8), bands=(3, 1))
        np.testing.assert_array_equal(reordered[0], data[2][:8, :8])
        np.testing.assert_array_equal(reordered[1], data[0][:8, :8])


def test_read_window_multiband_validates_bands(multiband_raster):
    p, _ = multiband_raster
    with RasterReader.open(p) as r:
        for bad in ((), (0,), (4,), (1, 4)):
            with pytest.raises(RasterReaderError):
                r.read_window((0, 0, 8, 8), bands=bad)


def test_read_window_multiband_budget_guard(tmp_path):
    """Multi-band window reads are bounded by the same whole-read byte
    budget per call (window cells × Σ band itemsize)."""
    from app.lib.geo_raster.reader import DEFAULT_FULL_READ_BUDGET_BYTES

    big = tmp_path / "big.tif"
    with rasterio.open(
        big, "w", driver="GTiff", width=30000, height=30000, count=3,
        dtype="float32", crs="EPSG:3857", transform=from_origin(0, 0, 10, 10),
    ):
        pass
    est = 30000 * 30000 * 3 * 4
    assert est > DEFAULT_FULL_READ_BUDGET_BYTES
    with RasterReader.open(str(big)) as r:
        with pytest.raises(RasterReaderError, match="budget"):
            r.read_window((0, 0, 30000, 30000), bands=(1, 2, 3))
        # bounded windows on the same raster remain fine
        assert r.read_window((0, 0, 32, 32), bands=(1, 2, 3)).shape == (3, 32, 32)


# ── (c) unified bounded content fingerprint ─────────────────────────────────


def test_content_fingerprint_stable_and_sensitive(tmp_path):
    p = str(_write_tiled(
        tmp_path / "fp.tif",
        (np.arange(256 * 256, dtype=np.uint16).reshape(256, 256) % 251).astype(np.uint8),
    ))
    with rasterio.open(p) as ds:
        fp1 = content_fingerprint(ds)
        digest = content_digest(ds)
    assert len(fp1) == 16 and fp1 == digest[:16]
    with rasterio.open(p) as ds:
        assert content_fingerprint(ds) == fp1  # same file → same fingerprint

    # flip one pixel INSIDE the sampled corner block → fingerprint changes
    with rasterio.open(p, "r+") as ds:
        ds.write(np.array([[42]], dtype=np.uint8), 1, window=Window(0, 0, 1, 1))
    with rasterio.open(p) as ds:
        assert content_fingerprint(ds) != fp1


def test_reader_fingerprint_value_compat(tmp_path):
    """RasterReader._fingerprint keeps its historical sha256[:32] format and
    the V5 content_fingerprint is the SAME digest truncated to 16 — strict
    prefix, so cross-entry-point comparison is startswith."""
    p = str(_write_tiled(tmp_path / "vc.tif", np.zeros((64, 64), dtype=np.uint8)))
    with rasterio.open(p) as ds:
        legacy = RasterReader._fingerprint(ds)
        v5 = content_fingerprint(ds)
    assert len(legacy) == 32
    assert v5 == legacy[:16]
    with RasterReader.open(p) as r:
        assert r.metadata().fingerprint == legacy  # cached metadata unchanged


def test_fingerprint_v5_entry_points(tmp_path):
    p = str(_write_tiled(tmp_path / "v5.tif", np.zeros((64, 64), dtype=np.uint8)))
    assert raster_content_fingerprint_v5(p) == raster_content_fingerprint_v5(p)
    with RasterReader.open(p) as r:
        assert raster_content_fingerprint_v5(r) == raster_content_fingerprint_v5(p)
    with pytest.raises(RasterReaderError):
        raster_content_fingerprint_v5(str(tmp_path / "missing.tif"))


def test_fingerprint_never_whole_reads(tmp_path, monkeypatch):
    """Fingerprint computation is bounded: at most the two documented corner
    sample reads, never a whole-band read."""
    p = str(_write_tiled(tmp_path / "fb.tif", np.zeros((256, 256), dtype=np.uint8)))
    spy = _ReadSpy()
    monkeypatch.setattr(rasterio, "open", spy)
    with rasterio.open(p) as ds:
        content_fingerprint(ds)
    assert len(spy.windowed_flags) <= 2  # documented block budget
    assert spy.whole_reads == []


# ── (d) GDAL env runtime property ───────────────────────────────────────────


def test_rasterio_env_pins_canonical_knobs():
    from rasterio.env import get_gdal_config

    with rasterio_env():
        assert get_gdal_config("GDAL_NUM_THREADS") == 1
        assert get_gdal_config("GDAL_DISABLE_READDIR_ON_OPEN") == "TRUE"
        assert get_gdal_config("GDAL_HTTP_TIMEOUT") == 5
        assert get_gdal_config("GDAL_HTTP_MAX_RETRY") == 0
        assert int(get_gdal_config("GDAL_CACHEMAX")) > 0  # capped, not GDAL default


def test_legacy_raster_math_env_delegates():
    """raster_math.rasterio_env keeps working (import path compat) and pins
    the same knobs via the canonical env module."""
    from rasterio.env import get_gdal_config

    from app.lib.geo_analysis.raster_math import rasterio_env as legacy_env

    with legacy_env():
        assert get_gdal_config("GDAL_NUM_THREADS") == 1
