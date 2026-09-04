"""geocompute 节点 × durable-job 运行时互操作（ADR-0096 D5，ADR-0052 修正案）。

设计不变式：
- **不加新任务表、不加第二状态机** —— 节点派发穿过既有
  ``AnalysisTask`` 行（submit_durable_job），终态由既有状态机守卫；
- 取消/超时传播：run 级 CancellationToken 取消 → 对 job 行请求持久取消
  （``request_cancel_sync``），worker 看门狗把它推进任务体 checkpoint；
- 结果经 **session ref** 交接（``result_ref``），载荷绝不进 DB 行
  （与既有 heavy tools 相同的有界交接契约）。

轮询使用 jobs 子系统自己的 session factory（可注入，测试指向临时库），
保持 geocompute 层不直接依赖工具层会话助手。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from app.services.geocompute.errors import (
    DeadlineExceededError,
    NodeExecutionError,
)
from app.services.geocompute.plan import ExecutionNode

logger = logging.getLogger(__name__)

#: job 终态轮询间隔（秒）。eager/本地模式下任务体同步完成，首轮即命中。
_POLL_INTERVAL_S = 0.05


def _default_session_factory():
    from app.services.jobs.worker import _default_session_factory

    return _default_session_factory


#: 可注入的会话工厂（测试替换为临时 SQLite 工厂）。
session_factory: Callable[[], Any] = _default_session_factory


def dispatch_node(
    node: ExecutionNode,
    *,
    session_id: str,
    plan_fingerprint: str,
    deadline_s: Optional[float],
) -> dict[str, Any]:
    """把节点提交为 durable job（幂等键 = 节点语义指纹 + 会话）。"""
    from app.services.geocompute.tasks import run_geocompute_node
    from app.services.jobs.submit import submit_durable_job

    node_dict = node.model_dump(mode="json")
    params = {"node": node_dict, "plan_fingerprint": plan_fingerprint}
    return submit_durable_job(
        celery_task=run_geocompute_node,
        task_type="geocompute_node",
        display_name=f"GeoCompute 节点 {node.node_id}",
        params=params,
        task_kwargs={
            "node": node_dict,
            "session_id": session_id,
            "deadline_s": deadline_s,
        },
        session_id=session_id,
    )


def await_node_job(
    job_id: str,
    *,
    session_id: str,
    deadline_ts: Optional[float],
    cancel_token: Any = None,
) -> dict[str, Any]:
    """等待 durable job 终态并把结果解到节点载荷。

    返回 ``{"payload": ..., "job_id": ...}``；失败/取消以类型化异常上抛
    （NodeExecutionError / DeadlineExceededError / OperationCancelled）。
    """
    from app.lib.cancellation import OperationCancelled
    from app.services.jobs import DurableJobStore
    from app.services.jobs.lifecycle import JobStatus

    cancel_requested = False
    terminal: Optional[dict[str, Any]] = None
    while True:
        factory = session_factory
        with factory() as db:
            job = DurableJobStore.get_sync(db, int(job_id))
            if job is None:
                raise NodeExecutionError(
                    f"durable job {job_id} row does not exist",
                    retry_safe=False,
                    details={"job_id": str(job_id)},
                )
            status = job.status
            if status == JobStatus.completed:
                terminal = {"result_ref": getattr(job, "result_ref", None)}
            error_message = getattr(job, "error_message", None)
        if terminal is not None:
            break
        if status in (JobStatus.failed, JobStatus.stale):
            raise NodeExecutionError(
                error_message or f"durable job {job_id} ended {status}",
                retry_safe=False,
                node_id=None,
                details={"job_id": str(job_id), "job_status": str(status)},
            )
        if status in (JobStatus.cancelled,):
            raise OperationCancelled(f"durable job {job_id} cancelled")
        # run 级取消 → 对 job 请求持久取消（一次）；deadline → 取消并超时
        if cancel_token is not None and cancel_token.cancelled and not cancel_requested:
            with factory() as db:
                DurableJobStore.request_cancel_sync(db, int(job_id))
            cancel_requested = True
        if deadline_ts is not None and time.monotonic() > deadline_ts:
            if not cancel_requested:
                with factory() as db:
                    DurableJobStore.request_cancel_sync(db, int(job_id))
                cancel_requested = True
            raise DeadlineExceededError(
                f"durable job {job_id} exceeded node deadline",
                details={"job_id": str(job_id)},
            )
        time.sleep(_POLL_INTERVAL_S)

    ref = terminal.get("result_ref")
    payload: dict[str, Any] = {}
    if ref:
        from app.services.geocompute._async_bridge import run_coro_sync
        from app.services.session_data import session_data_manager

        stored = run_coro_sync(session_data_manager.get(session_id, ref))
        if stored is None:
            raise NodeExecutionError(
                f"node result ref {ref} is no longer resolvable",
                retry_safe=False,
                details={"job_id": str(job_id), "result_ref": ref},
            )
        payload = {"ref_id": ref, "features": stored, "metadata": {"via": "durable_job"}}
    return {"payload": payload, "job_id": str(job_id)}
