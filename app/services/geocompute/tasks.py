"""GeoCompute 节点的 durable job 任务体（ADR-0096 D5 / ADR-0052 修正案）。

穿透既有 durable-job 运行时：``job_id`` 存在时走状态机（进度落库、取消
从 DB 推进到 checkpoint、重复投递被入口守卫拒绝）。生产派发只经
``submit_durable_job``；``job_id=None`` 仅作为 eager/测试直调路径存在
（任务体显式告警）—— 它没有持久语义，生产调用方不得使用。

结果交接：载荷存 session ref（有界），``finish_job(result_ref=...)`` 把
引用写回 job 行 —— 执行器轮询终态后按 ref 解析载荷。DB 行里只有有界
摘要（redaction 照常生效）。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from app.services.task_queue import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="app.services.geocompute.tasks.run_geocompute_node", bind=True)
def run_geocompute_node(
    self,
    node: dict,
    session_id: Optional[str] = None,
    job_id: Optional[int] = None,
    deadline_s: Optional[float] = None,
) -> dict[str, Any]:
    """执行单个 ExecutionNode（经 ops 注册表），结果落 session ref。"""
    from app.services.geocompute import ops
    from app.services.geocompute.plan import ExecutionNode

    exec_node = ExecutionNode(**node)
    if job_id is None:
        # 直调（无 durable 语义）只允许 eager 测试路径存在；生产派发必经
        # submit_durable_job。这里仍诚实执行，但无状态机保护。
        logger.warning(
            "[geocompute] run_geocompute_node called without job_id (eager/test path)"
        )
        ctx = ops.OperatorContext(
            run_id="direct", node_id=exec_node.node_id, session_id=session_id,
        )
        payload = ops.execute_node(ctx, exec_node, {})
        return _bounded_summary(payload)

    from app.services.jobs.worker import durable_job, finish_job

    with durable_job(job_id, celery_task=self) as job:
        job.progress(5, "GeoCompute 节点开始执行", phase="execute")
        job.ensure_not_cancelled()
        deadline_ts = time.monotonic() + deadline_s if deadline_s else None
        ctx = ops.OperatorContext(
            run_id=f"job-{job_id}",
            node_id=exec_node.node_id,
            session_id=session_id,
            deadline_ts=deadline_ts,
            cancel_token=job.token,  # durable_job 已 use_token；此处显式传入
        )
        payload = ops.execute_node(ctx, exec_node, {})
        job.ensure_not_cancelled()  # 不可逆副作用（落存/登记）前的强制检查

        ref_id = payload.get("ref_id")
        if ref_id is None:
            ref_id = _store_payload(session_id, payload, exec_node)
        payload = dict(payload)
        payload["ref_id"] = ref_id

        result = _bounded_summary(payload)
        finish_job(job_id, result=result, result_ref=ref_id)
        return result


def _store_payload(session_id: Optional[str], payload: dict, node) -> Optional[str]:
    """把节点载荷显式落存为 session ref（大载荷离开执行面的正门）。"""
    data = payload.get("features") or payload.get("rows")
    if data is None or not session_id:
        return None
    from app.services.geocompute._async_bridge import run_coro_sync
    from app.services.session_data import session_data_manager

    return run_coro_sync(
        session_data_manager.store(
            session_id, data, prefix=f"geocompute-node-{node.semantic_fingerprint()}"
        )
    )


def _bounded_summary(payload: dict) -> dict[str, Any]:
    """有界摘要（进 job 行，经 redaction；绝不含载荷本体）。"""
    meta = payload.get("metadata") or {}
    return {
        "rows": len(payload.get("features") or payload.get("rows") or []),
        "ref_id": payload.get("ref_id"),
        "metadata": {
            k: v for k, v in meta.items()
            if isinstance(v, (str, int, float, bool, type(None)))
        },
    }
