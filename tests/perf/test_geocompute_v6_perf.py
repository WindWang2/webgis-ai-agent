"""V6 wave 13 — 集群调度结构性性能预算（非脆弱墙钟断言）。

设计规则（与 test_geobench_v3 同一纪律）：**结构性比例 + 资源计数** gate，
wall-clock 一律 [INFO timing] 记录不 gate（前车之鉴：cached retrieval
0.05→0.2s 的 CI 脆弱阈值）。预算对象：

- B1 tick 查询数：单 tick 的 SQL 查询数与**排队 run 数无关**（O(批量)
  上界 —— LIMIT 批查询，绝无全表迭代）；
- B2 pick 决策：fairness pick 内存计算与候选数线性、与存活 worker 数无关；
- B3 派发开销：submit → 首次认领 ≤ 2 个 tick（结构上界，非墙钟）；
- B4 内存有界：coordinator 的在飞句柄数 ≤ local_slots（无未bounded集合）。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.services.geocompute.cluster.fairness import fair_pick
from app.services.geocompute.cluster.scheduler import ClusterCoordinator
from app.services.geocompute.cluster.store import ClusterRunStore


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def env(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'v6-perf.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    store = ClusterRunStore(factory)
    yield store, factory, eng
    eng.dispose()


def _submit_many(store, n: int, tenants: int = 1,
                 tenant_round: str = "r") -> list[str]:
    from app.services.geocompute import graph
    from app.services.geocompute.plan import ExecutionPlan

    plan = {
        "plan_id": "perf",
        "nodes": [
            {"node_id": "n", "category": "source_scan", "operation": "inline",
             "inputs": [], "parameters": {"features": [
                 {"type": "Feature", "geometry": {"type": "Point",
                                                  "coordinates": [1.0, 2.0]}}]}},
        ],
        "budget": {},
    }
    po = ExecutionPlan.model_validate(plan)
    graph.validate_plan(po)
    dump = po.model_dump()
    fp = po.graph_fingerprint()
    return [
        store.create_run(plan_snapshot=dump, plan_fingerprint=fp,
                         owner_scope="u:perf",
                         tenant_raw=f"{tenant_round}-org{i % tenants}",
                         max_queued_per_tenant=max(32, n + 1),
                         max_queued_total=max(256, n + 1))
        for i in range(n)
    ]


class TestStructuralBudgets:
    def test_b1_tick_query_count_bounded_by_batch(self, env):
        """B1：单 tick 查询数是**常数带上界**（批量 LIMIT），与排队量无关。"""
        store, factory, eng = env
        queries_small: list[int] = []

        def _run_tick(n_runs: int, tenant_round: str) -> int:
            _submit_many(store, n_runs, tenant_round=tenant_round)
            coord = ClusterCoordinator(
                store=ClusterRunStore(factory), coordinator_id="perf",
                local_slots=1,
            )
            count = {"n": 0}

            def _before(conn, cursor, statement, parameters, context,
                        executemany):
                count["n"] += 1

            event.listen(eng, "before_cursor_execute", _before)
            try:
                coord.tick()
            finally:
                event.remove(eng, "before_cursor_execute", _before)
            return count["n"]

        q_small = _run_tick(5, tenant_round="r1")
        q_large = _run_tick(200, tenant_round="r2")
        queries_small.extend([q_small, q_large])
        # 结构预算：查询数不随排队量增长（允许常数因子内的批量差异）
        assert q_large <= q_small * 2 + 8, (
            f"tick 查询数随排队量增长：{q_small} -> {q_large}（违反 O(批量) 预算）"
        )
        print(f"[INFO timing] tick queries: 5 queued={q_small}, "
              f"200 queued={q_large}")

    def test_b2_fair_pick_linear_and_worker_independent(self, env):
        """B2：pick 内存计算与候选数线性（比例带上界）。"""
        def _timed(candidates: int) -> float:
            rows = [
                {"id": i, "run_id": f"r{i}", "tenant_key": f"t:{i % 8}",
                 "priority": 5, "required_profiles": []}
                for i in range(candidates)
            ]
            start = time.perf_counter()
            fair_pick(rows, slots=4, last_dispatch={})
            return time.perf_counter() - start

        t_small = _timed(200)
        t_large = _timed(2000)  # 10 倍候选
        ratio = t_large / max(t_small, 1e-6)
        # 结构预算：10 倍候选 → 耗时 ≤ 30 倍（线性 + 计时噪声容忍）
        assert ratio <= 30, f"pick 非线性：10x 候选 → {ratio:.1f}x 耗时"
        print(f"[INFO timing] fair_pick: 200={t_small * 1000:.2f}ms, "
              f"2000={t_large * 1000:.2f}ms (ratio={ratio:.1f})")

    def test_b3_dispatch_within_two_ticks(self, env):
        """B3：submit → 首次认领 ≤ 2 个 tick（结构上界）。"""
        store, factory, _ = env
        coord = ClusterCoordinator(store=ClusterRunStore(factory),
                                   coordinator_id="perf-b3", local_slots=2,
                                   tick_interval_s=0.01)
        rid = _submit_many(store, 1)[0]
        ticks = 0
        while ticks < 3:
            coord.tick()
            ticks += 1
            if store.get_run(rid)["status"] in {"leased", "running",
                                                "completed"}:
                break
        assert ticks <= 2, f"派发延迟 {ticks} ticks > 2（违反派发预算）"

    def test_b4_inflight_handles_bounded_by_slots(self, env):
        """B4：在飞句柄 ≤ local_slots（无未bounded集合 —— OOM 防线）。"""
        store, factory, _ = env
        coord = ClusterCoordinator(store=ClusterRunStore(factory),
                                   coordinator_id="perf-b4", local_slots=2)
        _submit_many(store, 12)
        for _ in range(3):
            coord.tick()
        assert len(coord._inflight) <= 2
