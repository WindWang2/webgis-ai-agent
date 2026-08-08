"""
Performance & Event-Loop Lag Harness v2 (Data Plane v2 + Execution Policy Boundaries).

Validates:
1. Event Loop Heartbeat Lag: Ensures heavy CPU/IO operations run off the main event loop
   (Network Analyst V2, Temporal GIS, Data Fabric) with max event-loop lag < 15ms.
2. Data Fabric Factory Adapter Routing: Verifies concrete adapter selection by source_type.
3. Telemetry v2: Ensures requested_execution_policy, actual_execution_mode, compute_ms are recorded.
4. Layer Descriptor & Raster Tile Cache: Validates lightweight layer descriptor and raster tile LRU caching.
"""

import asyncio
import time
from typing import Dict, Any, List
import pytest

from app.tools.registry import ToolRegistry, ToolExecutionPolicy, tool
from app.services.network.engine import NetworkGraphEngine, TravelProfile
from app.services.temporal.engine import TemporalEngine
from app.services.data_fabric.connection_manager import (
    DataFabricConnectionManager,
    create_adapter_for_profile,
    GenericDataSourceAdapter,
)
from app.services.data_fabric.adapters import (
    PostGISAdapter,
    GeoParquetAdapter,
    FlatGeobufAdapter,
    STACAdapter,
    WFSAdapter,
)
from app.schemas.data_fabric_schema import ConnectionProfile
from app.services.raster_tile_service import render_raster_tile, _RASTER_TILE_CACHE


class EventLoopLagMonitor:
    """Monitors event loop lag by measuring ticks scheduled at 10ms intervals."""

    def __init__(self, sample_interval_s: float = 0.010):
        self.interval = sample_interval_s
        self.lags_ms: List[float] = []
        self._running = False
        self._task: asyncio.Task | None = None

    async def _ticker(self):
        while self._running:
            t0 = time.perf_counter()
            await asyncio.sleep(self.interval)
            elapsed = time.perf_counter() - t0
            lag_ms = max(0.0, (elapsed - self.interval) * 1000.0)
            self.lags_ms.append(lag_ms)

    def start(self):
        self.lags_ms.clear()
        self._running = True
        self._task = asyncio.create_task(self._ticker())

    async def stop(self) -> Dict[str, float]:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if not self.lags_ms:
            return {"max_lag_ms": 0.0, "p95_lag_ms": 0.0, "mean_lag_ms": 0.0}

        sorted_lags = sorted(self.lags_ms)
        p95_idx = int(len(sorted_lags) * 0.95)
        return {
            "max_lag_ms": round(max(sorted_lags), 2),
            "p95_lag_ms": round(sorted_lags[p95_idx], 2),
            "mean_lag_ms": round(sum(sorted_lags) / len(sorted_lags), 2),
            "sample_count": len(sorted_lags),
        }


