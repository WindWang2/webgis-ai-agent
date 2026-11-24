"""P3 逐行审查修复回归（2026-11-24，app/services/data_fabric）。

每条测试对应 ``.scratch/review-swarm/fixtasks/P3-app_services_data_fabric-01.md``
清单里的一条行为性发现 —— 在未修复代码上必须失败。
"""
from __future__ import annotations

import asyncio
import sys
import threading
import time
import types
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from app.services.data_fabric.errors import InvalidQueryError


# ── federation.py:1903 —— 单源富集失败必须 fail-open ─────────────────────────


def _chain_req(**kw):
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
    )

    return FederatedChainRequest(
        sources=[
            ChainSource(source_id="s0", dataset_id="__p3_d0__"),
            ChainSource(source_id="s1", dataset_id="__p3_d1__"),
        ],
        joins=[
            ChainJoin(
                kind="attribute_join", join_field_left="k", join_field_right="k",
                left_source_id="s0", right_source_id="s1",
            )
        ],
        **kw,
    )


def _resolved(sid, **kw):
    from app.services.data_fabric.fabric.runtime import ResolvedSource

    return ResolvedSource(
        adapter=object(),
        profile_id=f"p_{sid}",
        source_type="postgis",
        scope_key="org:_|owner:_|proj:_",
        revision="rev0123456789ab",
        **kw,
    )


def test_enrich_request_from_runtime_fail_open_per_source(monkeypatch):
    import app.services.data_fabric.fabric.runtime as runtime_mod
    from app.services.data_fabric.query.federation import enrich_request_from_runtime

    class _BrokenRuntime:
        def enrich(self, resolved, **kwargs):
            raise RuntimeError("single-source enrichment failed")

    monkeypatch.setattr(runtime_mod, "get_fabric_runtime", lambda: _BrokenRuntime())
    req = _chain_req()
    rs0 = _resolved("s0", facts_row_count=7, facts_row_count_basis="observed")
    enrich_request_from_runtime(req, {"s0": rs0, "s1": _resolved("s1")})

    # 富集异常不得中断请求：两个源都完成后续处理，行数事实仍注入。
    assert req.estimate_basis is not None
    assert set(req.estimate_basis) == {"s0", "s1"}
    assert req.sources[0].estimated_rows == 7
    assert req.estimate_basis["s0"]["rows_basis"] == "source_facts:observed"


def test_enrich_request_from_runtime_tolerates_non_numeric_feedback_factor(monkeypatch):
    import app.services.data_fabric.fabric.runtime as runtime_mod
    from app.services.data_fabric.query.federation import enrich_request_from_runtime

    class _NoopRuntime:
        def enrich(self, resolved, **kwargs):
            return resolved

    monkeypatch.setattr(runtime_mod, "get_fabric_runtime", lambda: _NoopRuntime())
    req = _chain_req()
    rs = _resolved(
        "s0", facts_row_count=10, facts_row_count_basis="observed",
        feedback_factor="not-a-number", feedback_samples=4,
    )
    enrich_request_from_runtime(req, {"s0": rs})

    assert req.sources[0].estimated_rows == 10
    assert "×feedback" not in (req.estimate_basis["s0"].get("rows_basis") or "")


# ── federation.py:2163 —— V6 semi_join_reduction 与 V5 顶层形状对齐 ──────────


def _feature(x, y, **props):
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [x, y]},
        "properties": props,
    }


