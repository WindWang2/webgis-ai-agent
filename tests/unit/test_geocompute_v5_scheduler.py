"""GeoCompute V5 异构调度（audit 06-geocompute-gaps.md §6.1 steps 1-4）。

覆盖：
1. ``queue_for_node`` 确定性路由（ResourceClass / 类别能力 / locality_hint /
   默认兜底 "celery"）+ task_queues/task_routes 声明一致性；
2. run 控制面 REST（``POST /plans/runs/{id}/cancel``）：401 / 404（他人与
   未知，无存在性泄漏）/ 200 协作取消自己的在飞 run；
3. durable 节点跨进程 checkpoint 复用索引：完成即记录、命中跳过派发、
   上游指纹陈旧拒绝、ref 失效拒绝、每 owner LRU ≤64；
4. run 终态证据快照：落库 + 进程「重启」后 get_run 回放（owner 校验）+
   有界 ≤16KB；
5. eager（无 Redis）诚实披露：``backend_variant="in_process_eager"``。

worker simulation（与 test_geocompute_durable.py 同一诚实声明）：Celery
eager + 临时 SQLite；不覆盖跨进程 broker 投递。
"""

from __future__ import annotations

import contextlib
import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    ExecutionRun,
    ExecutionRunStatus,
    GeoExecutionEngine,
    NodeCategory,
    NodeEvidence,
    ResourceClass,
    ops,
)
from app.services.geocompute.durable import (
    DEFAULT_QUEUE,
    EXECUTION_QUEUE_PROFILES,
    queue_for_node,
    queue_name_for_profile,
)
from app.services.geocompute.errors import NodeExecutionError
from app.services.geocompute.executor import engine, owner_scope_for

client = TestClient(app)


def _fc(n: int = 4) -> list[dict]:
    return [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [116.0 + i * 0.01, 39.0]},
            "properties": {"kind": "a" if i % 2 == 0 else "b", "v": i},
        }
        for i in range(n)
    ]


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture
def v5_env(tmp_path, monkeypatch):
    """jobs 子系统 + V5 索引/快照全部指向临时 SQLite（与 durable 测试同构）。"""
    eng = create_engine(f"sqlite:///{tmp_path / 'geocompute-v5.db'}")
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    Sess = sessionmaker(bind=eng)

    @contextlib.contextmanager
    def fake_db_session():
        db = Sess()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    import app.services.jobs.submit as submit_mod
    import app.services.jobs.worker as worker_mod
    from app.services.geocompute import durable, reuse_index, run_evidence
    from app.services.jobs.store import DurableJobStore

    monkeypatch.setattr(submit_mod, "db_session", fake_db_session)
    monkeypatch.setattr(worker_mod, "db_session", fake_db_session, raising=False)
    if hasattr(worker_mod, "_default_session_factory"):
        monkeypatch.setattr(
            worker_mod, "_default_session_factory", lambda: fake_db_session()
        )
    monkeypatch.setattr(durable, "session_factory", lambda: fake_db_session())
    monkeypatch.setattr(reuse_index, "session_factory", lambda: fake_db_session())
    monkeypatch.setattr(run_evidence, "session_factory", lambda: fake_db_session())
    return {"store": DurableJobStore, "db": fake_db_session}


@pytest.fixture()
def scan_stub(monkeypatch):
    """SOURCE_SCAN 测试桩（parameters._sleep / _boom 驱动，同 v4 惯例）。"""

    def _scan(ctx, node, payloads):
        params = node.parameters
        if params.get("_sleep"):
            time.sleep(params["_sleep"])
        if params.get("_boom"):
            raise NodeExecutionError(
                str(params["_boom"]), retry_safe=False, node_id=node.node_id
            )
        feats = params.get("features") or _fc(2)
        return {"features": feats, "metadata": {"rows": len(feats)}}

    monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, _scan)


