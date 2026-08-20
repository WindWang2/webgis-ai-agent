"""
TDD failing tests for issue #666 (7 work items).

Acceptance from issue:
- Same ref yields identical mvt_capable and bbox via store-time descriptor,
  route fast-path, and route fallback — parametrized over Point/Line/Polygon/GC.
- No descriptor-serving path calls str(data)/json.dumps on full payload.
- Redis meta-key-miss recomputes off-loop and caches; second poll does not re-parse.
- Alias input to GET /layers/descriptor/{ref} resolves without hydration.
"""

import asyncio
import json
import pytest
from unittest.mock import patch

from app.schemas.ref_descriptor import compute_descriptor
from app.services.session_data import MemorySessionStore


def _make_fc(geom_type, coords):
    return {
        "type": "FeatureCollection",
        "features": [
            {"type": "Feature", "geometry": {"type": geom_type, "coordinates": coords}, "properties": {}}
        ],
    }


# ── 1. mvt_capable + bbox parity across fast-path / fallback ──

@pytest.mark.parametrize("geom_type,coords,expected_mvt", [
    ("Point", [116.0, 39.9], True),
    ("LineString", [[0, 0], [1, 1]], True),
    ("Polygon", [[[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]], True),
    ("GeometryCollection", None, False),  # special: geometries member
])
@pytest.mark.asyncio
async def test_mvt_capable_parity(geom_type, coords, expected_mvt):
    """Same ref → identical mvt_capable via store descriptor, authorized seam, and route fallback."""
    if geom_type == "GeometryCollection":
        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": {"type": "GeometryCollection", "geometries": [{"type": "Point", "coordinates": [0, 0]}]}, "properties": {}}
        ]}
    else:
        fc = _make_fc(geom_type, coords)

    # store-time descriptor
    store_desc = compute_descriptor("ref:test", fc)
    assert store_desc.mvt_capable is expected_mvt, f"compute_descriptor mvt for {geom_type}"

    # seam fast-path (Memory)
    store = MemorySessionStore()
    sid = "parity_sid"
    ref_id = await store.store(sid, fc, prefix="data")
    seam = await store.get_ref_descriptor_authorized(sid, ref_id)
    assert seam.success
    assert seam.data["mvt_capable"] is expected_mvt, f"seam mvt for {geom_type}"
    assert seam.data["mvt_capable"] == store_desc.mvt_capable

    # route fallback path: delete descriptor to force fallback, then compute via _compute_descriptor_fallback
    store._descriptors[sid].pop(ref_id, None)
    from app.api.routes.layer import _compute_descriptor_fallback
    fallback = await asyncio.to_thread(_compute_descriptor_fallback, fc)
    # After fix, route derives mvt from geom_types (same as compute_descriptor), not len(points)
    route_mvt = bool(set(fallback["geom_types"]) - {"GeometryCollection"}) and len(fallback["features"]) > 0
    assert route_mvt == store_desc.mvt_capable, (
        f"fallback mvt diverged for {geom_type}: route={route_mvt} vs store={store_desc.mvt_capable}"
    )
    # Also ensure fallback estimated_bytes is heuristic, not str length
    expected_est = len(fallback["features"]) * 100 + 1024 if fallback["features"] else 1024
    assert fallback["estimated_bytes"] == expected_est, f"estimated_bytes heuristic mismatch for {geom_type}"
    # For non-GC, bbox should be non-None and identical across paths
    if geom_type != "GeometryCollection":
        assert fallback["bbox"] == store_desc.bbox, f"bbox mismatch for {geom_type}: {fallback['bbox']} vs {store_desc.bbox}"


