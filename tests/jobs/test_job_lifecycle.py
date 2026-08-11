"""统一 job 生命周期状态机（ADR-0052）。

这些断言是整个 durable job 运行时的安全基线：一旦迁移表被人「顺手放宽」，
late success 覆盖 cancelled / 取消后自动 retry 这类缺陷就会重新出现。
"""
import pytest

from app.services.jobs.lifecycle import (
    ACTIVE_STATUSES,
    ALLOWED_TRANSITIONS,
    IMMUTABLE_STATUSES,
    TERMINAL_STATUSES,
    InvalidJobTransition,
    JobStatus,
    can_transition,
    coerce_status,
    is_active,
    is_cancellable,
    is_retryable_status,
    is_terminal,
    sources_for,
    validate_transition,
)


def test_immutable_statuses_have_no_successors():
    """cancelled / completed 永不可再迁移 —— 这是「cancelled 不被 late success
    覆盖」和「取消永不 retry」两条不变式的共同根。"""
    for status in IMMUTABLE_STATUSES:
        assert ALLOWED_TRANSITIONS[status] == frozenset(), status


def test_failed_and_stale_are_terminal_but_retryable():
    """failed/stale 是终态（没有 worker 在跑）但可经显式新 attempt 回 queued。

    唯一后继必须是 queued —— 直接回 running 会绕过 attempt 递增。
    """
    assert JobStatus.failed in TERMINAL_STATUSES
    assert JobStatus.stale in TERMINAL_STATUSES
    assert JobStatus.failed not in IMMUTABLE_STATUSES
    assert JobStatus.stale not in IMMUTABLE_STATUSES
    assert ALLOWED_TRANSITIONS[JobStatus.failed] == frozenset({JobStatus.queued})
    assert ALLOWED_TRANSITIONS[JobStatus.stale] == frozenset({JobStatus.queued})


@pytest.mark.parametrize(
    "current,target",
    [
        (JobStatus.cancelled, JobStatus.completed),   # 规范 §14 明令禁止
        (JobStatus.cancelled, JobStatus.running),
        (JobStatus.cancelled, JobStatus.queued),      # 取消绝不 retry（规范 §17）
        (JobStatus.completed, JobStatus.running),
        (JobStatus.completed, JobStatus.failed),
        (JobStatus.failed, JobStatus.running),        # 只能经新 attempt 回 queued
        (JobStatus.cancelling, JobStatus.completed),  # cancelling 期间的成功不算成功
        (JobStatus.stale, JobStatus.completed),
    ],
)
def test_forbidden_transitions(current, target):
    assert can_transition(current, target) is False
    with pytest.raises(InvalidJobTransition):
        validate_transition(current, target)


@pytest.mark.parametrize(
    "current,target",
    [
        (JobStatus.pending, JobStatus.queued),
        (JobStatus.pending, JobStatus.cancelled),
        (JobStatus.queued, JobStatus.running),
        (JobStatus.queued, JobStatus.stale),
        (JobStatus.running, JobStatus.cancelling),
        (JobStatus.running, JobStatus.completed),
        (JobStatus.running, JobStatus.failed),
        (JobStatus.running, JobStatus.stale),
        (JobStatus.cancelling, JobStatus.cancelled),
        (JobStatus.cancelling, JobStatus.failed),
        (JobStatus.failed, JobStatus.queued),  # 显式新 attempt
        (JobStatus.stale, JobStatus.queued),
    ],
)
def test_allowed_transitions(current, target):
    assert can_transition(current, target) is True
    assert validate_transition(current, target) is target


def test_self_transition_is_not_allowed():
    """同状态自迁移一律非法 —— 调用方必须走幂等分支而不是重复写。"""
    for status in JobStatus:
        assert can_transition(status, status) is False


def test_sources_for_matches_transition_table():
    """sources_for 是 store 构造原子条件更新的唯一来源，必须与迁移表严格一致。"""
    for target in JobStatus:
        expected = {src for src, allowed in ALLOWED_TRANSITIONS.items() if target in allowed}
        assert sources_for(target) == frozenset(expected), target


def test_completed_only_reachable_from_running():
    """成功只能来自 running。queued 直接跳 completed 会绕过 started_at 记录。"""
    assert sources_for(JobStatus.completed) == frozenset({JobStatus.running})


def test_cancelled_never_reachable_from_success_or_failure():
    assert JobStatus.completed not in sources_for(JobStatus.cancelled)
    assert JobStatus.failed not in sources_for(JobStatus.cancelled)


def test_active_and_terminal_partition_all_statuses():
    """每个状态必须恰好属于 active 或 terminal 之一，不能有归类真空。"""
    assert ACTIVE_STATUSES.isdisjoint(TERMINAL_STATUSES)
    assert ACTIVE_STATUSES | TERMINAL_STATUSES == frozenset(JobStatus)


def test_cancellable_excludes_cancelling_and_terminal():
    """cancelling 不再可取消（重复取消走幂等分支而不是再次迁移）。"""
    assert is_cancellable(JobStatus.pending)
    assert is_cancellable(JobStatus.queued)
    assert is_cancellable(JobStatus.running)
    assert not is_cancellable(JobStatus.cancelling)
    for status in TERMINAL_STATUSES:
        assert not is_cancellable(status)


def test_retryable_statuses_exclude_cancelled():
    assert is_retryable_status(JobStatus.failed)
    assert is_retryable_status(JobStatus.stale)
    assert not is_retryable_status(JobStatus.cancelled)
    assert not is_retryable_status(JobStatus.completed)
    assert not is_retryable_status(JobStatus.running)


def test_coerce_status_tolerates_unknown_values():
    """未来版本写入的未知状态不能让整个任务中心 500。"""
    assert coerce_status("running") is JobStatus.running
    assert coerce_status(JobStatus.failed) is JobStatus.failed
    assert coerce_status(None) is JobStatus.pending
    assert coerce_status("") is JobStatus.pending
    assert coerce_status("some_future_status") is JobStatus.pending


def test_is_terminal_is_active_helpers():
    assert is_terminal("completed") and is_terminal("cancelled") and is_terminal("stale")
    assert not is_terminal("cancelling")
    assert is_active("cancelling") and is_active("queued")
    assert not is_active("failed")