def _node(node_id: str, category: NodeCategory = NodeCategory.FILTER, **kw) -> ExecutionNode:
    params = kw.pop("parameters", None) or {
        "predicate": {"op": "eq", "field": "kind", "value": "a"},
        "features": _fc(4),
    }
    return ExecutionNode(node_id=node_id, category=category, parameters=params, **kw)


def _durable_node(node_id: str = "dn1", **kw) -> ExecutionNode:
    return ExecutionNode(
        node_id=node_id,
        category=NodeCategory.FILTER,
        policy=ExecutionPolicyKind.DURABLE_JOB,
        parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": _fc(6),
        },
        **kw,
    )


# --------------------------------------------------- 1. queue_for_node 路由


class TestQueueForNode:
    def test_category_capability_profiles(self):
        assert queue_for_node(_node("r", NodeCategory.RASTER_OPERATION)) == "raster_queue"
        assert queue_for_node(
            _node("rw", NodeCategory.RASTER_WINDOW_OPERATION)
        ) == "raster_queue"
        assert queue_for_node(_node("i", NodeCategory.INTERPOLATION)) == "heavy_cpu_queue"
        assert queue_for_node(_node("sj", NodeCategory.SPATIAL_JOIN)) == "heavy_cpu_queue"
        assert queue_for_node(_node("n", NodeCategory.NETWORK_OPERATION)) == "network_queue"
        assert queue_for_node(_node("m", NodeCategory.MATERIALIZE, inputs=["x"])) == (
            "external_io_queue"
        )

    def test_resource_class_thresholds(self):
        mem = _node("m", parameters={"predicate": {"op": "eq", "field": "k", "value": "a"},
                                     "features": _fc(3)},
                    resource_class=ResourceClass(memory=4))
        assert queue_for_node(mem) == "high_memory_queue"
        cpu = _node("c", parameters={"predicate": {"op": "eq", "field": "k", "value": "a"},
                                     "features": _fc(3)},
                    resource_class=ResourceClass(cpu=5))
        assert queue_for_node(cpu) == "heavy_cpu_queue"
        io = _node("i", parameters={"predicate": {"op": "eq", "field": "k", "value": "a"},
                                    "features": _fc(3)},
                   resource_class=ResourceClass(io=4))
        assert queue_for_node(io) == "external_io_queue"

    def test_light_vector_categories_default_to_light_cpu(self):
        for cat in (NodeCategory.QUERY, NodeCategory.FILTER, NodeCategory.AGGREGATE,
                    NodeCategory.VECTOR_OPERATION):
            assert queue_for_node(_node("n", cat)) == "light_cpu_queue"

    def test_unknown_category_falls_back_to_default_queue(self):
        node = _node("d", NodeCategory.DECISION_OPERATION)
        assert queue_for_node(node) == DEFAULT_QUEUE == "celery"

    def test_locality_hint_overrides_category_and_resource_class(self):
        hinted = _node("h", parameters={"predicate": {"op": "eq", "field": "k", "value": "a"},
                                        "features": _fc(3)},
                       locality_hint="raster", resource_class=ResourceClass(cpu=5))
        assert queue_for_node(hinted) == "raster_queue"
        raster = _node("r", NodeCategory.RASTER_OPERATION, locality_hint="light_cpu")
        assert queue_for_node(raster) == "light_cpu_queue"
        # "{profile}_queue" 拼写容错；未知 hint 被忽略（落回类别推导）。
        assert queue_for_node(_node("h2", locality_hint="heavy_cpu_queue")) == (
            "heavy_cpu_queue"
        )
        assert queue_for_node(_node("h3", locality_hint="moon")) == "light_cpu_queue"

    def test_deterministic_and_fingerprint_stable(self):
        node = _node("d", NodeCategory.SPATIAL_JOIN, locality_hint=None)
        again = node.model_copy(deep=True)
        assert queue_for_node(node) == queue_for_node(again) == "heavy_cpu_queue"
        hinted = node.model_copy(update={"locality_hint": "raster",
                                         "resource_class": ResourceClass(memory=5)})
        # 路由提示不参与语义指纹（换队列不换节点身份 → 幂等键稳定）。
        assert hinted.semantic_fingerprint() == node.semantic_fingerprint()

    def test_profile_vocabulary_matches_queue_names(self):
        assert set(EXECUTION_QUEUE_PROFILES) == {
            "light_cpu", "heavy_cpu", "high_memory",
            "raster", "network", "external_io",
        }
        for profile in EXECUTION_QUEUE_PROFILES:
            assert queue_name_for_profile(profile) == f"{profile}_queue"
        assert queue_name_for_profile("bogus") == DEFAULT_QUEUE


