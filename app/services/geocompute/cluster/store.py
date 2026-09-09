"""GeoCompute V6 cluster 控制面持久存储（wave 2）。

全部并发控制都是**单语句条件 UPDATE**（CAS；SQLite/PG 同语义），
配短事务 —— 绝无 SELECT-then-UPDATE 的 TOCTOU 写路径：

- ``claim_lease``：queued/preempted → leased（epoch+1），同一事务内做
  集群账本 reserve（enforcing 拒绝即整体回滚）；
- ``finish_run`` / ``reclaim_expired``：epoch+coordinator CAS 转移 terminal/
  回队，同一事务内按行上 reserved_* 精确归还账本（CAS 保证 exactly-once，
  双 coordinator 竞争只有一方生效）；
- ``request_cancel`` / ``request_yield``：幂等旗标（COALESCE 语义）；
- leadership：coordinator 行上的 lease epoch CAS（任一时刻一个调度者）。

会话工厂可注入（与 durable.py / run_evidence.py 同一测试惯例）。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

from sqlalchemy import delete, func, select, update

from app.models.db_model import (
    GeoComputeClusterRun as _Run,
    GeoComputeClusterWorker as _Worker,
    GeoComputeResourceUsage as _Usage,
)

from app.services.geocompute.cluster.contracts import (
    DEFAULT_MAX_RUN_ATTEMPTS,
    DISPATCHABLE_STATUSES,
    LEASED_STATUSES,
    MAX_PLAN_SNAPSHOT_BYTES,
    MAX_PREEMPTS,
    TERMINAL_STATUSES,
    ClusterRunStatus,
    ResourceClaim,
    RunPriority,
    run_error_for_reclaim,
)
from app.services.geocompute.cluster.errors import (
    ClusterBackpressureError,
    PlanSnapshotTooLargeError,
)

logger = logging.getLogger(__name__)

__all__ = [
    "ClusterBackpressureError",
    "ClusterLedger",
    "ClusterRunStore",
    "PlanSnapshotTooLargeError",
    "hash_scope_key",
    "new_run_id",
]


def _default_session_factory():
    # 返回 **Session 实例**（jobs 层同一纪律；sessionmaker 在 SQLAlchemy 2.0
    # 无上下文协议 —— run_evidence/reuse_index/durable 的同因缺陷见各自修复）。
    from app.core.database import SessionLocal

    return SessionLocal()


#: 可注入的会话工厂（测试替换为临时 SQLite 工厂）。
session_factory: Callable[[], Any] = _default_session_factory


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_run_id() -> str:
    """与 engine 内存注册表同词表（"gexec-<hex12>"）。"""
    return f"gexec-{uuid.uuid4().hex[:12]}"


def hash_scope_key(raw: Optional[str], prefix: str) -> Optional[str]:
    """身份 → 账本/行上的域键（owner_scope 同款哈希纪律：绝不明文入控制面）。"""
    if not raw:
        return None
    import hashlib

    return prefix + hashlib.sha1(str(raw).encode(), usedforsecurity=False).hexdigest()[:12]


class ClusterRunStore:
    """geocompute_runs / geocompute_workers / geocompute_resource_usage 的 CAS 门面。"""

    def __init__(self, factory: Optional[Callable[[], Any]] = None):
        self._factory = factory or session_factory

    # ------------------------------------------------------------- runs

    def create_run(
        self,
        *,
        plan_snapshot: dict[str, Any],
        plan_fingerprint: str,
        owner_scope: str,
        session_id: Optional[str] = None,
        creator_id: Optional[str] = None,
        org_id: Optional[str] = None,
        project_id: Optional[str] = None,
        tenant_raw: Optional[str] = None,
        project_raw: Optional[str] = None,
        priority: int = RunPriority.NORMAL,
        required_profiles: Optional[list[str]] = None,
        resource_request: Optional[dict[str, Any]] = None,
        max_queued_per_tenant: int = 32,
        max_queued_total: int = 256,
    ) -> str:
        """登记 cluster run（submit 路径）。返回 run_id。

        有界背压：租户/全局 queued 计数超限 → ``ClusterBackpressureError``
        （防队列无界增长的自 DoS）。plan 快照超预算 →
        ``PlanSnapshotTooLargeError``。``resource_request`` ≤1KB（V7 资源
        envelope 投影；超界截断为 None —— envelope 缺席退回 V6 语义）。
        """
        encoded = json.dumps(plan_snapshot, ensure_ascii=False, default=str)
        if len(encoded.encode("utf-8")) > MAX_PLAN_SNAPSHOT_BYTES:
            raise PlanSnapshotTooLargeError(
                f"plan snapshot exceeds {MAX_PLAN_SNAPSHOT_BYTES} bytes",
                details={"limit_bytes": MAX_PLAN_SNAPSHOT_BYTES},
            )
        tenant_key = hash_scope_key(tenant_raw, "t:")
        project_key = hash_scope_key(project_raw, "p:")
        # V7：资源 envelope 写入侧钳制（≤1KB；超界截断为 None = V6 语义）
        if resource_request is not None:
            try:
                if len(json.dumps(
                    resource_request, ensure_ascii=False, default=str
                ).encode("utf-8")) > 1024:
                    resource_request = None
            except (TypeError, ValueError):
                resource_request = None
        run_id = new_run_id()
        with self._factory() as db:
            tenant_count = 0
            if tenant_key is not None:
                tenant_count = db.execute(
                    select(func.count())
                    .select_from(_Run)
                    .where(
                        _Run.tenant_key == tenant_key,
                        _Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]),
                    )
                ).scalar_one()
            if tenant_count >= max_queued_per_tenant:
                raise ClusterBackpressureError(
                    "tenant queue is at capacity",
                    details={"tenant_queued": tenant_count,
                             "limit": max_queued_per_tenant},
                )
            total_count = db.execute(
                select(func.count())
                .select_from(_Run)
                .where(_Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]))
            ).scalar_one()
            if total_count >= max_queued_total:
                raise ClusterBackpressureError(
                    "cluster queue is at capacity",
                    details={"cluster_queued": total_count,
                             "limit": max_queued_total},
                )
            db.add(_Run(
                run_id=run_id,
                owner_scope=owner_scope,
                status=ClusterRunStatus.QUEUED.value,
                plan_fingerprint=plan_fingerprint,
                plan_snapshot=plan_snapshot,
                session_id=session_id,
                creator_id=creator_id,
                org_id=org_id,
                project_id=project_id,
                tenant_key=tenant_key,
                project_key=project_key,
                priority=RunPriority.coerce(priority),
                required_profiles=sorted(set(required_profiles or [])),
                resource_request=resource_request,
            ))
            db.commit()
        return run_id

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._factory() as db:
            row = db.execute(
                select(_Run).where(_Run.run_id == run_id)
            ).scalar_one_or_none()
            return _run_projection(row) if row is not None else None

    def get_run_owned(self, run_id: str, owner_scope: str) -> Optional[dict[str, Any]]:
        """owner 域隔离读取（不符一律 None —— 与 get_run 读纪律一致）。"""
        row = self.get_run(run_id)
        if row is None or row["owner_scope"] != owner_scope:
            return None
        return row

    def list_runs(
        self,
        owner_scope: str,
        *,
        statuses: Optional[Iterable[str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """owner 域列表（id 降序 = 提交序倒序；limit 钳 ≤100）。"""
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))
        with self._factory() as db:
            q = select(_Run).where(_Run.owner_scope == owner_scope)
            if statuses:
                q = q.where(_Run.status.in_([str(s) for s in statuses]))
            q = q.order_by(_Run.id.desc()).limit(limit).offset(offset)
            rows = db.execute(q).scalars().all()
            return [_run_projection(r) for r in rows]

    def claim_lease(
        self,
        run_id: str,
        *,
        coordinator_id: str,
        ttl_s: float,
        max_attempts: int = DEFAULT_MAX_RUN_ATTEMPTS,
        ledger: Optional["ClusterLedger"] = None,
    ) -> Optional[int]:
        """认领：queued/preempted → leased（epoch+1）。

        同一事务内 reserve 集群账本（run 粒度）；enforcing 账本拒绝 →
        整体回滚返回 None（背压：这个 run 留队，不制造超卖）。attempts 已
        耗尽（≥ max_attempts，理论上有 reclaim 兜底）同样拒绝认领。
        返回新 epoch；竞争失败/不可派发 → None。
        """
        # 账本 scope 行预建（独立事务；业务事务内只读检查，无 session 中毒）
        if ledger is not None:
            internal = self.get_run_internal(run_id)
            if internal is not None:
                ledger.ensure_scopes(
                    _scope_keys_for("global", internal.get("tenant_key"),
                                    internal.get("project_key")),
                    factory=self._factory,
                )
        with self._factory() as db:
            row = db.execute(
                select(_Run).where(_Run.run_id == run_id)
            ).scalar_one_or_none()
            if row is None or row.attempts >= max_attempts:
                return None
            now = _utcnow()
            new_epoch = row.lease_epoch + 1
            claimed = db.execute(
                update(_Run)
                .where(
                    _Run.id == row.id,
                    _Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]),
                    _Run.lease_epoch == row.lease_epoch,
                    # 已请求取消的 run 不再认领（cancel sweep 负责收敛终态）
                    _Run.cancel_requested_at.is_(None),
                )
                .values(
                    status=ClusterRunStatus.LEASED.value,
                    lease_epoch=new_epoch,
                    coordinator_id=coordinator_id,
                    lease_expires_at=now + timedelta(seconds=ttl_s),
                    heartbeat_at=now,
                    dispatch_seq=row.id,
                    updated_at=now,
                )
            ).rowcount
            if not claimed:
                return None
            if ledger is not None:
                claims = _claims_for_row(row, units=1)
                ok = ledger.reserve_claims(db, claims)
                if not ok:
                    db.rollback()
                    return None
                db.execute(
                    update(_Run)
                    .where(_Run.id == row.id)
                    .values(reserved_rows=claims["global"].rows,
                            reserved_bytes=claims["global"].bytes,
                            reserved_units=claims["global"].units)
                )
            db.commit()
            return new_epoch
        return None

    def mark_running(self, run_id: str, *, epoch: int) -> bool:
        """leased → running（expect_epoch；执行体真正启动时）。"""
        with self._factory() as db:
            rowcount = db.execute(
                update(_Run)
                .where(
                    _Run.run_id == run_id,
                    _Run.lease_epoch == epoch,
                    _Run.status == ClusterRunStatus.LEASED.value,
                )
                .values(status=ClusterRunStatus.RUNNING.value,
                        started_at=_utcnow(), updated_at=_utcnow())
            ).rowcount
            db.commit()
            return bool(rowcount)

    def heartbeat_run(self, run_id: str, *, epoch: int, ttl_s: float) -> bool:
        """fencing 心跳：epoch 不匹配（lease 已易主）→ False（执行体必须停写）。"""
        with self._factory() as db:
            now = _utcnow()
            rowcount = db.execute(
                update(_Run)
                .where(
                    _Run.run_id == run_id,
                    _Run.lease_epoch == epoch,
                    _Run.status.in_([s.value for s in LEASED_STATUSES]),
                )
                .values(heartbeat_at=now,
                        lease_expires_at=now + timedelta(seconds=ttl_s),
                        updated_at=now)
            ).rowcount
            db.commit()
            return bool(rowcount)

    def finish_run(
        self,
        run_id: str,
        *,
        epoch: int,
        status: ClusterRunStatus,
        error_code: Optional[str] = None,
        ledger: Optional["ClusterLedger"] = None,
    ) -> bool:
        """执行体记录终态（或 RUNNING→PREEMPTED 的让出转移）。

        CAS（epoch+coordinator 时段）保证只有当前 lease 持有者能写；
        失败 = lease 已丢失/已 reclaim → False（执行体诚实丢弃结果）。
        同一事务内精确归还账本预留。
        """
        if status not in TERMINAL_STATUSES and status != ClusterRunStatus.PREEMPTED:
            return False
        if ledger is not None:
            internal = self.get_run_internal(run_id)
            if internal is not None:
                ledger.ensure_scopes(
                    _scope_keys_for("global", internal.get("tenant_key"),
                                    internal.get("project_key")),
                    factory=self._factory,
                )
        with self._factory() as db:
            row = db.execute(
                select(_Run).where(_Run.run_id == run_id)
            ).scalar_one_or_none()
            if row is None or row.lease_epoch != epoch:
                return False
            now = _utcnow()
            values = {
                "status": status.value,
                "error_code": error_code,
                "updated_at": now,
                "terminal_at": now if status in TERMINAL_STATUSES else None,
            }
            if status == ClusterRunStatus.PREEMPTED:
                # 抢占是治理行为不是失败：单独计数（livelock 保险丝的数据源）
                values["preempts"] = _Run.preempts + 1
                if int(row.preempts or 0) + 1 >= MAX_PREEMPTS:
                    # 保险丝（contracts 承诺的语义）：抢占次数达到上界 →
                    # 终态 failed[PREEMPT_EXHAUSTED]，绝不无限自噬。
                    values["status"] = ClusterRunStatus.FAILED.value
                    values["error_code"] = "PREEMPT_EXHAUSTED"
                    values["terminal_at"] = now
                    status = ClusterRunStatus.FAILED
            rowcount = db.execute(
                update(_Run)
                .where(
                    _Run.id == row.id,
                    _Run.lease_epoch == epoch,
                    _Run.status.in_([s.value for s in LEASED_STATUSES]),
                )
                .values(**values)
            ).rowcount
            if not rowcount:
                return False
            if ledger is not None:
                # 预留生命周期 = lease 生命周期：终态与 PREEMPTED（回队重排）
                # 都在此精确归还 —— 下次认领重新 reserve。否则 requeue 覆写
                # reserved_* 会把上一轮预留永久悬空（round2 C2 泄漏）。
                ledger.release_claims(db, _reserved_claims_from_row(row))
                db.execute(
                    update(_Run)
                    .where(_Run.id == row.id)
                    .values(reserved_rows=0, reserved_bytes=0, reserved_units=0)
                )
            db.commit()
            return True

    def requeue_preempted(self, run_id: str, *, epoch: int) -> bool:
        """PREEMPTED → QUEUED（由 coordinator 在记录 PREEMPTED 后立即执行；
        分两步是为了让客户端能看到「被抢占」可驻留态）。

        **必须**清除 yield_requested_at：旗标是让出请求的一次性指令，
        残留会让下一 attempt 的心跳立即再次让出 → 无限抢占自噬
        （round2 review C1 livelock）。
        """
        with self._factory() as db:
            rowcount = db.execute(
                update(_Run)
                .where(
                    _Run.run_id == run_id,
                    _Run.lease_epoch == epoch,
                    _Run.status == ClusterRunStatus.PREEMPTED.value,
                )
                .values(status=ClusterRunStatus.QUEUED.value,
                        coordinator_id=None,
                        lease_expires_at=None,
                        yield_requested_at=None,
                        updated_at=_utcnow())
            ).rowcount
            db.commit()
            return bool(rowcount)

    def reclaim_expired(
        self,
        *,
        now: Optional[datetime] = None,
        limit: int = 16,
        max_attempts: int = DEFAULT_MAX_RUN_ATTEMPTS,
        ledger: Optional["ClusterLedger"] = None,
    ) -> list[dict[str, str]]:
        """reclaim 过期 lease：attempt 未耗尽 → queued（重试），耗尽 →
        failed[WORKER_LOSS]。账本归还与状态转移同事务（exactly-once）。

        返回 [{run_id, outcome: requeued|failed}]。
        """
        now = now or _utcnow()
        outcomes: list[dict[str, str]] = []
        if ledger is not None:
            with self._factory() as db:
                keys = {
                    k
                    for r in db.execute(
                        select(_Run.tenant_key, _Run.project_key)
                        .where(_Run.status.in_(
                            [s.value for s in LEASED_STATUSES]))
                        .limit(max(1, int(limit)) * 4)
                    ).all()
                    for k in _scope_keys_for("global", r[0], r[1])
                }
                ledger.ensure_scopes(keys, factory=self._factory)
        with self._factory() as db:
            rows = db.execute(
                select(_Run)
                .where(
                    _Run.status.in_([s.value for s in LEASED_STATUSES]),
                    _Run.lease_expires_at.is_not(None),
                    _Run.lease_expires_at < now,
                )
                .order_by(_Run.lease_expires_at.asc())
                .limit(max(1, int(limit)))
            ).scalars().all()
            for row in rows:
                new_attempts = row.attempts + 1
                error_code = run_error_for_reclaim(new_attempts, max_attempts)
                to_status = (
                    ClusterRunStatus.FAILED if error_code
                    else ClusterRunStatus.QUEUED
                )
                rowcount = db.execute(
                    update(_Run)
                    .where(
                        _Run.id == row.id,
                        _Run.status.in_([s.value for s in LEASED_STATUSES]),
                        _Run.lease_epoch == row.lease_epoch,
                    )
                    .values(
                        status=to_status.value,
                        attempts=new_attempts,
                        error_code=error_code,
                        coordinator_id=None,
                        lease_expires_at=None,
                        updated_at=now,
                        terminal_at=now if error_code else None,
                    )
                ).rowcount
                if not rowcount:
                    continue  # 被并发转移（finish/另一 reclaim）→ 让给对方
                if ledger is not None:
                    ledger.release_claims(db, _reserved_claims_from_row(row))
                    db.execute(
                        update(_Run)
                        .where(_Run.id == row.id)
                        .values(reserved_rows=0, reserved_bytes=0, reserved_units=0)
                    )
                outcomes.append({
                    "run_id": row.run_id,
                    "outcome": "failed" if error_code else "requeued",
                })
            db.commit()
        return outcomes

    def request_cancel(self, run_id: str) -> tuple[bool, Optional[str]]:
        """持久取消旗标（幂等；任意进程可调用）。返回 (changed?, 观测状态)。

        已终态 → (False, status)（幂等 no-op）；旗标已存在 → (False, status)；
        首次写入 → (True, status)。
        """
        with self._factory() as db:
            row = db.execute(
                select(_Run).where(_Run.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                return False, None
            if row.status in {s.value for s in TERMINAL_STATUSES}:
                return False, row.status
            if row.cancel_requested_at is not None:
                return False, row.status
            # UPDATE 同时 guard 状态：SELECT→UPDATE 之间落入终态时旗标
            # 不写（终态 run 的取消是 no-op，与 docstring 一致；round1 m1）
            changed = db.execute(
                update(_Run)
                .where(
                    _Run.id == row.id,
                    _Run.cancel_requested_at.is_(None),
                    _Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]
                                    + [s.value for s in LEASED_STATUSES]),
                )
                .values(cancel_requested_at=_utcnow(), updated_at=_utcnow())
            ).rowcount
            db.commit()
            return bool(changed), row.status

    def cancel_flagged(self, *, limit: int = 32) -> list[str]:
        """取消收敛 sweep：cancel_requested_at 已置位的**未派发** run →
        直接 cancelled 终态（排队中的 run 没有执行体，旗标即终态指令）。
        在跑 run 的取消由执行侧心跳收敛（fencing），本方法不碰。

        幂等：CAS 限定 dispatchable 状态；已被认领/终态的行不动。
        """
        cancelled: list[str] = []
        with self._factory() as db:
            rows = db.execute(
                select(_Run)
                .where(
                    _Run.cancel_requested_at.is_not(None),
                    _Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]),
                )
                .order_by(_Run.id.asc())
                .limit(max(1, int(limit)))
            ).scalars().all()
            now = _utcnow()
            for row in rows:
                rowcount = db.execute(
                    update(_Run)
                    .where(
                        _Run.id == row.id,
                        _Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]),
                    )
                    .values(status=ClusterRunStatus.CANCELLED.value,
                            terminal_at=now, updated_at=now)
                ).rowcount
                if rowcount:
                    cancelled.append(row.run_id)
            db.commit()
        return cancelled

    def request_yield(self, run_id: str) -> bool:
        """持久抢占请求旗标（幂等；跨 coordinator）。"""
        with self._factory() as db:
            row = db.execute(
                select(_Run).where(_Run.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                return False
            if row.yield_requested_at is not None:
                return False
            db.execute(
                update(_Run)
                .where(_Run.id == row.id, _Run.yield_requested_at.is_(None))
                .values(yield_requested_at=_utcnow(), updated_at=_utcnow())
            )
            db.commit()
            return True

    def consume_cancel_if_requested(self, run_id: str, *, epoch: int) -> bool:
        """执行体心跳循环：读取消旗标（不清除 —— 旗标是历史事实，终态转移
        后自然失效）。epoch 校验防旧执行体误读新 attempt 的旗标？—— 不：
        取消语义是 run 级的，跨 attempt 仍成立（用户要的是这个 run 死）。
        """
        row = self.get_run(run_id)
        return bool(row and row["cancel_requested_at"])

    def consume_yield_if_requested(self, run_id: str) -> bool:
        row = self.get_run(run_id)
        return bool(row and row["yield_requested_at"])

    # ------------------------------------------------- control-plane scan

    def get_run_internal(self, run_id: str) -> Optional[dict[str, Any]]:
        """coordinator 专用的完整行读取（含 plan_snapshot/执行身份）。

        **绝不**进任何 REST/工具应答 —— plan 快照与 creator/org 是执行
        输入，不是用户可见投影（用户面走 ``get_run`` / engine 注册表）。
        """
        with self._factory() as db:
            row = db.execute(
                select(_Run).where(_Run.run_id == run_id)
            ).scalar_one_or_none()
            if row is None:
                return None
            proj = _run_projection(row)
            proj.update({
                "plan_snapshot": row.plan_snapshot,
                "creator_id": row.creator_id,
                "org_id": row.org_id,
                "project_id": row.project_id,
                "project_key": row.project_key,
                "coordinator_id": row.coordinator_id,
                "resource_request": row.resource_request if isinstance(
                    row.resource_request, dict
                ) else None,
            })
            return proj

    def scan_dispatchable(self, *, limit: int = 32) -> list[dict[str, Any]]:
        """可派发候选（queued/preempted；控制面信任域扫描，无 owner 过滤）。"""
        with self._factory() as db:
            rows = db.execute(
                select(_Run)
                .where(_Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]))
                .order_by(_Run.priority.desc(), _Run.id.asc())
                .limit(max(1, int(limit)))
            ).scalars().all()
            return [_scan_projection(r) for r in rows]

    def scan_running(self, *, limit: int = 64) -> list[dict[str, Any]]:
        """在跑 run（leased/running；抢占受害者扫描用）。

        排序 = 抢占受害者优先序（低优先级在前；同优先级后启动者在前）。
        """
        with self._factory() as db:
            rows = db.execute(
                select(_Run)
                .where(_Run.status.in_([s.value for s in LEASED_STATUSES]))
                .order_by(_Run.priority.asc(), _Run.started_at.desc())
                .limit(max(1, int(limit)))
            ).scalars().all()
            return [_scan_projection(r) for r in rows]

    def count_runs_by_status(self) -> dict[str, int]:
        """status → 计数（封闭词表；metrics 用）。"""
        with self._factory() as db:
            rows = db.execute(
                select(_Run.status, func.count()).group_by(_Run.status)
            ).all()
            return {status: int(n) for status, n in rows}

    def sum_preempts(self) -> int:
        with self._factory() as db:
            return int(db.execute(
                select(func.coalesce(func.sum(_Run.preempts), 0))
            ).scalar_one())

    def sum_attempts(self) -> int:
        with self._factory() as db:
            return int(db.execute(
                select(func.coalesce(func.sum(_Run.attempts), 0))
            ).scalar_one())

    def tenant_last_dispatch(self) -> dict[str, int]:
        """租户最近派发序（fairness 的可重建轮转状态；无隐藏内存态）。

        只聚合**非终态**行：终态行有 retention 清理（purge_terminal），
        全表聚合会随历史单调恶化 tick 热路径（round2 M1）。租户全部 run
        终态后轮转位归零 —— 新租户优先，公平语义不受影响。
        """
        with self._factory() as db:
            rows = db.execute(
                select(_Run.tenant_key, func.max(_Run.dispatch_seq))
                .where(
                    _Run.tenant_key.is_not(None),
                    _Run.status.notin_([s.value for s in TERMINAL_STATUSES]),
                )
                .group_by(_Run.tenant_key)
            ).all()
            return {tenant: int(seq or 0) for tenant, seq in rows}

    def purge_terminal(self, *, older_than_s: float, limit: int = 256) -> int:
        """终态行 retention（round2 M1）：run 生命周期真相有界，历史证据
        仍在 geocompute_run_evidence（append-once）—— 这里清控制面行。

        V7：同事务级联删除本批 run 的 run_events（V7 round1 #8 —— 终态
        证据 of record 是 evidence 表，events 是有界 trace，随 run 消失）。
        """
        cutoff = _utcnow() - timedelta(seconds=max(60.0, float(older_than_s)))
        with self._factory() as db:
            # PG 无 DELETE ... LIMIT —— 先选 id 再按 id 删（可移植、有界）
            ids = db.execute(
                select(_Run.id)
                .where(
                    _Run.status.in_([s.value for s in TERMINAL_STATUSES]),
                    _Run.terminal_at.is_not(None),
                    _Run.terminal_at < cutoff,
                )
                .order_by(_Run.terminal_at.asc())
                .limit(max(1, int(limit)))
            ).scalars().all()
            if not ids:
                return 0
            run_ids = db.execute(
                select(_Run.run_id).where(_Run.id.in_(ids))
            ).scalars().all()
            deleted = db.execute(
                delete(_Run).where(_Run.id.in_(ids))
            ).rowcount
            if run_ids:
                # 同事务级联（V7 round1 #8 MINOR：部分失败 = trace 丢而行在，
                # 终态行本就等下次 purge —— 单事务把窗口压到零）
                from app.models.db_model import GeoComputeRunEvent as _Event

                db.execute(
                    delete(_Event).where(_Event.run_id.in_([str(r) for r in run_ids]))
                )
            db.commit()
            return int(deleted or 0)

    # ---------------------------------------------------------- workers

    def upsert_worker(
        self,
        worker_id: str,
        *,
        role: str = "worker",
        profiles: Optional[dict[str, int]] = None,
        info: Optional[dict[str, str]] = None,
        capability: Optional[dict[str, Any]] = None,
        ttl_s: float = 60.0,
    ) -> None:
        with self._factory() as db:
            now = _utcnow()
            existing = db.execute(
                select(_Worker).where(_Worker.worker_id == worker_id)
            ).scalar_one_or_none()
            if existing is None:
                db.add(_Worker(
                    worker_id=worker_id, role=role, profiles=profiles or {},
                    capability=capability,
                    heartbeat_at=now, lease_expires_at=now + timedelta(seconds=ttl_s),
                    info=info or {},
                ))
            else:
                db.execute(
                    update(_Worker)
                    .where(_Worker.worker_id == worker_id)
                    .values(profiles=profiles or {},
                            capability=capability,
                            heartbeat_at=now,
                            lease_expires_at=now + timedelta(seconds=ttl_s),
                            info=info or existing.info or {})
                )
            db.commit()

    def worker_heartbeat(self, worker_id: str, *, ttl_s: float = 60.0) -> bool:
        with self._factory() as db:
            rowcount = db.execute(
                update(_Worker)
                .where(_Worker.worker_id == worker_id)
                .values(heartbeat_at=_utcnow(),
                        lease_expires_at=_utcnow() + timedelta(seconds=ttl_s))
            ).rowcount
            db.commit()
            return bool(rowcount)

    def remove_worker(self, worker_id: str) -> bool:
        with self._factory() as db:
            rowcount = db.execute(
                delete(_Worker).where(_Worker.worker_id == worker_id)
            ).rowcount
            db.commit()
            return bool(rowcount)

    def prune_workers(self, *, cutoff: Optional[datetime] = None,
                      ttl_s: Optional[float] = None) -> list[str]:
        """清理失联 worker（心跳过期即视为离开 —— 集群容量随之收缩）。

        返回被清理的 worker_id 列表（V7：调用方据此级联删除其对象缓存
        位置声明，防幽灵位置）。``ttl_s``：V7 起单一 TTL 来源（worker 心跳
        TTL）—— 缺省保留旧 180s 行为（兼容显式调用方）。
        """
        if ttl_s is not None:
            cutoff = _utcnow() - timedelta(seconds=max(5.0, float(ttl_s)))
        else:
            cutoff = cutoff or (_utcnow() - timedelta(seconds=180))
        with self._factory() as db:
            doomed = db.execute(
                select(_Worker.worker_id)
                .where(
                    _Worker.heartbeat_at < cutoff,
                    _Worker.role == "worker",
                )
            ).scalars().all()
            if not doomed:
                return []
            deleted = db.execute(
                _Worker.__table__.delete().where(
                    _Worker.worker_id.in_(doomed),
                    _Worker.heartbeat_at < cutoff,
                )
            ).rowcount
            db.commit()
            return [str(w) for w in doomed] if deleted else []

    def live_workers(
        self, *, role: Optional[str] = None, within_s: Optional[float] = None
    ) -> list[dict[str, Any]]:
        """存活 worker 投影（含 V7 capability）。

        ``within_s`` 缺省 = worker 心跳 TTL（单一容量真相来源；V6 的 90s
        硬编码会造成「心跳已死、容量仍在」的假窗口 —— 审计 M1）。
        """
        if within_s is None:
            try:
                from app.services.geocompute.cluster.workers import _WORKER_TTL_S

                within_s = _WORKER_TTL_S
            except Exception:  # noqa: BLE001 - 循环 import 防御（理论不触发）
                within_s = 30.0
        cutoff = _utcnow() - timedelta(seconds=max(1.0, float(within_s)))
        with self._factory() as db:
            q = select(_Worker).where(_Worker.heartbeat_at >= cutoff)
            if role is not None:
                q = q.where(_Worker.role == role)
            rows = db.execute(q).scalars().all()
            return [
                {
                    "worker_id": w.worker_id,
                    "role": w.role,
                    "profiles": dict(w.profiles or {}),
                    "capability": w.capability if isinstance(
                        w.capability, dict
                    ) else None,
                    "heartbeat_age_s": max(
                        0.0, (_utcnow() - w.heartbeat_at).total_seconds()
                    ),
                }
                for w in rows
            ]

    # ------------------------------------------------- V7 admin / waves

    def waiting_profiles(self) -> dict[str, int]:
        """留队 run 的必需 profile 计数（封闭词表维度；metrics 用）。

        扫描上界 256（与 dispatch batch 同级 —— 热路径常数上界）。
        """
        counts: dict[str, int] = {}
        with self._factory() as db:
            rows = db.execute(
                select(_Run.required_profiles)
                .where(_Run.status.in_([s.value for s in DISPATCHABLE_STATUSES]))
                .limit(256)
            ).scalars().all()
        for profiles in rows:
            for p in profiles or []:
                counts[p] = counts.get(p, 0) + 1
        return counts

    def stuck_runs(self, *, grace_s: float = 5.0, limit: int = 50
                   ) -> list[dict[str, Any]]:
        """stuck 视图：占用 lease 且 lease 已过期但 reclaim 尚未收敛的 run
        （admin 排障用；有界 ≤50 行，绝无载荷）。"""
        now = _utcnow()
        cutoff = now - timedelta(seconds=max(0.0, float(grace_s)))
        with self._factory() as db:
            rows = db.execute(
                select(_Run)
                .where(
                    _Run.status.in_([s.value for s in LEASED_STATUSES]),
                    _Run.lease_expires_at.is_not(None),
                    _Run.lease_expires_at < cutoff,
                )
                .order_by(_Run.lease_expires_at.asc())
                .limit(max(1, int(limit)))
            ).scalars().all()
            return [_scan_projection(r) for r in rows]

    def force_requeue(
        self,
        run_id: str,
        *,
        max_attempts: int = DEFAULT_MAX_RUN_ATTEMPTS,
        ledger: Optional["ClusterLedger"] = None,
    ) -> Optional[str]:
        """admin 强制回队（stuck run 复位）：语义与 ``reclaim_expired`` 的
        单行版本完全一致 —— attempt 预算内 → queued（attempt++）；
        耗尽 → failed[WORKER_LOSS]；同事务精确归还账本。

        返回 "requeued" | "failed" | None（无 CAS 命中 = run 已被并发转移/
        不存在/不在占用态 —— 幂等安全）。
        """
        with self._factory() as db:
            row = db.execute(
                select(_Run).where(_Run.run_id == run_id)
            ).scalar_one_or_none()
            if row is None or row.status not in {
                s.value for s in LEASED_STATUSES
            }:
                return None
            new_attempts = row.attempts + 1
            error_code = run_error_for_reclaim(new_attempts, max_attempts)
            to_status = (
                ClusterRunStatus.FAILED if error_code else ClusterRunStatus.QUEUED
            )
            now = _utcnow()
            rowcount = db.execute(
                update(_Run)
                .where(
                    _Run.id == row.id,
                    _Run.status.in_([s.value for s in LEASED_STATUSES]),
                    _Run.lease_epoch == row.lease_epoch,
                )
                .values(
                    status=to_status.value,
                    attempts=new_attempts,
                    error_code=error_code,
                    coordinator_id=None,
                    lease_expires_at=None,
                    updated_at=now,
                    terminal_at=now if error_code else None,
                )
            ).rowcount
            if not rowcount:
                return None
            if ledger is not None:
                ledger.release_claims(db, _reserved_claims_from_row(row))
                db.execute(
                    update(_Run)
                    .where(_Run.id == row.id)
                    .values(reserved_rows=0, reserved_bytes=0, reserved_units=0)
                )
            db.commit()
            return "failed" if error_code else "requeued"

    # ------------------------------------------------------ leadership

    def acquire_leadership(self, coordinator_id: str, *, ttl_s: float) -> Optional[int]:
        """coordinator leadership CAS（epoch fencing）。

        仲裁条件（单语句，原子评估）：本行 leadership lease 已过期 **且**
        没有任何其他 coordinator 持有未过期 leadership —— 满足才当选并
        epoch+1。返回 leadership epoch；在任者健在 → None（standby）。

        诚实边界：SQLite 写序保证严格串行；PG READ COMMITTED 下两个同时
        首次到场的 coordinator 存在极窄的双当选窗口 —— 但调度权只决定
        谁跑 tick，所有 run 级写路径另有 lease epoch CAS fencing，双
        leader 不产生状态损坏（最坏是重复的 reclaim 尝试被 CAS 拒绝）。
        """
        with self._factory() as db:
            now = _utcnow()
            exists = db.execute(
                select(_Worker.worker_id).where(_Worker.worker_id == coordinator_id)
            ).scalar_one_or_none()
            if exists is None:
                db.add(_Worker(
                    worker_id=coordinator_id, role="coordinator", profiles={},
                    heartbeat_at=now, lease_expires_at=None,
                ))
                db.commit()
        with self._factory() as db:
            now = _utcnow()
            other_leader = (
                select(_Worker.worker_id)
                .where(
                    _Worker.role == "coordinator",
                    _Worker.worker_id != coordinator_id,
                    _Worker.lease_expires_at.is_not(None),
                    _Worker.lease_expires_at >= now,
                )
                .exists()
            )
            rowcount = db.execute(
                update(_Worker)
                .where(
                    _Worker.worker_id == coordinator_id,
                    _Worker.role == "coordinator",
                    (_Worker.lease_expires_at.is_(None))
                    | (_Worker.lease_expires_at < now),
                    ~other_leader,
                )
                .values(lease_epoch=_Worker.lease_epoch + 1,
                        lease_expires_at=now + timedelta(seconds=ttl_s),
                        heartbeat_at=now)
            ).rowcount
            db.commit()
            if rowcount:
                row = db.execute(
                    select(_Worker).where(_Worker.worker_id == coordinator_id)
                ).scalar_one()
                return int(row.lease_epoch)
            return None

    def renew_leadership(self, coordinator_id: str, *, epoch: int, ttl_s: float) -> bool:
        """续约（epoch fencing + 在任者互斥校验）。

        失败语义：epoch 不匹配（本行已被后来者当选过）**或**另一
        coordinator 持有未过期 leadership（failover 已发生，旧 leader 必须
        停止调度）→ False。调用方据此卸任。
        """
        with self._factory() as db:
            now = _utcnow()
            other_leader = (
                select(_Worker.worker_id)
                .where(
                    _Worker.role == "coordinator",
                    _Worker.worker_id != coordinator_id,
                    _Worker.lease_expires_at.is_not(None),
                    _Worker.lease_expires_at >= now,
                )
                .exists()
            )
            rowcount = db.execute(
                update(_Worker)
                .where(
                    _Worker.worker_id == coordinator_id,
                    _Worker.role == "coordinator",
                    _Worker.lease_epoch == epoch,
                    ~other_leader,
                )
                .values(lease_expires_at=_utcnow() + timedelta(seconds=ttl_s),
                        heartbeat_at=_utcnow())
            ).rowcount
            db.commit()
            return bool(rowcount)

    # ---------------------------------------------------------- ledger

    def ledger_snapshot(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """账本读投影（metrics 用；scope 维度按用量排序取前 N —— 封闭基数）。"""
        with self._factory() as db:
            rows = db.execute(
                select(_Usage).order_by(_Usage.updated_at.desc()).limit(max(1, int(limit)))
            ).scalars().all()
            return [
                {
                    "scope_key": u.scope_key,
                    "usage_rows": u.usage_rows,
                    "usage_bytes": u.usage_bytes,
                    "usage_units": u.usage_units,
                    "limit_rows": u.limit_rows,
                    "limit_bytes": u.limit_bytes,
                    "limit_units": u.limit_units,
                }
                for u in rows
            ]


class ClusterLedger:
    """集群资源账本操作（条件 UPDATE 记账；mode=enforcing 时 reserve 拒绝超限）。

    - ``advisory``（默认）：与既有 ``resource_counter.CrossProcessCounter``
      语义一致 —— 超限不阻塞，只记账（诚实暴露集群实际用量）；
    - ``enforcing``：reserve 超限 → False（调用方回滚认领）—— 显式部署决策。

    release 永远钳零（MAX(0, ...)）—— 与状态转移同事务、由 CAS 保证
    exactly-once，钳零只是最后防线。
    """

    def __init__(
        self,
        *,
        enforcing: bool = False,
        limits: Optional[dict[str, dict[str, Optional[int]]]] = None,
        factory: Optional[Callable[[], Any]] = None,
    ):
        #: scope_key → {rows, bytes, units}（None = 不设限）。
        #: 默认与进程内层级并发常数对齐（api.GOVERNOR_*_MAX_CONCURRENCY）。
        self._enforcing = bool(enforcing)
        self._limits = limits or {
            "global": {"rows": None, "bytes": None, "units": 8},
            "tenant": {"rows": None, "bytes": None, "units": 8},
            "project": {"rows": None, "bytes": None, "units": 4},
        }
        self._factory = factory or session_factory

    @property
    def enforcing(self) -> bool:
        return self._enforcing

    def set_scope_limits(
        self,
        scope_key: str,
        *,
        limit_rows: Optional[int],
        limit_bytes: Optional[int],
        limit_units: Optional[int],
    ) -> bool:
        """admin：设置 scope 限额（None = 解除该维限制）。

        幂等 upsert：scope 行缺席时以给定限额预建（与 ensure_scopes 同一
        竞争纪律 —— INSERT 冲突回滚复检）。enforcing/advisory 模式不变。
        """
        if not scope_key or len(scope_key) > 80:
            return False
        with self._factory() as db:
            try:
                existing = db.execute(
                    select(_Usage.scope_key).where(_Usage.scope_key == scope_key)
                ).scalar_one_or_none()
                if existing is None:
                    db.add(_Usage(
                        scope_key=scope_key,
                        usage_rows=0, usage_bytes=0, usage_units=0,
                        limit_rows=limit_rows, limit_bytes=limit_bytes,
                        limit_units=limit_units,
                    ))
                    try:
                        db.commit()
                    except Exception:  # noqa: BLE001 - 并发首用 → 复检后更新
                        db.rollback()
                db.execute(
                    update(_Usage)
                    .where(_Usage.scope_key == scope_key)
                    .values(
                        limit_rows=limit_rows, limit_bytes=limit_bytes,
                        limit_units=limit_units, updated_at=_utcnow(),
                    )
                )
                db.commit()
                return True
            except Exception:  # noqa: BLE001 - admin 操作失败如实返回
                return False

    def ensure_scopes(self, scope_keys: Iterable[str],
                      *, factory: Optional[Callable[[], Any]] = None) -> None:
        """预建 scope 行（**独立短事务**，在 claim/finish/reclaim 事务之前）。

        ``factory``：调用方（store）的会话工厂 —— 账本必须与 run 行同库
        （默认构造的 ledger 在测试/多库环境下会指向错误的数据库）。

        INSERT 竞争（并发首用）在此回滚本辅助会话并复检 —— 决不让
        IntegrityError 毒化调用方的业务事务（round1 M1：SQLAlchemy 2.0
        flush 失败后同 session 必抛 PendingRollbackError）。
        """
        target = factory or self._factory
        for scope_key in set(scope_keys):
            with target() as db:
                if db.execute(
                    select(_Usage.scope_key).where(_Usage.scope_key == scope_key)
                ).scalar_one_or_none() is not None:
                    continue
                limits = self._limits.get(scope_key) or self._limits.get(
                    scope_key.split(":", 1)[0],
                    {"rows": None, "bytes": None, "units": None},
                )
                db.add(_Usage(
                    scope_key=scope_key,
                    usage_rows=0, usage_bytes=0, usage_units=0,
                    limit_rows=limits.get("rows"),
                    limit_bytes=limits.get("bytes"),
                    limit_units=limits.get("units"),
                ))
                try:
                    db.commit()
                except Exception:  # noqa: BLE001 - 并发首用竞争 → 对方已建
                    db.rollback()

    def _ensure_scope_row(self, db: Any, scope_key: str) -> bool:
        """业务事务内的 scope 行检查 —— **只读**，绝不 INSERT（预建见
        ``ensure_scopes``）。缺席（罕见竞争）→ False，调用方按
        enforcing/advisory 语义处理；无 flush 失败即无 session 中毒。"""
        exists = db.execute(
            select(_Usage.scope_key).where(_Usage.scope_key == scope_key)
        ).scalar_one_or_none()
        return exists is not None

    def _scope_limit(self, scope_key: str, dim: str) -> Optional[int]:
        family = scope_key.split(":", 1)[0] if ":" in scope_key else scope_key
        limits = self._limits.get(scope_key) or self._limits.get(family) or {}
        return limits.get(dim)

    def reserve_claims(self, db: Any, claims: dict[str, ResourceClaim]) -> bool:
        """在**调用方事务内**做条件 UPDATE 预留。任一 enforcing 拒绝 → False
        （调用方回滚整个认领）。"""
        for scope_key, claim in claims.items():
            if not any((claim.rows, claim.bytes, claim.units)):
                continue
            if not self._ensure_scope_row(db, scope_key):
                if self._enforcing:
                    return False
                continue  # advisory：记账竞争失败 → 本轮放弃（fail-open 有界）
            values: dict[str, Any] = {"updated_at": _utcnow()}
            conds = []
            if claim.rows:
                values["usage_rows"] = _Usage.usage_rows + claim.rows
                limit = self._scope_limit(scope_key, "rows")
                if limit is not None:
                    conds.append(_Usage.usage_rows + claim.rows <= limit)
            if claim.bytes:
                values["usage_bytes"] = _Usage.usage_bytes + claim.bytes
                limit = self._scope_limit(scope_key, "bytes")
                if limit is not None:
                    conds.append(_Usage.usage_bytes + claim.bytes <= limit)
            if claim.units:
                values["usage_units"] = _Usage.usage_units + claim.units
                limit = self._scope_limit(scope_key, "units")
                if limit is not None:
                    conds.append(_Usage.usage_units + claim.units <= limit)
            q = update(_Usage).where(_Usage.scope_key == scope_key).values(**values)
            if conds:
                from sqlalchemy import and_

                q = q.where(and_(*conds))
            rowcount = db.execute(q).rowcount
            if not rowcount:
                if self._enforcing:
                    return False
                # advisory：条件被拒（超限）→ 补一条无条件记账（诚实暴露超卖）
                db.execute(
                    update(_Usage)
                    .where(_Usage.scope_key == scope_key)
                    .values(**values)
                )
        return True

    def release_claims(self, db: Any, claims: dict[str, ResourceClaim]) -> None:
        """在**调用方事务内**归还（钳零）。claims = 行上记录的 reserved_*。"""
        for scope_key, claim in claims.items():
            if not any((claim.rows, claim.bytes, claim.units)):
                continue
            if not self._ensure_scope_row(db, scope_key):
                continue  # 归还失败钳零兜底仍在（usage 从未累计）
            db.execute(
                update(_Usage)
                .where(_Usage.scope_key == scope_key)
                .values(
                    usage_rows=func.max(0, _Usage.usage_rows - claim.rows),
                    usage_bytes=func.max(0, _Usage.usage_bytes - claim.bytes),
                    usage_units=func.max(0, _Usage.usage_units - claim.units),
                    updated_at=_utcnow(),
                )
            )


# 私有别名已移至模块顶部 import（避免底部 import 的 lint 噪声）。


def _scope_keys_for(global_key: str, tenant_key: Optional[str],
                    project_key: Optional[str]) -> list[str]:
    keys = [global_key]
    if tenant_key:
        keys.append(tenant_key)
    if project_key:
        keys.append(project_key)
    return keys


def _estimate_from_snapshot(plan_snapshot: Optional[dict[str, Any]]
                            ) -> tuple[int, int]:
    est_rows = est_bytes = 0
    for node in (plan_snapshot or {}).get("nodes") or []:
        est = node.get("estimate") or {}
        est_rows += int(est.get("rows") or 0)
        est_bytes += int(est.get("bytes") or 0)
    return est_rows, est_bytes


def _claims_for_row(row: Any, *, units: int) -> dict[str, ResourceClaim]:
    """run 行 → 账本 claim 集（global + tenant + project；估计值钳上限防止
    预留值本身超限造成永久锁死 —— 无自 DoS）。"""
    claims: dict[str, ResourceClaim] = {}
    est_rows = 0
    est_bytes = 0
    for node in (row.plan_snapshot or {}).get("nodes") or []:
        est = node.get("estimate") or {}
        est_rows += int(est.get("rows") or 0)
        est_bytes += int(est.get("bytes") or 0)
    claims["global"] = ResourceClaim(
        scope_key="global", rows=est_rows, bytes=est_bytes, units=units
    )
    if row.tenant_key:
        claims[row.tenant_key] = ResourceClaim(
            scope_key=row.tenant_key, rows=est_rows, bytes=est_bytes, units=units
        )
    if row.project_key:
        claims[row.project_key] = ResourceClaim(
            scope_key=row.project_key, rows=est_rows, bytes=est_bytes, units=units
        )
    return claims


def _reserved_claims_from_row(row: Any) -> dict[str, ResourceClaim]:
    """行上记录的预留值 → 精确归还集（reclaim/finish 共用）。"""
    claims: dict[str, ResourceClaim] = {
        "global": ResourceClaim(
            scope_key="global", rows=row.reserved_rows,
            bytes=row.reserved_bytes, units=row.reserved_units,
        )
    }
    if row.tenant_key:
        claims[row.tenant_key] = ResourceClaim(
            scope_key=row.tenant_key, rows=row.reserved_rows,
            bytes=row.reserved_bytes, units=row.reserved_units,
        )
    if row.project_key:
        claims[row.project_key] = ResourceClaim(
            scope_key=row.project_key, rows=row.reserved_rows,
            bytes=row.reserved_bytes, units=row.reserved_units,
        )
    return claims


def _run_projection(row: Any) -> dict[str, Any]:
    """run 行 → 有界读投影（绝无载荷；plan_snapshot 不出控制面）。"""
    return {
        "run_id": row.run_id,
        "status": row.status,
        "owner_scope": row.owner_scope,
        "plan_fingerprint": row.plan_fingerprint,
        "session_id": row.session_id,
        "priority": row.priority,
        "attempts": row.attempts,
        "preempts": row.preempts,
        "lease_epoch": row.lease_epoch,
        # coordinator_id 是内部拓扑（hostname:pid），不进用户投影
        # （控制面扫描投影 _scan_projection 保留）
        "cancel_requested_at": row.cancel_requested_at.isoformat() + "Z"
        if row.cancel_requested_at else None,
        "yield_requested_at": row.yield_requested_at.isoformat() + "Z"
        if row.yield_requested_at else None,
        "error_code": row.error_code,
        "required_profiles": list(row.required_profiles or []),
        "resource_request": row.resource_request if isinstance(
            row.resource_request, dict
        ) else None,
        "created_at": row.created_at.isoformat() + "Z" if row.created_at else None,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "terminal_at": row.terminal_at.isoformat() + "Z" if row.terminal_at else None,
    }


def _scan_projection(row: Any) -> dict[str, Any]:
    """控制面扫描投影（调度决策输入；比用户投影多 run 内部键，绝无载荷）。"""
    proj = _run_projection(row)
    proj.update({
        "id": row.id,
        "tenant_key": row.tenant_key,
        # 内部拓扑键：仅控制面调度/leader 视图消费，绝不进用户 REST 投影
        "coordinator_id": row.coordinator_id,
        "dispatch_seq": row.dispatch_seq,
        "started_at": row.started_at.isoformat() + "Z" if row.started_at else None,
        "lease_expires_at": (
            row.lease_expires_at.isoformat() + "Z" if row.lease_expires_at else None
        ),
    })
    return proj
