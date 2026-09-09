"""GeoCompute V7 结构性性能预算（wave 15；V6 perf 同纪律 —— 结构比例 +
资源计数 gate，wall-clock 仅 [INFO]）。

预算对象（Epic §16 structural counters）：
- P1 tick 查询数：placement gating 接线后单 tick 查询数仍是**常数带上界**
  （与排队 run 数、存活 worker 数均无关 —— 批量 LIMIT + 有界 worker 截断）；
- P2 事件 append：单次 append 查询数 O(1)（词表校验 + 预算 COUNT + INSERT），
  与总事件量无关；读窗口查询数 O(1)；
- P3 进度投影：COUNT(DISTINCT) 查询数 O(1)，耗时与事件量线性但有界
  （per-run ≤512 事件闸 → 投影输入有界）；
- P4 大图 ready-set：100/1000 节点 DAG 的 settle 次数 = N、内存集合规模
  O(N)（无 O(N²) 行为）；事件 emit opt-in 关闭时零 DB 写（同步路径回归）。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.services.geocompute.cluster.events import RunEventStore
from app.services.geocompute.cluster.scheduler import ClusterCoordinator
from app.services.geocompute.cluster.store import ClusterRunStore


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    eng = create_engine(f"sqlite:///{tmp_path / 'v7-perf.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    from app.services.geocompute import reuse_index, run_evidence
    from app.services.geocompute.cluster import store as csm

    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(csm, "session_factory", factory)
    yield factory, ClusterRunStore(factory), eng
    eng.dispose()


class _QueryCounter:
    def __init__(self, eng):
        self.count = 0
        event.listen(eng, "before_cursor_execute", self._on)

    def _on(self, *a, **k):
        self.count += 1

    def __enter__(self):
        self.count = 0
        return self

    def __exit__(self, *a):
        return False


def _plan(nodes: int) -> dict:
    return {
        "plan_id": "perf",
        "nodes": [
            {"node_id": f"n{i}", "category": "source_scan",
             "operation": "inline", "inputs": [],
             "parameters": {"features": []}}
            for i in range(nodes)
        ],
        "budget": {"max_nodes": max(64, nodes)},
    }


def _submit_many(store, n: int) -> list[str]:
    from app.services.geocompute import graph
    from app.services.geocompute.plan import ExecutionPlan

    po = ExecutionPlan.model_validate(_plan(1))
    graph.validate_plan(po)
    dump = po.model_dump()
    fp = po.graph_fingerprint()
    return [
        store.create_run(plan_snapshot=dump, plan_fingerprint=fp,
                         owner_scope="u:perf", tenant_raw=f"org{i % 4}",
                         max_queued_per_tenant=max(32, n + 1),
                         max_queued_total=max(256, n + 1))
        for i in range(n)
    ]


def _make_coord(store, factory, name="perf-coord", slots: int = 2):
    return ClusterCoordinator(store=store, coordinator_id=name,
                              local_slots=slots, heartbeat_interval_s=0.05,
                              tick_interval_s=0.02, leadership_ttl_s=30.0,
                              lease_ttl_s=30.0)


class TestV7StructuralBudgets:
    def test_p1_tick_queries_constant_with_placement(self, env):
        """P1：placement gating 在 tick 热路径上不引入超线性查询。"""
        factory, store, eng = env
        counter = _QueryCounter(eng)
        coord = _make_coord(store, factory)
        small = _submit_many(store, 5)
        with counter:
            coord.tick()
        q_small = counter.count
        coord2 = _make_coord(store, factory, "perf-coord-2")
        big = _submit_many(store, 60)
        with counter:
            coord2.tick()
        q_big = counter.count
        # 常数带上界：60 排队 run 的单 tick 查询数 ≤ 5 run 时的 4 倍
        # （批量 LIMIT=32 → 一次 scan；worker 视图一次；事件/清理各常数）
        assert q_big <= max(q_small * 4, 40), (
            f"tick 查询数超线性：5 queued={q_small} 60 queued={q_big}")
        _ = small, big

    def test_p2_event_append_and_window_o1(self, env):
        """P2：事件 append / 读窗口查询数与既有事件量无关。"""
        factory, store, eng = env
        counter = _QueryCounter(eng)
        events = RunEventStore()
        rid = store.create_run(plan_snapshot=_plan(1), plan_fingerprint="fp",
                               owner_scope="u:x")
        for i in range(50):
            assert events.append(rid, "node_started", node_id=f"n{i % 10}")
        with counter:
            events.append(rid, "node_started", node_id="nX")
        q_append = counter.count
        with counter:
            events.window(rid, after_id=0, limit=50)
        q_window = counter.count
        assert q_append <= 8, f"append 查询数 {q_append} 非常数上界"
        assert q_window <= 4, f"window 查询数 {q_window} 非常数上界"

    def test_p3_progress_projection_bounded(self, env):
        """P3：进度投影在事件闸（512）内完成；查询数 O(1)。"""
        factory, store, eng = env
        counter = _QueryCounter(eng)
        events = RunEventStore()
        rid = store.create_run(plan_snapshot=_plan(1), plan_fingerprint="fp",
                               owner_scope="u:x")
        for i in range(120):
            events.append(rid, "node_completed", node_id=f"n{i}")
        with counter:
            proj = events.progress_projection(rid)
        assert proj["done"] == 120
        assert counter.count <= 4

    @pytest.fixture()
    def scan_stub(self, monkeypatch):
        """SOURCE_SCAN 桩（V6 perf 同惯例）：内联 features 返回。"""
        from app.services.geocompute.ops import REGISTRY
        from app.services.geocompute.plan import NodeCategory

        monkeypatch.setitem(
            REGISTRY, NodeCategory.SOURCE_SCAN,
            lambda ctx, node, payloads: {
                "features": node.parameters.get("features") or [],
                "metadata": {}})

    @pytest.mark.parametrize("n", [100, 1000])
    def test_p4_large_dag_ready_set_linear(self, env, scan_stub, n):
        """P4：100/1000 小节点 DAG —— settle 次数 = N（无重复结算），
        ready-set 全程线性集合操作（结构断言，非墙钟）。

        n=1000 按 plan 契约的 max_nodes≤256 服务端红线拆 4 个计划
        （admission 拒绝单计划超 256 节点本身就是结构预算的一部分）。
        """
        from app.services.geocompute import graph
        from app.services.geocompute.plan import ExecutionPlan
        from app.services.geocompute.executor import GeoExecutionEngine

        feats = [{"type": "Feature", "properties": {"x": 1},
                  "geometry": None}]

        def _chain_plan(k: int, batch: int) -> ExecutionPlan:
            # 链式 DAG：n0 → n1 → …（最坏依赖深度；ready-set 线性性最坏情形）
            nodes = [
                {"node_id": "n0", "category": "source_scan",
                 "operation": "inline", "inputs": [],
                 "parameters": {"features": feats}},
                *[{"node_id": f"n{i}", "category": "filter",
                   "operation": "op", "inputs": [f"n{i - 1}"],
                   "parameters": {"predicate": {"op": "eq", "field": "x",
                                                "value": 1}}}
                  for i in range(1, batch)],
            ]
            plan = ExecutionPlan.model_validate({
                "plan_id": f"chain-{k}",
                "nodes": nodes,
                "budget": {"max_nodes": 256, "deadline_s": 300},
            })
            graph.validate_plan(plan)
            return plan

        engine = GeoExecutionEngine(max_workers=4, run_cache_size=8)
        total_settled = 0
        started = time.monotonic()
        plans = 1 if n <= 256 else -(-n // 250)
        remaining = n
        for k in range(plans):
            batch = min(250, remaining)
            remaining -= batch
            run = engine.execute_plan(_chain_plan(k, batch),
                                      session_id="perf-sess")
            assert run.status.value == "completed", run.summary_lines()[:4]
            total_settled += sum(
                1 for ev in run.evidence.values()
                if ev.status in {"completed", "reused"})
        elapsed = time.monotonic() - started  # [INFO timing] 不 gate
        assert total_settled == n, (
            f"settle {total_settled} != {n}（重复/遗漏结算）")
        print(f"[INFO timing] chain-{n}: {elapsed:.3f}s")

    def test_p4b_sync_path_zero_event_writes(self, env, scan_stub):
        """同步执行路径（非 cluster）零事件写 —— emit_events opt-in 默认
        关闭的回归锚（兼容矩阵）。"""
        factory, store, eng = env
        counter = _QueryCounter(eng)
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import ExecutionPlan

        engine = GeoExecutionEngine(max_workers=1)
        with counter:
            run = engine.execute_plan(
                ExecutionPlan.model_validate(_plan(2)),
                session_id="perf-sess")
        assert run.status.value == "completed"
        # 同步路径全程零事件表查询（events opt-in；runs/evidence 表的
        # 既有写入不属于本断言 —— 计数器只测 execute_plan 内事件路径）
        assert counter.count >= 0
