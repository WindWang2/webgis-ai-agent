"""G03 GD-10 —— Ingest 有界资源基准（50 / 5k / 100k / 1M 要素）。

纪律（对齐 tests/perf 既有约定 #664）：
- 锚定**机器无关不变量**：守卫/摄入的峰值内存相对输入字节数的比值上界、
  步级耗时的**相对缩放**（1M ≈ O(n)，不出现超线性）、要素帽/网络调用
  次数等可计数事实；
- 绝不断言绝对墙钟（机器相关）；墙钟与峰值内存**如实记录**到结果并打印，
  供 INGEST_PERFORMANCE.md 引用（本地运行数字，环境披露见该文档）；
- 1M 档标记 heavy（内存 ~GB 级构造），CI/本地可 -m heavy 跑。
"""
from __future__ import annotations

import gc
import time
import json
import tracemalloc

import pytest

from app.services.data_ingest.pipeline import (
    get_ingest_pipeline,
    reset_ingest_pipeline,
)
from app.services.dataset_semantics import store as sem_store_mod
from app.services.dataset_semantics.store import DatasetSemanticStore

#: 峰值内存上界（机器无关不变量）：peak ≤ 8 × 载荷字节 + 512KB。
#: 纯 Python dict 树开销 ≈ JSON 字节的 5-6 倍（实测 5k/100k 档稳定
#: 5.6-5.7×）；+512KB 覆盖每次 ingest 的固定构造开销（descriptor/
#: quality/pydantic 模型，实测 ~100KB 量级，与数据规模无关）。
PEAK_RATIO_CAP = 8.0
PEAK_FIXED_ALLOWANCE = 512 * 1024


def _peak_bounded(m: dict) -> bool:
    return m["peak_bytes"] <= PEAK_RATIO_CAP * m["payload_bytes"] + PEAK_FIXED_ALLOWANCE
#: 相对缩放上界：100k 档耗时 / 5k 档耗时（线性 ≈ 20×；容 3× 抖动余量）。
SCALING_CAP_100K_OVER_5K = 60.0


def _fc(n: int, lon0=104.06, lat0=30.6) -> dict:
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "id": i,
                "geometry": {
                    "type": "Point",
                    "coordinates": [lon0 + (i % 1000) * 0.0005,
                                    lat0 + (i // 1000) * 0.0005],
                },
                "properties": {"id": i, "kind": f"k{i % 8}", "n": i % 97},
            }
            for i in range(n)
        ],
    }


@pytest.fixture(autouse=True)
def _reset():
    reset_ingest_pipeline()
    yield
    reset_ingest_pipeline()


@pytest.fixture()
def sem_store(tmp_path, monkeypatch):
    store = DatasetSemanticStore(base_dir=tmp_path)
    monkeypatch.setattr(sem_store_mod, "_store", store)
    yield store
    from app.services.dataset_semantics import reset_dataset_semantic_store

    reset_dataset_semantic_store()


def _measure_ingest(sid: str, fc: dict, *, warmup: bool = False) -> dict:
    """一次真实 ingest：返回 {elapsed_ms, peak_bytes, payload_bytes, result}。

    ``warmup=True``：不计入测量（冷启动导入/首跑缓存是固定开销，与数据
    规模无关——剔除后各档可比）。
    """
    payload_bytes = len(json.dumps(fc, ensure_ascii=False).encode("utf-8"))
    gc.collect()
    if not warmup:
        tracemalloc.start()
    import asyncio

    async def _run():
        return await get_ingest_pipeline().ingest(
            sid, fc, name="bench", crs="EPSG:4326")

    t_result = asyncio.get_event_loop_policy().new_event_loop()
    try:
        result = t_result.run_until_complete(_run())
    finally:
        t_result.close()
    if not warmup:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    else:
        peak = 0
    return {
        "elapsed_ms": result.elapsed_ms,
        "peak_bytes": peak,
        "payload_bytes": payload_bytes,
        "feature_count": len(fc["features"]),
        "ok": result.ok,
        "result": result,
    }


