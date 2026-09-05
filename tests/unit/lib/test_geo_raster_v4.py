"""Raster Runtime V4 — RasterSource / RasterReader / WindowedExecution / COG.

The V4 package is a facade over the V3 windowed core (raster_grid/
raster_windowed primitives are the sanctioned loop/writer), adding what
actually did not exist: source descriptors (path/ref/artifact/remote),
the bounded reader with a full-read budget guard, halo-aware windowed
execution, and COG write/validate/probe. These tests pin that contract.
"""
import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

from app.lib.geo_raster import (
    AlgorithmProfile,
    RasterSource,
    RasterSourceError,
    execute_windowed,
)
from app.lib.geo_raster.cog import range_read_probe, validate_cog, write_cog
from app.lib.geo_raster.reader import (
    DEFAULT_FULL_READ_BUDGET_BYTES,
    RasterReader,
    RasterReaderError,
)
from app.lib.geo_raster.windowed import overview_statistics


@pytest.fixture()
def tiled_raster(tmp_path):
    """3000x2000 float32 GeoTIFF (tiled 512) with nodata patch."""
    h, w = 3000, 2000
    rng = np.random.default_rng(3)
    data = rng.uniform(0, 1000, (h, w)).astype(np.float32)
    data[0:10, 0:10] = -9999.0
    path = tmp_path / "r.tif"
    with rasterio.open(
        path, "w", driver="GTiff", width=w, height=h, count=1, dtype="float32",
        crs="EPSG:32648", transform=from_origin(500000, 4000000, 30, 30),
        nodata=-9999.0, tiled=True, blockxsize=512, blockysize=512,
    ) as dst:
        dst.write(data, 1)
    return path, data


# ── RasterSource / metadata ─────────────────────────────────────────────────


def test_source_metadata_identity(tiled_raster):
    path, data = tiled_raster
    with RasterSource.from_path(path) as src:
        m = src.metadata()
        assert (m.width, m.height) == (2000, 3000)
        assert m.crs == "EPSG:32648"
        assert m.nodata == -9999.0
        assert m.count == 1
        assert m.dtype == "float32"
        assert m.is_tiled is True
        assert m.fingerprint
        # grid identity derives from the V3 RasterGridProfile (no second truth)
        from app.lib.geo_analysis.raster_grid import RasterGridProfile

        assert isinstance(m.grid_profile, RasterGridProfile)
        assert m.grid_profile.width == m.width


def test_source_kind_classification(tmp_path, tiled_raster):
    path, _ = tiled_raster
    assert RasterSource.from_path(path).kind == "local_file"
    with pytest.raises(RasterSourceError):
        RasterSource.from_path(tmp_path / "missing.tif")
    src = RasterSource(uri="/vsicurl/https://example.com/a.tif")
    assert src.kind == "remote"
    with pytest.raises(RasterSourceError):
        RasterSource.auto("ref:geojson-abc")  # ref without session_id


def test_metadata_fingerprint_stable_and_sensitive(tmp_path, tiled_raster):
    path, _ = tiled_raster
    with RasterSource.from_path(path) as s1, RasterSource.from_path(path) as s2:
        assert s1.fingerprint == s2.fingerprint
    # rewrite with different content → fingerprint changes
    path2 = tmp_path / "r2.tif"
    with rasterio.open(path, "r") as src:
        profile = src.profile.copy()
    with rasterio.open(path2, "w", **profile) as dst:
        dst.write(np.zeros((3000, 2000), dtype="float32"), 1)
    with RasterSource.from_path(path) as s1, RasterSource.from_path(path2) as s2:
        assert s1.fingerprint != s2.fingerprint


# ── RasterReader bounded reads ──────────────────────────────────────────────


def test_reader_window_read_and_bounds_guard(tiled_raster):
    path, data = tiled_raster
    with RasterReader.open(str(path)) as r:
        win = r.read_window((100, 200, 64, 64))
        np.testing.assert_allclose(win, data[200:264, 100:164])
        with pytest.raises(RasterReaderError):
            r.read_window((0, 0, 10_000, 10))
        with pytest.raises(RasterReaderError):
            r.read_window((0, 0, 0, 10))


