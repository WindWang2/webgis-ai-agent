"""AmapProvider behavioral tests via the injected fake-GET seam.

These exercise provider capability methods against canned JSON, without
touching the network or ``tracked_provider_get``. The fake-GET seam
(``AmapProvider(get=fake)``) is the deepening payoff of architecture-review F1:
before F1 these 13 amap impls had zero behavioral coverage because they called
the module-level ``_amap_get`` with no injection point.

Includes two regression tests that lock the fix for the verified
``NameError: name 'asyncio' is not defined`` (and ``aiohttp``) bug — amap.py
previously never imported either module yet used them in the isochrone /
distance-matrix concurrency branches. The fix landed when these methods moved
into ``AmapProvider`` whose module imports ``asyncio``/``aiohttp`` at the top.
"""
import aiohttp

from app.tools.chinese_maps.amap import AmapProvider


class FakeGet:
    """A canned-JSON stand-in for the tracked-GET transport seam.

    Routes by endpoint substring to a canned response dict. Records every call
    so tests can assert request params (incl. CRS-transformed coordinates).
    """

    def __init__(self, routes: dict, default: dict | None = None):
        # routes: {endpoint_substring: response_dict_or_exception}
        self._routes = routes
        self._default = default if default is not None else {}
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, endpoint: str, params: dict) -> dict:
        self.calls.append((endpoint, dict(params)))
        for needle, resp in self._routes.items():
            if needle in endpoint:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        return self._default


# ── search_poi: end-to-end POI shaping via the provider ──────────────────────


async def test_search_poi_amap_shapes_canned_response():
    fake = FakeGet({
        "/place/text": {
            "pois": [
                {"name": "火锅店", "location": "116.40,39.90", "address": "中关村"},
            ],
        },
    })
    prov = AmapProvider(get=fake)
    fc = await prov.search_poi("火锅店", "北京", 20)

    assert fc["type"] == "FeatureCollection"
    assert fc["provider"] == "amap"
    assert fc["count"] == 1
    feat = fc["features"][0]
    assert feat["geometry"]["type"] == "Point"
    assert feat["properties"]["name"] == "火锅店"
    # GCJ-02 → WGS-84: coords must shift away from the raw 116.40 / 39.90.
    out_lng, out_lat = feat["geometry"]["coordinates"]
    assert abs(out_lng - 116.40) > 1e-6 or abs(out_lat - 39.90) > 1e-6
    # request carried the amap field names
    assert fake.calls[0][1]["keywords"] == "火锅店"


async def test_search_poi_amap_propagates_transport_error():
    fake = FakeGet({}, default={"error": "boom"})
    prov = AmapProvider(get=fake)
    out = await prov.search_poi("x", "", 5)
    assert out == {"error": "boom"}


# ── geocode / reverse_geocode: coord round-trip ──────────────────────────────


