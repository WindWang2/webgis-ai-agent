"""V8 结果缓存强化（ADR-0130 Phase F）：stampede 单飞 + 可选分布式后端。

覆盖：
- SingleFlight：并发同键只执行一次（其余共享首问结果）；owner 失败 →
  等待者自行执行；等待超时 → 自行执行；结果与直接执行一致；
- execute_chain_v6 集成：shared 命中带 basis=singleflight 披露；
- Redis 后端 seam：写穿/回读/故障 fail-open（fake client，无真实网络）；
- stats 披露 distributed_backend / singleflight_inflight。
"""
import threading
import time
from typing import Any, Dict, List

import pytest

from app.services.data_fabric.fabric.result_cache import (
    FederatedResultCache,
    ResultCacheBackend,
    SingleFlight,
    canonical_request_payload,
    reset_result_cache,
    result_cache_key,
)


# ── SingleFlight 单元 ─────────────────────────────────────────────────


def test_singleflight_executes_once_for_concurrent_keys():
    sf = SingleFlight(max_wait_s=5.0)
    calls = []
    barrier = threading.Barrier(4)
    results = []
    lock = threading.Lock()

    def fn():
        with lock:
            calls.append(1)
        time.sleep(0.15)
        return {"v": 42}

    def worker():
        barrier.wait()
        out, shared = sf.run("k", fn)
        with lock:
            results.append((out["v"], shared))

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(calls) == 1  # 只执行一次
    shared_count = sum(1 for _, s in results if s)
    assert (out_v for out_v, _ in results)  # 全部拿到 42
    assert all(v == 42 for v, _ in results)
    assert 1 <= shared_count <= 3


def test_singleflight_owner_failure_lets_waiter_proceed():
    sf = SingleFlight(max_wait_s=5.0)
    attempts = []

    def owner_fn():
        attempts.append("owner")
        time.sleep(0.1)
        raise RuntimeError("owner blew up")

    def waiter_fn():
        attempts.append("waiter")
        return {"v": "waiter-ok"}

    out_box = {}

    def owner_thread():
        try:
            sf.run("k", owner_fn)
        except RuntimeError:
            pass

    t = threading.Thread(target=owner_thread)
    t.start()
    time.sleep(0.02)  # 确保 owner 先占位
    out, shared = sf.run("k", waiter_fn)
    t.join()
    assert out == {"v": "waiter-ok"} and shared is False
    assert "waiter" in attempts  # owner 失败后等待者自行执行


def test_singleflight_timeout_proceeds_self():
    sf = SingleFlight(max_wait_s=0.05)
    box = {}

    def slow_owner():
        time.sleep(0.3)
        return {"v": "slow"}

    t = threading.Thread(target=lambda: sf.run("k", slow_owner))
    t.start()
    time.sleep(0.02)
    out, shared = sf.run("k", lambda: {"v": "fast"})
    t.join()
    assert out == {"v": "fast"} and shared is False
    box["done"] = True


# ── 分布式后端 seam ───────────────────────────────────────────────────


class _FakeRedis(ResultCacheBackend):
    """dict 后端（契约级 fake；无网络）。"""

    def __init__(self, *, broken: bool = False):
        self.store: Dict[str, str] = {}
        self.ttls: Dict[str, int] = {}
        self.broken = broken
        self.failures = 0

    def get(self, key: str):
        if self.broken:
            self.failures += 1
            return None
        return self.store.get(f"fabric_rc:{key}")

    def put(self, key: str, value: str, *, ttl_s: float):
        if self.broken:
            self.failures += 1
            return
        self.store[f"fabric_rc:{key}"] = value
        self.ttls[f"fabric_rc:{key}"] = int(ttl_s)

    def ping(self) -> bool:
        return not self.broken


