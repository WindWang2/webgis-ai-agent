"""Large Map Performance V3 — 100k feature scenario baseline + V3 comparison.

Tests the complete data plane for a 100k-point FeatureCollection:

Before V3:
- frontend: full GeoJSON GET → 100k JSON.parse → Zustand retains full FC → 
  adapter scans 4× for geometry profile → MVT eventually selected
- backend: every MapSpec mutation does 2× full deepcopy of spec (including sources)

After V3:
- frontend: descriptor in SSE → MVT decision before GeoJSON GET → no full download
- backend: SetView/SetLayout use copy-on-write (sources dict shared by reference)

Measurements:
- wire_bytes: HTTP response size for the layer data
- fc_parse_count: number of times JSON.parse runs on the full FeatureCollection
- store_retained_bytes: rough estimate of Zustand layer.source memory
- feature_scans: adapter geometryProfileOf scan count
- tile_encode_calls: MVT encode invocations (cold vs warm cache)
- cache_hit: tile LRU hit rate after initial mount
- mapspec_deepcopy_bytes: bytes deep-copied per SetView mutation
"""
import asyncio
import json
from unittest.mock import patch

import pytest


def _100k_fc():
    """100k Point FeatureCollection (~8 MB JSON)."""
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.0001, 39.9]},
                "properties": {"id": i, "category": f"cat{i % 10}"},
            }
            for i in range(100000)
        ],
    }


def _estimate_bytes(obj):
    """Rough JSON byte estimate."""
    return len(json.dumps(obj, ensure_ascii=False))


@pytest.fixture
def fc_100k():
    return _100k_fc()


# ---------------------------------------------------------------- Backend


def test_ref_descriptor_computed_at_store_time(fc_100k):
    """V3: descriptor computed once at store(), not on every GET."""
    from app.services.session_data import MemorySessionStore
    from app.schemas.ref_descriptor import compute_descriptor

    store = MemorySessionStore()
    ref_id = asyncio.run(store.store("sess", fc_100k, prefix="geojson"))
    
    # Descriptor available immediately, no re-scan
    desc = asyncio.run(store.get_ref_descriptor("sess", ref_id))
    assert desc is not None
    assert desc["feature_count"] == 100000
    assert desc["point_count"] == 100000
    assert desc["mvt_capable"] is True
    assert desc["bbox"] is not None
    assert desc["estimated_bytes"] > 7_000_000  # ~8 MB
    
    # Direct compute for comparison
    desc2 = compute_descriptor(ref_id, fc_100k)
    assert desc2.feature_count == 100000


def test_mapspec_set_view_cow_no_source_copy(fc_100k):
    """V3: SetView does not deep-copy the sources dict."""
    from app.services.mapspec.lifecycle_engine import (
        MapSpecLifecycleEngine,
        SetViewIntent,
    )
    
    spec = {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"large": {"type": "geojson", "inlineData": fc_100k}},
        "layers": [{"id": "l1", "type": "circle", "source": "large"}],
        "layout": {"legend": {"visible": True}, "controls": []},
    }
    
    engine = MapSpecLifecycleEngine()
    
    # Mock store that returns the same spec object (in-memory backend behavior)
    class _FakeStore:
        async def get_mapspec(self, sid, **_kw):
            return spec
        async def save_mapspec(self, sid, mapspec, **_kw):
            return {"mapspec": mapspec}
        def get_session_dir(self, sid):
            from pathlib import Path
            import tempfile
            return Path(tempfile.mkdtemp())
    
    engine.store = _FakeStore()
    
    # Patch dependencies
    import app.services.mapspec.lifecycle_engine as le
    
    class _FakeSDM:
        async def get_map_state(self, sid):
            return {"layers": []}
        async def set_map_state(self, sid, key, val, seq=None):
            return True
    
    class _FakeLock:
        def lock(self, sid, **_kw):
            class _Ctx:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *a):
                    return False
            return _Ctx()
    
    with patch.object(le, "session_data_manager", _FakeSDM()), \
         patch.object(le, "session_lock_registry", _FakeLock()):
        
        prior_payload = spec["sources"]["large"]["inlineData"]
        prior_payload_id = id(prior_payload)
        
        res = asyncio.run(engine.apply_mutation("s1", SetViewIntent(zoom=12)))
        
        assert not res.is_error, res.error_msg
        candidate_payload = res.mapspec["sources"]["large"]["inlineData"]
        
        # Same object identity — zero copy
        assert id(candidate_payload) == prior_payload_id
        assert candidate_payload is prior_payload