def test_v6_semi_join_reduction_exposes_v5_flat_fields():
    from app.schemas.data_fabric_schema import QueryResult
    from app.services.data_fabric.query.federation import (
        ChainJoin,
        ChainSource,
        FederatedChainRequest,
        FederatedExecutor,
        chain_explain_lines,
    )

    class _Fake:
        def __init__(self, data):
            self._data = data

        def query(self, dataset_id, spec):
            feats = list(self._data[dataset_id])
            limit = getattr(spec, "limit", None) or 100
            return QueryResult(
                dataset_id=dataset_id, features=feats[:limit],
                total_count=len(feats), returned_count=min(limit, len(feats)),
            )

    left = [_feature(1, 1, k="A"), _feature(2, 2, k="A")]
    right = [
        _feature(10, 10, k="A", v=1),
        _feature(11, 11, k="B", v=2),
        _feature(12, 12, k="C", v=3),
    ]
    adapters = {"a": _Fake({"cities": left}), "b": _Fake({"totals": right})}
    req = FederatedChainRequest(
        sources=[
            ChainSource(source_id="a", dataset_id="cities"),
            ChainSource(source_id="b", dataset_id="totals"),
        ],
        joins=[ChainJoin(
            kind="attribute_join", join_field_left="k", join_field_right="k",
            left_source_id="a", right_source_id="b",
        )],
        limit=100, engine="v6",
    )
    result = FederatedExecutor(lambda sid: adapters.get(sid)).execute_chain(req)
    assert result["status"] == "success" and result["engine"] == "v6"

    reduction = result["semi_join_reduction"]
    assert reduction[0]["right_rows_before"] == 3
    assert reduction[0]["right_rows_after"] == 1
    # chain_explain_lines 只读顶层字段：不得退化为 "0 right rows skipped"。
    assert "2 right rows skipped" in "\n".join(chain_explain_lines(result))


# ── normalize.py:311 —— 非法预算 → typed InvalidQueryError ───────────────────


@pytest.mark.parametrize("deadline", ["soon", 0])
def test_invalid_execution_budget_raises_typed_error(deadline):
    from app.schemas.data_fabric_schema import QuerySpec
    from app.services.data_fabric.query.normalize import normalize_query_spec

    with pytest.raises(InvalidQueryError):
        normalize_query_spec(QuerySpec(deadline_s=deadline))


# ── planner.py:314 —— pagination_note 拼接缺分隔符 ──────────────────────────


def test_pagination_note_keeps_sentence_separator():
    from app.services.data_fabric.query.capabilities import get_capabilities
    from app.services.data_fabric.query.models import CursorPage, QuerySpecV2
    from app.services.data_fabric.query.planner import plan_query

    descriptor = SimpleNamespace(
        id="ds-1", source_type="postgis", feature_count=100,
        bbox=[0.0, 0.0, 1.0, 1.0], metadata={},
    )
    caps = get_capabilities("postgis").model_copy(
        update={"cursor_pagination": False, "offset_pagination": True,
                "sort_pushdown": True}
    )
    plan = plan_query(
        QuerySpecV2(page=CursorPage(limit=10)), descriptor, caps=caps,
    )
    assert plan.pagination_strategy == "offset"
    assert "; stable order key appended" in (plan.pagination_note or "")


# ── predicates.py:534 —— IN 与 eq 使用同一数值强转 ──────────────────────────


def test_in_predicate_coerces_numeric_strings_like_eq():
    from app.services.data_fabric.query.predicates import (
        evaluate_predicate,
        predicate_from_dict,
    )

    assert evaluate_predicate(
        predicate_from_dict({"op": "eq", "field": "x", "value": 5}), {"x": "5"}
    ) is True
    assert evaluate_predicate(
        predicate_from_dict({"op": "in", "field": "x", "values": [5]}), {"x": "5"}
    ) is True
    assert evaluate_predicate(
        predicate_from_dict({"op": "not_in", "field": "x", "values": [5]}), {"x": "5"}
    ) is False


# ── ranker.py:85 —— temporal 覆盖比较不得混排原始字符串 ─────────────────────


def test_temporal_coverage_normalizes_iso_math():
    from app.services.data_fabric.retrieval.cards import DatasetCard
    from app.services.data_fabric.retrieval.ranker import coverage_score

    card = DatasetCard(
        card_id="s/d", source_id="s", dataset_id="d", title="t",
        temporal_start="2024-01-01T00:00:00Z",
        temporal_end="2024-12-31T23:59:59Z",
    )
    _, matched = coverage_score(card, temporal=("2024-01-01", "2024-01-01"))
    assert "temporal" in matched


