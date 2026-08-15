"""Issue #379 regression tests: compass-convention aspect/hillshade and
cos(lat)-corrected east-west DEM cell size.

Aspect must be the downhill azimuth measured clockwise from north (0-360°),
and hillshade must illuminate the hemisphere facing the configured sun
azimuth (previously the mixed convention rotated the light ~90°). Geographic
DEM cell sizes must shrink in x by cos(lat) so east-west slopes are not
underestimated.
"""
import math

import numpy as np
import pytest

from app.services.rs.band_math import compute_aspect, compute_hillshade, compute_slope

CELL = 30.0


def _rising_plane(direction: str, n: int = 16, slope_deg: float = 45.0) -> np.ndarray:
    """Synthetic plane (row 0 = north, col 0 = west) whose elevation rises
    toward ``direction`` ('e'/'n'/'s'/'w') at ``slope_deg`` from horizontal.
    Cell size 30 m, so the per-cell elevation step is 30·tan(slope)."""
    step = CELL * math.tan(math.radians(slope_deg))
    coords = np.arange(n) * step
    east = np.tile(coords, (n, 1)).astype(float)           # z grows with col
    west = np.tile(coords[::-1], (n, 1)).astype(float)     # z shrinks with col
    north = np.tile(coords[::-1], (n, 1)).T.astype(float)  # z shrinks with row (row 0 = north)
    south = np.tile(coords, (n, 1)).T.astype(float)        # z grows with row
    return {"e": east, "w": west, "n": north, "s": south}[direction]


def test_aspect_is_compass_clockwise_from_north():
    """#379: downhill aspect must be compass, clockwise from north:
    east-rising -> 270 (downhill W), north-rising -> 180 (downhill S),
    south-rising -> 0 (downhill N), west-rising -> 90 (downhill E).
    The old arctan2(-dzdy, dzdx) returned the math angle (0/90/270/180)."""
    expected = {"e": 270.0, "n": 180.0, "s": 0.0, "w": 90.0}
    for direction, want in expected.items():
        aspect = compute_aspect(_rising_plane(direction), CELL)
        interior = aspect[1:-1, 1:-1]
        assert np.all(np.isfinite(interior)), direction
        assert float(interior.mean()) == pytest.approx(want, abs=1e-6), direction
        assert np.abs(interior - want).max() < 1e-6, direction


def test_aspect_flat_is_nan():
    aspect = compute_aspect(np.full((9, 9), 1234.0), CELL)
    assert np.all(np.isnan(aspect))


def _expected_hillshade(aspect_deg: float, azimuth: float = 315.0,
                        altitude: float = 45.0) -> float:
    """Closed-form illumination of a 45° plane for the fixed compass model:
    255·(sin(alt)cos(θ) + cos(alt)sin(θ)cos(az - aspect))."""
    return 255.0 * (
        math.sin(math.radians(altitude)) * math.cos(math.radians(45.0))
        + math.cos(math.radians(altitude)) * math.sin(math.radians(45.0))
        * math.cos(math.radians(azimuth - aspect_deg))
    )


def test_hillshade_illumination_hemispheres():
    """#379 acceptance: with the sun at 315° (NW), the north-facing slope is
    the bright hemisphere (~218) and the south-facing slope the dark one
    (~37). The old mirrored convention returned ~37 for the north-facing
    slope (light rotated ~90°)."""
    # South-rising plane -> downhill aspect 0° (north-facing slope).
    hs = compute_hillshade(_rising_plane("s"), CELL, azimuth=315, altitude=45)
    assert float(hs[1:-1, 1:-1].mean()) == pytest.approx(218.0, abs=0.5)
    # North-rising plane -> aspect 180° (south-facing slope).
    hs = compute_hillshade(_rising_plane("n"), CELL, azimuth=315, altitude=45)
    assert float(hs[1:-1, 1:-1].mean()) == pytest.approx(37.0, abs=0.5)


def test_hillshade_matches_closed_form_compass_model():
    """#379: hillshade equals the closed-form compass illumination for every
    cardinal plane and both sun azimuths (315° NW and 45° NE) — no mirroring,
    no rotation of the light source."""
    aspects = {"e": 270.0, "n": 180.0, "s": 0.0, "w": 90.0}
    for direction, aspect_deg in aspects.items():
        for azimuth in (315.0, 45.0):
            hs = compute_hillshade(_rising_plane(direction), CELL,
                                   azimuth=azimuth, altitude=45)
            got = float(hs[1:-1, 1:-1].mean())
            assert got == pytest.approx(_expected_hillshade(aspect_deg, azimuth),
                                        abs=0.1), (direction, azimuth)


