"""Workflow Runtime V6 —— worker 能力注册表（调度域自有事实）。

Phase D「worker capability registry」：任何执行 workflow 节点的进程
（API 内 driver / 独立 worker）注册能力并维持心跳。调度面据此做：

- **准入**（resource reservation 的轻量形态）：节点声明所需 profile，
  ``list_active_workers(profile=...)`` 回答「有没有活 worker 能跑」；
- **local/durable 派发决策**：``dispatch.py`` 消费；
- **dead worker recovery**：心跳过期 → ``status='stale'``；其在飞节点
  **不**由本表接管 —— 节点租约（workflow_instance_nodes.lease_expires_at）
  是执行活性的独立真相，两级解耦（worker 表挂了不代表节点表要动，
  反之亦然）。

能力词表（capabilities JSON，全部有界）：
``cpu``（核）、``mem_mb``、``gpu``（卡数）、``profiles``（{profile: 槽位}，
profile 词表 = geocompute EXECUTION_QUEUE_PROFILES —— 队列路由语义单一）、
``io_mbps``（IO 带宽上界提示）、``backends``（支持的执行后端
["inprocess","durable"]）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import timedelta
from typing import Any, Dict, List, Optional

import sqlalchemy as sa

from app.services.workflow_runtime.store import StoreUnavailable, _utcnow

logger = logging.getLogger(__name__)

#: worker 心跳默认 TTL（秒）；过期 = stale（死亡）。
DEFAULT_WORKER_TTL_S = 90.0
#: 注册表查询上界。
MAX_WORKERS_LISTED = 64
#: 负载投影键（有界白名单）。
_LOAD_KEYS = ("in_flight", "queue_depth", "mem_used_mb")


def new_worker_id(role: str = "worker") -> str:
    return f"{role}-{uuid.uuid4().hex[:12]}"


def default_capabilities() -> Dict[str, Any]:
    """本进程能力自检（保守估计；不打爆机器的调度前提）。"""
    caps: Dict[str, Any] = {"cpu": 1, "mem_mb": 1024, "gpu": 0,
                            "profiles": {}, "io_mbps": 0,
                            "backends": ["inprocess"]}
    try:
        import os

        caps["cpu"] = max(1, len(os.sched_getaffinity(0)))
    except (AttributeError, OSError):  # pragma: no cover — Windows/受限沙箱
        try:
            import os

            caps["cpu"] = max(1, os.cpu_count() or 1)
        except Exception:  # noqa: BLE001
            pass
    return caps


def _cap(capabilities: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """能力投影（词表白名单 + 值钳制；防自授无界资源）。"""
    caps = dict(capabilities or {})
    profiles_raw = caps.get("profiles") or {}
    from app.services.geocompute.durable import EXECUTION_QUEUE_PROFILES

    profiles = {
        str(p)[:24]: max(0, min(int(n), 64))
        for p, n in list(profiles_raw.items())[:8]
        if str(p) in EXECUTION_QUEUE_PROFILES
    }
    try:
        from app.services.geocompute.durable import DEFAULT_QUEUE

        queue_ok = DEFAULT_QUEUE
    except Exception:  # noqa: BLE001
        queue_ok = "celery"
    out = {
        "cpu": max(0, min(int(caps.get("cpu", 1) or 0), 1024)),
        "mem_mb": max(0, min(int(caps.get("mem_mb", 1024) or 0), 2_097_152)),
        "gpu": max(0, min(int(caps.get("gpu", 0) or 0), 8)),
        "io_mbps": max(0, min(int(caps.get("io_mbps", 0) or 0), 1_000_000)),
        # 默认队列恒可用（所有全队列 worker 都消费它）
        "profiles": {**profiles, queue_ok: profiles.get(queue_ok, 1)},
        "backends": [str(b)[:16] for b in
                     (caps.get("backends") or ["inprocess"])[:4]],
    }
    return out


def _load(load: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k in _LOAD_KEYS:
        if load and k in load:
            try:
                out[k] = max(0, int(load[k]))
            except (TypeError, ValueError):
                continue
    return out


class WorkerRegistry:
    """worker 注册/心跳/活性（同步 SQLAlchemy；调用方 to_thread 卸载）。"""

    def __init__(self, factory: Optional[Any] = None):
        if factory is None:
            from app.services.workflow_runtime import store as ST

            factory = ST.session_factory
        self._factory = factory

    def register(
        self, worker_id: str, *, role: str = "worker",
        capabilities: Optional[Dict[str, Any]] = None,
        load: Optional[Dict[str, Any]] = None,
        runtime: str = "inprocess",
        locality: Optional[Dict[str, Any]] = None,
        ttl_s: float = DEFAULT_WORKER_TTL_S,
    ) -> bool:
        """注册/幂等刷新（upsert；活跃即活性证据）。"""
        from app.models.db_model import WorkflowWorkerRow

        now = _utcnow()
        try:
            with self._factory() as db:
                row = db.get(WorkflowWorkerRow, worker_id[:64])
                values = dict(
                    role="driver" if role == "driver" else "worker",
                    status="active",
                    capabilities=_cap(capabilities),
                    load=_load(load),
                    runtime=str(runtime)[:24],
                    locality=dict(locality or {}),
                    last_heartbeat_at=now,
                )
                if row is None:
                    db.add(WorkflowWorkerRow(
                        worker_id=worker_id[:64],
                        created_at=now, **values))
                else:
                    db.execute(
                        sa.update(WorkflowWorkerRow)
                        .where(WorkflowWorkerRow.worker_id == worker_id[:64])
                        .values(**values))
                db.commit()
                return True
        except Exception:  # noqa: BLE001 — 注册表故障不阻断执行面
            logger.warning("[WorkflowRuntime] worker register failed",
                           exc_info=True)
            return False

    def heartbeat(
        self, worker_id: str, *,
        load: Optional[Dict[str, Any]] = None,
        ttl_s: float = DEFAULT_WORKER_TTL_S,
    ) -> bool:
        """心跳（仅 active 可续；stale worker 复活走显式 register）。"""
        from app.models.db_model import WorkflowWorkerRow

        values: Dict[str, Any] = {"last_heartbeat_at": _utcnow()}
        if load is not None:
            values["load"] = _load(load)
        try:
            with self._factory() as db:
                updated = db.execute(
                    sa.update(WorkflowWorkerRow)
                    .where(
                        WorkflowWorkerRow.worker_id == worker_id[:64],
                        WorkflowWorkerRow.status == "active",
                    )
                    .values(**values)
                )
                db.commit()
                return bool(updated.rowcount)
        except StoreUnavailable:
            return False
        except Exception:  # noqa: BLE001
            return False

    def retire(self, worker_id: str) -> bool:
        from app.models.db_model import WorkflowWorkerRow

        try:
            with self._factory() as db:
                updated = db.execute(
                    sa.update(WorkflowWorkerRow)
                    .where(WorkflowWorkerRow.worker_id == worker_id[:64])
                    .values(status="retired", last_heartbeat_at=_utcnow())
                )
                db.commit()
                return bool(updated.rowcount)
        except Exception:  # noqa: BLE001
            return False

    def sweep_dead(self, *, ttl_s: float = DEFAULT_WORKER_TTL_S,
                   limit: int = 64) -> List[str]:
        """心跳过期 active → stale（多副本安全：条件更新幂等）。"""
        from app.models.db_model import WorkflowWorkerRow

        cutoff = _utcnow() - timedelta(seconds=max(1.0, float(ttl_s)))
        swept: List[str] = []
        try:
            with self._factory() as db:
                rows = db.query(WorkflowWorkerRow).filter(
                    WorkflowWorkerRow.status == "active",
                    sa.or_(
                        WorkflowWorkerRow.last_heartbeat_at.is_(None),
                        WorkflowWorkerRow.last_heartbeat_at < cutoff,
                    ),
                ).limit(max(1, min(int(limit), MAX_WORKERS_LISTED))).all()
                for row in rows:
                    row.status = "stale"
                    swept.append(row.worker_id)
                db.commit()
        except Exception:  # noqa: BLE001 — 清扫失败不阻断
            logger.warning("[WorkflowRuntime] worker sweep failed",
                           exc_info=True)
        return swept

    def list_active(
        self, *, profile: str = "", backend: str = "",
        limit: int = MAX_WORKERS_LISTED,
    ) -> List[Dict[str, Any]]:
        """活跃 worker（可按 profile 槽位 / 后端过滤；调度准入输入）。"""
        from app.models.db_model import WorkflowWorkerRow

        try:
            with self._factory() as db:
                rows = db.query(WorkflowWorkerRow).filter(
                    WorkflowWorkerRow.status == "active",
                ).limit(max(1, min(int(limit), MAX_WORKERS_LISTED))).all()
                out: List[Dict[str, Any]] = []
                for r in rows:
                    caps = dict(r.capabilities or {})
                    if profile:
                        slots = int((caps.get("profiles") or {})
                                    .get(profile, 0) or 0)
                        if slots <= 0:
                            continue
                    if backend and backend not in (
                            caps.get("backends") or []):
                        continue
                    out.append({
                        "worker_id": r.worker_id,
                        "role": r.role,
                        "runtime": r.runtime,
                        "capabilities": caps,
                        "load": dict(r.load or {}),
                        "locality": dict(r.locality or {}),
                        "last_heartbeat_at":
                            r.last_heartbeat_at.isoformat()
                            if r.last_heartbeat_at else "",
                    })
                return out
        except Exception:  # noqa: BLE001 — 查询失败按「无 worker」降级
            return []

    def total_active_slots(self, profile: str) -> int:
        """某 profile 的活跃槽位总量（调度准入的硬上界输入）。"""
        return sum(
            int((w["capabilities"].get("profiles") or {})
                .get(profile, 0) or 0)
            for w in self.list_active(profile=profile))
