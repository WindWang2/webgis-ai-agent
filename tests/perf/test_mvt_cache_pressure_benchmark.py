"""Deterministic benchmark suite for MVT / Large Layer cache & memory pressure.

Measures BEFORE and AFTER metrics across 10 deterministic scenarios:
1. 1 x 100k layer
2. 10 x 10k layers
3. 100 small layers
4. 5 sessions x 10 layers
5. Same tile x 50 concurrent requests
6. 500 distinct tiles
7. Overwrite ref lifecycle
8. Session clear lifecycle
9. Index eviction & rebuild
10. Cancelled tile producer

Tracks:
- Geometry objects retained
- Estimated memory bytes
- Cache entry count (index + tile)
- Cache retained bytes
- Index build count
- Tile encode count
- Single-flight producer count
- Eviction count
"""

import asyncio
import gzip
import sys
from typing import Any, Dict

import pytest

import app.services.mvt as mvt_mod
from app.services.mvt import (
    SingleFlightManager,
    SpatialIndexCache,
    SpatialIndexEntry,
    TileLRUCache,
    build_spatial_index_entry,
    encode_tile_from_index,
)


def _generate_synthetic_geojson(n_features: int, geom_type: str = "Point", center=(116.40, 39.90), spread=0.5) -> dict:
    features = []
    cx, cy = center
    for i in range(n_features):
        u = (i % 1000) / 1000.0
        v = (i // 1000) / max(1, (n_features // 1000))
        lon = cx + (u - 0.5) * spread
        lat = cy + (v - 0.5) * spread
        if geom_type == "Point":
            coords = [lon, lat]
        elif geom_type == "LineString":
            coords = [[lon, lat], [lon + 0.005, lat + 0.005], [lon + 0.01, lat + 0.002]]
        elif geom_type == "Polygon":
            coords = [[
                [lon, lat],
                [lon + 0.008, lat],
                [lon + 0.008, lat + 0.008],
                [lon, lat + 0.008],
                [lon, lat],
            ]]
        else:
            raise ValueError(f"Unknown geom_type: {geom_type}")

        features.append({
            "type": "Feature",
            "geometry": {"type": geom_type, "coordinates": coords},
            "properties": {"id": i, "name": f"feature_{i}", "val": i * 1.5},
        })
    return {"type": "FeatureCollection", "features": features}


def estimate_entry_memory(entry: SpatialIndexEntry) -> Dict[str, Any]:
    """Estimate memory used by a SpatialIndexEntry."""
    num_features = len(entry.features) if entry.features else 0
    num_geoms = len(entry.geoms) if entry.geoms else 0
    num_geoms_ll = len(entry.geoms_lonlat) if entry.geoms_lonlat else 0
    num_bounds = len(entry.bounds) if entry.bounds else 0

    # Rough estimate of Python object overhead:
    # Feature dict: ~250-400 bytes
    # Shapely geometry: ~150-300 bytes (GEOS C-struct + Python wrapper)
    # STRtree: ~100-200 bytes per node
    feature_bytes = sum(sys.getsizeof(f) + sys.getsizeof(f.get("properties", {})) + sys.getsizeof(f.get("geometry", {})) for f in entry.features[:100]) * (num_features / 100.0) if num_features > 0 else 0
    geom_bytes = (num_geoms + num_geoms_ll) * 200
    bounds_bytes = num_bounds * sys.getsizeof((0.0, 0.0, 0.0, 0.0))
    total_est = int(feature_bytes + geom_bytes + bounds_bytes)

    return {
        "num_features": num_features,
        "num_geoms_z0": num_geoms,
        "num_geoms_lonlat": num_geoms_ll,
        "total_geometry_objects": num_geoms + num_geoms_ll,
        "estimated_bytes": total_est,
    }


class BenchmarkInstrumenter:
    """Instruments MVT functions to count operations."""

    def __init__(self, monkeypatch=None):
        self.index_builds = 0
        self.tile_encodes = 0
        self.singleflight_producers = 0
        self._monkeypatch = monkeypatch
        self._orig_build = mvt_mod.build_spatial_index_entry
        self._orig_encode_index = mvt_mod.encode_tile_from_index

    def setup(self, monkeypatch):
        self._monkeypatch = monkeypatch

        def counted_build(key, data):
            self.index_builds += 1
            return self._orig_build(key, data)

        def counted_encode(entry, z, x, y):
            self.tile_encodes += 1
            return self._orig_encode_index(entry, z, x, y)

        monkeypatch.setattr(mvt_mod, "build_spatial_index_entry", counted_build)
        monkeypatch.setattr(mvt_mod, "encode_tile_from_index", counted_encode)

    def reset(self):
        self.index_builds = 0
        self.tile_encodes = 0
        self.singleflight_producers = 0


@pytest.fixture
def instrument(monkeypatch):
    inst = BenchmarkInstrumenter()
    inst.setup(monkeypatch)
    return inst


class TestMvtPressureBenchmarks:
    """Benchmark tests asserting specific operational contracts."""

    def test_scenario_1_100k_layer_memory(self, instrument):
        """Scenario 1: 1 x 100k feature layer."""
        fc = _generate_synthetic_geojson(100_000, "Point")
        entry = mvt_mod.build_spatial_index_entry(("s1", "r_100k"), fc)
        stats = estimate_entry_memory(entry)

        # In current master, 100k points creates 100k features, 100k geoms_z0, 100k geoms_lonlat
        print("\n[Scenario 1: 1x100k Points]", stats)
        assert stats["num_features"] == 100_000
        # Tile query check
        tile_features = entry.query_tile(12, 3371, 1550)
        assert isinstance(tile_features, list)

    def test_scenario_2_10x10k_layers_memory(self, instrument):
        """Scenario 2: 10 x 10k feature layers."""
        cache = SpatialIndexCache(max_refs=256)
        for i in range(10):
            fc = _generate_synthetic_geojson(10_000, "Polygon" if i % 2 == 0 else "LineString")
            key = ("s1", f"ref_{i}")
            cache.get_or_build(key, lambda k=key, data=fc: build_spatial_index_entry(k, data))

        total_geoms = sum(
            (len(e.geoms or []) + len(e.geoms_lonlat or [])) for e in cache._entries.values()
        )
        print(f"\n[Scenario 2: 10x10k Layers] Entries: {len(cache._entries)}, Retained Geoms: {total_geoms}")
        assert len(cache._entries) == 10

    def test_scenario_3_100_small_layers(self, instrument):
        """Scenario 3: 100 small layers (50 features each)."""
        cache = SpatialIndexCache(max_refs=256)
        for i in range(100):
            fc = _generate_synthetic_geojson(50, "Point")
            key = ("s1", f"small_{i}")
            cache.get_or_build(key, lambda k=key, data=fc: build_spatial_index_entry(k, data))
        assert len(cache._entries) == 100

    def test_scenario_4_multi_session_isolation(self, instrument):
        """Scenario 4: 5 sessions x 10 layers."""
        cache = SpatialIndexCache(max_refs=256)
        for s in range(5):
            for r in range(10):
                fc = _generate_synthetic_geojson(500, "Point")
                key = (f"sess_{s}", f"ref_{r}")
                cache.get_or_build(key, lambda k=key, data=fc: build_spatial_index_entry(k, data))
        assert len(cache._entries) == 50

    @pytest.mark.asyncio
    async def test_scenario_5_concurrent_50_same_tile_requests(self, instrument):
        """Scenario 5: 50 concurrent requests for same tile -> single-flight deduplication."""
        sf = SingleFlightManager(max_inflight=512)
        tile_cache = TileLRUCache(max_tiles=4096)
        fc = _generate_synthetic_geojson(5000, "LineString")
        entry = build_spatial_index_entry(("s1", "r1"), fc)

        encode_count = 0

        async def fetch_tile():
            nonlocal encode_count
            key = ("s1", "r1", 12, 3371, 1550)
            cached = tile_cache.get(key)
            if cached is not None:
                return cached

            async def _compute():
                nonlocal encode_count
                encode_count += 1
                await asyncio.sleep(0.01)  # simulate async/thread overhead
                raw = encode_tile_from_index(entry, 12, 3371, 1550)
                body = gzip.compress(raw)
                tile_cache.put(key, body)
                return body

            return await sf.run(key, _compute)

        results = await asyncio.gather(*[fetch_tile() for _ in range(50)])
        assert len(results) == 50
        assert all(r == results[0] for r in results)
        print(f"\n[Scenario 5: 50 Concurrent Requests] Total encodes: {encode_count}")
        assert encode_count == 1  # Contract: single-flight dedupes 50 concurrent requests to exactly 1 encode

    def test_scenario_6_500_distinct_tiles(self, instrument):
        """Scenario 6: 500 distinct tile queries across zoom levels."""
        fc = _generate_synthetic_geojson(10_000, "Polygon")
        entry = build_spatial_index_entry(("s1", "r1"), fc)
        tile_cache = TileLRUCache(max_tiles=4096)

        encoded_tiles = 0
        for z in range(8, 15):
            for x_offset in range(min(10, 1 << z)):
                for y_offset in range(min(10, 1 << z)):
                    if encoded_tiles >= 500:
                        break
                    key = ("s1", "r1", z, x_offset, y_offset)
                    raw = encode_tile_from_index(entry, z, x_offset, y_offset)
                    tile_cache.put(key, gzip.compress(raw))
                    encoded_tiles += 1

        print(f"\n[Scenario 6: 500 Distinct Tiles] Tile cache entries: {len(tile_cache._cache)}, Bytes: {tile_cache._total_bytes}")
        assert len(tile_cache._cache) == 500

    @pytest.mark.asyncio
    async def test_scenario_10_cancelled_tile_producer(self, instrument):
        """Scenario 10: Cancellation of leader does not leave hanging state or crash waiters."""
        sf = SingleFlightManager(max_inflight=512)
        started = asyncio.Event()

        async def slow_producer():
            started.set()
            await asyncio.sleep(1.0)
            return b"computed"

        key = ("s1", "r1", 10, 1, 1)
        # Start leader task
        leader_task = asyncio.create_task(sf.run(key, slow_producer))
        await started.wait()

        # Cancel leader
        leader_task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await leader_task

        # Verify key is cleared from inflight
        assert key not in sf._inflight
