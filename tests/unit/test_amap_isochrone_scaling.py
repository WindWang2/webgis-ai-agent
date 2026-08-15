"""Regression tests for the Amap isochrone scaling fix (#380).

The old implementation probed each radial with a FIXED ~1.1 km (driving) /
~390 m (riding) / ~66 m (walking) route and then interpolated with
``min(ratio, 1.0)`` — the ratio was always > 1, so every ``minutes`` value
produced the same probe-distance polygon while ``radius_m`` (speed × time)
contradicted the geometry.

The new algorithm probes beyond the nominal budget once to MEASURE the real
route speed, scales proportionally (speed × time), then runs one bounded
correction probe. Fallback circles are cos(lat)-corrected on the lng axis.

All tests run through the injected fake-GET seam — no real network calls.
"""
import math

import aiohttp

from app.tools.chinese_maps.amap import AmapProvider
from app.utils.coord_transform import wgs84_to_gcj02
from shapely.geometry import shape


class FixedRouteFake:
    """Returns a constructed route (distance/duration) for every direction probe.

    ``dist_fn(dest_lng, dest_lat) -> (distance_m, duration_s)`` lets tests
    simulate fixed routes (independent of probe length) or speed-aware routes
    (proportional to the probe distance).
    """

    def __init__(self, dist_fn):
        self._dist_fn = dist_fn
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, endpoint: str, params: dict) -> dict:
        self.calls.append((endpoint, dict(params)))
        d_lng, d_lat = map(float, params["destination"].split(","))
        dist_m, dur_s = self._dist_fn(d_lng, d_lat)
        return {"route": {"paths": [{"distance": str(dist_m), "duration": str(dur_s)}]}}


def _polygon_bbox_meters(feature, center_lat):
    """Bounding box of the polygon in meters, using cos(lat) degree scaling."""
    geom = shape(feature["geometry"])
    minx, miny, maxx, maxy = geom.bounds
    cos_lat = max(math.cos(math.radians(center_lat)), 0.02)
    width_m = (maxx - minx) * 111320.0 * cos_lat
    height_m = (maxy - miny) * 111320.0
    return width_m, height_m


# ── Fixed GET: polygon must grow with minutes ──────────────────────────────


async def test_isochrone_polygon_grows_with_minutes():
    """A fixed route (800 m / 60 s ≈ 13.3 m/s, close to nominal driving) must
    yield strictly growing polygons for 5 / 15 / 30 minutes — previously all
    three minutes saturated at the same ~1.1 km probe polygon."""
    fake = FixedRouteFake(lambda lng, lat: (800.0, 60.0))
    prov = AmapProvider(get=fake)

    radii = []
    polygons = []
    for minutes in (5, 15, 30):
        feat = await prov.isochrone(center=[116.40, 39.90], minutes=minutes, mode="driving")
        assert feat["geometry"]["type"] == "Polygon"
        radii.append(feat["properties"]["radius_m"])
        polygons.append(feat["geometry"])

    assert radii[0] < radii[1] < radii[2]
    # 15 min driving ≈ speed × time order of magnitude (13.3 m/s × 900 s ≈ 12 km)
    nominal_15 = 15 * 60 * 13.9
    assert 0.7 * nominal_15 < radii[1] < 1.3 * nominal_15
    # distinct minutes → distinct polygons
    assert polygons[0] != polygons[1]
    assert polygons[1] != polygons[2]
    # radius_m now matches the geometry (mean radial distance of the hull points)
    width_m, height_m = _polygon_bbox_meters(feat, 39.90)
    assert 0.8 * width_m / 2 < radii[2] < 1.3 * width_m / 2
    assert 0.8 * height_m / 2 < radii[2] < 1.3 * height_m / 2


async def test_isochrone_uses_measured_route_speed_not_nominal():
    """When the network is slower than nominal (route speed ≈ 0.7 × nominal),
    the isochrone must shrink accordingly — the old code always returned the
    probe-distance polygon regardless of measured speed."""
    center = [116.40, 39.90]
    gcj_center = wgs84_to_gcj02(center[0], center[1])
    cos_lat = math.cos(math.radians(gcj_center[1]))
    measured_speed = 0.7 * 13.9

    def dist_fn(d_lng, d_lat):
        dx = (d_lng - gcj_center[0]) * 111320.0 * cos_lat
        dy = (d_lat - gcj_center[1]) * 111320.0
        dist_m = math.hypot(dx, dy) * 0.7  # route is 30% slower per meter
        return dist_m, dist_m / measured_speed

    prov = AmapProvider(get=FixedRouteFake(dist_fn))
    feat = await prov.isochrone(center=center, minutes=15, mode="driving")

    expected = measured_speed * 15 * 60  # ≈ 8.76 km
    nominal = 13.9 * 15 * 60             # ≈ 12.5 km
    radius = feat["properties"]["radius_m"]
    assert abs(radius - expected) < 0.03 * expected, f"radius {radius} vs {expected}"
    assert radius < 0.8 * nominal  # must track measured speed, not nominal


# ── Fallback circle: cos(lat) correction + speed×time radius ───────────────


async def test_isochrone_fallback_circle_cos_lat_corrected():
    """All probes failing must fall back to the speed × time circle. The lng
    axis must be scaled by cos(lat) so the ground bbox is square — the old
    fallback divided both axes by 111,000 and produced an east-west bbox
    ~1/cos(40°) wider than the north-south one."""
    def boom(*_a, **_k):
        raise aiohttp.ClientError("simulated transport failure")

    prov = AmapProvider(get=boom)
    feat = await prov.isochrone(center=[116.40, 40.00], minutes=15, mode="driving")

    nominal = 15 * 60 * 13.9
    assert feat["properties"]["radius_m"] == nominal
    width_m, height_m = _polygon_bbox_meters(feat, 40.00)
    # square-ish circle: width and height within 2% of each other
    assert abs(width_m - height_m) < 0.02 * height_m, (width_m, height_m)
    assert 0.95 * 2 * nominal < width_m < 1.05 * 2 * nominal
    assert 0.95 * 2 * nominal < height_m < 1.05 * 2 * nominal


async def test_isochrone_fallback_walking_radius_matches_geometry():
    """Walking fallback: 30 minutes at 1.4 m/s → ~2.5 km circle, not the old
    ~67 m probe polygon."""
    def boom(*_a, **_k):
        raise aiohttp.ClientError("boom")

    prov = AmapProvider(get=boom)
    feat = await prov.isochrone(center=[116.40, 39.90], minutes=30, mode="walking")

    expected = 30 * 60 * 1.4
    assert feat["properties"]["radius_m"] == expected
    width_m, height_m = _polygon_bbox_meters(feat, 39.90)
    assert 0.9 * 2 * expected < height_m < 1.1 * 2 * expected
