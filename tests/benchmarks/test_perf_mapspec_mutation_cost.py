"""Issue #669 mutation-cost + save-path benchmarks (perf, count-based).

Deterministic fixtures + bytes-written harness. All assertions are count /
bytes based, not wall-clock. See F-memory-profile measured: legacy deepcopy
1106ms vs CoW SetView 0.3ms at 100k features — this suite locks the CoW gain
via deepcopy spy + save-path byte counting.

Run:
    .venv/bin/pytest -m perf --no-cov tests/benchmarks/test_perf_mapspec_mutation_cost.py -q
Unfiltered runs self-skip per tests/conftest.py #664.
"""
import asyncio
import copy
import json
import shutil
import uuid
from unittest.mock import patch

import pytest

from app.services.mapspec.lifecycle_engine import (
    MapSpecLifecycleEngine,
    SetViewIntent,
    UpsertLayerIntent,
)
from app.services.mapspec.store import BASE_STORAGE_DIR
from app.services.session_data import session_data_manager
from tests.fixtures.mapspec_cow_fixtures import (
    FakeLockReg,
    FakeSDM,
    FakeStore,
    fake_checkpoint,
    get_large_inline_fc,
    get_large_ref_spec,
    get_line_50k,
    get_point_100k,
    get_poly_10k,
)


@pytest.mark.perf
def test_perf_fixture_100k_point_generator():
    fc = get_point_100k()
    assert len(fc["features"]) == 100000
    assert fc["features"][0]["geometry"]["type"] == "Point"
    assert fc["features"][99999]["properties"]["id"] == 99999
    # deterministic coordinate check
    assert fc["features"][1000]["geometry"]["coordinates"] == [116.0 + 0 * 0.001, 39.9 + 1 * 0.001]


@pytest.mark.perf
def test_perf_fixture_50k_linestring_generator():
    fc = get_line_50k()
    assert len(fc["features"]) == 50000
    assert fc["features"][0]["geometry"]["type"] == "LineString"
    assert len(fc["features"][0]["geometry"]["coordinates"]) == 3


@pytest.mark.perf
def test_perf_fixture_10k_polygon_generator():
    fc = get_poly_10k()
    assert len(fc["features"]) == 10000
    assert fc["features"][0]["geometry"]["type"] == "Polygon"
    assert fc["features"][0]["geometry"]["coordinates"][0][0] == [116.0, 39.0]


# ── save-path byte counting harness ─────────────────────────────────────────
@pytest.mark.perf
@pytest.mark.asyncio
async def test_perf_set_view_save_bytes_bounded():
    """SetView against a large-ref spec must not inflate spec I/O.

    Count-based: spy on _atomic_write_json_sync, assert total bytes written
    for a SetView mutation stays O(spec) ~KB, not O(payload) ~MB.
    """
    import app.services.mapspec.store as store_mod

    # store a real 100k payload behind a ref so the session has large data,
    # but the mapspec itself stays ref-only (small)
    sid = f"perf-save-view-{uuid.uuid4().hex[:8]}"
    # use benchmark fixture payload (100k points) — register as session ref
    ref_id = await session_data_manager.store(sid, get_point_100k(), prefix="geojson")
    assert ref_id.startswith("ref:")

    # prime a mapspec that references the ref (small spec)
    spec = get_large_ref_spec(ref_id)
    # manually persist spec via store so engine can load it
    store = MapSpecLifecycleEngine().store
    await store.save_mapspec(sid, spec)

    # now spy on atomic writes for the SetView mutation
    writes = []

    orig = store_mod._atomic_write_json_sync

    def spy(path, payload):
        # count bytes as json.dumps would write (indent=2)
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        writes.append(len(data.encode("utf-8")))
        return orig(path, payload)

    engine = MapSpecLifecycleEngine()
    # ensure checkpoint not inflating via ref materialization (presentation_only)
    with patch.object(store_mod, "_atomic_write_json_sync", side_effect=spy):
        res = await engine.apply_mutation(sid, SetViewIntent(zoom=12))

    assert not res.is_error, res.error_msg
    assert writes, "expected at least one atomic write"
    total = sum(writes)
    # spec with ref should be ~0.5-2KB per write; allow generous 100KB total for
    # mapspec.json + revisions + checkpoint manifests (presentation_only)
    # but must be far below 12MB inline.
    assert total < 100 * 1024, f"SetView write amplification: {total} bytes (writes={writes}) — must be <100KB for ref spec"
    print(f"\n[perf-setview-bytes] ref_spec_writes={writes} total={total}")

    # cleanup
    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