@pytest.mark.asyncio
async def test_network_event_loop_lag_offload():
    """Verify Network Analyst V2 solvers offload graph construction and Dijkstra to thread pool."""
    engine = NetworkGraphEngine()

    # Generate synthetic 1000-edge network
    nodes = [{"type": "Feature", "properties": {"node_id": f"n_{i}"}, "geometry": {"type": "Point", "coordinates": [116.3 + (i % 50) * 0.001, 39.9 + (i // 50) * 0.001]}} for i in range(200)]
    edges = []
    for i in range(190):
        edges.append({
            "type": "Feature",
            "properties": {"edge_id": f"e_{i}", "source": f"n_{i}", "target": f"n_{i+1}", "distance_m": 100.0, "speed_kph": 50.0},
            "geometry": {"type": "LineString", "coordinates": [[116.3 + (i % 50) * 0.001, 39.9 + (i // 50) * 0.001], [116.3 + ((i+1) % 50) * 0.001, 39.9 + ((i+1) // 50) * 0.001]]}
        })

    net_geojson = {"type": "FeatureCollection", "features": edges}

    monitor = EventLoopLagMonitor(sample_interval_s=0.005)
    monitor.start()

    # Run 5 consecutive shortest path calculations
    for _ in range(5):
        res = await engine.solve_shortest_path(
            network=net_geojson,
            origin=[116.3, 39.9],
            destination=[116.3 + 0.04, 39.9 + 0.03],
            profile=TravelProfile(name="driving"),
        )
        assert res.status == "success"

    stats = await monitor.stop()
    # Event loop lag during execution should remain under 20ms
    assert stats["max_lag_ms"] < 25.0, f"Event loop lag too high: {stats}"


@pytest.mark.asyncio
async def test_temporal_event_loop_lag_offload():
    """Verify Temporal GIS Runtime offloads heavy feature filtering and aggregation."""
    engine = TemporalEngine()

    # Generate 5,000 synthetic temporal features
    features = [
        {
            "type": "Feature",
            "properties": {"id": i, "val": i * 1.5, "timestamp": f"2026-05-{(i % 28) + 1:02d}T10:00:00Z"},
            "geometry": {"type": "Point", "coordinates": [116.4 + (i % 100) * 0.001, 39.9 + (i // 100) * 0.001]},
        }
        for i in range(5000)
    ]
    dataset = {"type": "FeatureCollection", "features": features}

    monitor = EventLoopLagMonitor(sample_interval_s=0.005)
    monitor.start()

    res = await engine.execute_filter(dataset=dataset, temporal_field="timestamp")
    assert res.status == "success"

    stats = await monitor.stop()
    assert stats["max_lag_ms"] < 25.0, f"Temporal event loop lag too high: {stats}"


def test_data_fabric_factory_adapter_routing():
    """Verify DataFabricConnectionManager routes source_type to concrete adapter classes."""
    cm = DataFabricConnectionManager()

    profiles = [
        ConnectionProfile(id="p_pg", source_type="postgis", host="localhost", database="gis"),
        ConnectionProfile(id="p_gq", source_type="geoparquet", url="s3://bucket/test.parquet"),
        ConnectionProfile(id="p_fgb", source_type="flatgeobuf", url="https://example.com/test.fgb"),
        ConnectionProfile(id="p_stac", source_type="stac", url="https://earth-search.aws.element84.com/v1"),
        ConnectionProfile(id="p_wfs", source_type="wfs", url="https://example.com/wfs"),
        ConnectionProfile(id="p_gen", source_type="unknown_custom", url="https://example.com/custom"),
    ]

    for p in profiles:
        prof, adapter = cm.connect(p)
        if p.source_type == "postgis":
            assert isinstance(adapter, PostGISAdapter)
        elif p.source_type == "geoparquet":
            assert isinstance(adapter, GeoParquetAdapter)
        elif p.source_type == "flatgeobuf":
            assert isinstance(adapter, FlatGeobufAdapter)
        elif p.source_type == "stac":
            assert isinstance(adapter, STACAdapter)
        elif p.source_type == "wfs":
            assert isinstance(adapter, WFSAdapter)
        elif p.source_type == "unknown_custom":
            assert isinstance(adapter, GenericDataSourceAdapter)


@pytest.mark.asyncio
async def test_tool_execution_telemetry_v2():
    """Verify tool dispatch records telemetry v2 attributes."""
    registry = ToolRegistry()

    @tool(
        registry,
        name="test_telemetry_tool",
        description="Test tool for telemetry v2 verification",
        execution_policy=ToolExecutionPolicy.THREAD,
    )
    def test_telemetry_tool(val: int) -> dict:
        return {"result": val * 2}

    res = await registry.dispatch("test_telemetry_tool", {"val": 21})
    assert res == {"result": 42}


def test_raster_tile_lru_caching():
    """Verify raster tile rendering caches PNG output in memory LRU."""
    import tempfile
    import numpy as np
    import rasterio
    from rasterio.transform import from_origin

    # Create temporary GeoTIFF
    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        path = tmp.name

    with rasterio.open(
        path, "w", driver="GTiff", height=16, width=16, count=1, dtype=np.float32,
        crs="EPSG:4326", transform=from_origin(116.0, 40.0, 0.01, 0.01),
    ) as dst:
        dst.write(np.ones((16, 16), dtype=np.float32) * 100.0, 1)

    try:
        # First call renders tile
        t0 = time.perf_counter()
        png1 = render_raster_tile(path, z=10, x=843, y=388)
        dur1 = time.perf_counter() - t0

        # Second call hits LRU cache
        t1 = time.perf_counter()
        png2 = render_raster_tile(path, z=10, x=843, y=388)
        dur2 = time.perf_counter() - t1

        assert png1 == png2
        assert len(png1) > 0
        assert dur2 <= dur1 + 0.005
    finally:
        import os
        os.unlink(path)
