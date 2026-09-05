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