@pytest.mark.perf
@pytest.mark.asyncio
async def test_perf_upsert_layer_ref_save_bytes_bounded():
    """UpsertLayer with a ref source must not inflate spec I/O (pipeline strips inlineData)."""
    import app.services.mapspec.store as store_mod

    sid = f"perf-save-upsert-{uuid.uuid4().hex[:8]}"
    ref_id = await session_data_manager.store(sid, get_point_100k(), prefix="geojson")
    spec = get_large_ref_spec(ref_id)
    store = MapSpecLifecycleEngine().store
    await store.save_mapspec(sid, spec)

    # UpsertLayer with a tiny second source, but existing large ref stays
    small_fc = {"type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [1, 2]}, "properties": {}}]}

    writes = []
    orig = store_mod._atomic_write_json_sync

    def spy(path, payload):
        data = json.dumps(payload, ensure_ascii=False, indent=2)
        writes.append(len(data.encode("utf-8")))
        return orig(path, payload)

    engine = MapSpecLifecycleEngine()
    with patch.object(store_mod, "_atomic_write_json_sync", side_effect=spy):
        res = await engine.apply_mutation(sid, UpsertLayerIntent(layer={"id": "l2", "type": "circle", "source": "small"}, source_data=small_fc))

    assert not res.is_error, res.error_msg
    total = sum(writes)
    # Upsert adds one small source + layer; still <100KB
    assert total < 150 * 1024, f"UpsertLayer write amplification: {total} bytes (writes={writes})"
    print(f"\n[perf-upsert-bytes] writes={writes} total={total}")

    await session_data_manager.clear_session(sid)
    d = BASE_STORAGE_DIR / sid
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)


# ── mutation-cost bytes/count harness (perf) ─────────────────────────────────
@pytest.mark.perf
def test_perf_mutation_copy_cost_bytes_bounded():
    """Deterministic copy-cost: SetView/UpsertLayer with 100k inline source copies ≤ O(1) KB.

    Uses deepcopy spy counting; not wall-clock. Mirrors the unit harness but
    runs in perf lane as the benchmark proof.
    """
    from app.services.mapspec.lifecycle_engine import MapSpecLifecycleEngine, SetViewIntent
    import app.services.mapspec.lifecycle_engine as le
    import pathlib
    import tempfile

    large_fc = get_large_inline_fc()
    spec = {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"big": {"type": "geojson", "inlineData": large_fc}},
        "layers": [{"id": "l1", "type": "circle", "source": "big"}],
        "layout": {"legend": {"visible": True}, "controls": []},
    }

    class _PerfFakeStore(FakeStore):
        def get_session_dir(self, sid):
            return pathlib.Path(tempfile.mkdtemp())

    engine = MapSpecLifecycleEngine()
    engine.store = _PerfFakeStore(spec)

    copied_feature_counts = []
    real_dc = copy.deepcopy

    def spy(obj, *a, **kw):
        if isinstance(obj, dict) and isinstance(obj.get("features"), list):
            copied_feature_counts.append(len(obj["features"]))
        return real_dc(obj, *a, **kw)

    with patch.object(le.copy, "deepcopy", side_effect=spy):
        with patch.object(le, "session_data_manager", FakeSDM(layers=spec["layers"])):
            with patch.object(le, "create_checkpoint", fake_checkpoint):
                with patch.object(le, "session_lock_registry", FakeLockReg()):
                    res = asyncio.run(engine.apply_mutation("s1", SetViewIntent(zoom=13)))
    assert not res.is_error, res.error_msg
    assert res.mapspec["sources"]["big"]["inlineData"] is large_fc
    total_copied = sum(copied_feature_counts)
    assert total_copied < 1000, f"perf mutation copy-cost exceeded: {total_copied} features copied (must be <1000)"
    print(f"\n[perf-copy-cost] copied_features={total_copied} writes_share_ok={res.mapspec['sources']['big']['inlineData'] is large_fc}")


# ── #687：提交面解耦基准（loop tick 探针，非 wall-clock） ─────────────────

