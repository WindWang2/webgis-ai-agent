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


# ── distance_matrix: the REAL /v3/distance contract (driving/walking) ────────
#
# Issue #440: the driving/walking branch was written against an imagined N×M
# batch API. The real contract (lbs.amap.com webservice direction#t7):
#   - origins: |-separated, at most 100 points per request
#   - destination: a SINGLE coordinate point (multi-destination unsupported)
#   - results: one entry per origin, origin_id 1-based within the request,
#     dest_id always "1"
# So an N×M matrix = one request per destination; each request's results fill
# exactly one matrix column. The old test mocked a multi-dest_id shape the API
# never returns, keeping CI green while the feature was broken.

from app.utils.coord_transform import wgs84_to_gcj02

_ORIGINS_3 = [[116.0, 39.0], [116.1, 39.1], [116.2, 39.2]]
_DESTS_3 = [[117.0, 40.0], [118.0, 41.0], [119.0, 42.0]]


def _dest_index_of(dest_str: str, wgs_dests: list) -> int:
    """Map a request's single `destination` param back to its WGS84 index."""
    lng, lat = map(float, dest_str.split(","))
    for di, (w_lng, w_lat) in enumerate(wgs_dests):
        gx, gy = wgs84_to_gcj02(w_lng, w_lat)
        if abs(gx - lng) < 1e-9 and abs(gy - lat) < 1e-9:
            return di
    raise AssertionError(f"unknown destination param: {dest_str}")


class DistanceContractFakeGet:
    """Fake GET that ENFORCES the documented /v3/distance request contract.

    Every request must carry a single destination and |-separated origins
    (≤100); the response uses the real shape (per-origin results, dest_id=1).
    """

    def __init__(self, wgs_dests: list, dist_fn, n_origins: int):
        self.calls: list[tuple[str, dict]] = []
        self._wgs_dests = wgs_dests
        self._dist_fn = dist_fn
        self._n_origins = n_origins
        self._seen_origins: dict[int, int] = {}  # per-destination chunk offset

    async def __call__(self, endpoint: str, params: dict) -> dict:
        assert endpoint == "/distance", f"wrong endpoint: {endpoint}"
        self.calls.append((endpoint, dict(params)))
        # contract: single destination, |-joined origins, ≤100 per request
        assert "|" not in params["destination"] and ";" not in params["destination"], (
            f"destination must be a single point, got: {params['destination']}"
        )
        origins = params["origins"].split("|")
        assert ";" not in params["origins"], (
            f"origins must be |-separated, got: {params['origins']}"
        )
        assert 1 <= len(origins) <= 100, f"origins per request must be ≤100, got {len(origins)}"
        di = _dest_index_of(params["destination"], self._wgs_dests)
        base = self._seen_origins.get(di, 0)
        self._seen_origins[di] = base + len(origins)
        return {
            "status": "1",
            "results": [
                {
                    # origin_id is chunk-local (1-based); dist_fn gets the global index
                    "origin_id": str(i + 1),
                    "dest_id": "1",
                    "distance": str(self._dist_fn(base + i, di)),
                    "duration": str(60 * (base + i + 1)),
                }
                for i in range(len(origins))
            ],
        }


async def test_distance_matrix_amap_driving_matches_v3_distance_contract():
    """3×3 driving matrix via one single-destination request per destination."""
    fake = DistanceContractFakeGet(
        _DESTS_3, dist_fn=lambda oi, di: 1000 * (oi + 1) + 100 * (di + 1), n_origins=3,
    )
    prov = AmapProvider(get=fake)
    out = await prov.distance_matrix(_ORIGINS_3, _DESTS_3, "driving")

    # one request per destination — multi-destination calls must split
    assert len(fake.calls) == 3
    assert out["mode"] == "driving"
    assert out["provider"] == "amap"
    assert out["origins_count"] == 3
    assert out["dests_count"] == 3
    assert out["matrix"][0][1] is not None
    # every cell present and correctly placed (distance encodes (oi, di))
    for oi in range(3):
        for di in range(3):
            cell = out["matrix"][oi][di]
            assert cell is not None, f"cell [{oi}][{di}] missing"
            assert cell["origin_index"] == oi
            assert cell["dest_index"] == di
            assert cell["distance_km"] == (1000 * (oi + 1) + 100 * (di + 1)) / 1000.0
            assert cell["duration_sec"] == 60 * (oi + 1)
    # each request carried 3 |-joined origins and driving type=1
    for _, params in fake.calls:
        assert len(params["origins"].split("|")) == 3
        assert params["type"] == "1"