class TestTaskQueueConfig:
    def test_task_queues_declared_for_every_profile_plus_default(self):
        from app.services.task_queue import GEOCOMPUTE_PROFILE_QUEUES, celery_app

        names = [q.name for q in celery_app.conf.task_queues]
        assert names[0] == DEFAULT_QUEUE
        assert set(GEOCOMPUTE_PROFILE_QUEUES) <= set(names)
        assert len(names) == len(set(names))

    def test_task_routes_wellformed_and_cover_every_queue(self):
        from app.services.task_queue import (
            GEOCOMPUTE_PROFILE_QUEUES,
            celery_app,
        )

        routes = celery_app.conf.task_routes
        assert isinstance(routes, dict)
        targets = set()
        for pattern, route in routes.items():
            assert isinstance(pattern, str) and pattern
            assert isinstance(route, dict)
            queue = route.get("queue")
            assert isinstance(queue, str) and queue
            targets.add(queue)
        # 每个 profile 队列 + 默认队列都被 routes 覆盖（routes ↔ queues 对齐）。
        assert set(GEOCOMPUTE_PROFILE_QUEUES) <= targets
        assert DEFAULT_QUEUE in targets
        # geocompute 任务名的无名投递（手工 retry 的 send_task）确定落默认队列。
        assert routes["app.services.geocompute.tasks.run_geocompute_node"]["queue"] == (
            DEFAULT_QUEUE
        )


# ------------------------------------------------------- 2. cancel REST 面孔


def _auth(user_id: str) -> dict[str, str]:
    from app.core.auth import create_access_token

    return {
        "Authorization": f"Bearer {create_access_token({'sub': user_id, 'role': 'editor'})}"
    }