def _tick_probe():
    """返回 (runner, stats)：runner(coro_fn) 执行期间采样事件循环 tick 延迟。"""
    import time as _t

    ticks: list[float] = []
    state = {"stop": False}

    async def _probe():
        last = _t.perf_counter()
        while not state["stop"]:
            await asyncio.sleep(0.005)
            now = _t.perf_counter()
            ticks.append(now - last)
            last = now

    async def _run(coro_fn):
        task = asyncio.get_event_loop().create_task(_probe())
        try:
            await coro_fn()
        finally:
            state["stop"] = True
            await task
        return max(ticks) if ticks else 0.0

    return _run


@pytest.mark.perf
@pytest.mark.asyncio
async def test_noop_save_loop_tick_bounded_for_large_spec(tmp_path, monkeypatch):
    """幂等保存大 spec 时事件循环停顿必须有界（指纹快比较替代深比较）。"""
    import app.services.mapspec.store as store_mod
    from app.services.mapspec.store import MapSpecStore
    from tests.fixtures.mapspec_cow_fixtures import get_large_inline_fc

    monkeypatch.setattr(store_mod, "BASE_STORAGE_DIR", tmp_path)

    class _SDM:
        def __init__(self):
            self.state = {}
            self.fps = {}

        async def set_map_state(self, sid, key, value, seq=None):
            self.state[key] = value
            return True

        async def get_map_state(self, sid):
            return dict(self.state)

        async def get_map_spec_fingerprint(self, sid):
            return self.fps.get(sid)

        async def set_map_spec_fingerprint(self, sid, fp):
            self.fps[sid] = fp

    sdm = _SDM()
    monkeypatch.setattr(store_mod, "session_data_manager", sdm)

    spec = {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"big": {"type": "geojson", "inlineData": get_large_inline_fc()}},
        "layers": [{"id": "l1", "type": "circle", "source": "big"}],
    }
    store = MapSpecStore()
    sid = "perf-687-noop"
    await store.save_mapspec(sid, spec)  # 首次真实落盘

    run = _tick_probe()
    max_tick = await run(lambda: store.save_mapspec(sid, spec))
    # 旧路径：两次全树 == 深比较跑在循环上（~数十 ms/MB）；新路径 O(1) 命中。
    # 25ms 上限留足 CI 抖动余量，同时远低于旧的深比较停顿。
    assert max_tick < 0.025, f"no-op save loop tick {max_tick*1000:.1f}ms — must be O(1) fingerprint compare"


@pytest.mark.perf
@pytest.mark.asyncio
async def test_get_map_state_cold_parse_loop_tick_bounded(tmp_path):
    """冷读（L1 打穿）ref-only 常态 spec 的循环停顿必须有界（解析已 to_thread）。

    #687 平台限制记录：CPython 的 json C 编解码不释放 GIL，单个 MB 级字段
    的 loads 即便在线程内也会造成等长的循环停顿——任何 to_thread 方案都
    无法对巨型单字段封顶。因此本基准锚定门禁后的常态（inline >5000 特征
    已被 mapspec_source 拒绝，大结果走 ref），此时 mapspec 字段是 KB 级，
    解析（无论线程内外）都不会冻结循环。巨型 inline 的防御在上游门。
    """
    from app.services.session_data_redis import RedisSessionStore

    inst = RedisSessionStore.__new__(RedisSessionStore)  # 绕过 __init__ 的连接
    ref_only_spec = {
        "version": "1.0",
        "view": {"center": [116.0, 39.0], "zoom": 10},
        "sources": {"big": {"type": "geojson", "ref": "ref:geojson:abc123"}},
        "layers": [{"id": "l1", "type": "circle", "source": "big"}],
    }
    raw_fields = {
        "mapspec": json.dumps(ref_only_spec, ensure_ascii=False).encode(),
        "layers": json.dumps(ref_only_spec["layers"], ensure_ascii=False).encode(),
        "_started_at": "2026-08-21T00:00:00",
    }

    async def cold_parse():
        for _ in range(50):
            out = await asyncio.to_thread(inst._parse_state_fields_sync, list(raw_fields.items()))
            assert "mapspec" in out

    run = _tick_probe()
    max_tick = await run(cold_parse)
    assert max_tick < 0.025, f"cold parse loop tick {max_tick*1000:.1f}ms — ref-only spec parse must not stall the loop"
