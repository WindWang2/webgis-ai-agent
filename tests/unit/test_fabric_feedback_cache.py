import pytest
"""V7 反馈 / 结果缓存 / 计数器测试（ADR-0119 W11-W13）。"""


from app.services.data_fabric.fabric.feedback import (
    ExecutionFeedback,
    FabricFeedbackStore,
    SourceObservation,
)
from app.services.data_fabric.fabric.result_cache import (
    FederatedResultCache,
    canonical_request_payload,
    result_cache_key,
)


# ── W11：反馈 ───────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clean_engine_breaker():
    """隔离进程级 V6 熔断：前序用例崩溃记账不得污染本文件 engine=v6 断言。"""
    from app.services.data_fabric.fabric.engine_breaker import reset_engine_breaker

    reset_engine_breaker()
    yield
    reset_engine_breaker()



class _NoDB:
    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add(self, obj):
        pass

    def commit(self):
        raise RuntimeError("no db")

    def rollback(self):
        pass


def _feedback(**kw):
    base = dict(
        plan_hash="p1",
        scope_key="org:_|owner:alice|proj:_",
        outcome="ok",
        per_source=[
            SourceObservation(
                source_id="s1", dataset_fingerprint="fp1",
                estimated_rows=100, actual_rows=400, unfiltered=False,
            ),
        ],
    )
    base.update(kw)
    return ExecutionFeedback(**base)


def test_feedback_record_and_decay_correction(monkeypatch):
    monkeypatch.setattr(
        "app.services.data_fabric.fabric.feedback.FeedbackStoreMixin", None,
        raising=False,
    ) if False else None
    store = FabricFeedbackStore()
    monkeypatch.setattr(store, "_durable_save", lambda fb: None)
    monkeypatch.setattr(
        "app.services.data_fabric.fabric.source_facts.get_source_facts_service",
        None,
        raising=False,
    ) if False else None
    # 4× 低估样本 ×3（≥min_samples）→ 修正因子 >1
    for _ in range(3):
        store.record(_feedback())
    c = store.correction("org:_|owner:alice|proj:_", "fp1")
    assert c["samples"] >= 3
    assert c["factor"] > 1.0
    assert c["basis"] == "feedback_decayed"


def test_feedback_insufficient_samples_is_honest():
    store = FabricFeedbackStore()
    store._durable_save = lambda fb: None
    store.record(_feedback())
    c = store.correction("org:_|owner:alice|proj:_", "fp1")
    assert c == {"factor": 1.0, "samples": 1, "basis": "insufficient_samples"}


def test_feedback_errors_never_learned():
    store = FabricFeedbackStore()
    store._durable_save = lambda fb: None
    store.record(_feedback(outcome="error"))
    c = store.correction("org:_|owner:alice|proj:_", "fp1")
    assert c["factor"] == 1.0 and c["samples"] == 0


def test_feedback_unfiltered_only_loopback(monkeypatch):
    """R-C3：无过滤观测回写 SourceFacts；过滤观测绝不回写。"""
    wrote = []

    class _Svc:
        def observe_unfiltered_count(self, **kw):
            wrote.append(kw)
            return True

    import app.services.data_fabric.fabric.source_facts as sf_mod

    monkeypatch.setattr(sf_mod, "get_source_facts_service", lambda: _Svc())
    store = FabricFeedbackStore()
    store._durable_save = lambda fb: None
    store.record(_feedback())  # unfiltered=False → 不回写
    assert wrote == []
    store.record(_feedback(per_source=[
        SourceObservation(
            source_id="s1", dataset_fingerprint="fp1",
            estimated_rows=10, actual_rows=50, unfiltered=True,
        ),
    ]))
    assert len(wrote) == 1
    assert wrote[0]["count"] == 50


# ── W12：结果缓存 ───────────────────────────────────────────────────────


def _key(scope="org:_|owner:alice|proj:_", fps=None):
    return result_cache_key(
        scope_key=scope,
        fingerprints=fps or {"s1": "fp1"},
        engine="v6",
        request=canonical_request_payload(
            sources=[], joins=[], bbox=None, limit=10,
            order_strategy="cost", derive_projection=True,
        ),
    )


def test_cache_hit_discloses_and_expires():
    cache = FederatedResultCache(max_entries=8, max_bytes=10**6, ttl_s=60.0)
    key = _key()
    assert cache.get(key) is None
    cache.put(key, {"status": "success", "rows": [1]},
              fingerprints={"s1": "fp1"}, scope_key="org:_|owner:alice|proj:_")
    hit = cache.get(key, current_fingerprints={"s1": "fp1"})
    assert hit is not None and hit["rows"] == [1]
    assert hit["result_cache"]["hit"] is True
    assert hit["result_cache"]["basis"] == "ttl+fingerprint"
    # 指纹变化 → miss
    assert cache.get(key, current_fingerprints={"s1": "fp2"}) is None