class TestCancelRest:
    def test_cancel_requires_auth(self):
        resp = client.post("/api/v1/geocompute/plans/runs/gexec-abc/cancel")
        assert resp.status_code == 401, resp.text

    def test_cancel_unknown_run_is_404(self):
        resp = client.post("/api/v1/geocompute/plans/runs/gexec-nope/cancel",
                           headers=_auth("v5-canceller"))
        assert resp.status_code == 404
        assert resp.json()["detail"] == {"code": "RUN_NOT_FOUND"}

    def test_cancel_own_running_run_cooperatively(self, monkeypatch):
        """在飞 run：他人 404（存在性不泄漏），本人 200 且节点经 checkpoint 收敛。"""
        seen: dict[str, str] = {}
        started = threading.Event()

        def _slow_scan(ctx, node, payloads):
            seen["run_id"] = ctx.run_id
            started.set()
            from app.lib.cancellation import checkpoint

            for _ in range(250):  # ≤5s 上界；正常由取消提前打断
                time.sleep(0.02)
                checkpoint()
            return {"features": _fc(2)}

        monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, _slow_scan)
        plan = ExecutionPlan(plan_id="v5-cancel",
                             nodes=[_node("slow", NodeCategory.SOURCE_SCAN,
                                          parameters={"features": _fc(2)})])
        holder: dict[str, ExecutionRun] = {}

        def _execute():
            holder["run"] = engine.execute_plan(
                plan, caller={"user_id": "v5-cancel-owner"}
            )

        worker = threading.Thread(target=_execute, daemon=True)
        worker.start()
        try:
            assert started.wait(5.0), "fake node never started"
            run_id = seen["run_id"]

            foreign = client.post(
                f"/api/v1/geocompute/plans/runs/{run_id}/cancel",
                headers=_auth("v5-cancel-other"))
            assert foreign.status_code == 404  # 他人 run：与未知同形 404

            ok = client.post(f"/api/v1/geocompute/plans/runs/{run_id}/cancel",
                             headers=_auth("v5-cancel-owner"))
            assert ok.status_code == 200, ok.text
            body = ok.json()
            assert body["cancelled"] is True
            assert body["run_id"] == run_id
        finally:
            worker.join(timeout=10.0)
        assert not worker.is_alive(), "cancelled run must not leak threads"
        run = holder["run"]
        assert run.status is ExecutionRunStatus.CANCELLED
        assert run.evidence["slow"].status == "cancelled"

        got = client.get(f"/api/v1/geocompute/runs/{run_id}",
                         headers=_auth("v5-cancel-owner"))
        assert got.status_code == 200
        assert got.json()["status"] == "cancelled"

    def test_cancel_terminal_run_is_idempotent_noop(self, scan_stub):
        plan = ExecutionPlan(plan_id="v5-cancel-done",
                             nodes=[_node("quick", NodeCategory.SOURCE_SCAN,
                                          parameters={"features": _fc(2)})])
        run = engine.execute_plan(plan, caller={"user_id": "v5-cancel-done-owner"})
        assert run.status is ExecutionRunStatus.COMPLETED
        resp = client.post(f"/api/v1/geocompute/plans/runs/{run.run_id}/cancel",
                           headers=_auth("v5-cancel-done-owner"))
        assert resp.status_code == 200
        body = resp.json()
        assert body["cancelled"] is False
        assert body["status"] == "completed"


# ------------------------------------------- 3. 跨进程 checkpoint 复用索引