@pytest.mark.asyncio
async def test_bbox_polygon_hole_covered():
    """Polygon hole coordinates must be included in bbox."""
    # Exterior 0-10, hole extends to -5..15 (invalid but tests coverage)
    fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [
        [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]],
        [[-5, -5], [-5, 15], [15, 15], [15, -5], [-5, -5]],
    ]}, "properties": {}}]}
    desc = compute_descriptor("ref:hole", fc)
    assert desc.bbox == [-5, -5, 15, 15], f"hole bbox not covered: {desc.bbox}"
    # All MVT types must have non-None bbox when features present (except GC)
    for gtype, coords in [
        ("Point", [1, 2]),
        ("MultiPoint", [[1, 2], [3, 4]]),
        ("LineString", [[0, 0], [1, 1]]),
        ("MultiLineString", [[[0, 0], [1, 1]], [[2, 2], [3, 3]]]),
        ("Polygon", [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]),
        ("MultiPolygon", [[[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]]),
    ]:
        fc2 = _make_fc(gtype, coords)
        d = compute_descriptor("ref:b", fc2)
        assert d.bbox is not None, f"bbox missing for {gtype}"
        # must cover every leaf coordinate
        # simple check: min/max encloses all leaf points via recursive extraction
        def iter_leaves(c):
            if isinstance(c, list) and c and isinstance(c[0], (int, float)):
                yield c[0], c[1]
            elif isinstance(c, list):
                for x in c:
                    yield from iter_leaves(x)
        leaves = list(iter_leaves(coords))
        lons, lats = zip(*leaves)
        assert d.bbox[0] == min(lons) and d.bbox[2] == max(lons)


# ── 2. No str(data)/json.dumps on full payload in descriptor paths ──

@pytest.mark.asyncio
async def test_fallback_does_not_str_serialize():
    """_compute_descriptor_fallback must not call len(str(data)) on big payload."""
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0 + i*0.001, 39.9]}, "properties": {"id": i}}
        for i in range(5000)
    ]}
    # heuristic expected:
    expected = 5000 * 100 + 1024
    from app.api.routes.layer import _compute_descriptor_fallback
    # Spy on str and json.dumps
    import app.api.routes.layer as layer_mod
    import app.schemas.ref_descriptor as desc_mod
    for mod in (layer_mod, desc_mod):
        src = open(mod.__file__).read()
        # No descriptor path should fully serialize a large payload: neither branch may call
        # json.dumps/str on the whole payload to estimate bytes.
        # For FC path, heuristic must be used; check that the file does not contain a
        # payload-size json.dumps/str in the estimate branch (allow json.dumps for tiny descriptor dict).
        assert "len(str(data))" not in src, f"{mod.__name__} still does len(str(data)) — violates work item 2"
        # The non-FC fallback in ref_descriptor previously did len(json.dumps(data)); ensure gone
        # Search for len(json.dumps(data patterns — large-payload serialization
        assert "len(json.dumps(data" not in src, f"{mod.__name__} still serializes full payload via json.dumps"
    # Also runtime check: estimated_bytes must be heuristic, not len(str)
    res = await asyncio.to_thread(_compute_descriptor_fallback, fc)
    assert res["estimated_bytes"] == expected, f"estimated_bytes should be heuristic {expected}, got {res['estimated_bytes']}"
    assert res["estimated_bytes"] != len(str(fc)), "fallback still uses str length"


def test_shared_helpers_public():
    """Review fix 1: iter_leaf_coords and is_mvt_capable must be public and single-sourced."""
    import app.schemas.ref_descriptor as m
    assert hasattr(m, "iter_leaf_coords"), "iter_leaf_coords must be public"
    assert hasattr(m, "is_mvt_capable"), "is_mvt_capable must be public"
    # is_mvt_capable is used by both compute_descriptor and layer fallback — check import in layer
    import app.api.routes.layer as lm
    src = open(lm.__file__).read()
    assert "is_mvt_capable" in src, "layer.py must use shared is_mvt_capable"
    assert "iter_leaf_coords" in src, "layer.py must use public iter_leaf_coords"
    assert "_iter_leaf_coords" not in src or "_iter_leaf_coords = iter_leaf_coords" in src, "layer.py should not import private _iter_leaf_coords"
    # Back-compat alias still exists
    assert hasattr(m, "_iter_leaf_coords")
    # is_mvt_capable semantics spot-check
    assert m.is_mvt_capable(["Point"], 1) is True
    assert m.is_mvt_capable(["LineString"], 1) is True
    assert m.is_mvt_capable(["GeometryCollection"], 1) is False
    assert m.is_mvt_capable([], 10) is False
    assert m.is_mvt_capable(["Point"], 0) is False


