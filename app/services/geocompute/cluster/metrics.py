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
    RESOURCE_DIMENSIONS,
    ClusterRunStatus,
)
from app.services.geocompute.cluster.store import ClusterRunStore

#: 取消/让出延迟样本上界（环形；无基数爆炸）。
_MAX_LATENCY_SAMPLES = 128

#: 账本投影暴露的 scope 数上界。
_MAX_LEDGER_SCOPES = 20

_lock = threading.Lock()
_cancel_latencies: deque[float] = deque(maxlen=_MAX_LATENCY_SAMPLES)
_queue_waits: deque[float] = deque(maxlen=_MAX_LATENCY_SAMPLES)


def record_cancellation_latency(seconds: float) -> None:
    """cancel 请求 → 终态 的观测延迟（任意进程本地采样）。"""
    if seconds < 0:
        return
    with _lock:
        _cancel_latencies.append(float(seconds))


def record_queue_wait_s(seconds: float) -> None:
    """submit → 首次认领 的排队延迟（V7；有界采样，分位数暴露）。"""
    if seconds < 0:
        return
    with _lock:
        _queue_waits.append(float(seconds))


# ── V8：资源拒绝 / OOM 避免观察（进程内有界计数；词表维度防基数爆炸）──

_resource_rejections: dict[str, int] = {}
_oom_avoided = 0
_gpu_fallbacks = 0


def record_resource_rejection(dim: str) -> None:
    """enforcing 账本按维拒绝认领（调度决策可观测；词表外维丢弃）。"""
    if dim not in RESOURCE_DIMENSIONS:
        return
    with _lock:
        _resource_rejections[dim] = _resource_rejections.get(dim, 0) + 1


def resource_rejections_snapshot() -> dict[str, int]:
    with _lock:
        return dict(_resource_rejections)


def record_oom_avoided() -> None:
    """内存维拒绝 = 一次「先启动再 OOM」被预防（V8 核心承诺的量化）。"""
    global _oom_avoided
    with _lock:
        _oom_avoided += 1


def oom_avoided_snapshot() -> int:
    with _lock:
        return _oom_avoided


def record_gpu_fallback() -> None:
    """GPU run 剥离 gpu 要求改派 CPU（诚实可见的降级决策）。"""
    global _gpu_fallbacks
    with _lock:
        _gpu_fallbacks += 1


def gpu_fallbacks_snapshot() -> int:
    with _lock:
        return _gpu_fallbacks


# ── V8 Phase E/H：spill 与传输观测 ──────────────────────────────

_spill_count = 0
_spill_bytes = 0
_spill_rehydrate = {True: 0, False: 0}


def record_spill(size_bytes: int) -> None:
    """checkpoint 大载荷落盘（一次 spill = 一次内存压力规避）。"""
    global _spill_count, _spill_bytes
    if size_bytes < 0:
        return
    with _lock:
        _spill_count += 1
        _spill_bytes += int(size_bytes)


def record_spill_rehydrate(ok: bool) -> None:
    """spill 条目重读（True=命中回填；False=失败退化为重算）。"""
    with _lock:
        _spill_rehydrate[bool(ok)] += 1


def spill_snapshot() -> dict[str, int]:
    with _lock:
        return {"count": _spill_count, "bytes": _spill_bytes,
                "rehydrate_hits": _spill_rehydrate[True],
                "rehydrate_misses": _spill_rehydrate[False]}


def _percentile(samples: list[float], q: float) -> Optional[float]:
    if not samples:
        return None
    ordered = sorted(samples)
    idx = min(len(ordered) - 1, max(0, int(q * (len(ordered) - 1))))
    return round(ordered[idx], 4)


