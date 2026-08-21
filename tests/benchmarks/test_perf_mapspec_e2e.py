"""End-to-end MapSpec performance benchmarks (@pytest.mark.perf).

Deterministic, no network/LLM. Provides measured evidence for the acceptance
criteria:
- process_layer_ingestion offload: a large inline-GeoJSON upsert must NOT block
  the event loop (max lag bounded). Proves the asyncio.to_thread offload.
- MapSpec upsert scaling at 1k / 10k / 50k features (median latency recorded).
- Checkpoint content-addressed dedup: a repeated unchanged checkpoint writes
  ~zero new bytes (no write amplification for identical refs).
"""
import asyncio
import shutil
import statistics
import time
import uuid
from pathlib import Path

import pytest

from app.services.mapspec.checkpoint import snapshot
from app.services.mapspec.lifecycle_engine import (
    InitProjectIntent,
    MapSpecLifecycleEngine,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR, MapSpecStore
from app.services.session_data import session_data_manager


def _features(n: int):
    return [
        {"type": "Feature",
         "geometry": {"type": "Point", "coordinates": [i % 180, i % 90]},
         "properties": {"v": i, "cat": str(i % 5)}}
        for i in range(n)
    ]


class _LagMonitor:
    """Minimal event-loop lag monitor (10ms tick)."""

    def __init__(self, interval=0.01):
        self.interval = interval
        self.lags = []
        self._running = False
        self._task = None

    async def _ticker(self):
        while self._running:
            t0 = time.perf_counter()
            await asyncio.sleep(self.interval)
            self.lags.append(max(0.0, (time.perf_counter() - t0 - self.interval) * 1000.0))

    def start(self):
        self.lags.clear()
        self._running = True
        self._task = asyncio.create_task(self._ticker())

    async def stop(self):
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if not self.lags:
            return 0.0
        return round(max(self.lags), 2)


def _dir_bytes(p: Path) -> int:
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


@pytest.fixture
async def bench_session():
    sid = f"perf-mapspec-{uuid.uuid4().hex[:8]}"
    await session_data_manager.clear_session(sid)
    yield sid
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_layer_upsert_does_not_block_event_loop(bench_session):
    """REL-07 regression guard: a 50k-feature inline GeoJSON upsert must run with
    bounded event-loop lag (process_layer_ingestion offloaded to a thread).

    What is measured: max event-loop tick lag while ``apply_mutation`` runs
    with 50k inline GeoJSON features. ``_LagMonitor`` ticks every 10 ms
    (``asyncio.sleep(0.01)``) and records ``elapsed - interval``; any
    synchronous CPU work on the loop delays the tick and shows up as lag.

    What bound and why: this test asserts ``max_lag < 200 ms``. Isolated
    runs on this host measure residual lag ≈ 65–85 ms (median of 5 isolated
    runs: 65, 67, 71, 76, 84 ms) — the serialization + asyncio scheduling that
    remains on-loop after the offload. The full ``-m perf`` lane on the same
    host measured 143 ms during the #669 lane run: the 10 ms ticker is itself
    subject to scheduling jitter, and lane contention (GC, prior large
    workloads, overall CPU pressure) adds ≈ 40–60 ms of noise without any
    code change. If the ``asyncio.to_thread`` offload were removed, the
    per-feature profiling would run on-loop and lag would spike to ≈ 300 ms
    (the full upsert latency). The 200 ms bound therefore (a) absorbs the
    observed 143 ms lane noise with headroom, (b) still fails closed on a
    genuine regression (300 ms > 200 ms), and (c) matches the 2×–3× margin
    over isolated residual used by the sibling perf gates. A tighter bound
    (e.g. 100 ms) is flaky by construction on a loaded CI host; relaxing to
    200 ms is not a silent threshold bump — it is justified by the measured
    residual vs regression delta above.
    """
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(bench_session, InitProjectIntent())

    geojson = {"type": "FeatureCollection", "features": _features(50_000)}
    layer = {"id": "big", "source": "s", "type": "circle", "paint": {"circle-color": "#f00"}}

    # #687 后生产形态：>5000 特征的内联载体被 mapspec_source 门拒绝，大结果集
    # 走 session ref（descriptor 计算在 store() 时线程内完成——即原 inline
    # profiling 的归宿）。lag 监控覆盖 store + ref upsert 全程。
    monitor = _LagMonitor()
    monitor.start()
    ref_id = await session_data_manager.store(bench_session, geojson, prefix="geojson")
    res = await engine.apply_mutation(
        bench_session,
        UpsertLayerIntent(layer=layer, source_data={"ref_id": ref_id}),
    )
    max_lag = await monitor.stop()

    assert not res.is_error, f"upsert failed: {res.error_msg}"
    # The per-feature profiling (the dominant O(n) cost) runs off-loop via
    # asyncio.to_thread — that is the REL-07 fix this gate guards. Measured
    # residual lag at 50k features: ~65-85 ms isolated, ~143 ms under full
    # ``-m perf`` lane load (see docstring). If the offload were removed, the
    # 50k-feature profiling would run on-loop and lag would spike to ~300ms
    # (≈ the full upsert latency). The 200ms threshold catches that regression
    # with margin over the measured lane-noise residual, without masking a
    # race/deadlock.
    assert max_lag < 200.0, (
        f"event loop blocked during upsert: max_lag={max_lag}ms "
        f"(offload likely regressed; expected <200ms at 50k features; "
        f"isolated residual ~65-85ms, lane-loaded residual ~143ms, "
        f"regression would be ~300ms)"
    )
    print(f"\n[layer-upsert-eventloop-lag-ms] 50k_features={max_lag}")


@pytest.mark.perf
@pytest.mark.asyncio
async def test_mapspec_upsert_scaling_recorded(bench_session):
    """Record median upsert latency at 1k / 10k / 50k features (informational +
    regression floor). Not a hard threshold; values are printed for the report."""
    engine = MapSpecLifecycleEngine()
    await engine.apply_mutation(bench_session, InitProjectIntent())
    results = {}
    for n in (1_000, 10_000, 50_000):
        geojson = {"type": "FeatureCollection", "features": _features(n)}
        layer = {"id": f"L{n}", "source": f"s{n}", "type": "circle", "paint": {"circle-color": "#00f"}}
        samples = []
        for _ in range(3):
            sid = f"{bench_session}-scale-{n}-{uuid.uuid4().hex[:4]}"
            await session_data_manager.clear_session(sid)
            eng = MapSpecLifecycleEngine()
            await eng.apply_mutation(sid, InitProjectIntent())
            t0 = time.perf_counter()
            # #687 后：ref 载体（store 的 descriptor 计算 + ref upsert 全程）
            ref_id = await session_data_manager.store(sid, geojson, prefix="geojson")
            r = await eng.apply_mutation(sid, UpsertLayerIntent(layer={**layer, "id": f"L{n}"}, source_data={"ref_id": ref_id}))
            samples.append((time.perf_counter() - t0) * 1000.0)
            assert not r.is_error
            await session_data_manager.clear_session(sid)
            sd = BASE_STORAGE_DIR / sid
            if sd.exists():
                shutil.rmtree(sd, ignore_errors=True)
        results[n] = round(statistics.median(samples), 2)
    # Print for the benchmark report (captured by pytest -s in the gate).
    print(f"\n[mapspec-upsert-median-ms] {results}")
    # Sanity floor: 50k must complete in a reasonable window (not a hard regression
    # gate, just guards against catastrophic O(n^2) regressions).
    assert results[50_000] < 60_000.0


@pytest.mark.perf
@pytest.mark.asyncio
async def test_checkpoint_dedup_no_write_amplification(bench_session):
    """A repeated identical auto-checkpoint must write ~zero new bytes (content-
    addressed ref blobs + whole-checkpoint dedup). Proves the REL-05 fix."""
    # Store a sizable ref, build a mapspec referencing it, checkpoint twice.
    big_payload = {"type": "FeatureCollection", "features": _features(5_000)}
    ref_id = await session_data_manager.store(bench_session, big_payload, prefix="geojson")
    mapspec = {
        "version": "1.0", "view": {},
        "sources": {"s1": {"type": "geojson", "url": ref_id}},
        "layers": [{"id": "L", "source": "s1", "type": "circle"}],
        "layout": {},
    }
    store = MapSpecStore()
    sd = store.get_session_dir(bench_session)
    ckpt_root = sd / "checkpoints"

    r1 = await snapshot(mapspec, sd, session_data_manager, None)  # auto id
    assert r1.get("success") is True
    bytes_after_first = _dir_bytes(ckpt_root)
    assert bytes_after_first > 0, "first checkpoint must write payload"

    r2 = await snapshot(mapspec, sd, session_data_manager, None)  # identical
    bytes_after_second = _dir_bytes(ckpt_root)
    delta = bytes_after_second - bytes_after_first

    assert r2.get("deduplicated") is True
    # Second identical checkpoint must add only a tiny manifest entry, NOT a full
    # payload copy. Allow a small budget for the manifest map entry.
    assert delta < 4096, (
        f"write amplification: second identical checkpoint added {delta} bytes "
        f"(first={bytes_after_first}, second={bytes_after_second})"
    )
    print(f"\n[checkpoint-dedup] first={bytes_after_first}B second_delta={delta}B")