@pytest.mark.asyncio
async def test_non_fc_payload_no_serialization_and_heuristic():
    """Review fix 2: non-FC / raster-wrapper branch must use heuristic, never full serialization."""
    from app.schemas.ref_descriptor import compute_descriptor
    from app.api.routes.layer import _compute_descriptor_fallback
    # Simulate raster-wrapper large payload (non-FC) — 5 MB string would have blown up if serialized
    large_payload = {"file_path": "/data/raster.tif", "type": "raster", "extra": "x" * 50000}
    # Also wrapped non-FC with geojson absent
    for payload in [large_payload, {"foo": "bar"}, {"type": "raster", "path": "/tmp/a.tif"}]:
        desc = compute_descriptor("ref:nonfc", payload)
        fb = await asyncio.to_thread(_compute_descriptor_fallback, payload)
        # Both paths must yield heuristic 1024 and identical fields, without serializing
        assert desc.estimated_bytes == 1024, f"compute_descriptor non-FC should be 1024, got {desc.estimated_bytes}"
        assert fb["estimated_bytes"] == 1024
        assert desc.mvt_capable is False
        assert fb["geom_types"] == []
        # Work-count style: patch json.dumps and ensure it is not called with the large payload
        with patch("json.dumps", wraps=json.dumps) as spy_dumps:
            # compute_descriptor should not json.dumps the full large payload
            d2 = compute_descriptor("ref:nonfc2", payload)
            assert d2.estimated_bytes == 1024
            for call in spy_dumps.call_args_list:
                arg = call.args[0] if call.args else None
                assert arg is not payload, "compute_descriptor serialized full non-FC payload"
            # _compute_descriptor_fallback similarly
            fb2 = await asyncio.to_thread(_compute_descriptor_fallback, payload)
            assert fb2["estimated_bytes"] == 1024


@pytest.mark.asyncio
async def test_wrapped_payload_unwrapping_parity():
    """Review fix 3: wrapped payload unwrapping must be identical in both paths."""
    from app.schemas.ref_descriptor import compute_descriptor
    from app.api.routes.layer import _compute_descriptor_fallback
    fc = _make_fc("Point", [116.0, 39.9])
    wrapped_variants = [
        fc,  # bare FC
        {"geojson": fc},  # {"geojson": FC}
        {"type": "tool_result", "geojson": fc},  # {"type":..., "geojson": FC}
        {"type": "poi_query", "geojson": fc},
    ]
    for payload in wrapped_variants:
        desc = compute_descriptor("ref:wrap", payload)
        fb = await asyncio.to_thread(_compute_descriptor_fallback, payload)
        # Both must see same feature_count/geometry/mvt/bbox/estimate
        assert desc.feature_count == len(fb["features"]) == 1
        assert desc.geometry_types == sorted(fb["geom_types"])
        # Use shared helper for mvt parity
        from app.schemas.ref_descriptor import is_mvt_capable
        assert desc.mvt_capable == is_mvt_capable(fb["geom_types"], len(fb["features"]))
        assert is_mvt_capable(desc.geometry_types, desc.feature_count) == is_mvt_capable(fb["geom_types"], len(fb["features"]))
        assert desc.mvt_capable is True  # Point → capable
        # bbox identical
        assert desc.bbox == fb["bbox"] == [116.0, 39.9, 116.0, 39.9]
        # estimated_bytes identical (heuristic)
        assert desc.estimated_bytes == fb["estimated_bytes"] == 1 * 100 + 1024
    # Raster-ish non-FC wrapped should also be consistent (already covered but add explicit)
    raster_wrapped = {"file_path": "/data/a.tif", "geojson": None}
    desc2 = compute_descriptor("ref:raster", raster_wrapped)
    fb2 = await asyncio.to_thread(_compute_descriptor_fallback, raster_wrapped)
    assert desc2.estimated_bytes == fb2["estimated_bytes"] == 1024
    assert desc2.mvt_capable is False
    assert fb2["geom_types"] == []


@pytest.mark.asyncio
async def test_descriptor_serving_never_json_dumps_payload():
    """Memory seam and route fast-path must not hydrate payload → no json.dumps on full payload."""
    store = MemorySessionStore()
    sid = "no_dump_sid"
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0, 39.9]}, "properties": {}}
    ]}
    ref_id = await store.store(sid, fc, prefix="data")
    # Counting mock for json.dumps
    with patch("json.dumps", wraps=json.dumps) as spy_dumps, patch.object(store, "get", wraps=store.get) as spy_get:
        res = await store.get_ref_descriptor_authorized(sid, ref_id)
        assert res.success
        assert spy_get.call_count == 0
        # json.dumps may be called for small descriptor dict but must never be called with the full FC
        for call in spy_dumps.call_args_list:
            arg = call.args[0] if call.args else call.kwargs.get("obj")
            # if arg is the full FC, it would have 1 feature with Point
            if isinstance(arg, dict) and arg.get("type") == "FeatureCollection":
                # ensure it's not the full payload serialization
                assert len(arg.get("features", [])) != 1 or "Point" not in str(arg), "descriptor path serialized full payload"


