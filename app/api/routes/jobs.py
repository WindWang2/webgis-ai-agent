"""统一任务中心 API（ADR-0052，规范 §9 / §10 / §34）。

与既有 `/api/v1/tasks/*` 的关系（expand-contract）：

  * 旧端点全部保留、语义不变 —— `GET /tasks/{task_id}`、`GET /tasks`、
    `DELETE /tasks/{task_id}`、`GET|DELETE /tasks/status/{celery_task_id}`。
    它们是公开 API，本次不删不改。
  * 新增本模块的 `/tasks/jobs*`，提供统一视图：内存 agent task 与 durable GIS job
    在同一个列表里，形状一致（JobView），前端只需处理一种形状。

为什么是独立 router 而不是往 task.py 里塞：`GET /tasks/{task_id}` 会把字面量
`jobs` 当成 task_id 吃掉，所以 `/tasks/jobs` 必须**先**注册。独立模块 + 在
main.py 中先 include，比依赖同文件内的定义顺序更难写错。

安全：所有端点 owner scoped。归属证明有三条链（已认证 user_id / 匿名 owner_token /
已验证归属的 session_id），任一命中即可；三者皆无 → 看不到任何 job。不存在与无权
一律 404，不泄露存在性。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.chat import get_engine
from app.core.auth import get_current_user_optional, get_owner_token, verify_session_owner
from app.core.database import get_async_db
from app.services.task_queue import celery_app
from app.services.jobs import (
    ACTIVE_POLL_INTERVAL_MS,
    DEFAULT_LIST_LIMIT,
    DurableJobStore,
    JobCancelResponse,
    JobListResponse,
    JobNotFound,
    JobRetryResponse,
    JobStatus,
    JobView,
    build_list_response,
    classify_job_id,
    coerce_status,
    job_from_agent_task,
    job_from_record,
    registry as cancellation_registry,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tasks/jobs", tags=["任务管理"])


def _resolved_owner(user: dict | None) -> Optional[str]:
    """把 auth 依赖的返回收敛成 owner_id。匿名用户没有 owner_id。"""
    if not user:
        return None
    user_id = user.get("user_id")
    if not user_id or user_id == "anonymous":
        return None
    return str(user_id)


async def _proven_session_ids(
    db: AsyncSession,
    session_id: Optional[str],
    *,
    owner_id: Optional[str],
    owner_token: Optional[str],
) -> list[str]:
    """校验调用方确实拥有 session_id，通过则返回 [session_id]。

    这一步不能省：session_id 是查询参数，若不验证就直接用作归属谓词，任何人都能
    用别人的 session_id 拉到别人的 job（这正是审计 S33 修过的那类漏洞）。
    """
    if not session_id:
        return []
    # 不属于调用方时 verify_session_owner 抛 404（不泄露存在性）
    await verify_session_owner(db, session_id, user_id=owner_id, owner_token=owner_token)
    return [session_id]


@router.get("", response_model=JobListResponse)
async def list_jobs(
    session_id: Optional[str] = Query(
        default=None,
        description="按会话过滤。匿名调用方必须提供（否则无归属证明，返回空列表）",
    ),
    active_only: bool = Query(default=False, description="只返回未终结的 job"),
    limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=200),
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> JobListResponse:
    """统一任务列表：durable GIS job + 本进程内存中的 agent task。

    浏览器刷新后前端调这个接口恢复任务中心 —— durable job 来自数据库，所以
    API 重启/前端重连都不影响可见性。
    """
    owner_id = _resolved_owner(user)
    session_ids = await _proven_session_ids(
        db, session_id, owner_id=owner_id, owner_token=owner_token
    )

    records = await DurableJobStore.list_for_owner(
        db,
        owner_id=owner_id,
        owner_token=owner_token,
        session_ids=session_ids,
        active_only=active_only,
        limit=limit,
    )
    jobs: list[JobView] = [job_from_record(r) for r in records]

    # agent task 只在证明了 session 归属时才纳入 —— 它们只存在于内存，没有
    # creator_id 可比对，session 归属是唯一可验证的证明链。
    if session_ids:
        try:
            tracker = get_engine().tracker
        except Exception:  # pragma: no cover - 引擎未初始化（单测/降级）
            tracker = None
        if tracker is not None:
            for task_info in tracker.list_by_session(session_ids[0]):
                view = job_from_agent_task(
                    task_info, background_job_ids=task_info.background_job_ids
                )
                if active_only and not view.active:
                    continue
                jobs.append(view)

    jobs.sort(key=lambda j: (j.created_at or ""), reverse=True)
    # has_active 必须基于**全量**归属范围判断：若只看被 limit 截断的这一页，当活跃
    # job 比这一页更旧时会误判「无活跃任务」→ 前端停止轮询 → 运行中的任务卡住。
    has_active_durable = await DurableJobStore.has_active_for_owner(
        db, owner_id=owner_id, owner_token=owner_token, session_ids=session_ids
    )
    page = jobs[:limit]
    response = build_list_response(page)
    if has_active_durable and not response.has_active:
        response.has_active = True
        response.poll_after_ms = ACTIVE_POLL_INTERVAL_MS
    return response


@router.get("/{job_id}", response_model=JobView)
async def get_job(
    job_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> JobView:
    """查询单个 job。durable job id（数字）与 agent task id（task-xxxx）都接受。"""
    owner_id = _resolved_owner(user)

    if classify_job_id(job_id) == "agent":
        task_info = _agent_task_or_404(job_id)
        await verify_session_owner(
            db, task_info.session_id, user_id=owner_id, owner_token=owner_token
        )
        return job_from_agent_task(task_info, background_job_ids=task_info.background_job_ids)

    record = await _durable_or_404(db, job_id, owner_id=owner_id, owner_token=owner_token)
    return job_from_record(record)


@router.delete("/{job_id}", response_model=JobCancelResponse)
async def cancel_job(
    job_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> JobCancelResponse:
    """请求取消 job。

    幂等（规范 §5）：重复取消返回 200 且 ``cancel_requested=false``，不报错。
    终态 job 取消是 no-op —— 绝不把 completed 改回 cancelling。

    取消是两段式：先把 ``cancel_requested_at`` 落库（持久事实，API 重启不丢），再
    点燃本进程的 token 让在飞执行体立即感知。worker 在另一个进程时，靠它自己的
    探针从 DB 读到取消 —— 所以取消不依赖「取消请求恰好落在执行它的那个进程」。
    """
    owner_id = _resolved_owner(user)

    if classify_job_id(job_id) == "agent":
        task_info = _agent_task_or_404(job_id)
        await verify_session_owner(
            db, task_info.session_id, user_id=owner_id, owner_token=owner_token
        )
        already = bool(getattr(task_info, "_cancelled", False))
        get_engine().tracker.cancel(job_id)
        # 级联：本 turn 派生的 durable job 一起请求取消
        for durable_id in task_info.background_job_ids:
            await DurableJobStore.request_cancel(db, durable_id)
            cancellation_registry.cancel(durable_id, "parent agent task cancelled")
        await db.commit()
        return JobCancelResponse(
            id=job_id,
            status=JobStatus.cancelling.value if not already else JobStatus.cancelled.value,
            cancel_requested=not already,
            cancelling=not already,
        )

    record = await _durable_or_404(db, job_id, owner_id=owner_id, owner_token=owner_token)
    changed, current = await DurableJobStore.request_cancel(db, record.id)
    await db.commit()
    # 同进程的执行体（eager Celery / 线程池工具）立即感知；跨进程 worker 走 DB 探针
    cancellation_registry.cancel(str(record.id), "cancelled by user")
    return JobCancelResponse(
        id=str(record.id),
        status=current.value,
        cancel_requested=changed,
        cancelling=(current == JobStatus.cancelling),
    )


@router.post("/{job_id}/retry", response_model=JobRetryResponse)
async def retry_job(
    job_id: str,
    db: AsyncSession = Depends(get_async_db),
    user: dict = Depends(get_current_user_optional),
    owner_token: Optional[str] = Depends(get_owner_token),
) -> JobRetryResponse:
    """重试 failed/stale job，作为**新 attempt**。

    只有 durable job 可重试（agent turn 的重试语义是「用户重发消息」，不在这里）。
    被取消的 job 永不重试（规范 §17）；attempt 递增且保留首次失败的 error_trace
    作为证据（规范 §18）。
    """
    if classify_job_id(job_id) == "agent":
        raise HTTPException(status_code=400, detail="Agent tasks are not retryable; resend the message")

    owner_id = _resolved_owner(user)
    record = await _durable_or_404(db, job_id, owner_id=owner_id, owner_token=owner_token)

    spec = record.dispatch_spec if isinstance(record.dispatch_spec, dict) else None
    ok, reason = await DurableJobStore.start_retry(db, record.id)
    await db.commit()

    if ok:
        # 状态已是 queued —— 现在必须真的把任务重新交给 worker。
        # 只改状态而不入队会留下一个永远不推进的 job（比不提供 retry 更糟）。
        try:
            # 计算隔离不变式 1：send_task publish 是 broker socket I/O，
            # offload 到线程（#386）。
            async_result = await asyncio.to_thread(
                celery_app.send_task,
                spec["task"],
                args=list(spec.get("args") or []),
                kwargs={**(spec.get("kwargs") or {}), "job_id": int(record.id)},
            )
        except Exception as exc:  # noqa: BLE001 —— broker 不可用不应让端点 500
            # 入队本身失败 → 消息不存在，把 job 收敛为 failed 才是真话
            logger.error("[jobs] retry enqueue failed job_id=%s: %s", record.id, exc)
            await DurableJobStore.mark_failed(
                db, record.id, error=exc, message="retry enqueue failed"
            )
            await db.commit()
            ok, reason = False, "enqueue_failed"
        else:
            # 消息已经进 broker：从这里开始**绝不**把 job 标成 failed —— 那会让
            # worker 拿到消息时看到终态而直接跳过，一次已投递的重试被静默丢弃。
            # 回填 celery id 失败只是旧 /tasks/status 端点查不到，不影响执行。
            try:
                await DurableJobStore.attach_celery_task(db, record.id, async_result.id)
                await db.commit()
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.warning(
                    "[jobs] retry enqueued but celery id backfill failed job_id=%s: %s",
                    record.id, exc,
                )

    refreshed = await DurableJobStore.get(db, record.id)
    if refreshed is not None:
        await db.refresh(refreshed)
    status = coerce_status(refreshed.status if refreshed else None)
    return JobRetryResponse(
        id=str(record.id),
        status=status.value,
        retried=ok,
        reason=reason,
        attempt=int((refreshed.attempt if refreshed else 1) or 1),
    )


# ── helpers ─────────────────────────────────────────────────────────


def _agent_task_or_404(task_id: str):
    """取内存 agent task；不存在或无法证明归属一律 404。"""
    try:
        tracker = get_engine().tracker
    except Exception:  # pragma: no cover
        tracker = None
    task_info = tracker.get(task_id) if tracker is not None else None
    if task_info is None:
        raise HTTPException(status_code=404, detail=f"Job {task_id} not found")
    if not task_info.session_id:
        # 无 session 的老任务无法证明归属 —— 统一拒绝（沿用审计 S34 的判断）
        raise HTTPException(status_code=404, detail=f"Job {task_id} not found")
    return task_info


async def _durable_or_404(
    db: AsyncSession, job_id: str, *, owner_id: Optional[str], owner_token: Optional[str]
):
    """按归属取 durable job。

    注意这里**不**接受 session_ids 作为归属证明 —— 单条读取时调用方没有提供
    session_id 参数，只能凭 user_id 或 owner_token。这样匿名用户换个 session
    也拿不到别人的 job。
    """
    try:
        return await DurableJobStore.get_for_owner(
            db, job_id, owner_id=owner_id, owner_token=owner_token
        )
    except JobNotFound:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
