"""GeoCompute Cluster Runtime V6（run 级持久生命周期 + coordinator 调度）。

单一事实源边界（01-architecture.md）：
- run 生命周期真相 = ``geocompute_runs``（本包 store）；
- 节点 job 真相 = ``analysis_tasks``（jobs 子系统，不动）；
- 终态证据 = ``geocompute_run_evidence``（run_evidence 模块，本包不复制）；
- 载荷 = session ref。
"""
from app.services.geocompute.cluster.fairness import fair_pick, matches_profiles
from app.services.geocompute.cluster.store import (
    ClusterBackpressureError,
    ClusterLedger,
    ClusterRunStore,
    PlanSnapshotTooLargeError,
)

from app.services.geocompute.cluster.contracts import (
    DEFAULT_MAX_RUN_ATTEMPTS,
    DISPATCHABLE_STATUSES,
    LEASED_STATUSES,
    MAX_PLAN_SNAPSHOT_BYTES,
    MAX_PREEMPTS,
    TERMINAL_STATUSES,
    TRANSITIONS,
    ClusterRunStatus,
    ResourceClaim,
    RunLease,
    RunPriority,
    WorkerCapability,
    is_terminal,
    run_error_for_reclaim,
    transition_allowed,
)

__all__ = [
    "DEFAULT_MAX_RUN_ATTEMPTS",
    "DISPATCHABLE_STATUSES",
    "LEASED_STATUSES",
    "MAX_PLAN_SNAPSHOT_BYTES",
    "MAX_PREEMPTS",
    "TERMINAL_STATUSES",
    "TRANSITIONS",
    "ClusterRunStatus",
    "ResourceClaim",
    "RunLease",
    "RunPriority",
    "WorkerCapability",
    "is_terminal",
    "run_error_for_reclaim",
    "transition_allowed",
    "fair_pick",
    "matches_profiles",
    "ClusterBackpressureError",
    "ClusterLedger",
    "ClusterRunStore",
    "PlanSnapshotTooLargeError",
]