# ── retrieval/service.py:95 —— 索引重建 I/O 不阻塞并发检索 ──────────────────


def test_retrieval_rebuild_does_not_block_concurrent_index_reads(monkeypatch):
    import app.services.data_fabric.retrieval.service as service_mod
    from app.services.data_fabric.retrieval.cards import DatasetCard
    from app.services.data_fabric.retrieval.service import RetrievalService

    card = DatasetCard(card_id="s/d", source_id="s", dataset_id="d", title="t")
    svc = RetrievalService()
    monkeypatch.setattr(service_mod, "build_cards", lambda: [card])
    monkeypatch.setattr(svc, "_registry_signature", lambda: {"a": 1.0})
    svc._ensure_index()

    build_started = threading.Event()
    release = threading.Event()
    calls = {"n": 0}

    def cards_after_first():
        calls["n"] += 1
        if calls["n"] == 1:
            build_started.set()
            release.wait(5.0)
        return [card]

    monkeypatch.setattr(service_mod, "build_cards", cards_after_first)
    monkeypatch.setattr(svc, "_registry_signature", lambda: {"a": 2.0})

    errors: list[BaseException] = []

    def rebuild():
        try:
            svc._ensure_index()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    second_done = threading.Event()

    def reader():
        try:
            svc._ensure_index()
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            second_done.set()

    t1 = threading.Thread(target=rebuild)
    t1.start()
    assert build_started.wait(2.0)
    t2 = threading.Thread(target=reader)
    t2.start()
    try:
        assert second_done.wait(1.0), "并发检索被锁外的索引重建阻塞"
    finally:
        release.set()
        t1.join(5.0)
        t2.join(5.0)
    assert not errors


# ── granularity.py:52 —— 专名 / 裸子串置信度分层可达 ────────────────────────


def test_granularity_confidence_distinguishes_dedicated_terms():
    from app.services.data_fabric.semantic.granularity import parse_granularity

    assert parse_granularity("县级") == ("county", 0.9)
    assert parse_granularity("按县汇总") == ("county", 0.6)
    assert parse_granularity("statement") == ("province", 0.6)


# ── time_parser.py:122 —— 近 N 个月按日历月，不随 today 日号漂移 ────────────


def test_near_one_month_uses_calendar_month_boundary():
    from app.services.data_fabric.semantic.time_parser import parse_time_expr

    parsed = parse_time_expr("近1个月", now=date(2026, 11, 22))
    assert (parsed.start, parsed.end) == ("2026-11-01", "2026-11-22")


# ── source_registry.py:309 —— 凭证检测覆盖 access_key/authorization/cookie ──


_VALID_SOURCE = {
    "source_id": "test_src",
    "name": "Test Source",
    "protocol": "ogc_api",
    "endpoint": "https://ads-test.example/ogc",
}


@pytest.mark.parametrize("options", [
    {"access_key": "AKIA-PLAINTEXT"},
    {"authorization": "Bearer plaintext"},
    {"cookie": "session=plaintext"},
    {"headers": {"Authorization": "Bearer plaintext"}},
])
def test_plaintext_credential_variants_rejected(tmp_path, options):
    from app.services.data_fabric.source_registry import (
        SourceRegistryError,
        SourceRegistryService,
    )

    path = tmp_path / "a.yaml"
    path.write_text(yaml.safe_dump(dict(_VALID_SOURCE, options=options)), encoding="utf-8")
    with pytest.raises(SourceRegistryError):
        SourceRegistryService(tmp_path).load()


# ── source_registry.py:331 —— glob 与 stat 之间文件消失不应穿透 ──────────────


