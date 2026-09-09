"""GeoCompute V6 cluster 控制面类型化错误（REST 语义映射）。

store 层抛出、routes 层映射：BACKPRESSURE → 429、PLAN 快照超限 → 413。
"""
from __future__ import annotations

from app.services.geocompute.errors import GeoComputeError


class ClusterBackpressureError(GeoComputeError):
    """队列背压（租户/全局 queued 上限）。submit 侧映射 429。"""

    code = "CLUSTER_BACKPRESSURE"


class PlanSnapshotTooLargeError(GeoComputeError):
    """plan 快照超出落库上界（typed 413 语义）。submit 侧映射 413。"""

    code = "PLAN_SNAPSHOT_TOO_LARGE"
