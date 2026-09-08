"""V6 wave 1 — Cluster 执行契约：状态机白名单 / fencing 语义 / 有界词表。

纯类型层测试（无 I/O）：转移表完整性、终态封闭性、幂等语义由 store 层
CAS 落实（wave 2 测试），这里锁定**词表真相**不被悄悄改坏。
"""

from __future__ import annotations

import pytest

from app.services.geocompute.cluster import (
    DEFAULT_MAX_RUN_ATTEMPTS,
    DISPATCHABLE_STATUSES,
    LEASED_STATUSES,
    MAX_PLAN_SNAPSHOT_BYTES,
    TERMINAL_STATUSES,
    TRANSITIONS,
    ClusterRunStatus,
    RunPriority,
    WorkerCapability,
    is_terminal,
    run_error_for_reclaim,
    transition_allowed,
)

S = ClusterRunStatus


class TestTransitionTable:
    def test_terminal_states_have_no_outgoing_edges(self):
        for st in TERMINAL_STATUSES:
            assert TRANSITIONS[st] == frozenset(), f"{st} 必须无出边"

    def test_every_nonterminal_has_outgoing_edges(self):
        for st in S:
            if st not in TERMINAL_STATUSES:
                assert TRANSITIONS[st], f"{st} 无出边 = 活锁状态"

    def test_happy_path(self):
        assert transition_allowed(S.QUEUED, S.LEASED)
        assert transition_allowed(S.LEASED, S.RUNNING)
        assert transition_allowed(S.RUNNING, S.COMPLETED)

    def test_reclaim_paths(self):
        # lease 过期：执行未启动回 queued；执行中丢失也回 queued（reclaim）
        assert transition_allowed(S.LEASED, S.QUEUED)
        assert transition_allowed(S.RUNNING, S.QUEUED)

    def test_preemption_paths(self):
        assert transition_allowed(S.RUNNING, S.PREEMPTED)
        # PREEMPTED 重派必须经 LEASED（新 epoch），绝不原地 RUNNING
        assert transition_allowed(S.PREEMPTED, S.LEASED)
        assert not transition_allowed(S.PREEMPTED, S.RUNNING)

    def test_cancellation_from_every_active_state(self):
        for st in (S.QUEUED, S.LEASED, S.RUNNING, S.PREEMPTED):
            assert transition_allowed(st, S.CANCELLED), f"{st} 必须可取消"

    def test_terminal_rejections(self):
        assert not transition_allowed(S.COMPLETED, S.RUNNING)
        assert not transition_allowed(S.FAILED, S.QUEUED)
        assert not transition_allowed(S.CANCELLED, S.QUEUED)
        assert not transition_allowed(S.COMPLETED, S.CANCELLED)

    def test_dispatchable_and_leased_partitions(self):
        assert DISPATCHABLE_STATUSES == frozenset({S.QUEUED, S.PREEMPTED})
        assert LEASED_STATUSES == frozenset({S.LEASED, S.RUNNING})
        # 分区互斥：不存在既可派发又占 lease 的状态
        assert not (DISPATCHABLE_STATUSES & LEASED_STATUSES)

    def test_is_terminal_accepts_raw_strings(self):
        assert is_terminal("completed")
        assert is_terminal("cancelled")
        assert not is_terminal("running")
        assert not is_terminal("nonsense")  # 未知词 → 非终态（诚实）


class TestFencingContract:
    def test_run_error_for_reclaim(self):
        # attempt 未耗尽 → 回队（None）；耗尽 → WORKER_LOSS
        assert run_error_for_reclaim(1, DEFAULT_MAX_RUN_ATTEMPTS) is None
        assert run_error_for_reclaim(3, DEFAULT_MAX_RUN_ATTEMPTS) == "WORKER_LOSS"
        assert run_error_for_reclaim(4, DEFAULT_MAX_RUN_ATTEMPTS) == "WORKER_LOSS"

    def test_plan_snapshot_bound_is_actionable(self):
        # ≤256KB：普通计划（几十节点 JSON）必须放得下，超大载荷被拒
        assert 16 * 1024 < MAX_PLAN_SNAPSHOT_BYTES <= 1024 * 1024


