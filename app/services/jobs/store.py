"""Durable job store（ADR-0052）。

事实源在 PostgreSQL 的 ``analysis_tasks`` 表，不再是 ``TaskTracker._tasks`` 或
``TaskQueueService._task_owners`` 这两个进程内 dict。API 重启后：

  * 合法 owner 仍能查询自己的 job；
  * 合法 owner 仍能取消自己的 job；
  * 其他用户仍然 404。

并发正确性靠**原子条件更新**而不是 read-modify-write：每次状态迁移都是

    UPDATE analysis_tasks SET status=:target, ...
     WHERE id=:id AND status IN (<legal predecessors of target>)

合法前驱集合由 lifecycle.sources_for() 提供 —— 规则只定义一次。rowcount==0 说明
状态已被别人改走（典型场景：API 取消与 worker 完成同时到达），调用方据此走幂等
分支，而不是盲写覆盖。这直接杜绝了规范 §14 点名的 ``cancelled → completed``。

同时提供 async（API 路径，AsyncSession）与 sync（Celery worker 路径，Session）两套
入口。两套共享同一批语句构造函数，避免规则分叉。
"""
from __future__ import annotations

import logging
import os
import socket
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import Select, and_, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from app.models.db_model import AnalysisTask
from app.services.jobs import redaction
from app.services.jobs.lifecycle import (
    ACTIVE_STATUSES,
    JobKind,
    JobStatus,
    coerce_status,
    is_terminal,
    sources_for,
)
from app.services.jobs.progress import JobProgress

logger = logging.getLogger(__name__)

#: running job 心跳超时。超过此时长未心跳的 running/cancelling job 被判定 stale。
#: 取 5 分钟：远大于正常心跳周期（30s），又不会让用户面对无限 running。
DEFAULT_STALE_AFTER_S = 300
#: 单次任务中心查询返回上限，防止无界扫描。
DEFAULT_LIST_LIMIT = 50
MAX_LIST_LIMIT = 200


def _utcnow() -> datetime:
    # DB 列是 naive DateTime（既有约定），统一存 naive UTC，避免混用导致比较异常
    return datetime.now(timezone.utc).replace(tzinfo=None)


#: 未认证调用方在 auth 层被表示为字面量 "anonymous"（app/core/auth.py）。
#: 它不是 users 表里的一行 —— 直接写进 creator_id 会在 PostgreSQL 上违反外键，
#: 于是匿名用户根本无法创建 durable job（SQLite 因默认不校验 FK 而掩盖了这点）。
#: 归属改由 owner_token + session_id 证明。
_NON_OWNER_IDS = frozenset({"", "anonymous", "none", "null"})


def normalize_owner_id(owner_id: Optional[str]) -> Optional[str]:
    """把伪 owner id 收敛为 None。"""
    if owner_id is None:
        return None
    text = str(owner_id).strip()
    if not text or text.lower() in _NON_OWNER_IDS:
        return None
    return text


def worker_identity() -> str:
    """当前执行者标识。用于 stale 归因与日志。"""
    return f"{socket.gethostname()}:{os.getpid()}"[:128]


class JobNotFound(LookupError):
    """job 不存在或不属于调用方。路由层统一转 404，不泄露存在性。"""


def _ownership_predicate(owner_id: Optional[str], owner_token: Optional[str], session_ids: Sequence[str] | None):
    """构造归属谓词。

    三条合法证明链，任一命中即视为拥有：
      1. creator_id == 已认证 user_id（登录用户自己的 job）
      2. owner_token 精确匹配（匿名会话的能力令牌，镜像 SEC-08）
      3. session_id ∈ 调用方已证明拥有的会话集合（由路由层用
         require_owned_session / verify_session_owner 先证明）

    三条都不成立时返回一个恒假谓词 —— 绝不退化成「无过滤」。
    """
    clauses = []
    owner_id = normalize_owner_id(owner_id)
    if owner_id:
        clauses.append(AnalysisTask.creator_id == owner_id)
    if owner_token:
        clauses.append(AnalysisTask.owner_token == owner_token)
    if session_ids:
        clauses.append(AnalysisTask.session_id.in_(list(session_ids)))
    if not clauses:
        # 恒假：没有任何归属证明的调用方看不到任何 job
        return AnalysisTask.id.is_(None)
    return or_(*clauses)


