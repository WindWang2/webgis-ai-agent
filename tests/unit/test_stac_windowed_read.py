"""Issue #381 regression tests: STAC band reads must be cropped to the
request AOI (bbox ∩ item footprint) instead of reading the whole scene tile
and slapping the request bbox onto the full array.

Verified: the returned bands cover only the AOI window, the reported bounds
describe the actual data extent (not the request bbox), NDVI computed from
the windowed bands matches a manual window read, and results flow the real
footprint through SpectralRasterEngine.
"""
import numpy as np
import pytest

from app.services.rs.band_math import compute_index_array

# Synthetic scene: 100 x 80 cells, 0.001 deg pixels, EPSG:4326.
_WEST, _NORTH, _PX = 10.0, 60.0, 0.001
_SCENE_WIDTH, _SCENE_HEIGHT = 100, 80
_SCENE_BOUNDS = [_WEST, _NORTH - _PX * _SCENE_HEIGHT,
                 _WEST + _PX * _SCENE_WIDTH, _NORTH]


def _write_scene(path, values):
    import rasterio
    from rasterio.transform import from_origin
    transform = from_origin(_WEST, _NORTH, _PX, _PX)
    with rasterio.open(
        str(path), "w", driver="GTiff", height=_SCENE_HEIGHT, width=_SCENE_WIDTH,
        count=1, dtype="float64", crs="EPSG:4326", transform=transform,
    ) as dst:
        dst.write(values, 1)


class _FakeAsset:
    def __init__(self, href: str):
        self.href = href


class _FakeItem:
    def __init__(self, item_id: str, bbox, hrefs):
        self.id = item_id
        self.bbox = list(bbox)
        self.assets = {key: _FakeAsset(href) for key, href in hrefs.items()}


class _FakeSearch:
    def __init__(self, items):
        self._items = items

    def items(self):
        return self._items


class _FakeCatalog:
    def __init__(self, items):
        self._items = items

    def search(self, **kwargs):
        return _FakeSearch(self._items)


def _fake_band_values(seed: int) -> np.ndarray:
    """Spatially varying band so a windowed mean is a discriminating check."""
    rng = np.random.default_rng(seed)
    return 100.0 + rng.random((_SCENE_HEIGHT, _SCENE_WIDTH)) * 2000.0


@pytest.fixture
def scene(tmp_path):
    red_path = tmp_path / "red.tif"
    nir_path = tmp_path / "nir.tif"
    red = _fake_band_values(7)
    nir = _fake_band_values(11)
    _write_scene(red_path, red)
    _write_scene(nir_path, nir)
    return red, nir, red_path, nir_path


def _fetch(monkeypatch, item, bbox, bands_needed, ds_factor=1):
    from app.services.rs.stac_client import StacClientPrimitive, stac_primitive
    monkeypatch.setattr(StacClientPrimitive, "_get_catalog",
                        lambda self: _FakeCatalog([item]))
    import asyncio
    return asyncio.run(stac_primitive.fetch_stac_items_and_bands(
        collection="sentinel-2-l2a", bbox=bbox,
        bands_needed=bands_needed, ds_factor=ds_factor,
    ))


def test_windowed_read_crops_to_aoi_and_reports_data_bounds(monkeypatch, scene, tmp_path):
    """#381 acceptance: a small AOI must yield the AOI-sized window (not the
    full scene tile), and result bounds must be the actual data extent —
    here the bbox pokes outside the scene, so bounds == bbox ∩ scene."""
    red, nir, red_path, nir_path = scene
    item = _FakeItem("s2-item", _SCENE_BOUNDS, {"red": str(red_path), "nir": str(nir_path)})

    # AOI extends beyond the scene on the east and south edges.
    bbox = [10.08, 59.90, 10.15, 60.005]
    res = _fetch(monkeypatch, item, bbox, {"red": "red", "nir": "nir"}, ds_factor=1)
    assert "error" not in res, res.get("error")

    # Window expected by hand: col 80..100, row 0..80 of the 100x80 scene.
    assert res["bands"]["red"].shape == (_SCENE_HEIGHT, 20)
    assert res["bands"]["nir"].shape == (_SCENE_HEIGHT, 20)
    # Bounds describe the cropped data footprint, not the request bbox.
    assert res["bounds"] == pytest.approx([10.08, 59.92, 10.1, 60.0], abs=1e-9)