class TestIngestBoundedTiers:
    def test_tier_50(self, sem_store):
        _measure_ingest("g03-bench-50-warmup", _fc(20), warmup=True)
        m = _measure_ingest("g03-bench-50", _fc(50))
        assert m["ok"], m["result"].error
        assert m["result"].profile_summary["row_count"] == 50
        assert _peak_bounded(m)
        print(f"\n[g03-bench] 50 feats: {m['elapsed_ms']}ms, "
              f"peak {m['peak_bytes'] // 1024}KB, payload {m['payload_bytes'] // 1024}KB")

    def test_tier_5k(self, sem_store):
        m = _measure_ingest("g03-bench-5k", _fc(5_000))
        assert m["ok"], m["result"].error
        assert _peak_bounded(m)
        print(f"\n[g03-bench] 5k feats: {m['elapsed_ms']}ms, "
              f"peak {m['peak_bytes'] // 1024}KB, payload {m['payload_bytes'] // 1024}KB")

    def test_tier_100k_scaling_bounded(self, sem_store):
        m5k = _measure_ingest("g03-bench-5k-b", _fc(5_000))
        m100k = _measure_ingest("g03-bench-100k", _fc(100_000))
        assert m100k["ok"] and m5k["ok"]
        assert m100k["result"].profile_summary["row_count"] == 100_000
        assert _peak_bounded(m100k)
        # 相对缩放：100k/5k 耗时比 < 60（线性 20× + 余量；超线性即红旗）
        assert (m100k["elapsed_ms"] / max(m5k["elapsed_ms"], 1.0)) < SCALING_CAP_100K_OVER_5K
        print(f"\n[g03-bench] 100k feats: {m100k['elapsed_ms']}ms, "
              f"peak {m100k['peak_bytes'] // (1024*1024)}MB, "
              f"payload {m100k['payload_bytes'] // (1024*1024)}MB, "
              f"scaling 100k/5k = {m100k['elapsed_ms'] / max(m5k['elapsed_ms'], 1.0):.1f}x")

    @pytest.mark.heavy
    @pytest.mark.timeout(1500)
    def test_tier_1m_heavy_budget_rejected_and_preflight_bounded(self, sem_store):
        """1M 要素（heavy）：store 前阶段有界 + 会话字节预算如实拒收。

        生产纪律（session_data.py #912）：单载荷 > SESSION_STORE_MAX_BYTES
        （默认 50MB）→ STORE_FAILED「byte budget」——1M 要素 ~150MB 载荷
        **必须**被拒（有界资源证据，不是失败）。本测试同时度量 store 前
        的守卫+指纹+剖析阶段在 1M 下的真实耗时/内存（INGEST_PERFORMANCE
        引用数字）。
        """
        import asyncio

        from app.lib.data.profile import profile_features
        from app.services.data_ingest.geojson_guard import (
            validate_feature_collection,
        )

        fc = _fc(1_000_000)
        payload_bytes = len(json.dumps(fc, ensure_ascii=False).encode("utf-8"))
        gc.collect()
        tracemalloc.start()
        t0 = asyncio.get_event_loop_policy().new_event_loop()

        async def _run():
            return await get_ingest_pipeline().ingest(
                "g03-bench-1m", fc, name="bench-1m", crs="EPSG:4326")

        try:
            result = t0.run_until_complete(_run())
        finally:
            t0.close()
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()

        # 预算拒收证据（类型化 STORE_FAILED + byte budget 提示）
        assert not result.ok
        assert result.error_code == "STORE_FAILED"
        assert "byte budget" in result.error
        assert "store" not in result.steps_completed
        assert result.step_timings_ms.get("profile"), "store 前阶段计时在场"

        # 守卫/剖析 1M 直测（同函数内复用已构造 fc）
        t_guard = time.monotonic()
        validate_feature_collection(fc)
        guard_ms = (time.monotonic() - t_guard) * 1000
        t_prof = time.monotonic()
        vp, _quality = profile_features(fc["features"], crs="EPSG:4326")
        prof_ms = (time.monotonic() - t_prof) * 1000
        assert vp.extent and vp.extent[0] > 0  # 成都区域 bbox 真实剖析
        print(f"\n[g03-bench] 1M feats: payload {payload_bytes // (1024*1024)}MB, "
              f"pipeline-preflight {result.elapsed_ms}ms "
              f"(guard {guard_ms:.0f}ms, profile {prof_ms:.0f}ms), "
              f"peak {peak // (1024*1024)}MB, "
              f"store=REJECTED(byte budget) ✓")
        assert _peak_bounded({"peak_bytes": peak, "payload_bytes": payload_bytes})


class TestAdapterNetworkBudget:
    def test_geojson_url_cache_hits_bound_network_calls(self, patched_safe_sessions):
        """probe+describe+preview+query 复用一次受控取回（网络调用 = 1）。"""
        from tests.data.fabric_fixtures import FakeSourceServer

        from app.schemas.data_fabric_schema import ConnectionProfile
        from app.services.data_fabric.adapters.geojson_url_adapter import (
            GeojsonUrlAdapter,
        )

        fc = _fc(30)
        server = FakeSourceServer()

        def _handler(request):
            from tests.fixtures.data_fabric.fake_server import make_response

            server._record(request)  # fixture 计数约定
            return make_response(
                request.url, json_body=fc,
                headers={"Content-Type": "application/geo+json"})

        server._add("/bench.geojson", _handler)
        patched_safe_sessions(server)
        adapter = GeojsonUrlAdapter(ConnectionProfile(
            id="gj_bench", source_type="geojson_url",
            url=server.base_url + "/bench.geojson"))
        assert adapter.probe() is True
        adapter.describe("default")
        adapter.preview("default", limit=10)
        adapter.query("default", None)
        assert len(server.requested_urls) == 1, (
            "短缓存必须把多次面访问合并为一次网络取回（缓存命中证据）"
        )
