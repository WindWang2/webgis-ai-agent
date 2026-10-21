"""#1718 回归：durable 路径 caller 穿透 + rows 型载荷记账/计数形状。

背景（issue #1718，P2）：
(a) durable/worker 执行路径的 ``OperatorContext`` 丢弃 caller —— 目录类
    算子（QUERY/SOURCE_SCAN）的目录项准入被按匿名 fail-closed 裁决
    （租户隔离在 worker 路径失效）；
(b) rows 型载荷（``_store_payload`` 落存 ``{"rows": [...]}`` 信封）经
    await 回传面被盲包成 ``{"features": {"rows": [...]}}`` ——
    ``_count_rows``/``_governor_charge`` 少报为 1、
    ``NodeResultStore._measure`` 对 dict 切片抛异常，governor 记账被
    炸断、``_record_durable_result`` 被跳过（复用事实从不记录）。

worker simulation（同 tests/unit/test_geocompute_durable.py 的诚实声明）：
Celery eager + 临时 SQLite；不覆盖跨进程投递/broker 重投。
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
from app.services.geocompute.budgets import ResourceGovernor
from app.services.geocompute.executor import NodeResultStore, owner_scope_for
from app.services.geocompute.graph import checkpoint_reuse_key


@pytest.fixture(autouse=True)
def _celery_offline(monkeypatch):
    from app.services.task_queue import celery_app

    monkeypatch.setitem(celery_app.conf, "result_backend", None)
    monkeypatch.setitem(celery_app.conf, "broker_url", "memory://")
    monkeypatch.setitem(celery_app.conf, "task_always_eager", True)


@pytest.fixture(autouse=True)
def _quarantine_isolated(tmp_path, monkeypatch):
    """隔离毒任务隔离区（指向临时 SQLite + 重置进程级单例）。"""
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
    """jobs 子系统 + 复用索引的会话工厂全部指向临时 SQLite。"""
    engine = create_engine(f"sqlite:///{tmp_path / 'geocompute-1718.db'}")
    from app.models.db_model import Base

    Base.metadata.create_all(engine)
    Sess = sessionmaker(bind=engine, expire_on_commit=False)

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
    from app.services.geocompute import durable, reuse_index

    monkeypatch.setattr(submit_mod, "db_session", fake_db_session)
    monkeypatch.setattr(worker_mod, "db_session", fake_db_session, raising=False)
    if hasattr(worker_mod, "_default_session_factory"):
        monkeypatch.setattr(
            worker_mod, "_default_session_factory", lambda: fake_db_session()
        )
    monkeypatch.setattr(durable, "session_factory", lambda: fake_db_session())
    monkeypatch.setattr(reuse_index, "session_factory", lambda: fake_db_session())
    yield fake_db_session
    engine.dispose()


# ── (a) caller 穿透 ────────────────────────────────────────────────


def _source_scan_node() -> ExecutionNode:
    return ExecutionNode(
        node_id="ss1",
        category=NodeCategory.SOURCE_SCAN,
        policy=ExecutionPolicyKind.DURABLE_JOB,
        parameters={},
    )


def test_dispatch_node_threads_caller_through_task_kwargs(job_env, monkeypatch):
    """caller 只进 task_kwargs（不进 params/幂等键）—— 与 run_id/budget 同款
    通道（issue #1718 建议修复方向 1）。"""
    from app.services.geocompute import durable
    import app.services.jobs.submit as submit_mod

    captured: dict = {}

    def spy(**kwargs):
        captured.update(kwargs)
        return {"job_id": "1", "task_id": "t", "status": "queued"}

    monkeypatch.setattr(submit_mod, "submit_durable_job", spy)
    caller = {"user_id": "u-1718", "org_id": "org-1718", "role": "analyst"}
    durable.dispatch_node(
        _source_scan_node(), session_id="sess-1718", plan_fingerprint="fp-1718",
        deadline_s=30.0, run_id="run-1718", caller=caller,
    )
    assert captured["task_kwargs"]["caller"] == caller
    assert "caller" not in captured["params"]


def test_durable_worker_operator_context_receives_caller(job_env, monkeypatch):
    """端到端：execute_plan(caller=...) → worker 任务体 OperatorContext.caller
    恢复（修复前 durable 路径恒为 None，目录准入按匿名裁决）。"""
    from app.services.geocompute import ops
    from app.services.geocompute.plan import NodeCategory

    seen: list = []

    def _scan(ctx, node, payloads):
        seen.append(dict(ctx.caller) if ctx.caller else None)
        return {"features": [{"type": "Feature", "geometry": None,
                              "properties": {"kind": "a"}}],
                "metadata": {}}

    monkeypatch.setitem(ops.REGISTRY, NodeCategory.SOURCE_SCAN, _scan)
    caller = {"user_id": "u-1718-e2e", "org_id": "org-1718", "role": "analyst"}
    plan = ExecutionPlan(plan_id="pd-1718-caller", nodes=[_source_scan_node()])
    run = GeoExecutionEngine(max_workers=1).execute_plan(
        plan, session_id="sess-1718-caller", caller=caller,
    )
    assert run.status is ExecutionRunStatus.COMPLETED, run.summary_lines()
    assert run.evidence["ss1"].status == "completed"
    assert seen == [caller]


