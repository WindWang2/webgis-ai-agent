"""durable_job 策略端到端测试（ADR-0096 D5 / ADR-0052 修正案）。

worker simulation（同 tests/jobs/test_job_celery_e2e.py 的诚实声明）：
Celery eager + 临时 SQLite；真实覆盖任务体、状态机、提交桥、取消传播、
result_ref 交接；不覆盖跨进程投递/broker 重投。
"""

from __future__ import annotations

import contextlib
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.services.geocompute import (
    ExecutionNode,
    ExecutionPlan,
    ExecutionPolicyKind,
    ExecutionRunStatus,
    GeoExecutionEngine,
    NodeCategory,
)


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture(autouse=True)
def _quarantine_isolated(tmp_path, monkeypatch):
    """测试隔离：毒任务隔离区指向临时 SQLite，且每用例重置进程级单例。

    无 session 的 durable 失败路径经 get_quarantine() 单例把 NODE_FAILED
    计数写进 cluster.store 默认会话工厂（共享开发库 data/webgis.db）——
    同机连跑 3 次后 is_quarantined 命中，用例转 POISON_QUARANTINED 稳定红。
    纪律同 test_geocompute_v8_robustness._reset_quarantine。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.db_model import Base
    from app.services.geocompute.cluster import store as cluster_store
    from app.services.geocompute.cluster.quarantine import (
        reset_quarantine_for_tests,
    )

    eng = create_engine(f"sqlite:///{tmp_path / 'geocompute-quarantine.db'}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    monkeypatch.setattr(cluster_store, "session_factory",
                        sessionmaker(bind=eng, expire_on_commit=False))
    reset_quarantine_for_tests()
    yield
    reset_quarantine_for_tests()
    eng.dispose()


@pytest.fixture
def job_env(tmp_path, monkeypatch):
    """把 jobs 子系统的会话工厂与提交桥指向临时 SQLite。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'geocompute-durable.db'}")
    from app.models.db_model import Base

    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine)

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
    from app.services.jobs.store import DurableJobStore

    monkeypatch.setattr(submit_mod, "db_session", fake_db_session)
    monkeypatch.setattr(worker_mod, "db_session", fake_db_session, raising=False)
    if hasattr(worker_mod, "_default_session_factory"):
        monkeypatch.setattr(
            worker_mod, "_default_session_factory", lambda: fake_db_session()
        )
    from app.services.geocompute import durable

    monkeypatch.setattr(durable, "session_factory", lambda: fake_db_session())
    yield {"store": DurableJobStore, "db": fake_db_session}


def _durable_node() -> ExecutionNode:
    return ExecutionNode(
        node_id="dn1",
        category=NodeCategory.FILTER,
        policy=ExecutionPolicyKind.DURABLE_JOB,
        parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": [
                {"type": "Feature", "geometry": None,
                 "properties": {"kind": "a" if i % 2 == 0 else "b", "v": i}}
                for i in range(6)
            ],
        },
    )


def test_durable_node_executes_through_job_runtime(job_env):
    plan = ExecutionPlan(plan_id="pd", nodes=[_durable_node()])
    run = GeoExecutionEngine(max_workers=1).execute_plan(
        plan, session_id="geocompute-durable-test"
    )
    assert run.status is ExecutionRunStatus.COMPLETED, run.summary_lines()
    ev = run.evidence["dn1"]
    assert ev.status == "completed"
    assert ev.output_ref and ev.output_ref.startswith("ref:")
    # 任务体真实跑过 durable job 状态机：job 行存在且 completed
    from app.models.db_model import AnalysisTask

    with job_env["db"]() as db:
        jobs = db.query(AnalysisTask).all()
        assert len(jobs) == 1
        assert jobs[0].status in ("completed", "COMPLETED")
        assert jobs[0].result_ref == ev.output_ref


def test_durable_node_without_session_fails_typed():
    plan = ExecutionPlan(plan_id="pd2", nodes=[_durable_node()])
    run = GeoExecutionEngine(max_workers=1).execute_plan(plan, session_id=None)
    assert run.status is ExecutionRunStatus.FAILED
    assert run.evidence["dn1"].error_code == "NODE_FAILED"
    assert "session context" in (run.evidence["dn1"].error_message or "")


def test_durable_node_cancellation_propagates(job_env):
    from app.lib.cancellation import CancellationToken

    token = CancellationToken()
    token.cancel("user cancelled run")
    plan = ExecutionPlan(plan_id="pd3", nodes=[_durable_node()])
    run = GeoExecutionEngine(max_workers=1).execute_plan(
        plan, session_id="geocompute-durable-test", cancel_token=token
    )
    assert run.status is ExecutionRunStatus.CANCELLED


def test_in_process_policy_still_default_and_fast(job_env):
    node = ExecutionNode(
        node_id="ip1",
        category=NodeCategory.FILTER,
        parameters={
            "predicate": {"op": "eq", "field": "kind", "value": "a"},
            "features": [
                {"type": "Feature", "geometry": None, "properties": {"kind": "a"}}
            ],
        },
    )
    plan = ExecutionPlan(plan_id="pd4", nodes=[node])
    run = GeoExecutionEngine(max_workers=1).execute_plan(
        plan, session_id="geocompute-durable-test"
    )
    assert run.status is ExecutionRunStatus.COMPLETED