def test_slope_plane_recovers_angle():
    """Regression: the Horn slope kernel must keep recovering the true plane
    angle (unchanged by the #379 convention fixes)."""
    for slope_deg in (15.0, 30.0, 45.0):
        dem = _rising_plane("e", slope_deg=slope_deg)
        slope = compute_slope(dem, CELL)
        assert float(slope[1:-1, 1:-1].mean()) == pytest.approx(slope_deg, abs=1e-6)


def test_cell_size_x_doubles_east_west_gradient():
    """#379 (cos(lat) fix): a half-size x cell doubles the x gradient, so an
    east-west plane's slope rises to arctan(2·tan θ); the default (no
    cell_size_x) keeps the legacy cell_size behavior."""
    dem = _rising_plane("e", slope_deg=30.0)
    want = math.degrees(math.atan(2 * math.tan(math.radians(30.0))))
    slope = compute_slope(dem, CELL, cell_size_x=CELL / 2.0)
    assert float(slope[1:-1, 1:-1].mean()) == pytest.approx(want, abs=1e-6)
    # Legacy call shape: aspect direction unchanged, defaults preserved.
    aspect = compute_aspect(dem, CELL)
    assert float(aspect[1:-1, 1:-1].mean()) == pytest.approx(270.0, abs=1e-6)
    assert float(compute_slope(dem, CELL)[1:-1, 1:-1].mean()) == pytest.approx(30.0, abs=1e-6)


# ─── STAC read-site cell size: cos(lat) on the longitude axis ─────────────


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


def _write_dem(path, west, north, size_deg, height, width, crs="EPSG:4326"):
    import rasterio
    from rasterio.transform import from_origin
    transform = from_origin(west, north, size_deg, size_deg)
    with rasterio.open(
        str(path), "w", driver="GTiff", height=height, width=width,
        count=1, dtype="float64", crs=crs, transform=transform,
    ) as dst:
        dst.write(np.zeros((height, width), dtype=np.float64), 1)


@pytest.mark.asyncio
async def test_stac_cell_size_applies_cos_lat_to_east_west(monkeypatch, tmp_path):
    """#379: at the STAC read site, a geographic DEM's x cell size must be
    corrected by cos(lat) (AOI center latitude) — otherwise east-west slopes
    are underestimated ~cos(lat). The y (meridian) cell size is unchanged."""
    from app.services.rs.stac_client import StacClientPrimitive, stac_primitive

    dem_path = tmp_path / "dem.tif"
    _write_dem(dem_path, west=9.99, north=60.01, size_deg=0.001, height=20, width=20)
    item = _FakeItem("dem-item", [10.0, 59.99, 10.02, 60.01], {"data": str(dem_path)})
    monkeypatch.setattr(StacClientPrimitive, "_get_catalog",
                        lambda self: _FakeCatalog([item]))

    res = await stac_primitive.fetch_stac_items_and_bands(
        collection="cop-dem-glo-30",
        bbox=[10.003, 59.997, 10.007, 60.001],
        bands_needed={"dem": "data"},
        ds_factor=1,
    )
    assert "error" not in res
    assert res["bands"]["dem"].shape == (20, 20)
    assert res["cell_size_m"] == pytest.approx(0.001 * 111320.0, rel=1e-9)
    lat = (59.997 + 60.001) / 2.0
    assert res["cell_size_x_m"] == pytest.approx(
        0.001 * 111320.0 * math.cos(math.radians(lat)), rel=1e-9)


@pytest.mark.asyncio
async def test_stac_projected_dem_has_no_cos_lat_correction(monkeypatch, tmp_path):
    """Projected (UTM) DEMs are already in metres on both axes — no x
    correction and no cell_size_x_m key."""
    from app.services.rs.stac_client import StacClientPrimitive, stac_primitive

    dem_path = tmp_path / "dem_utm.tif"
    _write_dem(dem_path, west=500000.0, north=6640000.0, size_deg=30.0,
               height=10, width=10, crs="EPSG:32633")
    item = _FakeItem("dem-utm", [10.0, 59.9, 10.1, 60.0], {"data": str(dem_path)})
    monkeypatch.setattr(StacClientPrimitive, "_get_catalog",
                        lambda self: _FakeCatalog([item]))

    res = await stac_primitive.fetch_stac_items_and_bands(
        collection="cop-dem-glo-30",
        bbox=[10.0, 59.9, 10.1, 60.0],
        bands_needed={"dem": "data"},
        ds_factor=1,
    )
    assert "error" not in res
    assert res["cell_size_m"] == pytest.approx(30.0)
    assert "cell_size_x_m" not in res
