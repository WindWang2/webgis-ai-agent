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