async def test_distance_matrix_amap_walking_uses_type_3():
    fake = DistanceContractFakeGet(_DESTS_3[:1], dist_fn=lambda oi, di: 500, n_origins=2)
    prov = AmapProvider(get=fake)
    out = await prov.distance_matrix(_ORIGINS_3[:2], _DESTS_3[:1], "walking")
    assert out["mode"] == "walking"
    assert fake.calls[0][1]["type"] == "3"
    assert out["matrix"][0][0]["distance_km"] == 0.5


async def test_distance_matrix_amap_chunks_over_100_origins():
    """>100 origins must split into ≤100-origin requests (contract cap)."""
    origins = [[116.0 + i * 0.001, 39.0] for i in range(150)]
    dests = [[117.0, 40.0]]
    # local origin index per request; single destination → local == global
    fake = DistanceContractFakeGet(dests, dist_fn=lambda oi, di: 100 * (oi + 1), n_origins=150)
    prov = AmapProvider(get=fake)
    out = await prov.distance_matrix(origins, dests, "driving")

    sizes = [len(params["origins"].split("|")) for _, params in fake.calls]
    assert sizes == [100, 50], f"expected 100+50 chunking, got {sizes}"
    assert len(out["matrix"]) == 150
    # chunk-local origin_id maps back to the correct global row
    for oi in range(150):
        cell = out["matrix"][oi][0]
        assert cell is not None, f"row {oi} missing"
        assert cell["distance_km"] == 100 * (oi + 1) / 1000.0


async def test_distance_matrix_amap_all_destinations_error_returns_error():
    """Every per-destination request failed → the error dict surfaces."""
    async def fake(endpoint, params):
        return {"error": "Amap API HTTP 418"}

    prov = AmapProvider(get=fake)
    out = await prov.distance_matrix(_ORIGINS_3[:1], _DESTS_3[:2], "driving")
    assert out == {"error": "Amap API HTTP 418"}


async def test_distance_matrix_amap_partial_failure_surfaces_errors():
    """One destination failing must not discard the successful columns."""
    wgs_dests = _DESTS_3[:2]

    async def fake(endpoint, params):
        di = _dest_index_of(params["destination"], wgs_dests)
        if di == 1:
            return {"error": "Amap API HTTP 429"}
        return {
            "status": "1",
            "results": [
                {"origin_id": "1", "dest_id": "1", "distance": "1000", "duration": "60"},
            ],
        }

    prov = AmapProvider(get=fake)
    out = await prov.distance_matrix(_ORIGINS_3[:1], wgs_dests, "driving")
    # column 0 filled, column 1 None, failure surfaced
    assert out["matrix"][0][0] is not None
    assert out["matrix"][0][0]["distance_km"] == 1.0
    assert out["matrix"][0][1] is None
    assert any("429" in e for e in out["errors"])


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


# ── route transit: the REAL /direction/transit/integrated contract ──────────
#
# Issue #542: route(mode="transit") read ``route.paths``, but the transit
# endpoint returns plans under ``route.transits`` — the branch was dead code
# that always returned 未找到路线. It must now shape the first transit plan.


async def test_route_amap_transit_reads_transits_key():
    fake = FakeGet({
        "/direction/transit/integrated": {
            "route": {
                "transits": [{
                    "distance": "5000",
                    "duration": "1500",
                    "cost": "4",
                    "segments": [
                        {
                            "walking": {"steps": [
                                {"polyline": "116.40,39.90;116.41,39.91"},
                            ]},
                            "bus": {"buslines": [
                                {"name": "320路", "distance": "4200", "duration": "1200",
                                 "polyline": "116.41,39.91;116.42,39.92"},
                            ]},
                        },
                    ],
                }],
            },
        },
    })
    prov = AmapProvider(get=fake)
    out = await prov.route([116.0, 39.0], [117.0, 40.0], "transit", "北京")
    assert out["provider"] == "amap"
    assert out["distance_m"] == 5000
    assert out["duration_s"] == 1500
    # polyline assembled from walking steps + bus buslines, normalized to WGS84
    assert len(out["polyline"]) == 4
    for lng, lat in out["polyline"]:
        assert abs(lng - 116.40) > 1e-6 or abs(lat - 39.90) > 1e-6
    assert out["steps"][0]["instruction"] == "320路"
    # the caller's city reached the request params
    assert fake.calls[0][1]["city"] == "北京"


