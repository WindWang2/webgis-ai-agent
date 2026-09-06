"""GeoCompute & Data Fabric V4 benchmark — bounded structural gates + INFO timings.

Companion to ``bench_geocompute_v3.py`` (same conventions): standalone,
generated fixtures only, no network/docker. Wall-clock rows are printed as
**[INFO timing]** and NEVER gate — only structural invariants do.

Usage:
    .venv/bin/python tests/benchmarks/bench_geocompute_v4.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.schemas.data_fabric_schema import QueryResult
from app.services.data_fabric.query.capabilities import get_capabilities
from app.services.data_fabric.query.federation import (
    ChainJoin,
    ChainSource,
    FederatedChainRequest,
    FederatedExecutor,
)
from app.services.data_fabric.query.statistics import DatasetStatistics
from app.services.data_fabric.query.statistics import StatisticsStore
from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    NodeCategory,
)
from app.services.geocompute import ops
from app.services.geocompute.executor import GeoExecutionEngine
from app.services.geocompute.plan import ResourceBudget
from app.services.data_fabric.query.planner import plan_query

_FAILURES: list[str] = []


def _ok(cat: str, what: str) -> None:
    print(f"{cat:<20} OK {what}")


def _fail(cat: str, what: str, detail: str = "") -> None:
    _FAILURES.append(f"{cat} / {what}: {detail}")
    print(f"{cat:<20} FAIL {what:<44} {detail}")


def _row(cat: str, what: str, detail: str) -> None:
    print(f"{cat:<20} [INFO timing] {what:<38} {detail}")


def bench_planner_overhead() -> None:
    cat = "1 planner"
    descriptor = SimpleNamespace(
        id="bench-ds", source_type="postgis", feature_count=100_000,
        bbox=[0.0, 0.0, 1.0, 1.0], geometry_type="Point", metadata={},
        fields=[{"name": "v", "type": "int"}], srs="EPSG:4326",
    )
    caps = get_capabilities("postgis")
    from app.services.data_fabric.query.models import QuerySpecV2

    stats = DatasetStatistics(dataset_fingerprint="bench-ds", row_count=100_000,
                              extent=[0.0, 0.0, 1.0, 1.0], source_type="postgis")
    t0 = time.perf_counter()
    plans = [
        plan_query(QuerySpecV2(), descriptor, caps=caps, source_id="s",
                   dataset_fingerprint="bench-ds", query_fp="q", stats=None)
        for _ in range(200)
    ]
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    for _ in range(200):
        plan_query(QuerySpecV2(), descriptor, caps=caps, source_id="s",
                   dataset_fingerprint="bench-ds", query_fp="q", stats=stats)
    warm = time.perf_counter() - t0
    _row(cat, "200 plans no-stats / with-stats", f"{cold:.3f}s / {warm:.3f}s")
    if not plans or plans[0].estimated_rows is None:
        _fail(cat, "plans produced with estimates")
        return
    _ok(cat, "400 plans planned, estimates present")


def bench_stats_store() -> None:
    cat = "2 stats-lookup"
    store = StatisticsStore(ttl_s=60, max_entries=1024)
    stats = DatasetStatistics(dataset_fingerprint="fp", row_count=10)
    t0 = time.perf_counter()
    hits = 0
    for _ in range(1000):
        store.put(stats)
        if store.get("fp") is not None:
            hits += 1
    dt = time.perf_counter() - t0
    _row(cat, "1000 put+get", f"{dt:.3f}s")
    if hits == 1000:
        _ok(cat, "all lookups hit (bounded LRU intact)")
    else:
        _fail(cat, "lookups hit", f"{hits}/1000")


class _ChainAdapter:
    source_type = "generic"

    def __init__(self, data):
        self._data = data

    def query(self, dataset_id, spec):
        return QueryResult(dataset_id=dataset_id,
                           features=list(self._data.get(dataset_id, [])))


def bench_federation() -> None:
    cat = "3 federation"
    data = {
        "d-a": [{"properties": {"k": i, "v": i}} for i in range(2000)],
        "d-b": [{"properties": {"k": i, "w": i * 2}} for i in range(0, 2000, 2)],
        "d-c": [{"properties": {"k": i, "z": i}} for i in range(0, 2000, 10)],
        "d-a2": [{"properties": {"k": i, "q": i}} for i in range(2000)],
    }
    adapters = {
        "a": _ChainAdapter(data), "b": _ChainAdapter(data),
        "c": _ChainAdapter(data), "a2": _ChainAdapter(data),
    }
    executor = FederatedExecutor(lambda sid: adapters.get(sid))
    for n, sources in ((2, ["a", "b"]), (3, ["a", "b", "c"]), (4, ["a", "b", "c", "a2"])):
        datasets = [f"d-{s}" for s in sources]
        req = FederatedChainRequest(
            sources=[ChainSource(source_id=s, dataset_id=d, estimated_rows=2000)
                     for s, d in zip(sources, datasets)],
            joins=[ChainJoin(kind="attribute_join", join_field_left="k",
                             join_field_right="k") for _ in range(len(sources) - 1)],
            limit=50_000,
        )
        t0 = time.perf_counter()
        result = executor.execute_chain(req)
        dt = time.perf_counter() - t0
        _row(cat, f"{n}-source chain", f"{dt:.3f}s, rows={result['row_count']}")
        if result["status"] != "success" or result["row_count"] == 0:
            _fail(cat, f"{n}-source chain rows")
            return
    _ok(cat, "2/3/4-source chains correct")


def bench_executor_overhead() -> None:
    cat = "4 scheduler"
    engine = GeoExecutionEngine(max_workers=4, retain_outputs=True)
    nodes = [ExecutionNode(node_id="root", category=NodeCategory.SOURCE_SCAN,
                           parameters={"features": [
                               {"type": "Feature", "geometry": None,
                                "properties": {"v": i}} for i in range(50)]})]
    nodes += [ExecutionNode(node_id=f"leaf{i}", category=NodeCategory.FILTER,
                            inputs=["root"],
                            parameters={"predicate": {"op": "lt", "field": "v", "value": i + 1},
                                        "features": []})
              for i in range(50)]
    plan = ExecutionPlan(plan_id="fan", nodes=nodes,
                         budget=ResourceBudget(max_rows=1_000_000))
    t0 = time.perf_counter()
    run = engine.execute_plan(plan)
    dt = time.perf_counter() - t0
    _row(cat, "51-node fan DAG", f"{dt:.3f}s")
    if run.status.value != "completed" or len(run.evidence) != 51:
        _fail(cat, "fan DAG completed", run.status.value)
        return
    _ok(cat, "51 nodes scheduled and completed")


def bench_checkpoint_reuse() -> None:
    cat = "5 checkpoint"
    engine = GeoExecutionEngine(max_workers=2)
    nodes = []
    prev = None
    for i in range(20):
        node = ExecutionNode(node_id=f"n{i}", category=NodeCategory.FILTER,
                             inputs=[prev] if prev else [],
                             parameters={"predicate": {"op": "lt", "field": "v", "value": i},
                                         "features": [
                                             {"type": "Feature", "geometry": None,
                                              "properties": {"v": j}} for j in range(20)]})
        nodes.append(node)
        prev = f"n{i}"
    plan = ExecutionPlan(plan_id="chain20", nodes=nodes,
                         budget=ResourceBudget(max_rows=100_000))
    t0 = time.perf_counter()
    engine.execute_plan(plan)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    run2 = engine.execute_plan(plan)
    warm = time.perf_counter() - t0
    _row(cat, "20-node chain cold/warm", f"{cold:.3f}s / {warm:.3f}s")
    statuses = {ev.status for ev in run2.evidence.values()}
    if statuses == {"reused"}:
        _ok(cat, "warm run fully reused (checkpoint hits)")
    else:
        _fail(cat, "warm run fully reused", str(statuses))


def bench_spatial_index_reuse() -> None:
    cat = "6 index-reuse"
    from app.services.data_fabric.spatial_index_runtime import (
        SpatialIndexRuntime,
        build_strtree_index,
    )

    feats = [{"type": "Feature",
              "geometry": {"type": "Point", "coordinates": [i % 50, i // 50]},
              "properties": {}} for i in range(2500)]
    rt = SpatialIndexRuntime()
    t0 = time.perf_counter()
    tree1, _ = build_strtree_index(feats, content_fingerprint="fp-x", runtime=rt)
    cold = time.perf_counter() - t0
    t0 = time.perf_counter()
    tree2, _ = build_strtree_index(feats, content_fingerprint="fp-x", runtime=rt)
    warm = time.perf_counter() - t0
    _row(cat, "2500-pt tree build/cached", f"{cold:.3f}s / {warm:.4f}s")
    if tree1 is not tree2:
        _fail(cat, "cached tree identity")
        return
    _ok(cat, "STRtree reused from fingerprint cache")


def _stub_scan(ctx, node, payloads):
    """SOURCE_SCAN 桩（bench 脚本上下文；与单元测试同一注入模式）。"""
    feats = node.parameters.get("features") or []
    return {"features": feats, "metadata": {"rows": len(feats)}}


def main() -> int:
    ops.REGISTRY[NodeCategory.SOURCE_SCAN] = _stub_scan
    print("=" * 100)
    print("geobench-v4 (structural gates; timings informational)")
    print("=" * 100)
    bench_planner_overhead()
    bench_stats_store()
    bench_federation()
    bench_executor_overhead()
    bench_checkpoint_reuse()
    bench_spatial_index_reuse()
    print("=" * 100)
    if _FAILURES:
        print(f"geobench-v4 structural invariants: FAIL -> {len(_FAILURES)} failure(s)")
        for f in _FAILURES:
            print(f"  - {f}")
        return 1
    print("geobench-v4 structural invariants: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
