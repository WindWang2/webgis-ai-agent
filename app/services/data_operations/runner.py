"""G23 — asyncio 执行体的 durable job 运行时（G18 范式的提取复用）。

G18（数据交付）把 durable 语义直接内联在 ``data_delivery/service.py``；G23
需要同一范式服务两个域（导入/字段计算），故提取到此模块，语义保持一致：

  * 认领即原子条件迁移 ``pending|queued → running``（带 ``worker_id``）；
    两个副本同时补启动 runner 时只有一个认领成功，另一个直接退出 ——
    这是 at-least-once 执行下 effectively-once 提交的前提之一。
  * 看门狗 asyncio 任务：按周期**轮询 DB 取消事实**（跨副本取消不等待完成
    边界 —— 修 G18 已知弱点 RECON_RISKS R6）并刷新心跳（30s）。每个 tick
    用独立短事务 Session，绝不跨线程/跨协程共享 Session。
  * 终态带 ``worker_id + attempt`` fencing（#1398）：迟到/僵尸执行不能覆盖
    已被取消或已被新 attempt 接管的终态。
  * 进度写入沿用 jobs 的节流与「仅活跃状态可写」语义（规范 §19/§20）。

任务体协议：``body(handle) -> Any``。正常返回 → ``mark_succeeded``（返回值经
脱敏作 result_summary）；抛 :class:`OperationCancelled` → ``confirm_cancelled``；
其它异常 → ``mark_failed`` 后原样上抛。任务体不写终态；需要 G18 式两阶段
收尾（先算完再显式落终态）时传 ``manual_finish=True`` 自行负责终态写入
（同样必须带 fencing）。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from typing import Any, Awaitable, Callable, Optional

from app.lib.cancellation import (
    CancellationToken,
    OperationCancelled,
    registry as cancellation_registry,
    use_token,
)
from app.services.jobs.lifecycle import JobStatus, coerce_status, is_terminal
from app.services.jobs.progress import JobProgress, ProgressThrottle
from app.services.jobs.store import DurableJobStore
from app.services.jobs.worker_lifecycle import default_worker_id

logger = logging.getLogger(__name__)

#: 取消事实轮询周期（对齐 jobs.worker.CANCEL_POLL_INTERVAL_S，加抖动防同步轮询）
CANCEL_POLL_INTERVAL_S = 2.5
#: 心跳周期（远小于 DurableJobStore.DEFAULT_STALE_AFTER_S=300s）
HEARTBEAT_INTERVAL_S = 30.0


class AlreadyFinished(Exception):
    """job 已终态或认领失败，本次执行体不应运行（重复投递/并发补启动）。"""


def _sync_session():
    """同步短会话工厂（runner 的 DB 触点全部是短事务）。"""
    from app.core.database import SessionLocal

    return SessionLocal()


class AsyncJobHandle:
    """暴露给 asyncio 任务体的运行时句柄（对齐 Celery 侧 DurableJobHandle）。"""

    def __init__(
        self,
        job_id: int,
        token: CancellationToken,
        worker_id: str,
        attempt: int,
        *,
        poll_interval: float = CANCEL_POLL_INTERVAL_S,
        heartbeat_interval: float = HEARTBEAT_INTERVAL_S,
        throttle: Optional[ProgressThrottle] = None,
    ) -> None:
        self.job_id = job_id
        self.token = token
        self.worker_id = worker_id
        self.attempt = attempt
        self._poll_interval = poll_interval
        self._heartbeat_interval = heartbeat_interval
        self._throttle = throttle or ProgressThrottle()
        self._watchdog_task: Optional[asyncio.Task] = None
        self._cancel_event: Optional[asyncio.Event] = None
        self.progress_writes = 0

    # ── 取消 ────────────────────────────────────────────────────────────
    @property
    def cancelled(self) -> bool:
        return self.token.cancelled

    def attach_cancel_event(self, event: asyncio.Event) -> None:
        """把领域级取消事件（如导入批次的 asyncio.Event）接到看门狗。

        跨副本取消经 DB 轮询点燃 token 时同步 set 该事件，让领域循环里
        既有 ``event.is_set()`` 检查立即生效。
        """
        self._cancel_event = event

    def checkpoint(self) -> None:
        """安全边界取消检查点（内存读，可密集调用）。"""
        if self.token.cancelled:
            raise OperationCancelled(
                self.token.reason or "job cancelled", job_id=self.job_id
            )

    async def ensure_not_cancelled(self) -> None:
        """不可逆副作用前的强制 DB 复核（对齐 Celery 侧 ensure_not_cancelled）。

        本进程看门狗最多落后一个轮询周期；在「发布产物/写收据」这类点上必须
        同步查一次持久取消事实，不给已取消的 job 留下新副作用（规范 §23）。
        """
        requested = False
        try:
            with _sync_session() as db:
                requested = DurableJobStore.is_cancel_requested_sync(db, self.job_id)
                db.commit()
        except Exception:  # noqa: BLE001 — DB 瞬时不可用：按未取消处理，看门狗兜底
            logger.warning(
                "[data-ops] cancel probe failed job_id=%s", self.job_id, exc_info=True
            )
        if requested and not self.token.cancelled:
            self.token.cancel("cancel requested (durable)")
            self._fire_cancel_event()
        self.checkpoint()

    def _fire_cancel_event(self) -> None:
        if self._cancel_event is not None and not self._cancel_event.is_set():
            self._cancel_event.set()

    # ── 进度 ────────────────────────────────────────────────────────────
    async def progress(
        self,
        value: Optional[int] = None,
        message: Optional[str] = None,
        *,
        phase: Optional[str] = None,
        current_step: Optional[int] = None,
        total_steps: Optional[int] = None,
        force: bool = False,
    ) -> bool:
        """节流写进度（fenced：仅本 worker/attempt 且 job 仍活跃时生效）。

        ``value=None`` 表示 indeterminate —— 不伪造百分比（规范 §19）。
        """
        snapshot = JobProgress(
            progress=value, message=message, phase=phase,
            current_step=current_step, total_steps=total_steps,
        ).clamped()
        if not self._throttle.should_emit(snapshot.progress, force=force):
            return False
        try:
            with _sync_session() as db:
                ok = DurableJobStore.update_progress_sync(
                    db, self.job_id, snapshot,
                    worker_id=self.worker_id, attempt=self.attempt,
                )
                db.commit()
            if ok:
                self.progress_writes += 1
            return ok
        except Exception:  # noqa: BLE001 — 进度丢失不阻断执行
            logger.warning(
                "[data-ops] progress write failed job_id=%s", self.job_id, exc_info=True
            )
            return False

    # ── 看门狗 ──────────────────────────────────────────────────────────
    def _start_watchdog(self) -> None:
        self._watchdog_task = asyncio.get_running_loop().create_task(
            self._watchdog(), name=f"data-ops-watchdog-{self.job_id}"
        )

    async def _stop_watchdog(self) -> None:
        if self._watchdog_task is not None:
            self._watchdog_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._watchdog_task
            self._watchdog_task = None

    async def _watchdog(self) -> None:
        """周期轮询持久取消事实 + 心跳。每 tick 独立短事务。

        取消后循环继续维持心跳（不 return）：确认路径可能仍在收尾，
        清扫不应在这几秒内把 job 误判 stale。
        """
        last_heartbeat = time.monotonic()
        while True:
            await asyncio.sleep(self._poll_interval * random.uniform(0.8, 1.2))
            if not self.token.cancelled:
                try:
                    with _sync_session() as db:
                        requested = DurableJobStore.is_cancel_requested_sync(
                            db, self.job_id
                        )
                        db.commit()
                except Exception:  # noqa: BLE001
                    logger.warning(
                        "[data-ops] watchdog poll failed job_id=%s",
                        self.job_id, exc_info=True,
                    )
                    requested = None
                if requested:
                    self.token.cancel("cancel requested (durable)")
                    self._fire_cancel_event()
                    logger.info("[data-ops] cancel observed job_id=%s", self.job_id)
            now = time.monotonic()
            if now - last_heartbeat >= self._heartbeat_interval:
                last_heartbeat = now
                try:
                    with _sync_session() as db:
                        DurableJobStore.heartbeat_sync(
                            db, self.job_id,
                            worker_id=self.worker_id, attempt=self.attempt,
                        )
                        db.commit()
                except Exception:  # noqa: BLE001 — 瞬时失败由 stale 阈值吸收
                    logger.warning(
                        "[data-ops] heartbeat failed job_id=%s",
                        self.job_id, exc_info=True,
                    )


def _claim(job_id: int, worker_id: str) -> int:
    """认领（pending|queued → running）。返回 attempt；失败抛 AlreadyFinished。"""
    with _sync_session() as db:
        record = DurableJobStore.get_sync(db, job_id)
        if record is None:
            raise AlreadyFinished(f"job {job_id} not found")
        status = coerce_status(record.status)
        if is_terminal(status):
            raise AlreadyFinished(f"job {job_id} already {status.value}")
        if status == JobStatus.cancelling or record.cancel_requested_at is not None:
            DurableJobStore.confirm_cancelled_sync(
                db, job_id, message="cancelled before execution"
            )
            db.commit()
            raise AlreadyFinished(f"job {job_id} cancelled before execution")
        attempt = int(record.attempt or 1)
        claimed = DurableJobStore.mark_running_sync(db, job_id, worker_id=worker_id)
        db.commit()
    if not claimed:
        with _sync_session() as db:
            current = coerce_status(
                getattr(DurableJobStore.get_sync(db, job_id), "status", None)
            )
        raise AlreadyFinished(
            f"job {job_id} could not be claimed (status={current.value})"
        )
    return attempt


@contextlib.asynccontextmanager
async def async_durable_job(
    job_id: int,
    *,
    worker_id: Optional[str] = None,
    session_cancel_event: Optional[asyncio.Event] = None,
):
    """asyncio 执行体上下文：认领 → 看门狗 → yield handle。

    本上下文不写终态（由 :func:`run_async_job` 或任务体负责），只负责
    认领、看门狗与取消 token 的生命周期。
    """
    token = cancellation_registry.register(job_id)
    handle = AsyncJobHandle(job_id, token, worker_id or default_worker_id(), 1)
    handle.attempt = _claim(job_id, handle.worker_id)
    if session_cancel_event is not None:
        handle.attach_cancel_event(session_cancel_event)
    handle._start_watchdog()
    logger.info(
        "[data-ops] started job_id=%s worker=%s attempt=%s",
        job_id, handle.worker_id, handle.attempt,
    )
    try:
        with use_token(token):
            yield handle
    finally:
        await handle._stop_watchdog()
        cancellation_registry.discard(job_id)


async def run_async_job(
    job_id: int,
    body: Callable[[AsyncJobHandle], Awaitable[Any]],
    *,
    worker_id: Optional[str] = None,
    session_cancel_event: Optional[asyncio.Event] = None,
    result_ref_builder: Optional[Callable[[Any], str]] = None,
    manual_finish: bool = False,
) -> JobStatus:
    """包装一次 asyncio 任务体并落 fenced 终态。

    Args:
        body: 任务体；正常返回值经脱敏作 result_summary。
        result_ref_builder: 从返回值构造 result_ref（≤512 字符）。
        manual_finish: True 时 body 自行负责终态（G18 式两阶段收尾）。

    Returns:
        实际落定的终态（cancelling 期间完成 → cancelled，绝不虚报 completed）。
    """
    async with async_durable_job(
        job_id, worker_id=worker_id, session_cancel_event=session_cancel_event
    ) as handle:
        try:
            result = await body(handle)
        except AlreadyFinished:
            raise
        except OperationCancelled:
            with _sync_session() as db:
                ok = DurableJobStore.confirm_cancelled_sync(
                    db, job_id, message="cancelled by user",
                    worker_id=handle.worker_id, attempt=handle.attempt,
                )
                final = (
                    JobStatus.cancelled
                    if ok
                    else DurableJobStore._status_sync(db, job_id)
                )
                db.commit()
            logger.info("[data-ops] cancelled job_id=%s final=%s", job_id, final.value)
            return final
        except BaseException as exc:  # noqa: BLE001 — 未预期异常如实失败
            if handle.token.cancelled:
                # 底层库把取消翻译成自己的异常：按取消收敛，不误报 failed
                with _sync_session() as db:
                    DurableJobStore.confirm_cancelled_sync(
                        db, job_id, message="cancelled by user",
                        worker_id=handle.worker_id, attempt=handle.attempt,
                    )
                    db.commit()
                return JobStatus.cancelled
            with _sync_session() as db:
                DurableJobStore.mark_failed_sync(
                    db, job_id, error=exc,
                    worker_id=handle.worker_id, attempt=handle.attempt,
                )
                db.commit()
            logger.warning(
                "[data-ops] job %s failed: %s: %s", job_id, type(exc).__name__, exc,
                exc_info=exc,
            )
            raise
        if manual_finish:
            return JobStatus.running
        with _sync_session() as db:
            final = DurableJobStore.mark_succeeded_sync(
                db, job_id,
                result=result,
                result_ref=(
                    result_ref_builder(result)[:512] if result_ref_builder else None
                ),
                worker_id=handle.worker_id, attempt=handle.attempt,
            )
            db.commit()
        return final


def submit_async_job(
    *,
    task_type: str,
    display_name: str,
    params: dict[str, Any],
    idempotency_key: Optional[str],
    owner_id: Optional[str] = None,
    owner_token: Optional[str] = None,
    org_id: Optional[int] = None,
    session_id: Optional[str] = None,
    project_id: Optional[str] = None,
) -> tuple[int, str, bool]:
    """幂等创建 durable job 行（同步短事务；G18 语义：进程内 runner、
    不落 dispatch_spec、max_retries=0 —— 重试 = 以同规格重新提交，由幂等键
    兑现，绝不入队一个不存在的 Celery 任务）。

    Returns:
        (job_id, status, idempotent_reuse)
    """
    from app.services.jobs.context import current_origin

    origin = current_origin()
    with _sync_session() as db:
        job = DurableJobStore.create_sync(
            db,
            task_type=task_type,
            display_name=display_name,
            parameters=params,
            idempotency_key=idempotency_key,
            max_retries=0,
            owner_id=owner_id or (origin.owner_id if origin else None),
            owner_token=owner_token or (origin.owner_token if origin else None),
            org_id=org_id or (origin.org_id if origin else None),
            session_id=session_id or (origin.session_id if origin else None),
            project_id=project_id or (origin.project_id if origin else None),
            run_id=(origin.run_id if origin else None),
            turn_id=(origin.turn_id if origin else None),
            tool_call_id=(origin.tool_call_id if origin else None),
            agent_task_id=(origin.agent_task_id if origin else None),
            agent_step_id=(origin.agent_step_id if origin else None),
        )
        job_id = int(job.id)
        status = str(job.status)
        reused = bool(idempotency_key) and (
            job.idempotency_key != idempotency_key
            or status != JobStatus.pending.value
        )
        db.commit()
    if origin is not None:
        origin.record_job(job_id)
    logger.info(
        "[data-ops] submit job_id=%s type=%s status=%s reused=%s",
        job_id, task_type, status, reused,
    )
    return job_id, status, reused


def find_active_job_by_key(idempotency_key: str) -> Optional[tuple[int, str]]:
    """按幂等键查活跃（非终态）job。返回 (job_id, status) 或 None。

    领域恢复判据用它：存在活跃 job ⇒ 另一副本（或本副本的 runner）可能
    正在推进，读路径绝不把状态归一化。
    """
    from sqlalchemy import select

    from app.models.db_model import AnalysisTask

    with _sync_session() as db:
        job = db.execute(
            select(AnalysisTask).where(
                AnalysisTask.idempotency_key == idempotency_key
            )
        ).scalar_one_or_none()
        if job is None:
            return None
        status = coerce_status(job.status)
        if is_terminal(status):
            return None
        job_id, status_value = int(job.id), status.value
        db.commit()
    return job_id, status_value


def find_active_jobs(
    *, task_type: str, session_id: Optional[str], parameter_match: dict[str, Any]
) -> list[tuple[int, str]]:
    """按 task_type + 会话 + 参数子集查全部活跃 job（恢复判据的稳健形式）。

    幂等键只在提交时铸一次；恢复路径（重启/换副本后的任意读）不能假设调用方
    还持有键 —— 按 task_type/session/参数（如 batch_id）扫描活跃行更稳。
    ``parameters`` 是脱敏后的 JSON，只做存在性/等值匹配，不做结构断言。
    """
    from sqlalchemy import select

    from app.models.db_model import AnalysisTask
    from app.services.jobs.lifecycle import ACTIVE_STATUSES

    clauses = [
        AnalysisTask.task_type == task_type,
        AnalysisTask.status.in_([s.value for s in ACTIVE_STATUSES]),
    ]
    if session_id:
        clauses.append(AnalysisTask.session_id == session_id)
    out: list[tuple[int, str]] = []
    with _sync_session() as db:
        rows = db.execute(
            select(AnalysisTask).where(*clauses).order_by(AnalysisTask.id.desc()).limit(20)
        ).scalars().all()
        for job in rows:
            params = job.parameters if isinstance(job.parameters, dict) else {}
            if all(params.get(k) == v for k, v in parameter_match.items()):
                out.append((int(job.id), coerce_status(job.status).value))
        db.commit()
    return out