def test_windowed_ndvi_matches_manual_window_read(monkeypatch, scene, tmp_path):
    """#381 acceptance: NDVI over the small AOI equals the NDVI computed from
    a manual windowed read of the same window (ds_factor=1 -> exact)."""
    import rasterio
    from rasterio.windows import Window
    red, nir, red_path, nir_path = scene
    item = _FakeItem("s2-item", _SCENE_BOUNDS, {"red": str(red_path), "nir": str(nir_path)})

    bbox = [10.03, 59.96, 10.07, 59.99]  # pixel-aligned window col 30..70, row 10..40
    res = _fetch(monkeypatch, item, bbox, {"red": "red", "nir": "nir"}, ds_factor=1)
    assert "error" not in res, res.get("error")

    with rasterio.open(str(red_path)) as ds:
        r_win = ds.read(1, window=Window(30, 10, 40, 30))
    with rasterio.open(str(nir_path)) as ds:
        n_win = ds.read(1, window=Window(30, 10, 40, 30))

    assert res["bands"]["red"].shape == (30, 40)
    np.testing.assert_array_equal(res["bands"]["red"], r_win)
    np.testing.assert_array_equal(res["bands"]["nir"], n_win)

    ndvi_stac = compute_index_array("ndvi", red=res["bands"]["red"], nir=res["bands"]["nir"])
    ndvi_manual = compute_index_array("ndvi", red=r_win, nir=n_win)
    np.testing.assert_array_equal(ndvi_stac, ndvi_manual)
    assert float(np.nanmean(ndvi_stac)) == pytest.approx(float(np.nanmean(ndvi_manual)))


def test_windowed_read_downsampled_shape_and_bounds(monkeypatch, scene, tmp_path):
    """out_shape thinning must apply within the window; bounds stay the
    window's outer footprint."""
    import rasterio
    from rasterio.windows import Window
    red, nir, red_path, nir_path = scene
    item = _FakeItem("s2-item", _SCENE_BOUNDS, {"red": str(red_path), "nir": str(nir_path)})

    bbox = [10.03, 59.96, 10.07, 59.99]
    res = _fetch(monkeypatch, item, bbox, {"red": "red", "nir": "nir"}, ds_factor=2)
    assert "error" not in res, res.get("error")
    # window 40x30 at ds_factor=2 -> 20x15
    assert res["bands"]["red"].shape == (15, 20)
    assert res["bounds"] == pytest.approx([10.03, 59.96, 10.07, 59.99], abs=1e-9)


def test_windowed_read_no_overlap_returns_error(monkeypatch, scene, tmp_path):
    red, nir, red_path, nir_path = scene
    item = _FakeItem("s2-item", _SCENE_BOUNDS, {"red": str(red_path), "nir": str(nir_path)})
    res = _fetch(monkeypatch, item, [20.0, 30.0, 21.0, 31.0], {"red": "red"}, ds_factor=1)
    assert "error" in res
    assert "无重叠" in res["error"]