def _is_reusable(job: AnalysisTask) -> bool:
    """幂等键命中时，这一行是否可以直接复用。

    可复用：仍在推进中（未终态）——重复提交应当汇聚到它；或已成功完成 ——
    重复提交本就该拿到同一个结果。

    **不可**复用：cancelled / failed / stale。用户取消或失败之后再次请求同样的分析，
    必须能真的跑起来；若复用旧行，提交路径会永远返回「已复用」而什么都不执行，而
    cancelled 又永不可 retry —— 那是一个无法脱出的死胡同（除非改参数）。
    """
    status = coerce_status(job.status)
    if not is_terminal(status):
        return True
    return status == JobStatus.completed


class DurableJobStore:
    """analysis_tasks 上的 durable job 仓储。全为静态方法，无自身状态。"""

    # ── 构造 ────────────────────────────────────────────────────────

    @staticmethod
    def build(
        *,
        task_type: str,
        kind: JobKind | str = JobKind.analysis,
        display_name: Optional[str] = None,
        owner_id: Optional[str] = None,
        owner_token: Optional[str] = None,
        org_id: Optional[int] = None,
        session_id: Optional[str] = None,
        project_id: Optional[str] = None,
        parameters: Any = None,
        idempotency_key: Optional[str] = None,
        celery_task_id: Optional[str] = None,
        run_id: Optional[str] = None,
        turn_id: Optional[str] = None,
        tool_call_id: Optional[str] = None,
        agent_task_id: Optional[str] = None,
        agent_step_id: Optional[str] = None,
        max_retries: int = 3,
        status: JobStatus = JobStatus.pending,
        dispatch_spec: Optional[dict] = None,
    ) -> AnalysisTask:
        """构造（未持久化的）job 行。参数经脱敏与体积裁剪。"""
        now = _utcnow()
        kind_value = kind.value if isinstance(kind, JobKind) else str(kind)
        return AnalysisTask(
            task_type=task_type[:50],
            job_kind=kind_value[:20],
            display_name=(display_name or task_type)[:200],
            creator_id=normalize_owner_id(owner_id),
            owner_token=owner_token,
            org_id=org_id,
            session_id=session_id,
            project_id=project_id,
            parameters=redaction.safe_parameters(parameters),
            idempotency_key=idempotency_key,
            celery_task_id=celery_task_id,
            run_id=run_id,
            turn_id=turn_id,
            tool_call_id=tool_call_id,
            agent_task_id=agent_task_id,
            agent_step_id=agent_step_id,
            status=status.value,
            progress=0,
            attempt=1,
            retry_count=0,
            max_retries=max_retries,
            created_at=now,
            updated_at=now,
            dispatch_spec=redaction.safe_dispatch_spec(dispatch_spec),
        )

    @staticmethod
    async def create(db: AsyncSession, **kwargs) -> AnalysisTask:
        """创建 durable job。

        幂等：给了 ``idempotency_key`` 时，若已存在同键行则直接返回既有行 ——
        SSE 重连 / 用户双击 / API retry 不会产生第二个 job，也不会重复执行不可逆
        操作（规范 §16）。并发插入撞唯一约束时回滚并重读。
        """
        from app.services.jobs.context import current_origin

        origin = current_origin()
        if origin is not None:
            kwargs.setdefault("session_id", origin.session_id)
            kwargs.setdefault("owner_id", origin.owner_id)
            kwargs.setdefault("owner_token", origin.owner_token)
            kwargs.setdefault("org_id", origin.org_id)
            kwargs.setdefault("project_id", origin.project_id)
            kwargs.setdefault("run_id", origin.run_id)
            kwargs.setdefault("turn_id", origin.turn_id)
            kwargs.setdefault("tool_call_id", origin.tool_call_id)
            kwargs.setdefault("agent_task_id", origin.agent_task_id)
            kwargs.setdefault("agent_step_id", origin.agent_step_id)

        idem = kwargs.get("idempotency_key")
        if idem:
            existing = await DurableJobStore.get_by_idempotency_key(db, idem)
            if existing is not None:
                if _is_reusable(existing):
                    if origin is not None:
                        origin.record_job(existing.id)
                    return existing
                # 终态且非成功：释放旧行的幂等键，让这次提交能建新行。
                # 旧行保留（含 error_trace / attempt），是失败或取消的历史证据。
                existing.idempotency_key = None
                await db.flush()

        job = DurableJobStore.build(**kwargs)
        db.add(job)
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            if idem:
                existing = await DurableJobStore.get_by_idempotency_key(db, idem)
                if existing is not None:
                    if origin is not None:
                        origin.record_job(existing.id)
                    return existing
            raise

        if origin is not None:
            origin.record_job(job.id)
        logger.info(
            "[jobs] created job_id=%s kind=%s type=%s session=%s agent_task=%s",
            job.id, job.job_kind, job.task_type, job.session_id, job.agent_task_id,
        )
        return job

    @staticmethod
    def create_sync(db: Session, **kwargs) -> AnalysisTask:
        """worker 侧同步创建（Celery 任务内部用 db_session）。"""
        idem = kwargs.get("idempotency_key")
        if idem:
            existing = db.execute(
                select(AnalysisTask).where(AnalysisTask.idempotency_key == idem)
            ).scalar_one_or_none()
            if existing is not None:
                if _is_reusable(existing):
                    return existing
                # 见 create()：cancelled/failed 不可复用，否则重复提交永远是 no-op
                existing.idempotency_key = None
                db.flush()
        job = DurableJobStore.build(**kwargs)
        db.add(job)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            if idem:
                existing = db.execute(
                    select(AnalysisTask).where(AnalysisTask.idempotency_key == idem)
                ).scalar_one_or_none()
                if existing is not None:
                    return existing
            raise
        return job

    # ── 读取 ────────────────────────────────────────────────────────

    @staticmethod
    def _job_id_int(job_id: str | int) -> Optional[int]:
        try:
            return int(job_id)
        except (TypeError, ValueError):
            return None

    @staticmethod
    async def get(db: AsyncSession, job_id: str | int) -> Optional[AnalysisTask]:
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return None
        return (
            await db.execute(select(AnalysisTask).where(AnalysisTask.id == numeric))
        ).scalar_one_or_none()

    @staticmethod
    def get_sync(db: Session, job_id: str | int) -> Optional[AnalysisTask]:
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return None
        return db.execute(select(AnalysisTask).where(AnalysisTask.id == numeric)).scalar_one_or_none()

    @staticmethod
    async def get_by_idempotency_key(db: AsyncSession, key: str) -> Optional[AnalysisTask]:
        return (
            await db.execute(select(AnalysisTask).where(AnalysisTask.idempotency_key == key))
        ).scalar_one_or_none()

    @staticmethod
    async def get_by_celery_id(db: AsyncSession, celery_task_id: str) -> Optional[AnalysisTask]:
        if not celery_task_id:
            return None
        return (
            await db.execute(
                select(AnalysisTask).where(AnalysisTask.celery_task_id == celery_task_id)
            )
        ).scalar_one_or_none()

    @staticmethod
    async def get_for_owner(
        db: AsyncSession,
        job_id: str | int,
        *,
        owner_id: Optional[str] = None,
        owner_token: Optional[str] = None,
        session_ids: Sequence[str] | None = None,
    ) -> AnalysisTask:
        """按归属读取。不存在或不属于调用方一律抛 JobNotFound（→ 404）。"""
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            raise JobNotFound(str(job_id))
        stmt = select(AnalysisTask).where(
            and_(
                AnalysisTask.id == numeric,
                _ownership_predicate(owner_id, owner_token, session_ids),
            )
        )
        job = (await db.execute(stmt)).scalar_one_or_none()
        if job is None:
            raise JobNotFound(str(job_id))
        return job

    @staticmethod
    def _list_stmt(
        *,
        owner_id: Optional[str],
        owner_token: Optional[str],
        session_ids: Sequence[str] | None,
        active_only: bool,
        agent_task_id: Optional[str],
        limit: int,
    ) -> Select:
        stmt = select(AnalysisTask).where(
            _ownership_predicate(owner_id, owner_token, session_ids)
        )
        if active_only:
            stmt = stmt.where(AnalysisTask.status.in_([s.value for s in ACTIVE_STATUSES]))
        if agent_task_id:
            stmt = stmt.where(AnalysisTask.agent_task_id == agent_task_id)
        return stmt.order_by(AnalysisTask.created_at.desc(), AnalysisTask.id.desc()).limit(limit)

    @staticmethod
    async def list_for_owner(
        db: AsyncSession,
        *,
        owner_id: Optional[str] = None,
        owner_token: Optional[str] = None,
        session_ids: Sequence[str] | None = None,
        active_only: bool = False,
        agent_task_id: Optional[str] = None,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> list[AnalysisTask]:
        """归属范围内的 job 列表。始终有 limit 上界。"""
        bounded = max(1, min(int(limit or DEFAULT_LIST_LIMIT), MAX_LIST_LIMIT))
        stmt = DurableJobStore._list_stmt(
            owner_id=owner_id,
            owner_token=owner_token,
            session_ids=session_ids,
            active_only=active_only,
            agent_task_id=agent_task_id,
            limit=bounded,
        )
        return list((await db.execute(stmt)).scalars().all())

    # ── 状态迁移（原子条件更新） ──────────────────────────────────

    @staticmethod
    def _transition_stmt(
        job_id: int,
        target: JobStatus,
        *,
        expected: Iterable[JobStatus] | None = None,
        extra: Optional[dict[str, Any]] = None,
    ):
        allowed = list(expected) if expected is not None else list(sources_for(target))
        values: dict[str, Any] = {"status": target.value, "updated_at": _utcnow()}
        if extra:
            values.update(extra)
        return (
            update(AnalysisTask)
            .where(
                and_(
                    AnalysisTask.id == job_id,
                    AnalysisTask.status.in_([s.value for s in allowed]),
                )
            )
            .values(**values)
        )

    @staticmethod
    async def transition(
        db: AsyncSession,
        job_id: str | int,
        target: JobStatus,
        *,
        expected: Iterable[JobStatus] | None = None,
        **fields: Any,
    ) -> bool:
        """原子迁移。成功返回 True；状态已被别人改走返回 False（不抛错）。"""
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return False
        stmt = DurableJobStore._transition_stmt(numeric, target, expected=expected, extra=fields)
        result = await db.execute(stmt)
        changed = bool(result.rowcount)
        if changed:
            logger.info("[jobs] transition job_id=%s -> %s", numeric, target.value)
        return changed

    @staticmethod
    def transition_sync(
        db: Session,
        job_id: str | int,
        target: JobStatus,
        *,
        expected: Iterable[JobStatus] | None = None,
        **fields: Any,
    ) -> bool:
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return False
        stmt = DurableJobStore._transition_stmt(numeric, target, expected=expected, extra=fields)
        result = db.execute(stmt)
        return bool(result.rowcount)

    # ── 生命周期语义包装 ────────────────────────────────────────────

    @staticmethod
    async def mark_queued(db: AsyncSession, job_id: str | int, *, celery_task_id: Optional[str] = None) -> bool:
        """pending → queued。

        显式限定前驱为 pending：failed/stale → queued 是「重试」语义，必须走
        ``start_retry``（它会递增 attempt），不能被普通入队悄悄绕过。
        """
        fields: dict[str, Any] = {"queued_at": _utcnow()}
        if celery_task_id:
            fields["celery_task_id"] = celery_task_id
        return await DurableJobStore.transition(
            db, job_id, JobStatus.queued, expected=[JobStatus.pending], **fields
        )

    @staticmethod
    def mark_running_sync(db: Session, job_id: str | int, *, worker_id: Optional[str] = None) -> bool:
        now = _utcnow()
        return DurableJobStore.transition_sync(
            db,
            job_id,
            JobStatus.running,
            started_at=now,
            heartbeat_at=now,
            worker_id=(worker_id or worker_identity()),
        )

    @staticmethod
    async def mark_running(db: AsyncSession, job_id: str | int, *, worker_id: Optional[str] = None) -> bool:
        now = _utcnow()
        return await DurableJobStore.transition(
            db,
            job_id,
            JobStatus.running,
            started_at=now,
            heartbeat_at=now,
            worker_id=(worker_id or worker_identity()),
        )

    @staticmethod
    def _terminal_fields(now: datetime) -> dict[str, Any]:
        return {"completed_at": now, "heartbeat_at": None}

    @staticmethod
    async def mark_succeeded(
        db: AsyncSession,
        job_id: str | int,
        *,
        result: Any = None,
        result_ref: Optional[str] = None,
        message: Optional[str] = None,
    ) -> JobStatus:
        """标记成功。返回实际落定的终态。

        关键语义（规范 §14）：job 若已进入 ``cancelling``，worker 的 late success
        **不会**写成 completed —— 迁移表里 cancelling 无法到 completed，这里改为
        收敛到 cancelled 并返回 cancelled，调用方据此清理产物。
        """
        now = _utcnow()
        ok = await DurableJobStore.transition(
            db,
            job_id,
            JobStatus.completed,
            progress=100,
            progress_message=(message or "completed")[:255],
            result_summary=redaction.safe_result(result),
            result_ref=(result_ref[:512] if result_ref else None),
            **DurableJobStore._terminal_fields(now),
        )
        if ok:
            return JobStatus.completed

        current, _started = await DurableJobStore._status_and_started_at(db, job_id)
        if current == JobStatus.cancelling:
            await DurableJobStore.confirm_cancelled(db, job_id, message="late success discarded after cancel")
            logger.info("[jobs] late success discarded job_id=%s (cancelling → cancelled)", job_id)
            # 确认可能被并发清扫抢先 —— 回报真实终态而不是假定 cancelled
            final, _ = await DurableJobStore._status_and_started_at(db, job_id)
            return final
        logger.info("[jobs] mark_succeeded no-op job_id=%s already=%s", job_id, current.value)
        return current

    @staticmethod
    def mark_succeeded_sync(
        db: Session,
        job_id: str | int,
        *,
        result: Any = None,
        result_ref: Optional[str] = None,
        message: Optional[str] = None,
    ) -> JobStatus:
        now = _utcnow()
        ok = DurableJobStore.transition_sync(
            db,
            job_id,
            JobStatus.completed,
            progress=100,
            progress_message=(message or "completed")[:255],
            result_summary=redaction.safe_result(result),
            result_ref=(result_ref[:512] if result_ref else None),
            **DurableJobStore._terminal_fields(now),
        )
        if ok:
            return JobStatus.completed
        current = DurableJobStore._status_sync(db, job_id)
        if current == JobStatus.cancelling:
            DurableJobStore.confirm_cancelled_sync(db, job_id, message="late success discarded after cancel")
            return DurableJobStore._status_sync(db, job_id)
        return current

    @staticmethod
    async def mark_failed(
        db: AsyncSession,
        job_id: str | int,
        *,
        error: BaseException | str | None = None,
        message: Optional[str] = None,
    ) -> bool:
        return await DurableJobStore.transition(
            db,
            job_id,
            JobStatus.failed,
            error_trace=redaction.safe_error(error),
            progress_message=(message or redaction.safe_error(error) or "failed")[:255],
            **DurableJobStore._terminal_fields(_utcnow()),
        )

    @staticmethod
    def mark_failed_sync(
        db: Session,
        job_id: str | int,
        *,
        error: BaseException | str | None = None,
        message: Optional[str] = None,
    ) -> bool:
        return DurableJobStore.transition_sync(
            db,
            job_id,
            JobStatus.failed,
            error_trace=redaction.safe_error(error),
            progress_message=(message or redaction.safe_error(error) or "failed")[:255],
            **DurableJobStore._terminal_fields(_utcnow()),
        )

    # ── 取消 ────────────────────────────────────────────────────────

    @staticmethod
    async def request_cancel(db: AsyncSession, job_id: str | int) -> tuple[bool, JobStatus]:
        """请求取消。返回 (是否发生了状态变化, 当前状态)。

        幂等（规范 §5）：重复取消不报错 —— 第二次返回 (False, cancelling/cancelled)。
        终态 job 取消是 no-op，绝不把 completed 改回 cancelling。
        """
        now = _utcnow()
        changed = await DurableJobStore.transition(
            db,
            job_id,
            JobStatus.cancelling,
            cancel_requested_at=now,
            progress_message="cancelling",
        )
        # 只读列、不经身份映射：批量 UPDATE 之后 session 里可能仍持有这一行的旧 ORM
        # 对象，而 expire_on_commit=False + synchronize_session='evaluate' 会让它
        # 携带被就地改写过的属性。基于那种对象做判断会得出错误结论。
        current, started_at = await DurableJobStore._status_and_started_at(db, job_id)

        # pending job 没有 worker 会去确认取消 —— 直接落 cancelled 终态
        if changed and current == JobStatus.cancelling and started_at is None:
            if await DurableJobStore.confirm_cancelled(db, job_id, message="cancelled before start"):
                return True, JobStatus.cancelled
        return changed, current

    @staticmethod
    async def attach_celery_task(db: AsyncSession, job_id: str | int, celery_task_id: str) -> bool:
        """只回填 celery_task_id（async 版本，retry 重新入队后用）。"""
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None or not celery_task_id:
            return False
        result = await db.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == numeric)
            .values(celery_task_id=celery_task_id, updated_at=_utcnow())
        )
        return bool(result.rowcount)

    @staticmethod
    def set_celery_task_id_sync(db: Session, job_id: str | int, celery_task_id: str) -> bool:
        """只回填 celery_task_id，不触碰 status。

        入队后 worker 可能已经把状态推到 running/completed，此时任何状态迁移都会
        失败；但标识列必须写进去，否则旧的 `/tasks/status/{celery_task_id}` 端点
        永远查不到这个 job。
        """
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None or not celery_task_id:
            return False
        result = db.execute(
            update(AnalysisTask)
            .where(AnalysisTask.id == numeric)
            .values(celery_task_id=celery_task_id, updated_at=_utcnow())
        )
        return bool(result.rowcount)

    @staticmethod
    async def _status_and_started_at(
        db: AsyncSession, job_id: str | int
    ) -> tuple[JobStatus, Optional[datetime]]:
        """列级读取当前状态与 started_at，绕过 ORM 身份映射。"""
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return JobStatus.pending, None
        row = (
            await db.execute(
                select(AnalysisTask.status, AnalysisTask.started_at).where(
                    AnalysisTask.id == numeric
                )
            )
        ).first()
        if row is None:
            return JobStatus.pending, None
        return coerce_status(row[0]), row[1]

    @staticmethod
    def _status_sync(db: Session, job_id: str | int) -> JobStatus:
        """列级读取当前状态（worker 侧）。"""
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return JobStatus.pending
        row = db.execute(
            select(AnalysisTask.status).where(AnalysisTask.id == numeric)
        ).first()
        return coerce_status(row[0] if row else None)

    @staticmethod
    def request_cancel_sync(db: Session, job_id: str | int) -> tuple[bool, JobStatus]:
        """worker 侧请求取消。与 async 版本语义一致，含 pending 直落终态分支。"""
        now = _utcnow()
        changed = DurableJobStore.transition_sync(
            db,
            job_id,
            JobStatus.cancelling,
            cancel_requested_at=now,
            progress_message="cancelling",
        )
        numeric = DurableJobStore._job_id_int(job_id)
        row = None
        if numeric is not None:
            row = db.execute(
                select(AnalysisTask.status, AnalysisTask.started_at).where(
                    AnalysisTask.id == numeric
                )
            ).first()
        current = coerce_status(row[0] if row else None)
        started_at = row[1] if row else None
        if changed and current == JobStatus.cancelling and started_at is None:
            if DurableJobStore.confirm_cancelled_sync(db, job_id, message="cancelled before start"):
                return True, JobStatus.cancelled
        return changed, current

    @staticmethod
    async def confirm_cancelled(
        db: AsyncSession, job_id: str | int, *, message: Optional[str] = None
    ) -> bool:
        """worker 确认取消已生效 → cancelled 终态。"""
        return await DurableJobStore.transition(
            db,
            job_id,
            JobStatus.cancelled,
            progress_message=(message or "cancelled")[:255],
            **DurableJobStore._terminal_fields(_utcnow()),
        )

    @staticmethod
    def confirm_cancelled_sync(
        db: Session, job_id: str | int, *, message: Optional[str] = None
    ) -> bool:
        return DurableJobStore.transition_sync(
            db,
            job_id,
            JobStatus.cancelled,
            progress_message=(message or "cancelled")[:255],
            **DurableJobStore._terminal_fields(_utcnow()),
        )

    @staticmethod
    def is_cancel_requested_sync(db: Session, job_id: str | int) -> bool:
        """worker 侧读取持久取消事实。API 重启不影响它。"""
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return False
        row = db.execute(
            select(AnalysisTask.cancel_requested_at, AnalysisTask.status).where(
                AnalysisTask.id == numeric
            )
        ).first()
        if row is None:
            return False
        cancel_requested_at, status = row
        return cancel_requested_at is not None or coerce_status(status) in (
            JobStatus.cancelling,
            JobStatus.cancelled,
        )

    # ── 进度 / 心跳 ─────────────────────────────────────────────────

    @staticmethod
    def _progress_values(snapshot: JobProgress) -> dict[str, Any]:
        values: dict[str, Any] = {"updated_at": _utcnow(), "heartbeat_at": _utcnow()}
        if snapshot.progress is not None:
            values["progress"] = snapshot.progress
        msg = snapshot.as_message()
        if msg is not None:
            values["progress_message"] = msg
        return values

    @staticmethod
    def _progress_stmt(job_id: int, snapshot: JobProgress):
        # 只更新未终结的 job：stale progress 绝不覆盖终态（规范 §50）
        return (
            update(AnalysisTask)
            .where(
                and_(
                    AnalysisTask.id == job_id,
                    AnalysisTask.status.in_([s.value for s in ACTIVE_STATUSES]),
                )
            )
            .values(**DurableJobStore._progress_values(snapshot))
        )

    @staticmethod
    async def update_progress(db: AsyncSession, job_id: str | int, snapshot: JobProgress) -> bool:
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return False
        result = await db.execute(DurableJobStore._progress_stmt(numeric, snapshot.clamped()))
        return bool(result.rowcount)

    @staticmethod
    def update_progress_sync(db: Session, job_id: str | int, snapshot: JobProgress) -> bool:
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return False
        result = db.execute(DurableJobStore._progress_stmt(numeric, snapshot.clamped()))
        return bool(result.rowcount)

    @staticmethod
    def heartbeat_sync(db: Session, job_id: str | int) -> bool:
        numeric = DurableJobStore._job_id_int(job_id)
        if numeric is None:
            return False
        result = db.execute(
            update(AnalysisTask)
            .where(
                and_(
                    AnalysisTask.id == numeric,
                    AnalysisTask.status.in_([s.value for s in ACTIVE_STATUSES]),
                )
            )
            .values(heartbeat_at=_utcnow())
        )
        return bool(result.rowcount)

    # ── stale 清扫（worker crash 恢复，规范 §25） ──────────────────

    @staticmethod
    def _stale_predicate(cutoff: datetime):
        # running/cancelling 且（心跳超时 或 从未心跳但启动已久）
        return and_(
            AnalysisTask.status.in_([JobStatus.running.value, JobStatus.cancelling.value]),
            or_(
                AnalysisTask.heartbeat_at < cutoff,
                and_(AnalysisTask.heartbeat_at.is_(None), AnalysisTask.started_at < cutoff),
            ),
        )

    #: pending/queued 的「从未被取走」判定阈值。远大于正常入队延迟，
    #: 避免把只是排队较久的 job 误判成孤儿。
    ORPHAN_AFTER_S = 3600

    @staticmethod
    def _orphan_predicate(cutoff: datetime):
        """从未被 worker 取走的 job。

        pending/queued 不会心跳，所以心跳预测器永远抓不到它们。它们的产生场景很实在：
        提交路径在 create 与 apply_async 之间崩溃、broker 丢消息、retry 落库后
        send_task 之前进程退出。此时 job 会永远停在 pending/queued —— 而 queued 不
        可重试，用户除了取消无路可走。
        """
        return and_(
            AnalysisTask.status.in_([JobStatus.pending.value, JobStatus.queued.value]),
            AnalysisTask.created_at < cutoff,
        )

    @staticmethod
    async def sweep_orphans(
        db: AsyncSession, *, orphan_after_s: int | None = None, limit: int = 100
    ) -> int:
        """把长时间没人执行的 pending/queued job 收敛为 failed（可重试）。"""
        seconds = orphan_after_s if orphan_after_s is not None else DurableJobStore.ORPHAN_AFTER_S
        cutoff = _utcnow() - timedelta(seconds=seconds)
        rows = list(
            (
                await db.execute(
                    select(AnalysisTask).where(DurableJobStore._orphan_predicate(cutoff)).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        swept = 0
        for job in rows:
            ok = await DurableJobStore.transition(
                db,
                job.id,
                JobStatus.failed,
                expected=[JobStatus.pending, JobStatus.queued],
                progress_message="never picked up by a worker",
                error_trace="job orphaned: never picked up by a worker",
                **DurableJobStore._terminal_fields(_utcnow()),
            )
            if ok:
                swept += 1
                logger.warning("[jobs] orphaned job_id=%s created_at=%s", job.id, job.created_at)
        return swept

    @staticmethod
    async def has_active_for_owner(
        db: AsyncSession,
        *,
        owner_id: Optional[str] = None,
        owner_token: Optional[str] = None,
        session_ids: Sequence[str] | None = None,
    ) -> bool:
        """归属范围内是否存在活跃 job（不受列表 limit 截断影响）。

        任务中心用它决定要不要继续轮询：若只看被 limit 截断的那一页，当活跃 job 比
        50 条更旧时会误判「没有活跃任务」→ 前端停止轮询 → 正在跑的任务卡住不动。
        """
        stmt = (
            select(AnalysisTask.id)
            .where(_ownership_predicate(owner_id, owner_token, session_ids))
            .where(AnalysisTask.status.in_([s.value for s in ACTIVE_STATUSES]))
            .limit(1)
        )
        return (await db.execute(stmt)).first() is not None

    @staticmethod
    async def find_stale(
        db: AsyncSession, *, stale_after_s: int = DEFAULT_STALE_AFTER_S, limit: int = 100
    ) -> list[AnalysisTask]:
        cutoff = _utcnow() - timedelta(seconds=stale_after_s)
        stmt = select(AnalysisTask).where(DurableJobStore._stale_predicate(cutoff)).limit(limit)
        return list((await db.execute(stmt)).scalars().all())

    @staticmethod
    async def sweep_stale(
        db: AsyncSession, *, stale_after_s: int = DEFAULT_STALE_AFTER_S, limit: int = 100
    ) -> int:
        """把心跳超时的 running job 标记为 stale。返回处理条数。

        stale 是终态但可 retry（新 attempt），所以「worker 挂了」不会让用户永远看到
        running，也不会丢掉重跑的可能。
        """
        stale_jobs = await DurableJobStore.find_stale(db, stale_after_s=stale_after_s, limit=limit)
        swept = 0
        for job in stale_jobs:
            current = coerce_status(job.status)
            if current == JobStatus.cancelling:
                # worker 在确认取消之前就没了：用户的意图是取消，且已经没有执行体
                # 在推进它 —— 收敛到 cancelled 才是真话。
                # （若这里也写 stale，job 会变成「可重试」，等于给被取消的任务开了
                # 一条重跑后门，违反规范 §17。）
                ok = await DurableJobStore.confirm_cancelled(
                    db, job.id, message="cancelled (worker heartbeat lost)"
                )
                target = JobStatus.cancelled
            else:
                ok = await DurableJobStore.transition(
                    db,
                    job.id,
                    JobStatus.stale,
                    progress_message="worker heartbeat lost",
                    error_trace="job marked stale: worker heartbeat lost",
                    **DurableJobStore._terminal_fields(_utcnow()),
                )
                target = JobStatus.stale
            if ok:
                swept += 1
                logger.warning(
                    "[jobs] swept job_id=%s -> %s worker=%s started_at=%s",
                    job.id, target.value, job.worker_id, job.started_at,
                )
        return swept

    # ── 重试（显式新 attempt，规范 §17/§18） ─────────────────────

    @staticmethod
    async def start_retry(db: AsyncSession, job_id: str | int) -> tuple[bool, str]:
        """把 failed/stale job 重新入队为新 attempt。

        返回 (是否成功, 原因)。拒绝条件：
          * cancelled —— 取消绝不自动/手动转 retry（规范 §17）
          * 非 failed/stale 终态或仍活跃
          * retry_count 已达 max_retries
        """
        job = await DurableJobStore.get(db, job_id)
        if job is None:
            return False, "not_found"
        current = coerce_status(job.status)
        if current == JobStatus.cancelled:
            return False, "cancelled_jobs_are_never_retried"
        if current not in (JobStatus.failed, JobStatus.stale):
            return False, f"not_retryable_from_{current.value}"
        if (job.retry_count or 0) >= (job.max_retries or 0):
            return False, "max_retries_exhausted"

        # 没有 dispatch 描述符就无法忠实重跑 —— 与其把状态改成 queued 却永远没有
        # 执行体（用户看到一个永不推进的任务），不如明确拒绝（规范 §9：只有 retry
        # 真正可靠时才提供）。
        if not job.dispatch_spec or not isinstance(job.dispatch_spec, dict):
            return False, "no_dispatch_spec"
        if job.dispatch_spec.get("__truncated__"):
            # 描述符因携带敏感参数或体积超限被丢弃 —— 无法忠实重跑
            return False, "dispatch_spec_unusable"
        if not job.dispatch_spec.get("task"):
            return False, "no_dispatch_spec"

        ok = await DurableJobStore.transition(
            db,
            job_id,
            JobStatus.queued,
            expected=[JobStatus.failed, JobStatus.stale],
            attempt=(job.attempt or 1) + 1,
            retry_count=(job.retry_count or 0) + 1,
            queued_at=_utcnow(),
            started_at=None,
            completed_at=None,
            cancel_requested_at=None,
            heartbeat_at=None,
            progress=0,
            progress_message="requeued for retry",
            # 保留 error_trace：不覆盖第一次失败的证据（规范 §18）
        )
        return ok, ("requeued" if ok else "state_changed_concurrently")

    # ── 归属（替代进程内 _task_owners） ────────────────────────────

    @staticmethod
    async def verify_celery_owner(
        db: AsyncSession,
        celery_task_id: str,
        *,
        owner_id: Optional[str] = None,
        owner_token: Optional[str] = None,
        session_ids: Sequence[str] | None = None,
    ) -> bool:
        """基于 durable 行验证 celery task 归属。API 重启后依然有效。"""
        if not celery_task_id:
            return False
        stmt = select(AnalysisTask.id).where(
            and_(
                AnalysisTask.celery_task_id == celery_task_id,
                _ownership_predicate(owner_id, owner_token, session_ids),
            )
        )
        return (await db.execute(stmt)).first() is not None

    @staticmethod
    def is_terminal_job(job: AnalysisTask) -> bool:
        return is_terminal(job.status)