def test_reload_tolerates_file_deleted_between_glob_and_stat(tmp_path, monkeypatch):
    from app.services.data_fabric.source_registry import SourceRegistryService

    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text(yaml.safe_dump(dict(_VALID_SOURCE, source_id="src_a")), encoding="utf-8")
    b.write_text(yaml.safe_dump(dict(_VALID_SOURCE, source_id="src_b")), encoding="utf-8")
    svc = SourceRegistryService(tmp_path).load()
    assert {s.source_id for s in svc.list_sources()} == {"src_a", "src_b"}

    real_glob = Path.glob
    deleted = {"done": False}

    def fake_glob(self, pattern):
        items = list(real_glob(self, pattern))
        if not deleted["done"] and a in items:
            deleted["done"] = True
            a.unlink()  # 模拟 glob 返回后、stat 前文件被删除
        return items

    monkeypatch.setattr(Path, "glob", fake_glob)
    assert svc.reload_if_changed(force=True) is True
    assert {s.source_id for s in svc.list_sources()} == {"src_b"}


# ── spatial_catalog.py:198 —— search 的旁路索引同锁快照 ─────────────────────


def test_spatial_catalog_search_snapshots_side_maps_atomically():
    from app.schemas.data_fabric_schema import DatasetDescriptor
    from app.services.data_fabric.spatial_catalog import SpatialCatalogService

    class _ConcurrentUnregister(SpatialCatalogService):
        def _visible(self, dataset_id, owner):
            # 模拟 catalog 快照之后、旁路索引读取之前发生的并发 unregister。
            self.unregister_dataset(dataset_id)
            return True

    svc = _ConcurrentUnregister()
    svc.register_dataset(
        DatasetDescriptor(id="ds1", name="n", title="t", source_type="postgis"),
        tags=["alpha"], profile_id="p1",
    )
    out = svc.search(query="n")
    assert out["total"] == 1
    item = out["items"][0]
    assert item["tags"] == ["alpha"]
    assert item["profile_id"] == "p1"


# ── spatial_index_runtime.py:192 —— 经度轴 360° 满幅落格 ────────────────────


def test_grid_partition_spans_full_longitude_range():
    from app.services.data_fabric.spatial_index_runtime import grid_partition

    features = [_feature(-170.0, 0.0)]
    cells = grid_partition(features, cells_per_axis=16)
    assert (0, 8) in cells


# ── spatial_index_runtime.py:35 —— h3 可用性探测进程内缓存 ──────────────────


def test_h3_availability_caches_probe(monkeypatch):
    import app.services.data_fabric.spatial_index_runtime as module

    monkeypatch.setattr(module, "_H3_OK", None)
    monkeypatch.setitem(sys.modules, "h3", types.ModuleType("h3"))
    assert module.h3_available() is True
    assert module._H3_OK is True
    monkeypatch.delitem(sys.modules, "h3", raising=False)
    assert module.h3_available() is True  # 缓存命中，不再重新 import


# ── tile_cache.py:158 —— in-flight 表跨线程原子 ─────────────────────────────


def test_tile_build_coalescer_leader_election_atomic_across_threads():
    from app.services.data_fabric.tile_cache import TileBuildCoalescer

    coalescer = TileBuildCoalescer(wait_timeout_s=5.0)
    rendezvous = threading.Barrier(2, timeout=0.3)

    class _RendezvousDict(dict):
        """让同键并发双方都在「未登记」时刻做出 leader 判定（无锁竞态窗）。"""

        @staticmethod
        def _meet():
            try:
                rendezvous.wait()
            except threading.BrokenBarrierError:
                pass

        def __contains__(self, key):  # 旧实现（key not in self）路径
            absent = not dict.__contains__(self, key)
            if absent:
                self._meet()
                return True  # 双方都按入窗时的「缺失」判定继续
            return False

        def get(self, key, default=None):  # 新实现（.get）路径
            if not dict.__contains__(self, key):
                self._meet()
                return default
            return dict.get(self, key, default)

    coalescer._inflight = _RendezvousDict()
    builds: list[str] = []

    async def builder():
        builds.append("build")
        await asyncio.sleep(0.2)
        return "value"

    results: list[str] = []
    start_barrier = threading.Barrier(2)

    def worker():
        start_barrier.wait()
        results.append(asyncio.run(coalescer.run("k", builder)))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)

    assert results == ["value", "value"]
    assert builds == ["build"], "同键并发双方都当 leader（惊群）"