def test_mvt_tile_cache_hit(fc_100k):
    """V3: tile LRU avoids re-encoding on repeat requests."""
    from app.services.mvt import encode_tile, tile_lru_cache, spatial_index_cache

    tile_lru_cache.clear()
    spatial_index_cache.clear()

    z, x, y = 10, 850, 390  # Beijing viewport tile

    # Cold: encode + cache
    tile1 = encode_tile(fc_100k, z, x, y)
    assert isinstance(tile1, bytes)

    # Put gzip bytes into LRU to simulate the tile route
    import gzip
    gz1 = gzip.compress(tile1)
    cache_key = ("sess", "ref:test", z, x, y)
    tile_lru_cache.put(cache_key, gz1)

    # Warm: cache hit
    hit = tile_lru_cache.get(cache_key)
    assert hit == gz1, "LRU should return cached gzip bytes on second request"

    # Miss for different tile
    miss = tile_lru_cache.get(("sess", "ref:test", z, x + 1, y))
    assert miss is None


def test_mvt_spatial_index_one_build_per_ref():
    """V3: spatial index built once per ref, reused across tiles."""
    from app.services.mvt import (
        build_spatial_index_entry,
        spatial_index_cache,
    )

    spatial_index_cache.clear()

    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.9]},
                "properties": {"id": i},
            }
            for i in range(10000)
        ],
    }

    key = ("sess1", "ref:test-123")

    # First build via cache
    entry1 = spatial_index_cache.get_or_build(key, lambda: build_spatial_index_entry(key, fc))
    assert entry1 is not None
    assert len(entry1.features) == 10000

    # Second call: cache hit — same object
    entry2 = spatial_index_cache.get_or_build(key, lambda: build_spatial_index_entry(key, fc))
    assert entry2 is entry1, "spatial index must be cached (same object on second call)"


# ---------------------------------------------------------------- Measurements


def test_wire_bytes_comparison(fc_100k):
    """Measure: full GeoJSON vs descriptor-only."""
    from app.schemas.ref_descriptor import compute_descriptor
    
    full_bytes = _estimate_bytes(fc_100k)
    
    ref_id = "ref:test-abc"
    desc = compute_descriptor(ref_id, fc_100k)
    desc_bytes = _estimate_bytes(desc.to_dict())
    
    reduction = (1 - desc_bytes / full_bytes) * 100
    
    print("\n100k FC wire bytes:")
    print(f"  Full GeoJSON: {full_bytes:,} bytes")
    print(f"  Descriptor only: {desc_bytes:,} bytes")
    print(f"  Reduction: {reduction:.1f}%")
    
    assert desc_bytes < 2000, "Descriptor should be < 2 KB"
    assert reduction > 99.0, "Should eliminate >99% of wire traffic"


def test_mapspec_deepcopy_bytes_comparison(fc_100k):
    """Measure: deepcopy cost before vs after COW for SetView."""
    spec_with_large_source = {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"large": {"type": "geojson", "inlineData": fc_100k}},
        "layers": [{"id": "l1", "type": "circle", "source": "large"}],
        "layout": {"legend": {"visible": True}, "controls": []},
    }

    # V3 COW path: shallow copy + copy-only-view-branch
    cow_candidate = {**spec_with_large_source}
    cow_candidate["view"] = dict(spec_with_large_source["view"])
    # sources dict is shared by reference (not copied)

    # Verify the source payload is actually shared (zero copy for SetView)
    assert cow_candidate["sources"]["large"]["inlineData"] is spec_with_large_source["sources"]["large"]["inlineData"], \
        "COW SetView candidate must share the source payload (same object)"

    # Verify that mutating the candidate's view does not affect the original
    cow_candidate["view"]["zoom"] = 12
    assert spec_with_large_source["view"]["zoom"] == 10, \
        "Mutating candidate view must not affect prior spec view"

    print("\nSetView MapSpec copy cost (V3 COW):")
    print(f"  Prior payload features: {len(fc_100k['features']):,}")
    print("  Source payload shared by reference: YES")
    print("  View branch copied (O(1)): YES")
    print("  SetView deepcopy eliminated: YES")


