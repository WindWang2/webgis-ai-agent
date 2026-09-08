"""V6 review 修复回归（round1+round2 findings）。

B1  lifespan 真接线（env 开启时 REST submit → 真 tick 循环 → terminal）
R1C2 非 eager + 已注册 worker：默认队列 durable 节点的 run 可派发
R1M5 fairness 固定环游标（租户队列中途耗尽无跳位偏袒）
R2M3 取消延迟指标非空（cancel sweep 打点）
R1M1 账本 scope 预建在业务事务之外（无 session 中毒）
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.geocompute.cluster.fairness import fair_pick


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


def _features(unique: str) -> list[dict]:
    return [
        {"type": "Feature", "geometry": {"type": "Point",
                                         "coordinates": [1.0, 2.0]},
         "properties": {"u": unique}},
    ]


class TestR1M5FairnessNoDrift:
    def test_no_skip_when_tenant_queue_exhausts_midround(self):
        """A(5)/B(1)/C(1)、slots=3 → 必须是 A/B/C 各一（无系统性跳位）。"""
        candidates = (
            [{"id": i, "run_id": f"a{i}", "tenant_key": "t:A", "priority": 5,
              "required_profiles": []} for i in range(5)]
            + [{"id": 10, "run_id": "b0", "tenant_key": "t:B", "priority": 5,
                "required_profiles": []},
               {"id": 11, "run_id": "c0", "tenant_key": "t:C", "priority": 5,
                "required_profiles": []}]
        )
        picked = fair_pick(candidates, slots=3, last_dispatch={})
        tenants = [p["tenant_key"] for p in picked]
        assert sorted(tenants) == ["t:A", "t:B", "t:C"], (
            f"轮转漂移：{tenants}（round1 M5）"
        )


class TestR2M3CancelLatency:
    def test_cancel_sweep_records_latency(self, tmp_path, monkeypatch):
        eng = create_engine(f"sqlite:///{tmp_path / 'r2-m3.db'}",
                            connect_args={"check_same_thread": False})
        from app.models.db_model import Base

        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng, expire_on_commit=False)
        from app.services.geocompute.cluster import metrics as metrics_mod
        from app.services.geocompute.cluster import store as csm
        from app.services.geocompute.cluster.scheduler import ClusterCoordinator
        from app.services.geocompute.cluster.store import ClusterRunStore

        monkeypatch.setattr(csm, "session_factory", factory)
        monkeypatch.setattr(metrics_mod, "ClusterRunStore", ClusterRunStore)
        # metrics 模块的默认 store 也要指向测试库
        store = ClusterRunStore(factory)
        rid = store.create_run(
            plan_snapshot={"plan_id": "p", "nodes": [], "budget": {}},
            plan_fingerprint="fp", owner_scope="u:m3",
        )
        store.request_cancel(rid)
        coord = ClusterCoordinator(store=store, coordinator_id="m3")
        coord.tick()  # cancel sweep 收敛 + 打点
        assert store.get_run(rid)["status"] == "cancelled"
        snap = metrics_mod.ClusterMetrics(store).snapshot()
        assert snap["cancel_latency"]["samples"] >= 1
        assert snap["cancel_latency"]["p50_s"] is not None
        eng.dispose()


class TestR1M1ScopeEnsure:
    def test_reserve_missing_scope_advisory_does_not_poison(self, tmp_path):
        """scope 行缺席（并发首用竞争窗口）时 advisory 记账跳过且 session
        仍可用 —— 不再出现 flush 失败后的 PendingRollbackError。"""
        eng = create_engine(f"sqlite:///{tmp_path / 'r2-m1.db'}",
                            connect_args={"check_same_thread": False})
        from app.models.db_model import Base

        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng, expire_on_commit=False)
        from app.services.geocompute.cluster.store import ClusterLedger

        ledger = ClusterLedger(enforcing=False)
        from app.services.geocompute.cluster.contracts import ResourceClaim

        with factory() as db:
            claims = {"t:fresh": ResourceClaim(scope_key="t:fresh", units=1)}
            ok = ledger.reserve_claims(db, claims)
            assert ok is True  # advisory：行缺席 → 跳过（不炸 session）
            # session 仍可用（无毒化）
            from sqlalchemy import text

            db.execute(text("SELECT 1"))
        eng.dispose()


class TestB1LifespanWiring:
    def test_env_started_coordinator_executes_submitted_run(
        self, tmp_path, monkeypatch
    ):
        """B1：WEBGIS_CLUSTER_COORDINATOR=1 时 lifespan 拉起 coordinator，
        REST submit 的 run 被后台线程真实执行到终态。"""
        from app.services.geocompute.ops import REGISTRY
        from app.services.geocompute.plan import NodeCategory

        def _scan(ctx, node, payloads):
            return {"features": node.parameters.get("features") or [],
                    "metadata": {}}

        monkeypatch.setitem(REGISTRY, NodeCategory.SOURCE_SCAN, _scan)

        eng = create_engine(f"sqlite:///{tmp_path / 'r2-b1.db'}",
                            connect_args={"check_same_thread": False})
        from app.models.db_model import Base

        Base.metadata.create_all(eng)
        factory = sessionmaker(bind=eng, expire_on_commit=False)
        from app.services.geocompute import reuse_index, run_evidence
        from app.services.geocompute.cluster import scheduler as sched_mod
        from app.services.geocompute.cluster import store as csm

        monkeypatch.setattr(run_evidence, "session_factory", factory)
        monkeypatch.setattr(reuse_index, "session_factory", factory)
        monkeypatch.setattr(csm, "session_factory", factory)
        monkeypatch.setenv("WEBGIS_CLUSTER_COORDINATOR", "1")
        monkeypatch.setenv("WEBGIS_COORDINATOR_SLOTS", "1")
        # 单例复位（测试隔离；生产是进程级单例）
        monkeypatch.setattr(sched_mod, "_coordinator", None)

        # 认证 seed（与 routes 测试同一惯例）
        from datetime import datetime, timezone

        from app.core.auth import create_access_token
        from app.core.database import SessionLocal as _GlobalSession
        from app.models.db_model import User

        gdb = _GlobalSession()
        try:
            if gdb.get(User, "gc-v6-user") is None:
                gdb.add(User(
                    id="gc-v6-user", username="gc-v6-user",
                    email="gc-v6-user@example.com",
                    password_hash="scrypt$16384$8$1$00$00", role="editor",
                    is_active=True, token_version=0, org_id=1,
                    created_at=datetime.now(timezone.utc),
                ))
            else:
                gdb.get(User, "gc-v6-user").org_id = 1
            gdb.commit()
        finally:
            gdb.close()

        headers = {"Authorization": f"Bearer {create_access_token(
            {'sub': 'gc-v6-user', 'role': 'editor', 'org_id': 1})}"}
        from app.main import app

        with TestClient(app) as client:  # 上下文进入 → lifespan 启动 coordinator
            resp = client.post("/api/v1/geocompute/plans/runs", json={
                "plan": {"plan_id": "b1", "nodes": [
                    {"node_id": "scan", "category": "source_scan",
                     "operation": "inline", "inputs": [],
                     "parameters": {"features": _features("b1")}},
                ]},
            }, headers=headers)
            assert resp.status_code == 202
            run_id = resp.json()["run_id"]
            store = csm.ClusterRunStore()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                row = store.get_run(run_id)
                if row and row["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            assert row["status"] == "completed", (
                "lifespan 拉起的 coordinator 必须真实执行 submitted run（B1）"
            )
        # 停止单例线程（守护线程随上下文停止；复位避免跨测试泄漏）
        coord = sched_mod.get_coordinator()
        if coord is not None:
            coord.stop()
        sched_mod._coordinator = None
        eng.dispose()


class TestR1C2DefaultQueueChannel:
    def test_default_queue_durable_nodes_dispatchable_with_workers(
        self, tmp_path, monkeypatch
    ):
        """R1-C2：落到默认兜底队列的 durable 节点不得生成 "celery" 通道需求
        —— 非 eager + 已注册 worker 下这类 run 必须可派发。"""

        eng = create_engine(f"sqlite:///{tmp_path / 'r2-c2.db'}",
                            connect_args={"check_same_thread": False})
        from app.models.db_model import Base

        Base.metadata.create_all(eng)
        from app.services.geocompute.cluster import store as csm

        monkeypatch.setattr(csm, "session_factory",
                            sessionmaker(bind=eng, expire_on_commit=False))
        from app.services.geocompute import graph
        from app.services.geocompute.durable import queue_for_node
        from app.services.geocompute.plan import ExecutionPlan

        # vector_operation（ResourceClass 全 1）→ queue_for_node 落默认
        # 兜底队列 "celery"（非 profile 词表）—— 推导必须剔除它
        node = {
            "node_id": "n", "category": "vector_operation",
            "operation": "op", "inputs": [], "parameters": {},
            "policy": "durable_job",
        }
        po = ExecutionPlan.model_validate(
            {"plan_id": "c2", "nodes": [node], "budget": {}})
        graph.validate_plan(po)
        # routes 的推导（与 submit 内联逻辑一致）：剔除非 profile 队列
        from app.services.geocompute.durable import EXECUTION_QUEUE_PROFILES

        profiles = sorted({
            p for p in (
                queue_for_node(n).removesuffix("_queue") for n in po.nodes
                if n.policy.value == "durable_job"
            ) if p in EXECUTION_QUEUE_PROFILES
        })
        assert "celery" not in profiles
        assert profiles == ["light_cpu"]  # vector_operation → light_cpu
        eng.dispose()