def test_cache_write_through_and_distributed_hit():
    backend = _FakeRedis()
    cache = FederatedResultCache(ttl_s=120.0)
    cache._backend = backend  # 注入 fake（构造期 settings 未开 redis）

    key = result_cache_key(
        scope_key="org:_|owner:sess|proj:_",
        fingerprints={"s0": "fp1"},
        engine="v6",
        request=canonical_request_payload(sources=[], joins=[], bbox=None,
                                          limit=10, order_strategy="cost",
                                          derive_projection=True),
    )
    payload = {"status": "success", "rows": [{"a": 1}], "row_count": 1}
    cache.put(key, payload, fingerprints={"s0": "fp1"},
              scope_key="org:_|owner:sess|proj:_")
    # 写穿：本地 LRU + 分布式后端都有值。
    assert backend.store
    # 本地逐出 → 二线命中。
    cache.invalidate()
    hit = cache.get(key)
    assert hit is not None
    assert hit["result_cache"]["basis"] == "ttl+fingerprint+distributed"
    assert hit["rows"] == [{"a": 1}]


def test_backend_failure_is_fail_open():
    backend = _FakeRedis(broken=True)
    cache = FederatedResultCache(ttl_s=120.0)
    cache._backend = backend
    key = "k" * 64
    payload = {"status": "success", "rows": [], "row_count": 0}
    cache.put(key, payload, fingerprints={}, scope_key="org:_|owner:s|proj:_")
    # 后端写失败不抛（fail-open），本地 LRU 仍服务。
    assert backend.failures >= 1
    assert cache.get(key) is not None
    # 本地逐出后，后端读失败 → 诚实 miss（绝不假命中）。
    cache.invalidate()
    assert cache.get(key) is None
    stats = cache.stats()
    assert stats["distributed_backend"] == "_FakeRedis"
    assert stats["backend_failures"] >= 1


# ── execute_chain_v6 单飞集成 ─────────────────────────────────────────


class _SlowAdapter:
    def __init__(self, rows: List[Dict[str, Any]]):
        self._rows = rows
        self.calls = 0
        self._lock = threading.Lock()

    def query(self, dataset_id: str, spec) -> Any:
        from app.schemas.data_fabric_schema import QueryResult

        with self._lock:
            self.calls += 1
        time.sleep(0.15)
        return QueryResult(
            dataset_id=dataset_id,
            features=[
                {"type": "Feature", "geometry": None, "properties": r}
                for r in self._rows
            ],
            total_count=len(self._rows),
            schema_info={"fields": [{"name": "k", "type": "string"}]},
        )


@pytest.fixture(autouse=True)
def _clean_cache():
    reset_result_cache()
    yield
    reset_result_cache()


def test_concurrent_chain_requests_share_one_execution():
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        FederatedExecutor,
    )

    adapter0 = _SlowAdapter([{"k": "a"}])
    adapter1 = _SlowAdapter([{"k": "a"}])
    executor = FederatedExecutor(lambda sid: {"s0": adapter0, "s1": adapter1}.get(sid))
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="s0", dataset_id="d0", estimated_rows=2),
            ChainSource(source_id="s1", dataset_id="d1", estimated_rows=2),
        ],
        joins=[ChainJoin(kind="attribute_join", join_field_left="k",
                         join_field_right="k", left_source_id="s0",
                         right_source_id="s1")],
        engine="v6",
        session_owner="sess-sf",
        use_cache=True,
    )

    # catalog 注册 descriptor（owner 作用域）→ cache ctx 可解析 → 单飞生效。
    from app.schemas.data_fabric_schema import DatasetDescriptor
    from app.services.data_fabric.spatial_catalog import spatial_catalog_service

    for did in ("d0", "d1"):
        spatial_catalog_service.register_dataset(
            DatasetDescriptor(
                id=did, title=did, source_type="generic",
                geometry_type="Point", srs="EPSG:4326",
                bbox=[0.0, 0.0, 1.0, 1.0], feature_count=2,
            ),
            profile_id="p_cache",
            owner="sess-sf",
        )

    results: list = []
    barrier = threading.Barrier(3)
    lock = threading.Lock()

    def run():
        import copy

        barrier.wait()
        out = executor.execute_chain(copy.deepcopy(req))
        with lock:
            results.append(out)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3
    assert all(r["status"] == "success" for r in results)
    assert all(r["row_count"] == results[0]["row_count"] for r in results)
    # stampede 生效：3 个并发同键请求，每源实际取数次数远小于 3。
    assert adapter0.calls <= 2, f"expected stampede protection, got {adapter0.calls}"
    shared_hits = [r for r in results if (r.get("result_cache") or {}).get(
        "basis") == "singleflight"]
    assert len(shared_hits) >= 1  # 至少一个请求披露共享首问结果