# ── 深评 2026-10-08：rows/features 载荷经 durable ref 交接不丢形状 ──────────


def _bridge_get(coro):
    from app.services.geocompute._async_bridge import run_coro_sync

    return run_coro_sync(coro)


class TestDurableHandoffShape:
    """_store_payload 落存 ↔ worker 侧重取（_resolve_inputs）形状往返。

    修复前两种形状共用一条裸列表通道、重取时一律包装成 features —— rows
    型载荷（AGGREGATE/JOIN 输出）被折叠成无 properties 的"要素"。
    """

    def _store(self, session_id: str, payload: dict) -> str:
        from app.services.geocompute import tasks

        ref = tasks._store_payload(session_id, payload, _durable_node())
        assert ref, "载荷必须落存为 session ref"
        return ref

    def _resolve(self, session_id: str, ref: str) -> dict:
        from app.services.geocompute import tasks

        return tasks._resolve_inputs(
            session_id=session_id, input_refs={"up": ref}, input_keys={},
            owner_scope=None, run_id=None, node_id="down", worker_id="w-test",
            attempt=1,
        )["up"]

    def test_rows_payload_roundtrip_keeps_rows_key(self):
        from app.services.session_data import session_data_manager

        rows = [{"kind": "a", "count_v": 3}]
        ref = self._store("geocompute-shape-rows", {"rows": rows, "metadata": {}})
        # 落存侧：rows 信封（形状保留，区别于 features 的裸列表）
        assert _bridge_get(session_data_manager.get("geocompute-shape-rows", ref)) \
            == {"rows": rows}
        # worker 侧重取：rows 键归位，不再是 features 包装
        resolved = self._resolve("geocompute-shape-rows", ref)
        assert resolved["rows"] == rows
        assert "features" not in resolved

    def test_features_payload_roundtrip_stays_features(self):
        from app.services.session_data import session_data_manager

        feats = [{"type": "Feature", "geometry": None, "properties": {"kind": "a"}}]
        ref = self._store("geocompute-shape-features", {"features": feats, "metadata": {}})
        # features 型落存保持裸列表（既有行为不变）
        assert _bridge_get(session_data_manager.get("geocompute-shape-features", ref)) \
            == feats
        assert self._resolve("geocompute-shape-features", ref)["features"] == feats

    def test_fc_dict_dataset_ref_extracts_features(self):
        """外部 dataset ref 的 FC dict 形状 → 抽取 features 列表（修复前
        整个 FC dict 被当作 features 值）。"""
        from app.services.session_data import session_data_manager

        fc = {"type": "FeatureCollection", "features": [
            {"type": "Feature", "geometry": None, "properties": {"kind": "a"}},
        ]}
        ref = _bridge_get(session_data_manager.store("geocompute-shape-fc", fc))
        resolved = self._resolve("geocompute-shape-fc", ref)
        assert resolved["features"] == fc["features"]

    def test_bare_rows_list_wrapped_as_features_still_flows(self):
        """旧 ref / MATERIALIZE 落存的裸 rows 列表 → 按旧路径包装成
        features；ops 消费侧（_props_of）保证属性行不丢。"""
        from app.services.session_data import session_data_manager

        rows = [{"kind": "a", "count_v": 3}]
        ref = _bridge_get(session_data_manager.store("geocompute-shape-bare", rows))
        resolved = self._resolve("geocompute-shape-bare", ref)
        assert resolved["features"] == rows


def test_durable_rows_chain_downstream_filter_not_silently_emptied(job_env):
    """全 durable 链回归锚：FILTER → AGGREGATE（rows 输出）→ FILTER。

    修复前：AGGREGATE 的 rows 落存被下游 worker 重取时包装成 features，
    FILTER 对空 properties 求谓词 → 全部行被静默滤光、节点显示成功、
    尾节点无输出 ref。修复后：rows 键归位，下游按属性行求值。
    """
    from app.services.geocompute._async_bridge import run_coro_sync
    from app.services.session_data import session_data_manager

    agg = ExecutionNode(
        node_id="agg1",
        category=NodeCategory.AGGREGATE,
        policy=ExecutionPolicyKind.DURABLE_JOB,
        inputs=["dn1"],
        parameters={
            "aggregates": [{"func": "count", "field": "v"}],
            "group_by": ["kind"],
        },
    )
    filt = ExecutionNode(
        node_id="filt2",
        category=NodeCategory.FILTER,
        policy=ExecutionPolicyKind.DURABLE_JOB,
        inputs=["agg1"],
        parameters={"predicate": {"op": "gt", "field": "count_v", "value": 1}},
    )
    plan = ExecutionPlan(plan_id="pd-rows-chain", nodes=[_durable_node(), agg, filt])
    session_id = "geocompute-rows-chain-test"
    run = GeoExecutionEngine(max_workers=1).execute_plan(plan, session_id=session_id)
    assert run.status is ExecutionRunStatus.COMPLETED, run.summary_lines()
    assert run.evidence["agg1"].status == "completed"
    ev = run.evidence["filt2"]
    assert ev.status == "completed"
    # 静默滤光的症状是"成功 + 0 行 + 无输出 ref"；修复后尾节点必须有输出
    assert ev.output_ref, "rows 下游 FILTER 不得静默滤光到无输出"
    out = run_coro_sync(session_data_manager.get(session_id, ev.output_ref))
    assert out == {"rows": [{"kind": "a", "count_v": 3}]}
