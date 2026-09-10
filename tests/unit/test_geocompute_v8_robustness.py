"""GeoCompute V8 — runtime robustness（Phase F）：毒任务隔离 + 投机副本。

覆盖：quarantine 登记/阈值/冷却窗/惰性解封/owner 域隔离/fail-open；
executor durable 路径的隔离快失败；投机副本的先到先得收敛（primary
优先、败者取消、双输类型化失败）。
"""

from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.geocompute.cluster.quarantine import (
    TaskQuarantine,
    reset_quarantine_for_tests,
)


@pytest.fixture(autouse=True)
def _reset_quarantine():
    reset_quarantine_for_tests()
    yield
    reset_quarantine_for_tests()


@pytest.fixture()
def qenv(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'v8-q.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)
    yield factory
    eng.dispose()


class TestQuarantineCore:
    def test_counts_then_quarantines(self, qenv):
        q = TaskQuarantine(factory=qenv, threshold=3, cooldown_s=1800)
        fp = "abc123def4567890"
        assert q.is_quarantined("u:a", fp) is False
        assert q.record_failure("u:a", fp, error_code="INVALID_DATA") is False
        assert q.record_failure("u:a", fp, error_code="INVALID_DATA") is False
        assert q.record_failure("u:a", fp, error_code="INVALID_DATA") is True
        assert q.is_quarantined("u:a", fp) is True

    def test_owner_domain_isolation(self, qenv):
        """毒是 per-owner 事实：owner A 被隔离，owner B 同指纹不受影响。"""
        q = TaskQuarantine(factory=qenv, threshold=2, cooldown_s=600)
        fp = "abc123def4567890"
        q.record_failure("u:a", fp)
        q.record_failure("u:a", fp)
        assert q.is_quarantined("u:a", fp) is True
        assert q.is_quarantined("u:b", fp) is False

    def test_lazy_expiry(self, qenv):
        """冷却窗过期 → 惰性解封（无后台清扫依赖）。"""
        q = TaskQuarantine(factory=qenv, threshold=2, cooldown_s=0.05)
        fp = "abc123def4567890"
        q.record_failure("u:a", fp)
        assert q.record_failure("u:a", fp) is True
        assert q.is_quarantined("u:a", fp) is True
        time.sleep(0.08)
        assert q.is_quarantined("u:a", fp) is False

    def test_zero_cooldown_records_only(self, qenv):
        q = TaskQuarantine(factory=qenv, threshold=2, cooldown_s=0)
        fp = "abc123def4567890"
        q.record_failure("u:a", fp)
        assert q.record_failure("u:a", fp) is False
        assert q.is_quarantined("u:a", fp) is False

    def test_snapshot_pseudonymized(self, qenv):
        q = TaskQuarantine(factory=qenv, threshold=2, cooldown_s=600)
        q.record_failure("u:verylongowneridentifier12345", "abcdef0123456789")
        q.record_failure("u:verylongowneridentifier12345", "abcdef0123456789")
        snap = q.snapshot()
        assert len(snap) == 1
        assert snap[0]["active"] is True
        assert len(snap[0]["owner_scope"]) <= 17  # 16 + "…"
        assert len(snap[0]["fingerprint"]) == 16


# ═══════════════════════ executor 接线（隔离快失败 / 投机副本）══════════════════════


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "result_backend", "cache+memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture()
def job_env(tmp_path, monkeypatch):
    """jobs/集群/复用/事件全部指向同一临时 SQLite（与 durable 测试同惯例）。"""
    import contextlib

    eng = create_engine(f"sqlite:///{tmp_path / 'v8-rob.db'}",
                        connect_args={"check_same_thread": False})
    from app.models.db_model import Base

    Base.metadata.create_all(eng)
    factory = sessionmaker(bind=eng, expire_on_commit=False)

    @contextlib.contextmanager
    def fake_db_session():
        db = factory()
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
    from app.services.geocompute.cluster import store as csm
    from app.services.geocompute.ops import REGISTRY
    from app.services.geocompute.plan import NodeCategory

    def _scan(ctx, node, payloads):
        return {"features": node.parameters.get("features") or [],
                "metadata": {"via": "stub"}}

    monkeypatch.setitem(REGISTRY, NodeCategory.SOURCE_SCAN, _scan)

    monkeypatch.setattr(submit_mod, "db_session", fake_db_session)
    monkeypatch.setattr(worker_mod, "db_session", fake_db_session,
                        raising=False)
    if hasattr(worker_mod, "_default_session_factory"):
        monkeypatch.setattr(worker_mod, "_default_session_factory",
                            lambda: fake_db_session())
    monkeypatch.setattr(durable, "session_factory", lambda: fake_db_session())
    monkeypatch.setattr(reuse_index, "session_factory", factory)
    monkeypatch.setattr(run_evidence, "session_factory", factory)
    monkeypatch.setattr(csm, "session_factory", factory)
    yield factory
    eng.dispose()


def run_failed(engine, plan, session_id, owner):
    from app.services.geocompute.plan import ExecutionRunStatus

    run = engine.execute_plan(plan, session_id=session_id,
                              owner_scope_override=owner)
    return run.status is ExecutionRunStatus.FAILED


def _plan_boom_filter(unique: str) -> dict:
    return {
        "plan_id": "v8-poison",
        "nodes": [
            {"node_id": "scan", "category": "source_scan",
             "operation": "inline", "inputs": [],
             "parameters": {"features": [
                 {"type": "Feature", "geometry": {"type": "Point",
                                                  "coordinates": [1.0, 1.0]},
                  "properties": {"kind": unique}}],
             }},
            {"node_id": "filt", "category": "filter", "operation": "eq",
             "inputs": ["scan"], "policy": "durable_job",
             "parameters": {"predicate": {"op": "boom", "field": "kind",
                                          "value": unique}},
             "retry": {"max_attempts": 2}},
        ],
        "budget": {"max_rows": 100000},
    }


class TestExecutorQuarantine:
    def test_repeated_poison_fails_fast(self, job_env, monkeypatch):
        """同 owner 同指纹的毒节点：第一次 run 正常失败计数，第二次 run
        直接 POISON_QUARANTINED 快失败（不派发 job 行）。"""
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import ExecutionPlan, ExecutionRunStatus
        from app.services.geocompute import graph
        from app.services.geocompute.cluster.quarantine import (
            TaskQuarantine,
            reset_quarantine_for_tests,
        )

        q = TaskQuarantine(factory=job_env, threshold=2, cooldown_s=600)
        monkeypatch.setattr(
            "app.services.geocompute.cluster.quarantine._default_quarantine", q)

        plan = ExecutionPlan.model_validate(_plan_boom_filter("pz1"))
        graph.validate_plan(plan)
        engine = GeoExecutionEngine()

        run1 = engine.execute_plan(plan, session_id="s-q1",
                                   owner_scope_override="u:poison")
        assert run1.status is ExecutionRunStatus.FAILED
        assert run1.evidence["filt"].error_code == "NODE_FAILED"

        # 第二次 run：计数达标 → 隔离快失败
        run2 = engine.execute_plan(plan, session_id="s-q2",
                                   owner_scope_override="u:poison")
        assert run2.status is ExecutionRunStatus.FAILED
        assert run2.evidence["filt"].error_code in {"NODE_FAILED",
                                                    "POISON_QUARANTINED"}
        # 第三次 run：必然快失败（计数已 ≥2 且第二次至少再 +1）
        run3 = engine.execute_plan(plan, session_id="s-q3",
                                   owner_scope_override="u:poison")
        assert run3.status is ExecutionRunStatus.FAILED
        assert run3.evidence["filt"].error_code == "POISON_QUARANTINED"
        # 其它 owner 域不受影响（同样的毒在别的 owner 正常失败计数）
        reset_quarantine_for_tests()

    def test_isolation_from_other_owners(self, job_env, monkeypatch):
        from app.services.geocompute.executor import GeoExecutionEngine
        from app.services.geocompute.plan import ExecutionPlan, ExecutionRunStatus
        from app.services.geocompute import graph
        from app.services.geocompute.cluster.quarantine import TaskQuarantine

        q = TaskQuarantine(factory=job_env, threshold=2, cooldown_s=600)
        monkeypatch.setattr(
            "app.services.geocompute.cluster.quarantine._default_quarantine", q)
        # owner X 两次毒失败 → 隔离；owner Y 同样毒 → 正常失败计数
        plan_dict = _plan_boom_filter("pz2")
        plan = ExecutionPlan.model_validate(plan_dict)
        graph.validate_plan(plan)
        node_fp = plan.node_map()["filt"].semantic_fingerprint()
        engine = GeoExecutionEngine()
        engine.execute_plan(plan, session_id="s-x1",
                            owner_scope_override="u:x")
        engine.execute_plan(plan, session_id="s-x2",
                            owner_scope_override="u:x")
        assert q.is_quarantined("u:x", node_fp) is True
        engine.execute_plan(plan, session_id="s-y1",
                            owner_scope_override="u:y")
        assert q.is_quarantined("u:y", node_fp) is False
        assert run_failed(engine, plan, "s-y1", "u:y")


class TestSpeculativeDuplicate:
    """直接测 executor 的投机语义（monkeypatch durable 等待原语 —— 不做
    celery 内部体操）：primary 优先、败者取消请求、双输类型化失败。"""

    def _node(self):
        from app.services.geocompute.plan import (
            ExecutionNode,
            NodeCategory,
            NodeReusePolicy,
        )

        return ExecutionNode(
            node_id="n1", category=NodeCategory.FILTER, operation="eq",
            inputs=["src"], deterministic=True,
            reuse=NodeReusePolicy.ALLOW,
            parameters={"predicate": {"op": "eq", "field": "k", "value": "a"}},
        )

    def _run(self):
        from app.services.geocompute.plan import (
            ExecutionPlan,
            ExecutionRun,
            ExecutionRunStatus,
        )

        plan = ExecutionPlan(plan_id="p", nodes=[])
        return ExecutionRun(run_id="gexec-spec", plan_id="p",
                            plan_fingerprint="fp",
                            status=ExecutionRunStatus.PENDING)

    def _engine(self, after_s=0.05):
        from app.services.geocompute.executor import GeoExecutionEngine

        return GeoExecutionEngine(speculative_after_s=after_s)

    def test_window_waits_then_speculates_primary_wins(
            self, job_env, monkeypatch):
        from app.services.geocompute import durable
        from app.services.geocompute.errors import DeadlineExceededError

        engine = self._engine(after_s=0.05)
        node = self._node()
        run = self._run()
        calls = {"await": 0}

        def fake_await_one(job_id, *, session_id, deadline_ts, cancel_token):
            calls["await"] += 1
            raise DeadlineExceededError("window elapsed", details={})

        monkeypatch.setattr(durable, "await_node_job", fake_await_one)

        dispatched = []

        def fake_dispatch(node_copy, **kw):
            dispatched.append(node_copy)
            return {"job_id": "999", "status": "queued"}

        monkeypatch.setattr(durable, "dispatch_node", fake_dispatch)

        def fake_await_many(job_ids, *, session_id, deadline_ts,
                            cancel_token, cancel_on_deadline=True):
            return {int(job_ids[0]): {"status": "completed",
                                      "payload": {"features": [1]},
                                      "error": None},
                    int(job_ids[1]): {"status": "completed",
                                      "payload": {"features": [2]},
                                      "error": None}}

        monkeypatch.setattr(durable, "await_node_jobs", fake_await_many)

        cancel_calls = []
        from app.services.jobs import DurableJobStore

        monkeypatch.setattr(DurableJobStore, "request_cancel_sync",
                            staticmethod(
                                lambda db, job_id: cancel_calls.append(
                                    int(job_id)) or True))

        done = engine._await_with_speculative(
            node, run, {"job_id": 42}, session_id="s", deadline_ts=1e12,
            cancel_token=None, owner_scope="u:spec", budget=None,
            input_refs={}, input_keys={}, resource_envelope=None,
            emit_events=False)
        assert calls["await"] == 1
        assert len(dispatched) == 1
        # 副本带 _speculative 标记（独立幂等键）
        assert dispatched[0].parameters.get("_speculative") is True
        # primary 优先胜出；败者（副本）被请求取消
        assert done["payload"] == {"features": [1]}
        assert done["job_id"] == "42"
        assert cancel_calls == [999]

    def test_speculative_wins_when_primary_failed(self, job_env, monkeypatch):
        from app.services.geocompute import durable
        from app.services.geocompute.errors import DeadlineExceededError
        from app.services.jobs import DurableJobStore

        engine = self._engine(after_s=0.05)
        node = self._node()
        run = self._run()

        def _raise_window(*a, **kw):
            raise DeadlineExceededError("window", details={})

        monkeypatch.setattr(durable, "await_node_job", _raise_window)
        monkeypatch.setattr(
            durable, "dispatch_node",
            lambda node_copy, **kw: {"job_id": "999", "status": "queued"})
        monkeypatch.setattr(
            durable, "await_node_jobs",
            lambda job_ids, **kw: {
                int(job_ids[0]): {"status": "failed", "payload": {},
                                  "error": "boom"},
                int(job_ids[1]): {"status": "completed",
                                  "payload": {"features": ["spec"]},
                                  "error": None}})
        cancel_calls = []
        monkeypatch.setattr(
            DurableJobStore, "request_cancel_sync",
            staticmethod(lambda db, job_id:
                         cancel_calls.append(int(job_id)) or True))

        done = engine._await_with_speculative(
            node, run, {"job_id": 42}, session_id="s", deadline_ts=1e12,
            cancel_token=None, owner_scope="u:spec", budget=None,
            input_refs={}, input_keys={}, resource_envelope=None,
            emit_events=False)
        assert done["payload"] == {"features": ["spec"]}
        assert cancel_calls == [42]  # primary 败者被请求取消

    def test_both_lose_fails_typed(self, job_env, monkeypatch):
        from app.services.geocompute import durable
        from app.services.geocompute.errors import (
            DeadlineExceededError,
            NodeExecutionError,
        )

        engine = self._engine(after_s=0.05)
        node = self._node()
        run = self._run()

        def _raise_window(*a, **kw):
            raise DeadlineExceededError("window", details={})

        monkeypatch.setattr(durable, "await_node_job", _raise_window)
        monkeypatch.setattr(
            durable, "dispatch_node",
            lambda node_copy, **kw: {"job_id": "999", "status": "queued"})
        monkeypatch.setattr(
            durable, "await_node_jobs",
            lambda job_ids, **kw: {
                int(job_ids[0]): {"status": "failed", "payload": {},
                                  "error": "boom"},
                int(job_ids[1]): {"status": "failed", "payload": {},
                                  "error": "boom2"}})
        with pytest.raises(NodeExecutionError, match="speculative pair failed"):
            engine._await_with_speculative(
                node, run, {"job_id": 42}, session_id="s", deadline_ts=1e12,
                cancel_token=None, owner_scope="u:spec", budget=None,
                input_refs={}, input_keys={}, resource_envelope=None,
                emit_events=False)

    def test_disabled_or_non_idempotent_skips_speculation(
            self, job_env, monkeypatch):
        from app.services.geocompute import durable
        from app.services.geocompute.plan import NodeReusePolicy

        def fail_dispatch(*a, **kw):
            raise AssertionError("dispatch should not engage")

        def ok_await(*a, **kw):
            return {"payload": {"ok": 1}, "job_id": "7"}

        engine = self._engine(after_s=0)  # 停用
        node = self._node()
        monkeypatch.setattr(durable, "await_node_job", ok_await)
        monkeypatch.setattr(durable, "dispatch_node", fail_dispatch)
        engine._await_with_speculative(
            node, run=self._run(), primary_ret={"job_id": 7},
            session_id="s", deadline_ts=1e12, cancel_token=None,
            owner_scope="u", budget=None, input_refs={}, input_keys={},
            resource_envelope=None, emit_events=False)

        # reuse DISALLOW：即使阈值 > 0 也不投机（幂等门控）
        engine2 = self._engine(after_s=0.05)
        node2 = self._node()
        node2.reuse = NodeReusePolicy.DISALLOW
        monkeypatch.setattr(durable, "await_node_job", ok_await)
        monkeypatch.setattr(durable, "dispatch_node", fail_dispatch)
        engine2._await_with_speculative(
            node2, run=self._run(), primary_ret={"job_id": 7},
            session_id="s", deadline_ts=1e12, cancel_token=None,
            owner_scope="u", budget=None, input_refs={}, input_keys={},
            resource_envelope=None, emit_events=False)