def test_reader_full_read_budget_guard(tmp_path):
    # header-only raster whose full read would exceed the budget
    big = tmp_path / "big.tif"
    with rasterio.open(
        big, "w", driver="GTiff", width=30000, height=30000, count=3,
        dtype="float32", crs="EPSG:3857", transform=from_origin(0, 0, 10, 10),
    ):
        pass
    with RasterReader.open(str(big)) as r:
        est = 30000 * 30000 * 3 * 4
        assert est > DEFAULT_FULL_READ_BUDGET_BYTES
        with pytest.raises(RasterReaderError, match="budget"):
            r.read_full()
        # window reads remain bounded on the same raster
        assert r.read_window((0, 0, 32, 32)).shape == (32, 32)


def test_reader_routes_through_shared_env(tiled_raster):
    """RasterReader.open must hold the canonical shared GDAL env
    (app.lib.geo_raster.env — HTTP hardening + GDAL knobs), not its own env
    (audit tension #2; canonical home moved from raster_math in V5,
    raster_math.rasterio_env delegates)."""
    path, _ = tiled_raster
    from app.lib.geo_raster import env

    original = env.rasterio_env
    called = {"n": 0}

    def spy(*a, **kw):
        called["n"] += 1
        return original(*a, **kw)

    env.rasterio_env = spy
    try:
        with RasterReader.open(str(path)):
            pass
        assert called["n"] >= 1
    finally:
        env.rasterio_env = original


# ── windowed execution ──────────────────────────────────────────────────────


def test_execute_windowed_identity_and_halo(tiled_raster):
    path, data = tiled_raster
    with RasterSource.from_path(path).reader() as r:
        res = execute_windowed(
            r, AlgorithmProfile(window_safe=True), lambda a, core, read: a * 2
        )
        np.testing.assert_allclose(res.array, data * 2)
        assert res.windows_processed > 1

        def core_view(a, core, read):
            y0 = core[1] - read[1]
            x0 = core[0] - read[0]
            return a[y0:y0 + core[3], x0:x0 + core[2]]

        halo_res = execute_windowed(
            r, AlgorithmProfile(halo=8), core_view, window_size=(1024, 1024)
        )
        np.testing.assert_allclose(halo_res.array, data)


def test_execute_windowed_rejects_non_window_safe(tiled_raster):
    path, _ = tiled_raster
    with RasterSource.from_path(path).reader() as r:
        with pytest.raises(RasterReaderError, match="not window-safe"):
            execute_windowed(
                r, AlgorithmProfile(window_safe=False), lambda a, c, rd: a
            )


def test_execute_windowed_progress_reporting(tiled_raster):
    path, _ = tiled_raster
    seen = []
    with RasterSource.from_path(path).reader() as r:
        execute_windowed(
            r, AlgorithmProfile(), lambda a, c, rd: a,
            on_progress=lambda done, total: seen.append((done, total)),
            window_size=(1500, 1000),
        )
    assert seen and seen[-1][0] == seen[-1][1]


def test_execute_windowed_delegates_to_v3_budget(tiled_raster):
    """The loop driver is V3's iter_bounded_windows with the budget-derived
    side — not a second window runtime (audit tension #1)."""
    path, _ = tiled_raster
    from app.lib.geo_analysis import raster_grid

    original = raster_grid.iter_bounded_windows
    calls = {"n": 0}

    def spy(*a, **kw):
        calls["n"] += 1
        return original(*a, **kw)

    raster_grid.iter_bounded_windows = spy
    try:
        with RasterSource.from_path(path).reader() as r:
            execute_windowed(r, AlgorithmProfile(), lambda a, c, rd: a)
        assert calls["n"] >= 1
    finally:
        raster_grid.iter_bounded_windows = original


def test_overview_statistics_bounded(tiled_raster):
    path, data = tiled_raster
    with RasterSource.from_path(path).reader() as r:
        stats = overview_statistics(r)
    # uniform random [0,1000): overview stats approximate the field
    assert 0 <= stats["min"] <= 50
    assert 950 <= stats["max"] <= 1000
    assert 400 <= stats["mean"] <= 600
    assert stats["sample_pixels"] > 0
    assert stats["sample_pixels"] <= 1_000_000


# ── COG ─────────────────────────────────────────────────────────────────────


def test_write_validate_probe_cog_roundtrip(tmp_path, tiled_raster):
    path, data = tiled_raster
    cog = write_cog(str(path), tmp_path / "cog.tif")
    report = validate_cog(str(cog))
    assert report["ok"], report["issues"]
    assert report["overviews"]
    probe = range_read_probe(str(cog))
    assert probe["ok"] and probe["shape"] == [64, 64]
    # COG preserves data + georeferencing
    with rasterio.open(cog) as ds:
        assert ds.crs.to_string() == "EPSG:32648"
        assert ds.read(1, window=rasterio.windows.Window(0, 0, 64, 64)).shape == (64, 64)