class TestDurableReuseIndex:
    def _plan(self) -> ExecutionPlan:
        return ExecutionPlan(plan_id="v5-reuse", nodes=[_durable_node()])

    def test_completion_records_and_second_run_skips_dispatch(self, v5_env, monkeypatch):
        import app.services.geocompute.durable as durable_mod
        from app.services.geocompute import reuse_index

        calls = {"n": 0}
        original = durable_mod.dispatch_node

        def counting(node, **kw):
            calls["n"] += 1
            return original(node, **kw)

        monkeypatch.setattr(durable_mod, "dispatch_node", counting)

        first = GeoExecutionEngine(max_workers=1).execute_plan(
            self._plan(), session_id="v5-reuse-sess")
        assert first.status is ExecutionRunStatus.COMPLETED
        assert calls["n"] == 1
        node_fp = _durable_node().semantic_fingerprint()
        owner = owner_scope_for(None, "v5-reuse-sess")
        entry = reuse_index.find_result(owner, node_fp)
        assert entry is not None
        assert entry["result_ref"] == first.evidence["dn1"].output_ref
        assert entry["session_id"] == "v5-reuse-sess"

        # 第二个**全新引擎**（模拟另一进程/重启后）：索引命中 → 派发被跳过。
        second = GeoExecutionEngine(max_workers=1).execute_plan(
            self._plan(), session_id="v5-reuse-sess")
        assert second.status is ExecutionRunStatus.COMPLETED
        ev = second.evidence["dn1"]
        assert ev.status == "reused"
        assert ev.reuse_source == "cross_process_index"
        assert ev.checkpoint_verified is True
        assert calls["n"] == 1, "reuse hit must skip dispatch entirely"
        assert ev.rows_emitted == first.evidence["dn1"].rows_emitted
        # 单一 job 真相：复用不产生第二个 job 行。
        with v5_env["db"]() as db:
            from app.models.db_model import AnalysisTask

            assert db.query(AnalysisTask).count() == 1

    def test_reuse_hit_charges_governor_like_execution(self, v5_env, monkeypatch):
        """DIST（round1）：durable 复用也是资源消费 —— 命中路径必须与执行
        完成路径同一口径 charge governor（行数/字节/节点数）。"""
        from app.services.geocompute.budgets import ResourceGovernor

        gov = ResourceGovernor()
        charged: list[dict] = []
        real_charge = gov.charge

        def spy_charge(path, **kw):
            charged.append(kw)
            return real_charge(path, **kw)

        monkeypatch.setattr(gov, "charge", spy_charge)

        first = GeoExecutionEngine(max_workers=1).execute_plan(
            self._plan(), session_id="v5-reuse-charge-sess", governor=gov)
        assert first.status is ExecutionRunStatus.COMPLETED
        exec_rows = first.evidence["dn1"].rows_emitted
        assert exec_rows, "执行完成路径必须产出行数"
        assert any(
            c.get("rows") == exec_rows and c.get("nodes") == 1 for c in charged
        ), f"执行完成路径必须 charge: {charged}"
        charged.clear()

        # 第二个全新引擎：索引命中（reused）→ 同样 charge。
        second = GeoExecutionEngine(max_workers=1).execute_plan(
            self._plan(), session_id="v5-reuse-charge-sess", governor=gov)
        assert second.status is ExecutionRunStatus.COMPLETED
        assert second.evidence["dn1"].status == "reused"
        assert any(
            c.get("rows") == second.evidence["dn1"].rows_emitted
            and c.get("nodes") == 1
            for c in charged
        ), f"复用命中必须与执行同口径 charge: {charged}"

    def test_two_node_plan_reuses_with_upstream_validation(self, v5_env, scan_stub):
        from app.services.geocompute import reuse_index

        scan = _node("scan", NodeCategory.SOURCE_SCAN, parameters={"features": _fc(6)})
        dfilter = ExecutionNode(
            node_id="df",
            category=NodeCategory.FILTER,
            policy=ExecutionPolicyKind.DURABLE_JOB,
            inputs=["scan"],
            parameters={
                "predicate": {"op": "eq", "field": "kind", "value": "a"},
                "features": _fc(6),
            },
        )
        plan = ExecutionPlan(plan_id="v5-reuse-chain", nodes=[scan, dfilter])
        first = GeoExecutionEngine(max_workers=1).execute_plan(
            plan, session_id="v5-chain-sess")
        assert [first.evidence[n].status for n in ("scan", "df")] == [
            "completed", "completed"]
        owner = owner_scope_for(None, "v5-chain-sess")
        entry = reuse_index.find_result(owner, dfilter.semantic_fingerprint())
        assert entry is not None and "scan" in entry["upstream_fingerprints"]

        second = GeoExecutionEngine(max_workers=1).execute_plan(
            plan, session_id="v5-chain-sess")
        assert second.evidence["df"].status == "reused"
        assert second.evidence["df"].checkpoint_verified is True

    def test_stale_upstream_fingerprint_blocks_reuse(self, v5_env, scan_stub,
                                                     monkeypatch):
        import app.services.geocompute.durable as durable_mod
        from app.services.geocompute import reuse_index

        scan = _node("scan", NodeCategory.SOURCE_SCAN, parameters={"features": _fc(6)})
        dfilter = ExecutionNode(
            node_id="df",
            category=NodeCategory.FILTER,
            policy=ExecutionPolicyKind.DURABLE_JOB,
            inputs=["scan"],
            parameters={
                "predicate": {"op": "eq", "field": "kind", "value": "a"},
                "features": _fc(6),
            },
        )
        plan = ExecutionPlan(plan_id="v5-reuse-stale", nodes=[scan, dfilter])
        eng = GeoExecutionEngine(max_workers=1)
        first = eng.execute_plan(plan, session_id="v5-stale-sess")
        assert first.evidence["df"].status == "completed"

        calls = {"n": 0}
        original = durable_mod.dispatch_node

        def counting(node, **kw):
            calls["n"] += 1
            return original(node, **kw)

        monkeypatch.setattr(durable_mod, "dispatch_node", counting)
        # 篡改索引里的上游输出指纹 = 「上游重算后内容已变化」。
        owner = owner_scope_for(None, "v5-stale-sess")
        assert reuse_index.record_result(
            owner_scope=owner,
            node_fingerprint=dfilter.semantic_fingerprint(),
            result_ref=first.evidence["df"].output_ref,
            session_id="v5-stale-sess",
            upstream_fingerprints={"scan": "fp:stale0000000000"},
        )
        second = GeoExecutionEngine(max_workers=1).execute_plan(
            plan, session_id="v5-stale-sess")
        ev = second.evidence["df"]
        assert ev.status == "completed", "honest recompute"
        assert calls["n"] == 1, "stale entry must dispatch, not reuse"
        assert (ev.reuse_skipped_reason or "").startswith("upstream_changed:")
        assert "scan" in (ev.reuse_skipped_reason or "")
        assert ev.checkpoint_verified is False

    def test_unresolvable_result_ref_blocks_reuse_and_cleans_entry(
        self, v5_env, monkeypatch
    ):
        """索引条目的 ref 探测未命中（ref 失效/被会话回收）→ 诚实重算 + 死条目清除。"""
        import app.services.geocompute.durable as durable_mod
        from app.services.geocompute import reuse_index

        plan = self._plan()
        first = GeoExecutionEngine(max_workers=1).execute_plan(
            plan, session_id="v5-deadref-sess")
        assert first.evidence["dn1"].status == "completed"

        calls = {"n": 0}
        original = durable_mod.dispatch_node

        def counting(node, **kw):
            calls["n"] += 1
            return original(node, **kw)

        monkeypatch.setattr(durable_mod, "dispatch_node", counting)
        deletes = {"n": 0}
        real_delete = reuse_index.delete_result

        def spy_delete(owner_scope, node_fingerprint):
            deletes["n"] += 1
            return real_delete(owner_scope, node_fingerprint)

        monkeypatch.setattr(reuse_index, "delete_result", spy_delete)
        # 把索引条目的 ref 篡改为不可解析值（等价于 ref 已被会话回收）。
        owner = owner_scope_for(None, "v5-deadref-sess")
        node_fp = _durable_node().semantic_fingerprint()
        assert reuse_index.record_result(
            owner_scope=owner, node_fingerprint=node_fp,
            result_ref="ref:gone-000000", session_id="v5-deadref-sess",
            upstream_fingerprints={},
        )

        second = GeoExecutionEngine(max_workers=1).execute_plan(
            plan, session_id="v5-deadref-sess")
        ev = second.evidence["dn1"]
        assert ev.status == "completed"
        assert calls["n"] == 1, "dead ref must dispatch, not reuse"
        assert ev.reuse_skipped_reason == "result_ref_unresolvable"
        assert deletes["n"] == 1, "dead index entry must be pruned"
        # 索引卫生：重跑后写回的是**可解析**的新 ref。
        assert reuse_index.find_result(owner, node_fp)["result_ref"] == (
            ev.output_ref
        )

    def test_lru_cap_prunes_to_64_per_owner(self, v5_env):
        from app.services.geocompute import reuse_index

        owner = "u:lru-cap-owner"
        for i in range(70):
            assert reuse_index.record_result(
                owner_scope=owner,
                node_fingerprint=f"fp{i:04d}",
                result_ref=f"ref:{i}",
                session_id="v5-lru-sess",
                upstream_fingerprints={},
            )
        with v5_env["db"]() as db:
            from app.models.db_model import GeoComputeNodeResult

            rows = (
                db.query(GeoComputeNodeResult)
                .filter(GeoComputeNodeResult.owner_scope == owner)
                .all()
            )
            assert len(rows) == reuse_index.MAX_RESULTS_PER_OWNER == 64
            fps = {r.node_fingerprint for r in rows}
            assert "fp0000" not in fps, "oldest entry pruned"
            assert "fp0069" in fps, "newest entry kept"
            # 重写即刷新：同 (owner, fp) 再写不产生第二行。
            assert reuse_index.record_result(
                owner_scope=owner,
                node_fingerprint="fp0069",
                result_ref="ref:69b",
                session_id="v5-lru-sess",
                upstream_fingerprints={},
            )
            db.expire_all()  # 丢弃身份映射缓存，读刚被外部覆写的行
            same = (
                db.query(GeoComputeNodeResult)
                .filter(GeoComputeNodeResult.node_fingerprint == "fp0069")
                .all()
            )
            assert len(same) == 1 and same[0].result_ref == "ref:69b"

    def test_owner_scope_isolation(self, v5_env):
        from app.services.geocompute import reuse_index

        node_fp = _durable_node().semantic_fingerprint()
        assert reuse_index.record_result(
            owner_scope="u:alice", node_fingerprint=node_fp,
            result_ref="ref:a", session_id="sess-a", upstream_fingerprints={},
        )
        assert reuse_index.find_result("u:bob", node_fp) is None
        assert reuse_index.find_result("u:alice", node_fp)["result_ref"] == "ref:a"


