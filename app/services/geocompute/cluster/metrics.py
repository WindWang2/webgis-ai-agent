"""GeoCompute V6 集群可观察性（wave 12）。

有界基数纪律：所有维度取自**封闭词表**（status × 7 / priority × 3 /
role × 2 / profile × 6），绝无 per-run / per-user / per-plan 维度。
取消延迟样本是有界 deque（≤128），暴露分位数而非原始序列。

快照是**读投影**（store 聚合查询），不引入第二计数器状态。
"""
from __future__ import annotations

import threading
from collections import deque
from typing import Any, Optional

from app.services.geocompute.cluster.contracts import (
    DISPATCHABLE_STATUSES,
    ClusterRunStatus,
)
from app.services.geocompute.cluster.store import ClusterRunStore

#: 取消/让出延迟样本上界（环形；无基数爆炸）。
_MAX_LATENCY_SAMPLES = 128

#: 账本投影暴露的 scope 数上界。
_MAX_LEDGER_SCOPES = 20

_lock = threading.Lock()
_cancel_latencies: deque[float] = deque(maxlen=_MAX_LATENCY_SAMPLES)


def record_cancellation_latency(seconds: float) -> None:
    """cancel 请求 → 终态 的观测延迟（任意进程本地采样）。"""
    if seconds < 0:
        return
    with _lock:
        _cancel_latencies.append(float(seconds))


def _percentile(samples: list[float], q: float) -> Optional[float]:
    if not samples:
        return None
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, int(q * (len(ordered) - 1))))
    return round(ordered[idx], 4)


class ClusterMetrics:
    """集群快照聚合器（store 只读查询的组合）。"""

    def __init__(self, store: Optional[ClusterRunStore] = None):
        self._store = store or ClusterRunStore()

    def snapshot(self) -> dict[str, Any]:
        """完整集群快照（一次调用 ≤ ~10 个聚合查询；无行扫描）。"""
        runs_by_status = self._store.count_runs_by_status()
        waiting = sum(
            runs_by_status.get(s.value, 0) for s in DISPATCHABLE_STATUSES
        )
        running = runs_by_status.get(ClusterRunStatus.LEASED.value, 0) + runs_by_status.get(
            ClusterRunStatus.RUNNING.value, 0
        )
        with _lock:
            samples = list(_cancel_latencies)
        cancel_latency = {
            "p50_s": _percentile(samples, 0.5),
            "p95_s": _percentile(samples, 0.95),
            "samples": len(samples),
        }
        return {
            "runs_by_status": runs_by_status,
            "queue_depth": waiting,
            "inflight": running,
            "completed": runs_by_status.get(ClusterRunStatus.COMPLETED.value, 0),
            "failed": runs_by_status.get(ClusterRunStatus.FAILED.value, 0),
            "cancelled": runs_by_status.get(ClusterRunStatus.CANCELLED.value, 0),
            "preempted_total": self._store.sum_preempts(),
            "lease_loss_total": self._store.sum_attempts(),
            "cancel_latency": cancel_latency,
            "workers": self._worker_summary(),
            "leader": self._leader_summary(),
            "ledger": self._store.ledger_snapshot(limit=_MAX_LEDGER_SCOPES),
        }

    def _worker_summary(self) -> dict[str, Any]:
        workers = self._store.live_workers()
        by_role: dict[str, int] = {}
        profiles: dict[str, int] = {}
        for w in workers:
            by_role[w["role"]] = by_role.get(w["role"], 0) + 1
            for profile, slots in (w.get("profiles") or {}).items():
                if slots > 0:
                    profiles[profile] = profiles.get(profile, 0) + int(slots)
        return {"live": len(workers), "by_role": by_role,
                "profile_slots": profiles}

    def _leader_summary(self) -> Optional[dict[str, Any]]:
        leaders = [
            w for w in self._store.live_workers(role="coordinator", within_s=60)
        ]
        return {"count": len(leaders),
                "ids": [w["worker_id"] for w in leaders][:8]}