async def test_route_amap_transit_empty_transits_returns_error():
    fake = FakeGet({"/direction/transit/integrated": {"route": {"transits": []}}})
    prov = AmapProvider(get=fake)
    out = await prov.route([116.0, 39.0], [117.0, 40.0], "transit", "北京")
    assert out == {"error": "未找到路线"}


# ── Baidu distance_matrix: the REAL /routematrix/v2 contract ─────────────────
#
# Issue #542: the old test mocked a made-up ``/direction/v2/matrix`` with
# ``rows[].elements[]`` — an endpoint and response shape the Baidu API never
# returns. The real contract: GET /routematrix/v2/{driving|riding|walking}
# with plural pipe-joined origins/destinations, flat row-major ``result``.


async def _baidu_matrix_response(distances: list[float], durations: list[int]) -> dict:
    return {
        "status": 0,
        "message": "ok",
        "result": [
            {"distance": {"text": "x", "value": d}, "duration": {"text": "x", "value": t}}
            for d, t in zip(distances, durations)
        ],
    }


async def test_distance_matrix_baidu_route_matrix_contract_and_params():
    """Request contract: routematrix/v2/{mode} endpoint, plural origins/
    destinations params; response: flat row-major elements → correct matrix."""
    fake = FakeGet({
        "/routematrix/v2/driving": await _baidu_matrix_response(
            [1000.0, 2000.0, 3000.0, 4000.0], [60, 120, 180, 240],
        ),
    })
    prov = BaiduProvider(get=fake)
    out = await prov.distance_matrix(
        [[116.0, 39.0], [116.1, 39.1]],
        [[117.0, 40.0], [118.0, 41.0]],
        "driving",
    )

    assert out["provider"] == "baidu"
    assert out["origins_count"] == 2
    assert out["dests_count"] == 2
    # row-major: [o0d0=1km, o0d1=2km, o1d0=3km, o1d1=4km]
    assert out["matrix"][0][0]["distance_km"] == 1.0
    assert out["matrix"][0][0]["duration_sec"] == 60
    assert out["matrix"][0][1]["distance_km"] == 2.0
    assert out["matrix"][1][0]["distance_km"] == 3.0
    assert out["matrix"][1][1]["distance_km"] == 4.0

    endpoint, params = fake.calls[0]
    assert "routematrix/v2/driving" in endpoint
    assert "origin" not in params and "destination" not in params  # singular gone
    assert "mode" not in params  # no car/foot/bike mode param
    assert "|" in params["origins"] and "|" in params["destinations"]
    assert params["origins"].count("|") == 1
    assert params["destinations"].count("|") == 1


async def test_distance_matrix_baidu_missing_cells_are_none():
    """A truncated (missing-element) response → None cells, not a crash."""
    fake = FakeGet({
        "/routematrix/v2/walking": await _baidu_matrix_response([500.0], [90]),
    })
    prov = BaiduProvider(get=fake)
    out = await prov.distance_matrix(
        [[116.0, 39.0]], [[117.0, 40.0], [118.0, 41.0]], "walking",
    )
    assert "routematrix/v2/walking" in fake.calls[0][0]
    assert out["matrix"][0][0]["distance_km"] == 0.5
    assert out["matrix"][0][1] is None
    assert out["provider"] == "baidu"


# ── Tianditu provider: representative capability coverage (WGS84 identity) ───

from app.tools.chinese_maps.tianditu import TiandituProvider


async def test_search_poi_tianditu_no_crs_shift():
    """Tianditu (CGCS2000 ≈ WGS84): coords pass through unchanged."""
    fake = FakeGet({
        "/v2/search": {
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