# ── (b) rows 信封形状 ──────────────────────────────────────────────


def _envelope(rows: list) -> dict:
    """await 回传面历史上的盲包形状：rows 列表嵌在 features 键下的 dict 里。"""
    return {"ref_id": "ref:x", "features": {"rows": rows}, "metadata": {}}


def test_count_rows_and_measure_agree_across_three_shapes():
    """三种形状（features 列表 / rows 列表 / rows 信封 dict）计数与字节
    估计一致（修复前信封形状 count=1、_measure 抛异常）。"""
    feats = [{"type": "Feature", "geometry": None, "properties": {"v": i}}
             for i in range(2)]
    rows = [{"kind": "a", "count_v": 3}, {"kind": "b", "count_v": 3},
            {"kind": "c", "count_v": 3}]
    shapes = [
        ({"features": feats, "metadata": {}}, 2),
        ({"rows": rows, "metadata": {}}, 3),
        (_envelope(rows), 3),
    ]
    for payload, expected in shapes:
        assert GeoExecutionEngine._count_rows(payload) == expected
        assert NodeResultStore._measure(payload) > 0


def test_governor_charge_envelope_counts_real_rows():
    """信封形状下 governor 按真实行数记账且不抛异常（修复前 KeyError 炸断
    整条记账路径）。"""
    charges: list = []

    class _Gov:
        def charge(self, path, *, rows=0, bytes=0, nodes=0):
            charges.append((rows, bytes, nodes))

    rows = [{"a": i} for i in range(3)]
    payload = _envelope(rows)
    GeoExecutionEngine._governor_charge(_Gov(), "global:root/execution", None,
                                        payload, None)
    assert charges == [(3, NodeResultStore._measure(payload), 1)]


def test_payload_from_stored_ref_restores_rows_envelope():
    """await 回传面形状归位：rows 信封 → rows 键；FC dict/裸列表 → features。"""
    from app.services.geocompute.durable import payload_from_stored_ref

    rows = [{"kind": "a", "count_v": 3}]
    assert payload_from_stored_ref("ref:1", {"rows": rows}, "durable_job") == {
        "ref_id": "ref:1", "rows": rows, "metadata": {"via": "durable_job"}}
    feats = [{"type": "Feature", "geometry": None, "properties": {}}]
    assert payload_from_stored_ref(
        "ref:2", feats, "durable_job")["features"] == feats
    fc = {"type": "FeatureCollection", "features": feats}
    assert payload_from_stored_ref(
        "ref:3", fc, "partition_tile")["features"] == feats


def test_durable_rows_node_accounting_and_reuse_recorded(job_env):
    """端到端：rows 型 durable 节点（FILTER → AGGREGATE，2 个分组行）。

    修复前：``_governor_charge`` 对信封 dict 抛 KeyError → 记账路径炸断、
    ``_record_durable_result`` 被跳过（进程内 store + DB 索引均无记录）、
    rows_emitted 少报为 1。
    """
    class _RecordingGovernor(ResourceGovernor):
        def __init__(self):
            super().__init__()
            self.charges: list = []

        def charge(self, path, *, rows=0, bytes=0, nodes=0):
            self.charges.append((rows, bytes, nodes))
            super().charge(path, rows=rows, bytes=bytes, nodes=nodes)

    filt = ExecutionNode(
        node_id="dn1",
        category=NodeCategory.FILTER,
        policy=ExecutionPolicyKind.DURABLE_JOB,
        parameters={
            "predicate": {"op": "gt", "field": "v", "value": -1},
            "features": [
                {"type": "Feature", "geometry": None,
                 "properties": {"kind": "a" if i % 2 == 0 else "b", "v": i}}
                for i in range(6)
            ],
        },
    )
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
    plan = ExecutionPlan(plan_id="pd-1718-rows", nodes=[filt, agg])
    gov = _RecordingGovernor()
    engine = GeoExecutionEngine(max_workers=1)
    run = engine.execute_plan(
        plan, session_id="sess-1718-rows", governor=gov,
        governor_parent_path="global:root",
    )
    assert run.status is ExecutionRunStatus.COMPLETED, run.summary_lines()
    ev_f, ev_a = run.evidence["dn1"], run.evidence["agg1"]
    assert ev_f.status == "completed" and ev_f.rows_emitted == 6
    assert ev_a.status == "completed"
    # 分组后 2 行（kind a/b 各 3）—— 修复前信封 dict len 恒报 1
    assert ev_a.rows_emitted == 2
    # governor 沿链记账：两个节点各记一次真实行数
    assert any(r == 6 for r, _, _ in gov.charges), gov.charges
    assert any(r == 2 for r, _, _ in gov.charges), gov.charges
    # 复用事实：进程内 store 命中 agg 的 checkpoint 键（修复前 put 内
    # _measure 抛异常被 except 吞掉，条目从不落库）
    owner_scope = owner_scope_for(None, "sess-1718-rows")
    entry = engine._store.get(checkpoint_reuse_key(agg, owner_scope))
    assert entry is not None
    assert sorted(entry["rows"], key=lambda r: r["kind"]) == [
        {"kind": "a", "count_v": 3}, {"kind": "b", "count_v": 3}]