def test_windowed_read_projected_crs_reprojects_bbox(monkeypatch, tmp_path):
    """Sentinel-2 L2A COGs are UTM: the WGS84 request bbox must be
    transformed into the raster CRS before windowing, and the reported
    bounds converted back to WGS84."""
    import math
    import rasterio
    from rasterio.crs import CRS
    from rasterio.transform import from_origin
    from rasterio.warp import transform_bounds
    from rasterio.windows import Window, from_bounds

    path = tmp_path / "utm_band.tif"
    width, height, px = 100, 80, 30.0
    transform = from_origin(500000.0, 6640000.0, px, px)
    rng = np.random.default_rng(3)
    data = 100.0 + rng.random((height, width)) * 2000.0
    with rasterio.open(
        str(path), "w", driver="GTiff", height=height, width=width,
        count=1, dtype="float64", crs="EPSG:32633", transform=transform,
    ) as dst:
        dst.write(data, 1)

    # WGS84 bbox around a mid-scene UTM window.
    wgs_bbox = list(transform_bounds(
        CRS.from_epsg(32633), CRS.from_epsg(4326),
        500000 + 30 * px, 6640000 - 40 * px, 500000 + 70 * px, 6640000 - 10 * px,
    ))
    item = _FakeItem("utm-item", wgs_bbox, {"red": str(path), "nir": str(path)})
    res = _fetch(monkeypatch, item, wgs_bbox, {"red": "red", "nir": "nir"}, ds_factor=1)
    assert "error" not in res, res.get("error")

    # Expected window derived from the same reprojection primitives
    # (transform_bounds -> from_bounds -> snap -> clip), read manually.
    with rasterio.open(str(path)) as ds:
        xmin, ymin, xmax, ymax = transform_bounds(
            CRS.from_epsg(4326), ds.crs, *wgs_bbox, densify_pts=21)
        win = from_bounds(xmin, ymin, xmax, ymax, ds.transform)
        snap_eps = 1e-9
        win = Window(math.floor(win.col_off + snap_eps),
                     math.floor(win.row_off + snap_eps),
                     math.ceil(win.width - snap_eps),
                     math.ceil(win.height - snap_eps))
        win = win.intersection(Window(0, 0, ds.width, ds.height))
        r_win = ds.read(1, window=win)
    # The reprojected bbox may expand the ideal window by at most ~1 px;
    # it must never crop inside the originally requested UTM rect
    # (col 30..70, row 10..40).
    assert win.col_off <= 30 and win.row_off <= 10
    assert win.col_off + win.width >= 70 and win.row_off + win.height >= 40
    assert res["bands"]["red"].shape == (int(win.height), int(win.width))
    np.testing.assert_array_equal(res["bands"]["red"], r_win)
    # Reported bounds are WGS84 and land on the request bbox; the snapped
    # window may expand the ideal rect by at most ~1 pixel (30 m ≈ 5.4e-4
    # deg at lat 60) to guarantee full bbox coverage.
    assert res["bounds"] == pytest.approx(wgs_bbox, abs=6e-4)


# ─── SpectralRasterEngine: bounds flow from the actual data footprint ────


@pytest.mark.asyncio
async def test_engine_compute_index_uses_data_bounds(monkeypatch):
    from app.services.rs.spectral_engine import SpectralRasterEngine
    engine = SpectralRasterEngine()
    data_bounds = [10.08, 59.92, 10.1, 60.0]

    async def fake_fetch(**kwargs):
        return {
            "bands": {"red": np.full((2, 2), 0.2), "nir": np.full((2, 2), 0.6)},
            "bounds": data_bounds,
        }

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    res = await engine.compute_index([10.0, 59.9, 10.2, 60.1], "2024-01-01", "2024-01-02", "ndvi")
    assert res.is_error is False
    assert res.bounds == data_bounds  # 数据实际范围，而非请求 bbox


@pytest.mark.asyncio
async def test_engine_compute_index_falls_back_to_request_bbox(monkeypatch):
    """Stac responses without a bounds key (no bands read) keep legacy bbox."""
    from app.services.rs.spectral_engine import SpectralRasterEngine
    engine = SpectralRasterEngine()
    bbox = [10.0, 59.9, 10.2, 60.1]

    async def fake_fetch(**kwargs):
        return {"bands": {"red": np.full((2, 2), 0.2), "nir": np.full((2, 2), 0.6)}}

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    res = await engine.compute_index(bbox, "2024-01-01", "2024-01-02", "ndvi")
    assert res.is_error is False
    assert res.bounds == bbox


@pytest.mark.asyncio
async def test_engine_compute_terrain_uses_data_bounds(monkeypatch):
    from app.services.rs.spectral_engine import SpectralRasterEngine
    engine = SpectralRasterEngine()
    data_bounds = [10.08, 59.92, 10.1, 60.0]

    async def fake_fetch(**kwargs):
        return {"bands": {"dem": np.zeros((4, 4))}, "cell_size_m": 30.0,
                "bounds": data_bounds}

    monkeypatch.setattr(engine.stac, "fetch_stac_items_and_bands", fake_fetch)
    res = await engine.compute_terrain([10.0, 59.9, 10.2, 60.1], products=["slope"])
    assert res.is_error is False
    assert res.bounds == data_bounds