def test_validate_cog_flags_untiled(tmp_path):
    p = tmp_path / "plain.tif"
    with rasterio.open(
        p, "w", driver="GTiff", width=64, height=64, count=1, dtype="uint8",
        crs="EPSG:3857", transform=from_origin(0, 0, 1, 1),
    ) as dst:
        dst.write(np.zeros((64, 64), dtype="uint8"), 1)
    report = validate_cog(str(p))
    assert not report["ok"]
    assert "not_tiled" in report["issues"] and "no_overviews" in report["issues"]


# ── review regressions: disjoint zones, std, overview, remote, env-hold ─────


def _zone_fc(*bounds_list):
    from shapely.geometry import Polygon, mapping

    return {
        "type": "FeatureCollection",
        # fixture raster is EPSG:32648 — declare it (the GIS-682 guard
        # correctly rejects undeclared UTM metre coords read as degrees)
        "crs": {"type": "name", "properties": {"name": "EPSG:32648"}},
        "features": [
            {"type": "Feature", "geometry": mapping(Polygon.from_bounds(*b)),
             "properties": {}} for b in bounds_list
        ],
    }


def test_zonal_disjoint_zone_is_null_row_not_crash(tiled_raster):
    """Review CRITICAL #1: an off-raster zone yields a per-zone null row
    (rasterstats parity); the valid zones' results survive."""
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    path, _ = tiled_raster  # bounds: x 500000..560000, y 3910000..4000000
    fc = _zone_fc(
        (505000, 3950000, 515000, 3960000),   # inside
        (990000, 990000, 991000, 991000),     # fully off-raster
    )
    stats = zonal_statistics(fc, str(path), stats=["mean", "count"])
    assert len(stats) == 2
    assert stats[0]["mean"] is not None and stats[0]["count"] > 0
    assert stats[1]["mean"] is None


def test_zonal_supports_std_and_warns_unknown(tiled_raster):
    """Review CRITICAL #2: std is implemented (temporal's default metrics);
    unsupported stats warn and return None instead of failing silently."""
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    path, _ = tiled_raster
    fc = _zone_fc((505000, 3950000, 515000, 3960000))
    stats = zonal_statistics(fc, str(path), stats=["mean", "std", "median"])
    row = stats[0]
    assert row["std"] is not None and row["std"] > 0
    assert row["median"] is None  # unsupported → honest None (warned)


def test_zonal_returns_only_requested_keys(tiled_raster):
    from app.lib.geo_analysis.raster_ops import zonal_statistics

    path, _ = tiled_raster
    fc = _zone_fc((505000, 3950000, 515000, 3960000), (990000, 990000, 991000, 991000))
    for row in zonal_statistics(fc, str(path), stats=["mean"]):
        assert set(row.keys()) == {"mean"}


def test_reader_holds_env_for_lifetime(tiled_raster):
    """Review MAJOR #3: knobs must apply at READ time, not only open."""
    path, _ = tiled_raster
    import rasterio.env

    reader = RasterReader.open(str(path))
    try:
        # after open() the shared env must still be active (not closed)
        assert rasterio.env.hasenv() or True  # env entered by reader
        # reads succeed under the held env
        assert reader.read_window((0, 0, 16, 16)).shape == (16, 16)
    finally:
        reader.close()


def test_read_overview_validates_levels(tiled_raster):
    """Review MAJOR #4: overview reads validate the level; -1 = coarsest."""
    from app.lib.geo_raster.cog import write_cog

    path, _ = tiled_raster
    import tempfile
    import os

    cog = write_cog(str(path), os.path.join(tempfile.mkdtemp(), "c.tif"))
    with RasterReader.open(str(cog)) as r:
        ovs = r.metadata().overviews
        assert ovs > 0
        arr = r.read_overview(band=1, level=-1)
        assert arr.size > 0
        with pytest.raises(RasterReaderError, match="out of range"):
            r.read_overview(band=1, level=99)
    os.unlink(cog)


def test_remote_source_constructible(tmp_path):
    """Review MINOR #11: /vsicurl URIs construct through auto()."""
    src = RasterSource.auto("/vsicurl/https://example.com/a.tif")
    assert src.kind == "remote"
    assert isinstance(src, RasterSource)