# ── 3. Redis meta-miss off-loop + cache ──

@pytest.mark.asyncio
async def test_redis_meta_miss_caches_second_poll():
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore
    store = RedisSessionStore(redis_url="redis://unused", redis=fakeredis.aioredis.FakeRedis(decode_responses=False))
    sid = "redis_cache_sid"
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [116.0 + i*0.001, 39.9]}, "properties": {"id": i}}
        for i in range(100)
    ]}
    ref_id = await store.store(sid, fc, prefix="data")
    # delete meta key to simulate miss
    await store._r.delete(store._descriptor_key(sid, ref_id))
    # first poll recomputes
    d1 = await store.get_ref_descriptor(sid, ref_id)
    assert d1 is not None and d1["feature_count"] == 100
    # meta key must now exist (cached)
    raw = await store._r.get(store._descriptor_key(sid, ref_id))
    assert raw is not None, "meta key not cached after recompute"
    # second poll must not re-parse data key: spy on _r.get for data key
    data_key = store._data_key(sid, ref_id)
    calls = []
    orig_get = store._r.get
    async def spy(key, *a, **kw):
        calls.append(key.decode() if isinstance(key, bytes) else key)
        return await orig_get(key, *a, **kw)
    # wrap after cache is present
    store._r.get = spy
    try:
        d2 = await store.get_ref_descriptor(sid, ref_id)
    finally:
        store._r.get = orig_get
    assert d2 is not None
    # second poll should only hit descriptor key, not data key
    assert data_key not in calls, f"second poll re-parsed data key: {calls}"
    assert d1 == d2

    # off-loop check: recompute path must call asyncio.to_thread (check source)
    import app.services.session_data_redis as rmod
    src = open(rmod.__file__).read()
    # the fallback block should contain asyncio.to_thread for json.loads / compute
    assert "asyncio.to_thread" in src, "Redis fallback must do recompute off-loop"


# ── 4. Alias resolution inside seam ──

@pytest.mark.asyncio
async def test_alias_resolves_in_descriptor_seam():
    # Memory
    store = MemorySessionStore()
    sid = "alias_sid"
    fc = _make_fc("Point", [116.0, 39.9])
    ref_id = await store.store(sid, fc, prefix="data")
    alias = "my-alias"
    await store.set_alias(sid, ref_id, alias)
    res = await store.get_ref_descriptor_authorized(sid, alias)
    assert res.success is True, f"alias should resolve, got {res.error_type}: {res.error}"
    assert res.data["ref_id"] == ref_id
    # ensure it did NOT fall back to hydration (get not called)
    with patch.object(store, "get", wraps=store.get) as spy_get:
        res2 = await store.get_ref_descriptor_authorized(sid, alias)
        assert res2.success
        assert spy_get.call_count == 0

    # Redis
    import fakeredis.aioredis
    from app.services.session_data_redis import RedisSessionStore
    rstore = RedisSessionStore(redis_url="redis://unused", redis=fakeredis.aioredis.FakeRedis(decode_responses=False))
    ref2 = await rstore.store(sid, fc, prefix="data")
    await rstore.set_alias(sid, ref2, alias)
    rres = await rstore.get_ref_descriptor_authorized(sid, alias)
    assert rres.success is True, f"redis alias should resolve, got {rres.error_type}"
    assert rres.data["ref_id"] == ref2


@pytest.mark.asyncio
async def test_alias_missing_still_404():
    """Genuinely missing alias/ref must still be 404, not 403 leakage."""
    store = MemorySessionStore()
    sid = "alias_missing_sid"
    res = await store.get_ref_descriptor_authorized(sid, "no-such-alias")
    assert res.success is False
    assert res.error_type == "NotFound"


@pytest.mark.asyncio
async def test_alias_permission_denied_preserved():
    """Token mismatch on alias must still be PermissionDenied."""
    store = MemorySessionStore()
    sid = "alias_perm_sid"
    await store.set_map_state(sid, "owner_token", "secret")
    fc = _make_fc("Point", [116.0, 39.9])
    ref_id = await store.store(sid, fc, prefix="data")
    await store.set_alias(sid, ref_id, "alias1")
    res = await store.get_ref_descriptor_authorized(sid, "alias1", owner_token="wrong")
    assert res.success is False
    assert res.error_type == "PermissionDenied"