async def test_geocode_amap_normalizes_to_wgs84():
    fake = FakeGet({
        "/geocode/geo": {
            "geocodes": [{"location": "116.40,39.90", "formatted_address": "北京"}],
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.geocode("北京", "")
    assert out["count"] == 1
    lng, lat = out["results"][0]["location"]
    # transformed away from raw gcj02
    assert abs(lng - 116.40) > 1e-6 or abs(lat - 39.90) > 1e-6


async def test_reverse_geocode_amap_sends_gcj02_location():
    """Input WGS84 must be converted to GCJ-02 in the request location param."""
    fake = FakeGet({
        "/geocode/regeo": {
            "regeocode": {
                "formatted_address": "某地",
                "addressComponent": {},
                "pois": [],
            },
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.reverse_geocode(116.40, 39.90)
    assert out["formatted_address"] == "某地"
    # the request location must differ from the raw wgs84 input (it's gcj02 now)
    req_params = fake.calls[0][1]
    loc_lng, loc_lat = map(float, req_params["location"].split(","))
    assert abs(loc_lng - 116.40) > 1e-6 or abs(loc_lat - 39.90) > 1e-6


# ── route: polyline normalized via one transform pass ────────────────────────


async def test_route_amap_polyline_normalized():
    fake = FakeGet({
        "/direction/driving": {
            "route": {
                "paths": [{
                    "distance": "5000",
                    "duration": "600",
                    "steps": [{
                        "instruction": "go",
                        "distance": "5000",
                        "duration": "600",
                        # two gcj02 points on the polyline
                        "polyline": "116.40,39.90;116.41,39.91",
                    }],
                }],
            },
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.route([116.0, 39.0], [117.0, 40.0], "driving", "")
    assert out["distance_m"] == 5000
    assert out["duration_s"] == 600
    assert len(out["polyline"]) == 2
    # both polyline points normalized away from raw gcj02
    for lng, lat in out["polyline"]:
        assert abs(lng - 116.40) > 1e-6 or abs(lat - 39.90) > 1e-6


async def test_route_amap_no_paths_returns_error():
    fake = FakeGet({"/direction/driving": {"route": {"paths": []}}})
    prov = AmapProvider(get=fake)
    out = await prov.route([116.0, 39.0], [117.0, 40.0], "driving", "")
    assert out == {"error": "未找到路线"}


# ── district: polygon CRS normalization ──────────────────────────────────────


async def test_district_amap_polygon_normalized():
    fake = FakeGet({
        "/config/district": {
            "districts": [{
                "name": "海淀区",
                "center": "116.30,39.95",
                "polyline": "116.30,39.95;116.31,39.95;116.31,39.96;116.30,39.95",
            }],
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.district("海淀区", "district", "polygon")
    assert out["provider"] == "amap"
    assert out["count"] == 1
    geom = out["features"][0]["geometry"]
    assert geom["type"] == "Polygon"
    # polygon ring coords normalized to wgs84
    ring = geom["coordinates"][0]
    for lng, lat in ring:
        assert abs(lng - 116.30) > 1e-6 or abs(lat - 39.95) > 1e-6


async def test_district_amap_point_mode():
    fake = FakeGet({
        "/config/district": {
            "districts": [{"name": "海淀区", "center": "116.30,39.95"}],
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.district("海淀区", "district", "point")
    assert out["features"][0]["geometry"]["type"] == "Point"


# ── distance_matrix: batch path (driving) ────────────────────────────────────


async def test_distance_matrix_amap_driving_batch():
    fake = FakeGet({
        "/distance": {
            "results": [
                {"origin_id": 1, "dest_id": 1, "distance": "1000", "duration": "60"},
                {"origin_id": 1, "dest_id": 2, "distance": "2000", "duration": "120"},
            ],
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.distance_matrix([[116.0, 39.0]], [[117.0, 40.0], [118.0, 41.0]], "driving")
    assert out["mode"] == "driving"
    assert out["provider"] == "amap"
    assert out["matrix"][0][0]["distance_km"] == 1.0
    assert out["matrix"][0][1]["distance_km"] == 2.0


# ── NameError regression: isochrone concurrency + except branches ────────────
#
# amap.py previously never imported asyncio/aiohttp, yet _isochrone_analysis
# used asyncio.Semaphore / asyncio.gather and caught aiohttp.ClientError. Both
# branches raised NameError. Moving into AmapProvider (whose module imports
# both at the top) fixed it. These two tests lock the fix.


async def test_isochrone_concurrency_path_runs_without_nameerror():
    """Exercises the asyncio.gather concurrency path.

    Would raise ``NameError: name 'asyncio' is not defined`` pre-fix when
    ``asyncio.Semaphore`` / ``asyncio.gather`` executed.
    """
    fake = FakeGet({
        # _get_route_distance calls /direction/driving with a strategy param
        "/direction/driving": {
            "route": {"paths": [{"distance": "800"}]},
        },
    })
    prov = AmapProvider(get=fake)
    feat = await prov.isochrone(center=[116.40, 39.90], minutes=10, mode="driving")
    # the concurrency path produced a geometry (Polygon once ≥3 radial points)
    assert feat["type"] == "Feature"
    assert feat["geometry"]["type"] in ("Polygon", "Point")
    assert feat["properties"]["provider"] == "amap"
    assert feat["properties"]["radius_m"] > 0
    # the isochrone fanned out multiple radial probes (12 directions × 2 probes:
    # one far speed probe + one bounded correction probe per radial)
    assert len(fake.calls) >= 12


async def test_isochrone_except_branch_runs_without_nameerror():
    """Exercises the aiohttp.ClientError except branch (fallback radius).

    Would raise ``NameError: name 'aiohttp' is not defined`` pre-fix when the
    except clause referenced ``aiohttp.ClientError``.
    """
    fake = FakeGet({
        "/direction/driving": aiohttp.ClientError("simulated transport failure"),
    })
    prov = AmapProvider(get=fake)
    feat = await prov.isochrone(center=[116.40, 39.90], minutes=5, mode="driving")
    # except branch → fallback uniform-radius circle points → convex hull polygon
    assert feat["geometry"]["type"] in ("Polygon", "Point")
    # fallback radius derived from minutes * speed
    assert feat["properties"]["radius_m"] == 5 * 60 * 13.9  # driving 13.9 m/s


async def test_distance_matrix_riding_fallback_runs_without_nameerror():
    """riding mode uses the N×M asyncio.gather fallback (no batch API).

    Would raise ``NameError`` pre-fix. Locks the second concurrency site.
    """
    fake = FakeGet({
        "/direction/bicycling": {
            "route": {"paths": [{"distance": "3000"}]},
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.distance_matrix([[116.0, 39.0]], [[117.0, 40.0]], "riding")
    assert out["mode"] == "riding"
    assert out["matrix"][0][0] is not None
    assert out["matrix"][0][0]["distance_km"] == 3.0


# ── Baidu provider: representative capability coverage ───────────────────────

from app.tools.chinese_maps.baidu import BaiduProvider


async def test_search_poi_baidu_shapes_canned_response():
    fake = FakeGet({
        "/place/v2/search": {
            "results": [
                {"name": "便利店", "location": {"lng": 116.40, "lat": 39.90}, "address": "中关村"},
            ],
        },
    })
    prov = BaiduProvider(get=fake)
    fc = await prov.search_poi("便利店", "北京", 20)
    assert fc["provider"] == "baidu"
    assert fc["count"] == 1
    # BD-09 → WGS-84 shift
    out_lng, out_lat = fc["features"][0]["geometry"]["coordinates"]
    assert abs(out_lng - 116.40) > 1e-3 or abs(out_lat - 39.90) > 1e-3


async def test_geocode_baidu_normalizes_to_wgs84():
    fake = FakeGet({
        "/geocoding/v3/": {
            "result": {"location": {"lng": 116.40, "lat": 39.90}, "level": "ROAD"},
        },
    })
    prov = BaiduProvider(get=fake)
    out = await prov.geocode("北京", "")
    assert out["count"] == 1
    lng, lat = out["results"][0]["location"]
    assert abs(lng - 116.40) > 1e-3 or abs(lat - 39.90) > 1e-3


async def test_route_baidu_polyline_normalized():
    fake = FakeGet({
        "/directionlite/v1/driving": {
            "result": {
                "routes": [{
                    "distance": 5000,
                    "duration": 600,
                    "steps": [{
                        "instruction": "go",
                        "distance": "5000",
                        "duration": "600",
                        # baidu 'path' field, bd09 coords
                        "path": "116.40,39.90;116.41,39.91",
                    }],
                }],
            },
        },
    })
    prov = BaiduProvider(get=fake)
    out = await prov.route([116.0, 39.0], [117.0, 40.0], "driving", "")
    assert out["distance_m"] == 5000
    assert len(out["polyline"]) == 2
    for lng, lat in out["polyline"]:
        assert abs(lng - 116.40) > 1e-3 or abs(lat - 39.90) > 1e-3


async def test_distance_matrix_baidu_batch():
    fake = FakeGet({
        "/direction/v2/matrix": {
            "result": {
                "rows": [
                    {"elements": [
                        {"distance": {"value": 1000}, "duration": {"value": 60}},
                        {"distance": {"value": 2000}, "duration": {"value": 120}},
                    ]},
                ],
            },
        },
    })
    prov = BaiduProvider(get=fake)
    out = await prov.distance_matrix([[116.0, 39.0]], [[117.0, 40.0], [118.0, 41.0]], "driving")
    assert out["provider"] == "baidu"
    assert out["matrix"][0][0]["distance_km"] == 1.0
    assert out["matrix"][0][1]["distance_km"] == 2.0


# ── Tianditu provider: representative capability coverage (WGS84 identity) ───

from app.tools.chinese_maps.tianditu import TiandituProvider


async def test_search_poi_tianditu_no_crs_shift():
    """Tianditu (CGCS2000 ≈ WGS84): coords pass through unchanged."""
    fake = FakeGet({
        "/search": {
            "pois": [
                {"name": "学校", "lonlat": "116.40 39.90", "address": "某路"},
            ],
        },
    })
    prov = TiandituProvider(get=fake)
    fc = await prov.search_poi("学校", "", 20)
    assert fc["provider"] == "tianditu"
    out_lng, out_lat = fc["features"][0]["geometry"]["coordinates"]
    # identity — no shift
    assert out_lng == 116.40
    assert out_lat == 39.90


async def test_geocode_tianditu_identity():
    fake = FakeGet({
        "/geocoder": {
            "result": {"location": {"lon": 116.40, "lat": 39.90}, "level": "street"},
        },
    })
    prov = TiandituProvider(get=fake)
    out = await prov.geocode("北京", "")
    lng, lat = out["results"][0]["location"]
    assert lng == 116.40
    assert lat == 39.90


async def test_district_tianditu_v2_polygon():
    """district_v2 (non-Protocol, called by admin tools) parses polygons."""
    fake = FakeGet({
        "/administrative": {
            "status": "100",
            "data": [{
                "name": "海淀区",
                "lnt": 116.30,
                "lat": 39.95,
                "points": "116.30,39.95;116.31,39.95;116.31,39.96;116.30,39.95",
            }],
        },
    })
    prov = TiandituProvider(get=fake)
    out = await prov.district_v2("海淀区", child_level=0, return_polygon=True)
    assert out["provider"] == "tianditu"
    geom = out["features"][0]["geometry"]
    assert geom["type"] == "Polygon"
    # tianditu is WGS84 — coords unchanged
    assert geom["coordinates"][0][0] == [116.30, 39.95]
