"""Unified job lifecycle contract (ADR-0052).

单一生命周期语义：Agent task / 空间分析 job / Celery worker job 共用同一套状态与
同一套合法迁移。历史上有三套互不相通的 task state（内存 TaskTracker、
analysis_tasks 表、TaskQueueService._task_owners），状态名与语义都不一致；这里把
语义收敛到一处，并用显式迁移表挡住非法写入。

状态名沿用 analysis_tasks 表原有 CHECK 值（pending/queued/running/completed/
failed/cancelled），只新增两个必要状态 —— 这样迁移是 additive 的，老数据行不需要
改写：

    pending      已创建，尚未入队（= 规范里的 created）
    queued       已交给 worker/队列
    running      worker 正在执行
    cancelling   已收到取消请求，worker 尚未确认（非终态）
    cancelled    取消已生效（终态）
    completed    成功（= 规范里的 succeeded）
    failed       失败（终态）
    stale        worker 失联，心跳超时被清扫（终态）

关键不变式（见 test_job_lifecycle.py）：
  1. 终态不可再迁移 —— late success 不能覆盖 cancelled。
  2. cancelling 只能走向 cancelled/failed —— worker 若在 cancelling 期间跑完，
     结果按取消处理，绝不回写 completed（规范 §14）。
  3. failed/stale 只能通过显式新 attempt 回到 queued；cancelled 永不重试
     （规范 §17：取消绝不能自动 retry）。
"""
from __future__ import annotations

from enum import Enum


class JobStatus(str, Enum):
    """统一 job 状态。值与 analysis_tasks.status CHECK 约束一致。"""

    pending = "pending"
    queued = "queued"
    running = "running"
    cancelling = "cancelling"
    cancelled = "cancelled"
    completed = "completed"
    failed = "failed"
    stale = "stale"


class JobKind(str, Enum):
    """job 归属的执行域。决定前端图标/分组与可重试性默认值。"""

    agent = "agent"          # Agent turn 级任务（原 TaskTracker task）
    analysis = "analysis"    # 空间/遥感分析 job（Celery）
    workflow = "workflow"    # WorkflowRun
    explorer = "explorer"    # Explorer chain


#: 终态集合：执行已结束，不再有 worker 在推进它。
#: 注意「终态」≠「不可变」—— failed/stale 可以通过**显式新 attempt** 回到 queued
#: （见 IMMUTABLE_STATUSES 与 DurableJobStore.start_retry）。
TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.cancelled, JobStatus.completed, JobStatus.failed, JobStatus.stale}
)

#: 永不可再迁移的状态。这是最强不变式：
#:   * cancelled 不能被 worker 的 late success 改成 completed（规范 §14）；
#:   * completed 不能被任何后续写入改掉。
#: 取消也因此永远不会被 retry（规范 §17）—— cancelled 没有任何后继。
IMMUTABLE_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.cancelled, JobStatus.completed}
)

#: 可取消状态集合。
CANCELLABLE_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.pending, JobStatus.queued, JobStatus.running}
)

#: 允许通过新 attempt 重试的终态。cancelled 不在其中 —— 规范 §17。
RETRYABLE_STATUSES: frozenset[JobStatus] = frozenset({JobStatus.failed, JobStatus.stale})

#: 活跃（非终态）状态集合。前端轮询只在存在活跃 job 时进行。
ACTIVE_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.pending, JobStatus.queued, JobStatus.running, JobStatus.cancelling}
)

#: 显式迁移表。任何不在表中的 (from, to) 都是非法迁移。
ALLOWED_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.pending: frozenset(
        {
            JobStatus.queued,
            JobStatus.running,
            JobStatus.cancelling,
            JobStatus.cancelled,
            JobStatus.failed,
        }
    ),
    JobStatus.queued: frozenset(
        {
            JobStatus.running,
            JobStatus.cancelling,
            JobStatus.cancelled,
            JobStatus.failed,
            JobStatus.stale,
        }
    ),
    JobStatus.running: frozenset(
        {
            JobStatus.cancelling,
            JobStatus.completed,
            JobStatus.failed,
            JobStatus.stale,
        }
    ),
    # cancelling 绝不走向 completed：worker 的 late success 按取消收敛。
    JobStatus.cancelling: frozenset({JobStatus.cancelled, JobStatus.failed}),
    JobStatus.cancelled: frozenset(),
    JobStatus.completed: frozenset(),
    # failed/stale 只能经**显式新 attempt** 回到 queued（store.start_retry 会递增
    # attempt 并保留首次失败的 error_trace）。直接 failed → running 是被禁止的。
    JobStatus.failed: frozenset({JobStatus.queued}),
    JobStatus.stale: frozenset({JobStatus.queued}),
}


class InvalidJobTransition(ValueError):
    """非法状态迁移。携带 from/to 便于日志与测试断言。"""

    def __init__(self, current: JobStatus, target: JobStatus, job_id: str | int | None = None):
        self.current = current
        self.target = target
        self.job_id = job_id
        where = f" job={job_id}" if job_id is not None else ""
        super().__init__(f"illegal job transition {current.value} -> {target.value}{where}")


def coerce_status(value: JobStatus | str | None) -> JobStatus:
    """把 DB / API 传入的字符串收敛为 JobStatus。

    未知值（例如未来版本写入的状态）按 pending 处理而非抛错，避免一行坏数据
    让整个任务中心 500。
    """
    if isinstance(value, JobStatus):
        return value
    if not value:
        return JobStatus.pending
    try:
        return JobStatus(str(value))
    except ValueError:
        return JobStatus.pending


def is_terminal(status: JobStatus | str | None) -> bool:
    return coerce_status(status) in TERMINAL_STATUSES


def is_active(status: JobStatus | str | None) -> bool:
    return coerce_status(status) in ACTIVE_STATUSES


def can_transition(current: JobStatus | str | None, target: JobStatus | str) -> bool:
    """target 是否是 current 的合法后继。同状态自迁移视为非法（调用方应走幂等分支）。"""
    cur = coerce_status(current)
    tgt = coerce_status(target)
    return tgt in ALLOWED_TRANSITIONS.get(cur, frozenset())


def validate_transition(
    current: JobStatus | str | None,
    target: JobStatus | str,
    job_id: str | int | None = None,
) -> JobStatus:
    """校验并返回 target；非法则抛 InvalidJobTransition。"""
    cur = coerce_status(current)
    tgt = coerce_status(target)
    if tgt not in ALLOWED_TRANSITIONS.get(cur, frozenset()):
        raise InvalidJobTransition(cur, tgt, job_id)
    return tgt


def sources_for(target: JobStatus | str) -> frozenset[JobStatus]:
    """能合法迁移到 target 的所有前驱状态。

    store 层用它构造原子条件更新的 ``WHERE status IN (...)`` —— 迁移规则只在本
    模块定义一次，避免各处手写状态列表逐渐漂移。
    """
    tgt = coerce_status(target)
    return frozenset(src for src, allowed in ALLOWED_TRANSITIONS.items() if tgt in allowed)


def is_cancellable(status: JobStatus | str | None) -> bool:
    return coerce_status(status) in CANCELLABLE_STATUSES


def is_retryable_status(status: JobStatus | str | None) -> bool:
    return coerce_status(status) in RETRYABLE_STATUSES