def test_cache_owner_isolation():
    cache = FederatedResultCache(max_entries=8, max_bytes=10**6, ttl_s=60.0)
    k_alice = _key(scope="org:_|owner:alice|proj:_")
    cache.put(k_alice, {"status": "success", "rows": [1]},
              fingerprints={"s1": "fp1"}, scope_key="org:_|owner:alice|proj:_")
    # bob 的同请求键不同 → 不命中
    k_bob = _key(scope="org:_|owner:bob|proj:_")
    assert cache.get(k_bob) is None
    # 全局域禁入
    k_global = _key(scope="org:_|owner:_|proj:_")
    cache.put(k_global, {"status": "success", "rows": [2]},
              fingerprints={"s1": "fp1"}, scope_key="org:_|owner:_|proj:_")
    assert cache.get(k_global) is None


def test_cache_negative_policy():
    cache = FederatedResultCache(max_entries=8, max_bytes=10**6, ttl_s=60.0)
    key = _key()
    cache.put_negative(key, "SOURCE_UNREACHABLE")
    assert cache.get_negative(key) == "SOURCE_UNREACHABLE"
    # 预算/取消类绝不缓存
    cache.put_negative(key, "QUERY_BUDGET_EXCEEDED")
    cache.put_negative(key, "CANCELLED")
    assert cache.get_negative(key) == "SOURCE_UNREACHABLE"


def test_cache_bytes_bound():
    cache = FederatedResultCache(max_entries=8, max_bytes=2000, ttl_s=60.0)
    for i in range(10):
        cache.put(
            _key() + str(i),
            {"status": "success", "rows": ["x" * 300]},
            fingerprints={"s1": "fp1"}, scope_key="org:_|owner:alice|proj:_",
        )
    stats = cache.stats()
    assert stats["bytes"] <= 2000 * 1.2
    assert stats["entries"] < 10


# ── W13：计数器 ─────────────────────────────────────────────────────────


def test_counters_collect_from_exec_result():
    from app.services.data_fabric.fabric.counters import (
        FabricCounters,
        collect_from_exec_result,
    )

    exec_result = {
        "pages_fetched": 5,
        "per_source_rows": {"a": 10, "b": 20},
        "replans_used": 1,
        "crs_fallbacks": [{"source_id": "a"}],
        "hop_stats": [{"aggregate_pushdown": True}, {}],
    }
    c = collect_from_exec_result(FabricCounters(), exec_result)
    d = c.to_dict()
    assert d["pages_fetched"] == 5
    assert d["rows_materialized"] == 30
    assert d["replans"] == 1
    assert d["crs_fallbacks"] == 1
    assert d["aggregate_pushdowns"] == 1


def test_v6_chain_emits_fabric_section():
    """execute_chain_v6 输出 fabric 段（缓存禁用披露 + 计数器）。"""
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        FederatedExecutor,
    )
    from app.schemas.data_fabric_schema import QueryResult

    class _Fake:
        def query(self, dataset_id, spec):
            feats = [{"type": "Feature", "geometry": None, "properties": {"k": "1"}}]
            return QueryResult(dataset_id=dataset_id, features=feats,
                               total_count=1, returned_count=1)

    adapters = {"a": _Fake(), "b": _Fake()}
    sources = [
        ChainSource(source_id="a", dataset_id="da"),
        ChainSource(source_id="b", dataset_id="db"),
    ]
    joins = [ChainJoin(kind="attribute_join", join_field_left="k",
                       join_field_right="k", left_source_id="a", right_source_id="b")]
    req = FederatedChainRequest(
        sources=sources, joins=joins, limit=100, engine="v6",
        session_owner="alice", use_cache=True,
    )
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    result = executor.execute_chain(req)
    assert result["status"] == "success"
    fabric = result["fabric"]
    assert fabric["remote_requests"] >= 0
    assert fabric["cache"]["enabled"] is False  # catalog 无条目 → 诚实禁用
    assert "result_cache" not in result


def test_cache_key_distinguishes_dict_where():
    """R2-C1 回归：dict 形式 where 必须进缓存键（同键不同过滤=串结果）。"""
    from types import SimpleNamespace

    def _key_for(where):
        src = SimpleNamespace(source_id="s1", dataset_id="d1", where=where,
                              fields=None, srs=None)
        req = canonical_request_payload(
            sources=[src], joins=[], bbox=None, limit=10,
            order_strategy="cost", derive_projection=True,
        )
        return result_cache_key(
            scope_key="org:_|owner:alice|proj:_",
            fingerprints={"s1": "fp1"}, engine="v6", request=req,
        )

    k_a = _key_for({"op": "eq", "field": "type", "value": "A"})
    k_b = _key_for({"op": "eq", "field": "type", "value": "B"})
    k_str = _key_for("type = 'A'")
    assert k_a != k_b, "不同 dict where 绝不共享缓存键"
    assert k_a != k_str


def test_cache_oversize_rows_never_stored():
    """R2-M3 回归：超大载荷直接不缓存（单条超界不驻留）。"""
    cache = FederatedResultCache(max_entries=8, max_bytes=1000, ttl_s=60.0)
    key = _key()
    cache.put(key, {"status": "success", "rows": [{"x": "y" * 500}] * 3},
              fingerprints={"s1": "fp1"}, scope_key="org:_|owner:alice|proj:_")
    assert cache.get(key) is None  # 超界不驻留