class TestPriorityVocabulary:
    def test_coerce_whitelist(self):
        assert RunPriority.coerce(0) == RunPriority.LOW
        assert RunPriority.coerce(5) == RunPriority.NORMAL
        assert RunPriority.coerce(10) == RunPriority.HIGH
        assert RunPriority.coerce(None) == RunPriority.NORMAL
        assert RunPriority.coerce("9") == RunPriority.NORMAL  # 不在词表 → NORMAL
        assert RunPriority.coerce(99) == RunPriority.NORMAL  # 优先级通胀防护


class TestWorkerCapability:
    def test_covered_profiles_excludes_zero_slots(self):
        cap = WorkerCapability(
            worker_id="w1", profiles={"raster": 2, "light_cpu": 0, "heavy_cpu": 1}
        )
        assert cap.covered_profiles == frozenset({"raster", "heavy_cpu"})

    def test_role_pattern(self):
        with pytest.raises(Exception):
            WorkerCapability(worker_id="w", role="admin")


class TestWorkerBudgetPenetration:
    """V6 wave 6（P0-3 修复）：plan budget 穿透到 worker 任务体。"""

    def test_task_body_restores_budget(self):
        from app.services.geocompute.plan import ExecutionNode, ResourceBudget
        from app.services.geocompute.tasks import run_geocompute_node

        node = ExecutionNode(node_id="n1", category="filter", operation="eq")
        budget = ResourceBudget(max_rows=17)
        captured = {}

        class _FakeOps:
            @staticmethod
            def execute_node(ctx, node_, upstream):
                captured["budget"] = ctx.budget
                return {"features": [], "metadata": {}}

        import app.services.geocompute.ops as ops_mod

        real_execute = ops_mod.execute_node
        ops_mod.execute_node = _FakeOps.execute_node
        try:
            run_geocompute_node.run(
                node.model_dump(mode="json"), session_id=None, job_id=None,
                budget=budget.model_dump(mode="json"),
            )
        finally:
            ops_mod.execute_node = real_execute
        assert isinstance(captured["budget"], ResourceBudget)
        assert captured["budget"].max_rows == 17

    def test_budget_excluded_from_idempotency_params(self, monkeypatch):
        """budget 只进 task_kwargs 不进 params —— 治理元数据不改变幂等键。"""
        from app.services.geocompute import durable
        from app.services.geocompute.plan import (
            ExecutionNode, ExecutionPlan, ResourceBudget,
        )

        captured = []
        job_ids = iter(({"job_id": 1}, {"job_id": 1}))

        def fake_submit(**kw):
            captured.append(kw)
            return next(job_ids)

        import app.services.jobs.submit as submit_mod

        node = ExecutionNode(
            node_id="n1", category="filter", operation="eq",
            parameters={"features": [{"x": 1}]},
        )
        ExecutionPlan(plan_id="p", nodes=[node])  # 契约可构建（形状自检）
        monkeypatch.setattr(submit_mod, "submit_durable_job", fake_submit)
        monkeypatch.setattr(
            "app.services.geocompute.durable.submit_durable_job", fake_submit,
            raising=False,
        )
        # dispatch_node 内部 import submit_durable_job —— patch 源模块
        real_run = durable.dispatch_node
        budget = ResourceBudget(max_rows=99)
        ret1 = real_run(
            node, session_id="s1", plan_fingerprint="fp", deadline_s=None,
            budget=budget,
        )
        ret2 = real_run(
            node, session_id="s1", plan_fingerprint="fp", deadline_s=None,
            budget=None,
        )
        # 幂等键一致（同一 job 行）
        assert ret1["job_id"] == ret2["job_id"]
        # budget 确实进了 task_kwargs（第一次派发），且 params 不含它
        assert captured[0]["task_kwargs"]["budget"]["max_rows"] == 99
        assert "budget" not in captured[0]["params"]
        assert captured[1]["task_kwargs"]["budget"] is None