# ------------------------------------------------ 4. run 终态证据快照 + 回放


class TestRunEvidenceSnapshot:
    def test_terminal_snapshot_persisted_and_owner_checked(self, v5_env, scan_stub):
        from app.services.geocompute import run_evidence

        plan = ExecutionPlan(plan_id="v5-snap",
                             nodes=[_node("n1", NodeCategory.SOURCE_SCAN,
                                          parameters={"features": _fc(3)})])
        owner = {"user_id": "v5-snap-user"}
        run = engine.execute_plan(plan, caller=owner)
        assert run.status is ExecutionRunStatus.COMPLETED
        scope = owner_scope_for(owner)

        snap = run_evidence.load_snapshot(run.run_id, owner_scope=scope)
        assert snap is not None
        assert snap.source == "snapshot"
        assert snap.status is ExecutionRunStatus.COMPLETED
        assert snap.evidence["n1"].status == "completed"
        # 他人 owner 域：一律 None（与 REST 404 同形，无存在性泄漏）。
        assert run_evidence.load_snapshot(
            run.run_id, owner_scope=owner_scope_for({"user_id": "someone-else"})
        ) is None

        # 「重启」：全新引擎内存为空 → 快照回放（工具面的无 owner 语义 =
        # 进程内可信路径，同样能回放；REST 恒带 owner 域）。
        fresh = GeoExecutionEngine()
        replayed_trusted = fresh.get_run(run.run_id)
        assert replayed_trusted is not None
        assert replayed_trusted.source == "snapshot"
        replayed = fresh.get_run(run.run_id, owner_scope=scope)
        assert replayed is not None and replayed.source == "snapshot"

    def test_get_run_fallback_after_restart_via_rest(self, v5_env, scan_stub):
        plan = ExecutionPlan(plan_id="v5-snap-rest",
                             nodes=[_node("n1", NodeCategory.SOURCE_SCAN,
                                          parameters={"features": _fc(3)})])
        owner = {"user_id": "v5-snap-rest-user"}
        run = engine.execute_plan(plan, caller=owner)
        # 模拟进程重启：清空进程内注册表（LRU 逐出同形）。
        with engine._run_lock:
            engine._runs.pop(run.run_id, None)
            engine._run_outputs.pop(run.run_id, None)
            engine._run_owners.pop(run.run_id, None)

        got = client.get(f"/api/v1/geocompute/runs/{run.run_id}",
                         headers=_auth("v5-snap-rest-user"))
        assert got.status_code == 200, got.text
        body = got.json()
        assert body["source"] == "snapshot"
        assert body["status"] == "completed"
        assert body["evidence"]["n1"]["status"] == "completed"

        foreign = client.get(f"/api/v1/geocompute/runs/{run.run_id}",
                             headers=_auth("v5-someone-else"))
        assert foreign.status_code == 404

    def test_snapshot_is_bounded_under_16kb(self, v5_env):
        from app.services.geocompute.normalization import canonical_dumps
        from app.services.geocompute.run_evidence import (
            MAX_SNAPSHOT_BYTES,
            build_snapshot,
        )

        big = ExecutionRun(
            run_id="gexec-big", plan_id="p", plan_fingerprint="fp",
            status=ExecutionRunStatus.COMPLETED,
            evidence={
                f"n{i}": NodeEvidence(
                    status="completed", rows_emitted=i,
                    error_message="e" * 300,
                    output_summary={"pad": "x" * 300},
                )
                for i in range(200)
            },
        )
        snap = build_snapshot(big)
        size = len(canonical_dumps(snap).encode("utf-8"))
        assert size <= MAX_SNAPSHOT_BYTES <= 16 * 1024
        assert snap["evidence_truncated"] is True
        assert snap["evidence_total"] == 200

        small = ExecutionRun(
            run_id="gexec-small", plan_id="p", plan_fingerprint="fp",
            status=ExecutionRunStatus.COMPLETED,
            evidence={"n1": NodeEvidence(status="completed", rows_emitted=3)},
        )
        small_snap = build_snapshot(small)
        assert small_snap.get("evidence_truncated") is None
        assert small_snap["evidence"]["n1"]["status"] == "completed"
        # 回放往返：快照 → ExecutionRun（source 标注 snapshot）。
        from app.services.geocompute import run_evidence

        with v5_env["db"]() as db:
            from app.models.db_model import GeoComputeRunEvidence

            db.add(GeoComputeRunEvidence(
                run_id="gexec-small", org_id="1", owner_scope="u:rt",
                status="completed", snapshot=small_snap,
            ))
        replay = run_evidence.load_snapshot("gexec-small", owner_scope="u:rt")
        assert replay is not None and replay.evidence["n1"].rows_emitted == 3