class ClusterMetrics:
    """集群快照聚合器（store 只读查询的组合）。"""

    def snapshot(self) -> dict[str, Any]:
        """完整集群快照（V8：≤ ~20 个有界聚合查询；全部命中索引/常量
        投影，无行扫描；封闭词表防基数爆炸）。"""
        runs_by_status = self._store.count_runs_by_status()
        workers = self._store.live_workers()
        waiting = sum(
            runs_by_status.get(s.value, 0) for s in DISPATCHABLE_STATUSES
        )
        running = runs_by_status.get(ClusterRunStatus.LEASED.value, 0) + runs_by_status.get(
            ClusterRunStatus.RUNNING.value, 0
        )
        with _lock:
            samples = list(_cancel_latencies)
            waits = list(_queue_waits)
        cancel_latency = {
            "p50_s": _percentile(samples, 0.5),
            "p95_s": _percentile(samples, 0.95),
            "samples": len(samples),
        }
        queue_wait = {
            "p50_s": _percentile(waits, 0.5),
            "p95_s": _percentile(waits, 0.95),
            "samples": len(waits),
        }
        from app.services.geocompute.cluster.events import counters_snapshot

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
            "queue_wait": queue_wait,
            "waiting_by_profile": self._store.waiting_profiles(),
            "events_counters": counters_snapshot(),
            "workers": self._worker_summary(),
            "leader": self._leader_summary(),
            "ledger": self._store.ledger_snapshot(limit=_MAX_LEDGER_SCOPES),
            # V8：资源拒绝/OOM 避免/GPU 降级（调度决策可观测性）
            "resource_rejections": resource_rejections_snapshot(),
            "oom_avoided": oom_avoided_snapshot(),
            "gpu_fallbacks": gpu_fallbacks_snapshot(),
            "spill": spill_snapshot(),
            # ── V8 Phase H：transfer / cache / lineage / utilization ──
            "transfer": {"bytes_total": self._sum_bytes()},
            "cache": {"worker_cache_hits": self._count_kind("worker_cache_hit")},
            "lineage": {
                "node_completed": self._count_kind("node_completed"),
                "node_reused": self._count_kind("node_reused"),
                "node_lost": self._count_kind("node_lost"),
                "partition_planned": self._count_kind("partition_planned"),
                "speculative_dispatched": self._count_kind(
                    "speculative_dispatch"),
                "poison_quarantined": self._count_kind("poison_quarantined"),
            },
            "utilization": self._utilization_summary(workers=workers),
            "quarantine": self._quarantine_summary(),
        }

    def __init__(self, store: Optional[ClusterRunStore] = None):
        self._store = store or ClusterRunStore()
        self._event_store = self._make_event_store()

    @staticmethod
    def _make_event_store():
        try:
            from app.services.geocompute.cluster.events import RunEventStore

            return RunEventStore()
        except Exception:  # noqa: BLE001 - 观测缺席 = 空投影
            return None

    def _count_kind(self, event: str) -> int:
        if self._event_store is None:
            return 0
        return self._event_store.count_kind(event)

    def _sum_bytes(self) -> int:
        if self._event_store is None:
            return 0
        return self._event_store.sum_bytes()

    def _utilization_summary(self, *, workers=None) -> dict[str, Any]:
        """worker 利用率：账本在租 units ÷ 存活 worker 槽位总量（有界）。

        ``workers``：snapshot 主体已取的 live 投影（复用，免重复查询）。
        """
        try:
            if workers is None:
                workers = self._store.live_workers()
            capacity = 0
            for w in workers:
                for slots in (w.get("profiles") or {}).values():
                    capacity += max(0, int(slots or 0))
            reserved = 0
            for entry in self._store.ledger_snapshot(limit=_MAX_LEDGER_SCOPES):
                if entry.get("scope_key") == "global":
                    reserved = int(entry.get("usage_units") or 0)
            ratio = round(reserved / capacity, 3) if capacity > 0 else None
            return {"reserved_units": reserved, "capacity_units": capacity,
                    "ratio": ratio}
        except Exception:  # noqa: BLE001
            return {"reserved_units": 0, "capacity_units": 0, "ratio": None}

    def _quarantine_summary(self) -> list[dict[str, Any]]:
        try:
            from app.services.geocompute.cluster.quarantine import (
                get_quarantine,
            )

            return get_quarantine().snapshot(limit=10)
        except Exception:  # noqa: BLE001
            return []

    def _worker_summary(self) -> dict[str, Any]:
        workers = self._store.live_workers()
        by_role: dict[str, int] = {}
        profiles: dict[str, int] = {}
        gpu_workers = 0
        for w in workers:
            by_role[w["role"]] = by_role.get(w["role"], 0) + 1
            for profile, slots in (w.get("profiles") or {}).items():
                if slots > 0:
                    profiles[profile] = profiles.get(profile, 0) + int(slots)
            cap = w.get("capability") or {}
            if isinstance(cap, dict) and int(cap.get("gpu_count") or 0) > 0:
                gpu_workers += 1
        return {"live": len(workers), "by_role": by_role,
                "profile_slots": profiles, "gpu_workers": gpu_workers}

    def _leader_summary(self) -> Optional[dict[str, Any]]:
        leaders = [
            w for w in self._store.live_workers(role="coordinator", within_s=60)
        ]
        return {"count": len(leaders),
                "ids": [w["worker_id"] for w in leaders][:8]}