# ── tile_cache.py:175-178 —— leader 取消不得连带取消 follower ───────────────


async def test_tile_build_coalescer_leader_cancellation_does_not_cancel_follower():
    from app.services.data_fabric.tile_cache import TileBuildCoalescer

    coalescer = TileBuildCoalescer(wait_timeout_s=5.0)
    leader_started = asyncio.Event()

    async def leader_builder():
        leader_started.set()
        await asyncio.sleep(30)

    async def follower_builder():
        return "follower-value"

    leader = asyncio.create_task(coalescer.run("k", leader_builder))
    await leader_started.wait()
    follower = asyncio.create_task(coalescer.run("k", follower_builder))
    await asyncio.sleep(0)  # 让 follower 进入共享槽等待
    leader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await leader
    assert await asyncio.wait_for(follower, timeout=2.0) == "follower-value"


# ── vector_carrier.py:394 —— 首批列编码只做一次 ─────────────────────────────


def test_iter_features_to_arrow_batches_encodes_first_batch_once(monkeypatch):
    import app.services.data_fabric.vector_carrier as carrier

    if not carrier.arrow_available():
        pytest.skip("pyarrow unavailable (optional carrier dependency)")

    calls: list[int] = []
    real = carrier._features_to_columns

    def counting(features):
        calls.append(len(features))
        return real(features)

    monkeypatch.setattr(carrier, "_features_to_columns", counting)
    batch = [
        _feature(1.0, 2.0, a=1),
        _feature(3.0, 4.0, a=2),
    ]
    out = list(carrier.iter_features_to_arrow_batches([batch]))
    assert out and calls == [2]


# ── versioning_gate.py:241 —— snapshot store 单例惰建有锁 ───────────────────


def test_get_snapshot_store_singleton_is_thread_safe(monkeypatch):
    import app.services.data_fabric.versioning_gate as gate

    instances: list[object] = []

    class _SlowStore(gate.InMemorySnapshotStore):
        def __init__(self):
            time.sleep(0.2)
            instances.append(self)
            super().__init__()

    monkeypatch.setattr(gate, "InMemorySnapshotStore", _SlowStore)
    monkeypatch.setattr(gate, "_STORE", None)

    results: list[object] = []
    barrier = threading.Barrier(2)

    def worker():
        barrier.wait()
        results.append(gate.get_snapshot_store())

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)

    assert len(results) == 2
    assert results[0] is results[1]
    assert len(instances) == 1


# ── versioning_gate.py:365 —— pinned 回放复用已存 content_fingerprint ───────


def test_pinned_snapshot_replay_reuses_stored_fingerprint(monkeypatch):
    import app.services.data_fabric.versioning_gate as gate

    store = gate.InMemorySnapshotStore()
    features = [{"id": 1, "value": 2.5}]
    first = gate.pin_or_fetch("ds/pin", "v1", {}, lambda: features, store=store)
    assert first.content_fingerprint

    def _boom(_features):
        raise AssertionError("pinned replay recomputed content_fingerprint")

    def _no_fetch():
        raise AssertionError("pinned snapshot hit must not fetch")

    monkeypatch.setattr(gate, "content_fingerprint", _boom)
    replayed = gate.pin_or_fetch("ds/pin", "v1", {}, _no_fetch, store=store)
    assert replayed.source == "snapshot"
    assert replayed.content_fingerprint == first.content_fingerprint