# ------------------------------------------------------ 5. eager 诚实披露


class TestEagerHonesty:
    def test_durable_node_annotates_in_process_eager(self, v5_env):
        run = GeoExecutionEngine(max_workers=1).execute_plan(
            self._plan(), session_id="v5-eager-sess")
        assert run.status is ExecutionRunStatus.COMPLETED
        ev = run.evidence["dn1"]
        assert ev.status == "completed"
        assert ev.backend_variant == "in_process_eager"

    def _plan(self) -> ExecutionPlan:
        return ExecutionPlan(plan_id="v5-eager", nodes=[_durable_node()])

    def test_in_process_node_has_no_backend_variant(self, v5_env, scan_stub):
        plan = ExecutionPlan(plan_id="v5-eager-ip",
                             nodes=[_node("n1", NodeCategory.SOURCE_SCAN,
                                          parameters={"features": _fc(3)})])
        run = GeoExecutionEngine(max_workers=1).execute_plan(plan)
        assert run.evidence["n1"].backend_variant is None

    def test_broker_mode_dispatch_carries_queue_but_no_eager_marker(
        self, v5_env, monkeypatch,
    ):
        """非 eager（真实 broker 语义）：派发带 profile 队列，无 eager 标注。"""
        from app.services.task_queue import celery_app
        from app.services.geocompute import durable

        node = _durable_node()
        with monkeypatch.context() as m:
            m.setitem(celery_app.conf, "task_always_eager", False)
            ret = durable.dispatch_node(
                node, session_id="v5-broker-sess",
                plan_fingerprint="fp", deadline_s=None,
            )
        assert ret["queue"] == queue_for_node(node) == "light_cpu_queue"
        assert "backend_variant" not in ret