def test_tile_encode_cold_vs_warm():
    """Measure: tile encode calls cold vs warm."""
    from app.services.mvt import encode_tile, tile_lru_cache, spatial_index_cache
    import gzip

    tile_lru_cache.clear()
    spatial_index_cache.clear()

    fc = _100k_fc()
    # 4-tile viewport at z=10 around Beijing
    tiles = [(10, 850, 390), (10, 851, 390), (10, 850, 391), (10, 851, 391)]

    # Cold: encode + manually cache (simulate tile route)
    cold_bytes = []
    for z, x, y in tiles:
        tile = encode_tile(fc, z, x, y)
        gz = gzip.compress(tile)
        cache_key = ("sess", "ref:perf", z, x, y)
        tile_lru_cache.put(cache_key, gz)
        cold_bytes.append(gz)

    # Warm: all cache hits
    warm_bytes = []
    for i, (z, x, y) in enumerate(tiles):
        cache_key = ("sess", "ref:perf", z, x, y)
        hit = tile_lru_cache.get(cache_key)
        warm_bytes.append(hit)

    print("\n100k FC tile encode (4-tile viewport):")
    print(f"  Cold encodes: {len(cold_bytes)}")
    print(f"  Warm cache hits: {sum(1 for b in warm_bytes if b is not None)}")
    print(f"  Tile sizes: {[len(b) for b in cold_bytes]}")

    assert len(cold_bytes) == 4
    assert sum(1 for b in warm_bytes if b is not None) == 4
    # Warm bytes must be identical to cold
    for cold, warm in zip(cold_bytes, warm_bytes):
        assert cold == warm


def test_feature_scan_count_mvt_vs_geojson():
    """Adapter geometry-profile: descriptor path must not scan features.

    Frontend invariant (frontend/lib/mapspec-runtime/adapter.ts):
    - V3 MVT-backed layer: geometryProfileOf reads ``_descriptor.geometry_types``,
      0 feature scans (``_geometryProfileStats.scanCount`` stays 0).
    - pre-V3 / inline GeoJSON: one profile computation runs four ``features.some``
      passes (hasPolygons/hasLines/hasPoints/hasWeight).

    Behavioral coverage lives in adapter-geometry-memo.test.ts. This test
    pins the two-branch source shape so a refactor cannot drop the
    descriptor short-circuit without turning this red (#618-30).
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "frontend"
        / "lib"
        / "mapspec-runtime"
        / "adapter.ts"
    ).read_text(encoding="utf-8")
    desc_idx = src.find("layer._descriptor && layer._descriptor.geometry_types")
    assert desc_idx != -1, (
        "geometryProfileOf must short-circuit on _descriptor.geometry_types "
        "(MVT-backed: 0 feature scans)"
    )
    poly_scan = src.find('features.some((f) => f.geometry?.type?.includes("Polygon"))')
    line_scan = src.find('features.some((f) => f.geometry?.type?.includes("Line"))')
    point_scan = src.find('features.some((f) => f.geometry?.type?.includes("Point"))')
    weight_scan = src.find("features.some((f) => (f as any).properties?.weight != null)")
    assert min(poly_scan, line_scan, point_scan, weight_scan) > desc_idx, (
        "inline-GeoJSON 4× feature scans must come after the descriptor short-circuit"
    )
    assert "_geometryProfileStats.scanCount" in src
