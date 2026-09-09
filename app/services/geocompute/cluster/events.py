"""GeoCompute V7 分布式执行事件（wave 16 核心，01-architecture.md §2.5）。

有界 observability trace 的事实源（``geocompute_run_events`` 表）：

- **分层预算**：节点级事件（``node_*``）per-run ≤``MAX_NODE_EVENTS_PER_RUN``；
  run 级/终态/治理事件**豁免** —— 终态可见性不因节点事件洪泛丢失；
- **fail-open 钉死**：append 独立短事务；任何失败（含 run 行已被 retention
  purge 后的孤儿 append）= 丢弃 + metric，绝不抛进节点执行路径 ——
  事件是尽力而为的 trace，**终态证据 of record 仍是 geocompute_run_evidence**；
- **per-run 单调序 = 全局自增 id**（无稠密 seq 分配竞争面）；断点续读
  游标 = after_id；
- 写入方：coordinator（run 生命周期）+ worker 任务体（run_id 经 task_kwargs
  显式穿透 —— Celery 边界丢 contextvars）。

session factory 可注入（与 cluster.store 同一测试惯例）。
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any, Callable, Optional

from sqlalchemy import func, select

logger = logging.getLogger(__name__)

from app.models.db_model import GeoComputeRunEvent as _Event
from app.services.geocompute.cluster.store import (
    _utcnow,
    session_factory as _store_session_factory,
)

#: 节点级事件 per-run 上界（append 前 COUNT；超限丢弃 + metric ——
#: 证据只丢可观测性，不丢终态）。
MAX_NODE_EVENTS_PER_RUN = 512

#: 读窗口上界（单页）；断点续读由 after_id 游标承担。
MAX_EVENTS_PAGE = 200

#: 事件词表（封闭；append 侧强校验 —— 开放词表会让读投影/OTel 映射失效）。
EVENT_VOCABULARY: frozenset[str] = frozenset({
    "run_started",
    "node_dispatched", "node_started", "node_output_ready",
    "node_completed", "node_reused", "node_failed", "node_cancelled",
    "node_lost",
    "run_completed", "run_failed", "run_cancelled", "run_preempted",
    "waiting_resource", "worker_cache_hit", "straggler_detected",
})

#: 豁免节点级预算的事件（全 run ≤~10 条：run 级终态 + 治理可见性）。
_BUDGET_EXEMPT: frozenset[str] = frozenset({
    "run_started", "run_completed", "run_failed", "run_cancelled",
    "run_preempted", "waiting_resource", "straggler_detected",
})

#: 有界进程内计数（metrics 投影；无 per-run/per-user 维度）。
_counters_lock = threading.Lock()
_counters: dict[str, int] = {}


def _bump(metric: str) -> None:
    with _counters_lock:
        _counters[metric] = _counters.get(metric, 0) + 1


def counters_snapshot() -> dict[str, int]:
    with _counters_lock:
        return dict(_counters)


def _default_factory():
    return _store_session_factory()


#: 可注入会话工厂（测试指向临时库）。
session_factory: Callable[[], Any] = _default_factory


class RunEventStore:
    """run 事件的 append/读投影门面（全部有界、全部 fail-open）。"""

    def __init__(self, factory: Optional[Callable[[], Any]] = None):
        self._factory = factory or session_factory

    def append(
        self,
        run_id: str,
        event: str,
        *,
        node_id: Optional[str] = None,
        worker_id: Optional[str] = None,
        attempt: Optional[int] = None,
        status: Optional[str] = None,
        rows: Optional[int] = None,
        bytes_: Optional[int] = None,
        error_code: Optional[str] = None,
    ) -> bool:
        """追加一条事件（尽力而为；返回是否落库）。

        失败语义（架构钉死）：词表外事件 / 预算超限 / run 行缺席（retention
        后的孤儿 append）/ DB 故障 → False + 有界计数，**绝不抛出**。
        """
        if event not in EVENT_VOCABULARY:
            _bump("event_rejected_invalid")
            return False
        try:
            with self._factory() as db:
                if event not in _BUDGET_EXEMPT:
                    used = db.execute(
                        select(func.count())
                        .select_from(_Event)
                        .where(_Event.run_id == run_id,
                               _Event.event.like("node_%"))
                    ).scalar_one()
                    if int(used) >= MAX_NODE_EVENTS_PER_RUN:
                        _bump("event_budget_exhausted_total")
                        return False
                db.add(_Event(
                    run_id=run_id,
                    event=event,
                    node_id=(node_id or None),
                    worker_id=(worker_id or None),
                    attempt=attempt,
                    status=(status or None),
                    rows=rows,
                    bytes_=bytes_,
                    error_code=(error_code or None),
                ))
                db.commit()
                return True
        except Exception:  # noqa: BLE001 - fail-open：观测绝不倒灌控制面
            _bump("event_append_failed_total")
            return False

    def exists(self, run_id: str, event: str, *,
               node_id: Optional[str] = None) -> bool:
        """存在性检查（waiting_resource 一次性去重；失败按不存在处理）。"""
        try:
            with self._factory() as db:
                q = select(_Event.id).where(
                    _Event.run_id == run_id, _Event.event == event
                )
                if node_id is not None:
                    q = q.where(_Event.node_id == node_id)
                return db.execute(q.limit(1)).scalar_one_or_none() is not None
        except Exception:  # noqa: BLE001
            return False

    def window(
        self,
        run_id: str,
        *,
        after_id: int = 0,
        limit: int = MAX_EVENTS_PAGE,
    ) -> list[dict[str, Any]]:
        """(run_id, after_id) 起的有序读窗口（断点续读；有界页）。"""
        limit = max(1, min(int(limit), MAX_EVENTS_PAGE))
        try:
            with self._factory() as db:
                rows = db.execute(
                    select(_Event)
                    .where(_Event.run_id == run_id, _Event.id > max(0, int(after_id)))
                    .order_by(_Event.id.asc())
                    .limit(limit)
                ).scalars().all()
                return [_projection(r) for r in rows]
        except Exception:  # noqa: BLE001 - trace 读失败 = 空窗口（诚实 404 语义在调用方）
            return []

    def progress_projection(self, run_id: str) -> dict[str, Any]:
        """读时进度投影（天然幂等跨 attempt —— DISTINCT 节点去重）。

        total 由调用方从 plan_snapshot 派生（本方法只报已 settle 节点）。
        """
        try:
            with self._factory() as db:
                rows = db.execute(
                    select(_Event.node_id, _Event.event, _Event.id)
                    .where(
                        _Event.run_id == run_id,
                        _Event.event.in_([
                            "node_completed", "node_reused",
                            "node_cancelled", "node_failed",
                        ]),
                    )
                    .order_by(_Event.id.asc())
                ).all()
            # 每节点取**最新**一条终局事件（重跑后失败覆盖此前完成）
            latest: dict[str, str] = {}
            for node_id, event, _pk in rows:
                if node_id:
                    latest[node_id] = event
            done = {
                nid for nid, ev in latest.items()
                if ev in {"node_completed", "node_reused"}
            }
            return {
                "settled": len(latest),
                "done": len(done),
                "failed": sum(
                    1 for ev in latest.values() if ev == "node_failed"
                ),
            }
        except Exception:  # noqa: BLE001
            return {"settled": 0, "done": 0, "failed": 0}

    def purge_older_than(self, *, older_than_s: float, limit: int = 256) -> int:
        """独立 TTL 清理（孤儿事件兜底 —— 不依赖 run 行存活；每 tick 有界批）。"""
        cutoff = _utcnow() - timedelta(seconds=max(60.0, float(older_than_s)))
        try:
            with self._factory() as db:
                ids = db.execute(
                    select(_Event.id)
                    .where(_Event.created_at < cutoff)
                    .order_by(_Event.created_at.asc())
                    .limit(max(1, int(limit)))
                ).scalars().all()
                if not ids:
                    return 0
                from sqlalchemy import delete

                deleted = db.execute(
                    delete(_Event).where(_Event.id.in_(ids))
                ).rowcount
                db.commit()
                return int(deleted or 0)
        except Exception:  # noqa: BLE001 - 清理失败下轮再试
            return 0


def _projection(row: Any) -> dict[str, Any]:
    return {
        "id": int(row.id),
        "run_id": row.run_id,
        "event": row.event,
        "node_id": row.node_id,
        "worker_id": row.worker_id,
        "attempt": row.attempt,
        "status": row.status,
        "rows": row.rows,
        "bytes": row.bytes_,
        "error_code": row.error_code,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
    }
